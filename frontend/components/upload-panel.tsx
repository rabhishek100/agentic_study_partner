"use client";

import { AlertCircle, FileUp, ListTree, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { OutlineReviewEditor } from "@/components/outline-review";
import { apiFetch } from "@/lib/api";
import { accessToken, supabaseUrl } from "@/lib/supabase";
import type {
  CreateIngestionResponse,
  IngestionJob,
  IngestionJobList,
  IngestionLimitsResponse,
  JobStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// Only a starting guess for the first paint. The API serves the real limit,
// which is what actually gates an upload; hardcoding a second copy here is
// what let a 91 MiB file past the browser and into a Storage 413.
const FALLBACK_MAXIMUM_BYTES = 52_428_800;
// Supabase's resumable endpoint requires exactly 6 MiB chunks (final chunk
// excepted); other sizes are rejected.
const TUS_CHUNK_BYTES = 6 * 1024 * 1024;
const POLL_INTERVAL_MS = 2500;

const ACTIVE_STATUSES = new Set<JobStatus>([
  "queued",
  "validating",
  "parsing",
  "persisting",
  "captioning",
  "chunking",
  "embedding",
  "verifying",
  "classifying",
  "ocr",
  "needs_toc_review",
  "retry_scheduled",
]);

const STATUS_LABELS: Record<JobStatus, string> = {
  awaiting_upload: "Waiting for the upload",
  queued: "Waiting for the worker",
  validating: "Checking the file",
  parsing: "Reading pages",
  persisting: "Saving the book",
  captioning: "Describing figures",
  chunking: "Building search data",
  embedding: "Building semantic search",
  verifying: "Verifying the book",
  classifying: "Classifying the PDF",
  ocr: "Reading scanned pages",
  needs_toc_review: "Contents need review",
  retry_scheduled: "Retrying shortly",
  ready: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

function formatMegabytes(bytes: number) {
  // Storage limits are quoted in MB, not MiB: showing "50 MiB" next to a
  // ceiling the platform states as 50 MB invites exactly the confusion this
  // change removes.
  return Math.round(bytes / 1_000_000);
}

function statusLabel(status: JobStatus | undefined) {
  return status ? (STATUS_LABELS[status] ?? status) : "";
}

function statusVariant(
  status: JobStatus | undefined,
): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (
    status === "cancelled" ||
    status === "retry_scheduled" ||
    status === "needs_toc_review"
  )
    return "outline";
  if (status === "ready") return "default";
  return "secondary";
}

function duration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `${minutes}m ${String(whole % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** Round a countdown to something a reader can act on, never to "0s left". */
function remainingLabel(seconds: number | null) {
  if (seconds == null) return "estimate unavailable";
  if (seconds < 45) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60)
    return `about ${minutes} minute${minutes === 1 ? "" : "s"} left`;
  const hours = Math.floor(minutes / 60);
  return `about ${hours}h ${minutes % 60}m left`;
}

type Phase = "idle" | "uploading" | "processing";

interface TusUpload {
  abort: () => Promise<void>;
  start: () => void;
  findPreviousUploads: () => Promise<unknown[]>;
  resumeFromPreviousUpload: (previous: unknown) => void;
}

export function UploadPanel({ onBookReady }: { onBookReady: () => void }) {
  const [job, setJob] = useState<IngestionJob | null>(null);
  // The create response is narrower than a polled job, so the identity of an
  // in-flight upload is tracked separately until the first poll fills it in.
  const [pending, setPending] = useState<{
    jobId: string;
    status: JobStatus;
    filename: string;
  } | null>(null);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [maximumBytes, setMaximumBytes] = useState(FALLBACK_MAXIMUM_BYTES);
  // Advances once a second so elapsed time moves between the 2.5s polls,
  // instead of the clock visibly freezing and jumping.
  const [now, setNow] = useState(0);

  const uploadRef = useRef<TusUpload | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const notifiedRef = useRef(false);
  const polledAtRef = useRef<{ at: number; elapsed: number } | null>(null);

  const jobId = job?.job_id ?? pending?.jobId;
  const jobStatus = job?.status ?? pending?.status;
  const isActive = jobStatus !== undefined && ACTIVE_STATUSES.has(jobStatus);
  const waitingForReview = jobStatus === "needs_toc_review";

  // The upload ceiling is a deployment setting, not a constant, so it is read
  // from the API rather than compiled in. On failure the fallback still
  // rejects oversized files; it just may not name the exact number.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const limits =
          await apiFetch<IngestionLimitsResponse>("/ingestions/limits");
        if (!cancelled && limits.maximum_bytes > 0) {
          setMaximumBytes(limits.maximum_bytes);
        }
      } catch {
        // Keep the fallback; the API rejects an oversized file regardless.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Reattach to a job still running from an earlier visit. The job lives in
  // Postgres, not in this tab, so a reload or a different device should pick
  // up exactly where the worker has got to.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { jobs } = await apiFetch<IngestionJobList>("/ingestions?limit=5");
        const active = jobs.find((entry) => ACTIVE_STATUSES.has(entry.status));
        if (active && !cancelled) {
          setJob(active);
          setPhase("processing");
        }
      } catch {
        // Nothing to reattach to; the panel stays in its idle state.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Durable polling: the job lives in Postgres, so refreshing the page and
  // polling again shows the same truth the worker is writing.
  useEffect(() => {
    if (!jobId || !isActive) return;
    const timer = setInterval(async () => {
      try {
        setJob(await apiFetch<IngestionJob>(`/ingestions/${jobId}`));
      } catch {
        // Transient poll failures keep the last known state on screen.
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [jobId, isActive]);

  useEffect(() => {
    if (!jobId || !isActive) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [jobId, isActive]);

  const elapsedSeconds = job?.timing?.elapsed_seconds;
  useEffect(() => {
    if (elapsedSeconds == null) return;
    polledAtRef.current = { at: Date.now(), elapsed: elapsedSeconds };
  }, [elapsedSeconds]);

  useEffect(() => {
    if (jobStatus === "ready" && !notifiedRef.current) {
      notifiedRef.current = true;
      setPhase("idle");
      onBookReady();
    }
    if (jobStatus === "failed" || jobStatus === "cancelled") {
      setPhase("idle");
    }
  }, [jobStatus, onBookReady]);

  async function startUpload(file: File) {
    setError("");
    setJob(null);
    setPending(null);
    setUploadPercent(null);
    notifiedRef.current = false;

    if (file.type !== "application/pdf") {
      setError("Only PDF files are supported.");
      return;
    }
    if (file.size > maximumBytes) {
      setError(
        `This file is ${formatMegabytes(file.size)} MB, over the ` +
          `${formatMegabytes(maximumBytes)} MB upload limit.`,
      );
      return;
    }

    setPhase("uploading");
    try {
      // The TUS client is only needed once a file is chosen, so it stays out
      // of the initial bundle; the token fetch is independent of the create.
      const [created, token, tus] = await Promise.all([
        apiFetch<CreateIngestionResponse>("/ingestions", {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            original_filename: file.name,
            content_type: "application/pdf",
            content_length: file.size,
          }),
        }),
        accessToken(),
        import("tus-js-client"),
      ]);
      setPending({
        jobId: created.job_id,
        status: created.status,
        filename: file.name,
      });

      await new Promise<void>((resolve, reject) => {
        const upload = new tus.Upload(file, {
          endpoint: `${supabaseUrl}/storage/v1/upload/resumable`,
          chunkSize: TUS_CHUNK_BYTES,
          retryDelays: [0, 1000, 3000, 5000],
          removeFingerprintOnSuccess: true,
          headers: {
            authorization: `Bearer ${token}`,
            "x-upsert": "false",
          },
          metadata: {
            bucketName: created.storage_bucket,
            objectName: created.storage_path,
            contentType: "application/pdf",
          },
          onProgress: (sent: number, total: number) => {
            setUploadPercent(Math.round((sent / total) * 100));
          },
          onError: reject,
          onSuccess: () => resolve(),
        }) as unknown as TusUpload;
        uploadRef.current = upload;
        // Resume an interrupted upload of this same file when one exists.
        upload.findPreviousUploads().then((previous) => {
          if (previous.length > 0) {
            upload.resumeFromPreviousUpload(previous[0]);
          }
          upload.start();
        });
      });
      uploadRef.current = null;

      setJob(
        await apiFetch<IngestionJob>(`/ingestions/${created.job_id}/complete`, {
          method: "POST",
        }),
      );
      setPhase("processing");
    } catch (caught) {
      uploadRef.current = null;
      setPhase("idle");
      setError((caught as Error).message || "The upload failed.");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function cancel() {
    setError("");
    if (uploadRef.current) {
      // Mid-upload: stop sending bytes, then cancel the reserved job.
      await uploadRef.current.abort();
      uploadRef.current = null;
    }
    if (!jobId) {
      setPhase("idle");
      return;
    }
    try {
      setJob(
        await apiFetch<IngestionJob>(`/ingestions/${jobId}/cancel`, {
          method: "POST",
        }),
      );
    } catch (caught) {
      setError((caught as Error).message || "Could not cancel the job.");
    }
  }

  async function retry() {
    setError("");
    try {
      const requeued = await apiFetch<IngestionJob>(
        `/ingestions/${jobId}/retry`,
        { method: "POST" },
      );
      notifiedRef.current = false;
      setJob(requeued);
      setPhase("processing");
    } catch (caught) {
      setError((caught as Error).message || "Could not retry the job.");
    }
  }

  const busy = phase !== "idle";
  const showProgress =
    busy || jobStatus === "failed" || jobStatus === "cancelled";
  const timing = job?.timing;

  // The server's elapsed time is a snapshot from the last poll; advance it
  // locally so the clock moves every second rather than every 2.5.
  const sincePoll =
    polledAtRef.current && now ? (now - polledAtRef.current.at) / 1000 : 0;
  const liveElapsed = timing ? timing.elapsed_seconds + sincePoll : null;
  const liveRemaining =
    timing?.estimated_remaining_seconds == null
      ? null
      : Math.max(0, timing.estimated_remaining_seconds - sincePoll);

  const filename = job?.original_filename ?? pending?.filename;

  return (
    <section aria-labelledby="upload-heading" className="space-y-2">
      <h2
        id="upload-heading"
        className="text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        Add a book
      </h2>

      <label
        htmlFor="book-file"
        className={cn(
          "flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border border-dashed border-input bg-card px-4 py-5 text-center text-sm font-medium transition-colors",
          busy
            ? "cursor-default opacity-60"
            : "hover:border-primary hover:bg-accent",
        )}
      >
        {waitingForReview ? (
          <ListTree className="size-4 text-muted-foreground" aria-hidden />
        ) : busy ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <FileUp className="size-4 text-muted-foreground" aria-hidden />
        )}
        {waitingForReview
          ? "Finish the contents review"
          : busy
            ? "Working on your book…"
            : "Choose a PDF"}
        <span className="text-xs font-normal text-muted-foreground">
          Up to {formatMegabytes(maximumBytes)} MB
        </span>
        <input
          ref={fileInputRef}
          id="book-file"
          type="file"
          accept="application/pdf"
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) startUpload(file);
          }}
        />
      </label>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Digital PDFs and scanned books. A scan is transcribed page by page,
        which takes a few minutes and needs its contents confirmed before it
        can be read.
      </p>

      {showProgress && jobStatus && (
        <div
          role="status"
          className="space-y-2 rounded-lg border border-border bg-card p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={statusVariant(jobStatus)}>
              {phase === "uploading"
                ? `Uploading ${uploadPercent ?? 0}%`
                : statusLabel(jobStatus)}
            </Badge>
            {filename && (
              <span className="min-w-0 break-all text-xs text-muted-foreground">
                {filename}
              </span>
            )}
          </div>

          {phase === "uploading" && uploadPercent !== null && (
            <Progress value={uploadPercent} aria-label="Upload progress" />
          )}

          {phase === "processing" &&
            timing &&
            jobStatus !== "needs_toc_review" && (
            <>
              <Progress
                value={timing.percent}
                aria-label="Processing progress"
              />
              <p className="text-xs text-muted-foreground">
                <strong className="font-semibold text-foreground">
                  {Math.round(timing.percent)}%
                </strong>
                {" · "}
                {duration(liveElapsed)} elapsed
                {timing.overrunning
                  ? " · taking longer than expected"
                  : ` · ${remainingLabel(liveRemaining)}`}
              </p>
              {job?.progress.total != null && job.progress.completed > 0 && (
                <p className="text-xs text-muted-foreground">
                  {job.progress.completed} / {job.progress.total}{" "}
                  {job.progress.unit ?? ""}
                </p>
              )}
              {job?.page_count != null && job.progress.completed === 0 && (
                <p className="text-xs text-muted-foreground">
                  {job.page_count} pages
                </p>
              )}

              <ol className="space-y-1 border-t border-border pt-2">
                {timing.stages.map((entry) => (
                  <li
                    key={entry.stage}
                    className="grid grid-cols-[0.6rem_minmax(0,1fr)_auto] items-center gap-2 text-xs"
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "size-2 justify-self-center rounded-full border",
                        entry.state === "done" &&
                          "border-positive bg-positive",
                        entry.state === "active" &&
                          "animate-pulse border-citation bg-citation",
                        entry.state === "pending" && "border-input",
                      )}
                    />
                    <span
                      className={cn(
                        "truncate",
                        entry.state === "pending" && "text-muted-foreground",
                        entry.state === "active" &&
                          "font-semibold text-foreground",
                      )}
                    >
                      {entry.label}
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      {entry.state === "active"
                        ? duration(entry.elapsed_seconds)
                        : entry.state === "pending"
                          ? `~${duration(entry.expected_seconds)}`
                          : "done"}
                    </span>
                  </li>
                ))}
              </ol>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Times are estimates. You can close this page; processing
                continues and progress is restored when you return.
              </p>
            </>
          )}

          {jobStatus === "needs_toc_review" && jobId && (
            <div className="space-y-2 border-t border-border pt-2">
              <p className="text-xs leading-relaxed text-muted-foreground">
                The embedded contents were incomplete or unsafe. Review the
                headings inferred from the PDF before parsing continues.
              </p>
              <OutlineReviewEditor
                jobId={jobId}
                onConfirmed={(confirmed) => {
                  notifiedRef.current = false;
                  setJob(confirmed);
                  setPhase("processing");
                }}
              />
            </div>
          )}

          {jobStatus === "failed" && job?.error && (
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription>
                {job.error.message}
                {job.retryable && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={retry}
                  >
                    Try again
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          )}

          {busy && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={cancel}
            >
              Cancel
            </Button>
          )}
        </div>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </section>
  );
}
