"use client";

import { useEffect, useRef, useState } from "react";

import { apiFetch } from "../../lib/api";
import { accessToken, supabaseUrl } from "../../lib/supabase";

const MAXIMUM_BYTES = 52_428_800; // enforced again by the bucket, API, worker
// Supabase's resumable endpoint requires exactly 6 MiB chunks (final chunk
// excepted); other sizes are rejected.
const TUS_CHUNK_BYTES = 6 * 1024 * 1024;
const POLL_INTERVAL_MS = 2500;

const ACTIVE_STATUSES = new Set([
  "queued",
  "validating",
  "parsing",
  "persisting",
  "chunking",
  "embedding",
  "verifying",
  "retry_scheduled",
]);

const STATUS_LABELS = {
  awaiting_upload: "Waiting for the upload",
  queued: "Waiting for the worker",
  validating: "Checking the file",
  parsing: "Reading pages",
  persisting: "Saving the book",
  chunking: "Building search data",
  embedding: "Building semantic search",
  verifying: "Verifying the book",
  retry_scheduled: "Retrying shortly",
  ready: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status;
}

export default function UploadPanel({ onBookReady }) {
  const [job, setJob] = useState(null);
  const [uploadPercent, setUploadPercent] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | uploading | processing
  const [error, setError] = useState("");
  const uploadRef = useRef(null);
  const fileInputRef = useRef(null);
  const notifiedRef = useRef(false);

  const jobId = job?.job_id;
  const jobStatus = job?.status;

  // Durable polling: the job lives in Postgres, so refreshing the page and
  // polling again shows the same truth the worker is writing.
  useEffect(() => {
    if (!jobId || !ACTIVE_STATUSES.has(jobStatus)) return undefined;
    const timer = setInterval(async () => {
      try {
        const current = await apiFetch(`/ingestions/${jobId}`);
        setJob(current);
      } catch {
        // Transient poll failures keep the last known state on screen.
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [jobId, jobStatus]);

  useEffect(() => {
    if (jobStatus === "ready" && !notifiedRef.current) {
      notifiedRef.current = true;
      setPhase("idle");
      onBookReady?.();
    }
    if (jobStatus === "failed" || jobStatus === "cancelled") {
      setPhase("idle");
    }
  }, [jobStatus, onBookReady]);

  async function startUpload(file) {
    setError("");
    setJob(null);
    setUploadPercent(null);
    notifiedRef.current = false;

    if (file.type !== "application/pdf") {
      setError("Only PDF files are supported.");
      return;
    }
    if (file.size > MAXIMUM_BYTES) {
      setError("This file is larger than the 50 MiB upload limit.");
      return;
    }

    setPhase("uploading");
    try {
      // The TUS client is only needed once a file is chosen, so it stays out
      // of the initial bundle; the token fetch is independent of the create.
      const [created, token, tus] = await Promise.all([
        apiFetch("/ingestions", {
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
      setJob({ job_id: created.job_id, status: created.status });
      await new Promise((resolve, reject) => {
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
          onProgress: (sent, total) => {
            setUploadPercent(Math.round((sent / total) * 100));
          },
          onError: reject,
          onSuccess: resolve,
        });
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

      const queued = await apiFetch(`/ingestions/${created.job_id}/complete`, {
        method: "POST",
      });
      setJob(queued);
      setPhase("processing");
    } catch (uploadError) {
      uploadRef.current = null;
      setPhase("idle");
      setError(uploadError.message || "The upload failed.");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function cancel() {
    setError("");
    if (uploadRef.current) {
      // Mid-upload: stop sending bytes, then cancel the reserved job.
      uploadRef.current.abort();
      uploadRef.current = null;
    }
    if (!jobId) {
      setPhase("idle");
      return;
    }
    try {
      setJob(await apiFetch(`/ingestions/${jobId}/cancel`, { method: "POST" }));
    } catch (cancelError) {
      setError(cancelError.message || "Could not cancel the job.");
    }
  }

  async function retry() {
    setError("");
    try {
      const requeued = await apiFetch(`/ingestions/${jobId}/retry`, {
        method: "POST",
      });
      notifiedRef.current = false;
      setJob(requeued);
      setPhase("processing");
    } catch (retryError) {
      setError(retryError.message || "Could not retry the job.");
    }
  }

  const busy = phase !== "idle";
  const showProgress =
    busy || jobStatus === "failed" || jobStatus === "cancelled";
  const processingPercent = job?.progress?.percent;

  return (
    <div className="upload" aria-label="Upload a book">
      <p className="section-label">Add a book</p>

      <label
        className={`upload-drop ${busy ? "disabled" : ""}`}
        htmlFor="book-file"
      >
        {busy ? "Working on your book…" : "Choose a PDF (up to 50 MiB)"}
        <input
          ref={fileInputRef}
          id="book-file"
          type="file"
          accept="application/pdf"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) startUpload(file);
          }}
        />
      </label>
      <p className="upload-note">
        Digital PDFs with a table of contents. Scanned books are not supported
        yet.
      </p>

      {showProgress && job && (
        <div className="upload-status" role="status">
          <div className="upload-status-row">
            <span className={`status-chip ${jobStatus}`}>
              {phase === "uploading"
                ? `Uploading ${uploadPercent ?? 0}%`
                : statusLabel(jobStatus)}
            </span>
            {job.original_filename && <span>{job.original_filename}</span>}
          </div>

          {phase === "uploading" && uploadPercent !== null && (
            <progress max="100" value={uploadPercent} />
          )}
          {phase === "processing" &&
            (processingPercent != null ? (
              <progress max="100" value={processingPercent} />
            ) : (
              <progress />
            ))}
          {phase === "processing" && job.progress?.total != null && (
            <p className="upload-detail">
              {job.progress.completed} / {job.progress.total}{" "}
              {job.progress.unit || ""}
            </p>
          )}

          {jobStatus === "failed" && job.error && (
            <div className="error">
              {job.error.message}
              {job.retryable && (
                <button className="retry" type="button" onClick={retry}>
                  Try again
                </button>
              )}
            </div>
          )}

          {busy && (
            <button className="clear" type="button" onClick={cancel}>
              Cancel
            </button>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}
    </div>
  );
}
