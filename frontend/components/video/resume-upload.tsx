"use client";

import { AlertCircle, Loader2, Upload } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";

/**
 * Finish an upload whose reservation was created but never filled.
 *
 * Without this a video stuck at `awaiting_upload` was unreachable: the add
 * form only ever mints a new reservation, so the only way out was to delete
 * the video and start over — and the interface did not even say so, because
 * it reported the untouched reservation as work in progress.
 *
 * The server checks the bytes against the reservation, so this has to be the
 * same file the video was created for; a different one is refused rather than
 * silently ingested under the wrong name.
 */
export function ResumeUpload({
  jobId,
  expectedFilename,
  onUploaded,
}: {
  jobId: string;
  expectedFilename: string | null;
  onUploaded(): void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send() {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      const token = await accessToken();
      const response = await fetch(
        uploadUrl(`/video-ingestions/${jobId}/source`),
        {
          method: "PUT",
          headers: {
            "Content-Type": file.type || "video/mp4",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: file,
        },
      );
      if (!response.ok) {
        const raw = await response.text();
        let detail = raw.slice(0, 200);
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? detail;
        } catch {
          // keep the raw body
        }
        throw new Error(
          response.status === 422
            ? `${detail} — this must be the same file the video was created for${
                expectedFilename ? ` (${expectedFilename})` : ""
              }.`
            : detail,
        );
      }
      setFile(null);
      onUploaded();
    } catch (caught) {
      setError((caught as Error).message || "The upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-border bg-surface p-3">
      <div className="space-y-1.5">
        <Label htmlFor="resume-upload">
          Finish this upload
          {expectedFilename ? ` — ${expectedFilename}` : ""}
        </Label>
        <Input
          id="resume-upload"
          type="file"
          accept="video/mp4,video/webm,video/quicktime"
          disabled={busy}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Button size="sm" onClick={send} disabled={busy || file === null}>
        {busy ? (
          <Loader2 aria-hidden className="animate-spin" />
        ) : (
          <Upload aria-hidden />
        )}
        {busy ? "Uploading…" : "Upload video"}
      </Button>
      {busy ? (
        <p className="text-xs text-muted-foreground" role="status">
          Sending the file. A long lecture takes a few minutes; leave this tab
          open until it finishes.
        </p>
      ) : null}
    </div>
  );
}
