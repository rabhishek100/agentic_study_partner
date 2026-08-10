"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const MAX_CAPTURE_WIDTH = 1600;

export interface ScreenShare {
  supported: boolean;
  sharing: boolean;
  error: string;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  start(): Promise<void>;
  stop(): void;
  capture(): Promise<Blob>;
}

/** Local-only screen preview. Bytes leave the browser only through capture. */
export function useScreenShare(): ScreenShare {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState("");
  const supported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getDisplayMedia === "function";

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setSharing(false);
  }, []);

  useEffect(() => stop, [stop]);

  const start = useCallback(async () => {
    if (!supported || streamRef.current) return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 5, max: 10 } },
        audio: false,
      });
      const track = stream.getVideoTracks()[0];
      if (!track) {
        stream.getTracks().forEach((item) => item.stop());
        throw new Error("No screen video track was shared.");
      }
      track.addEventListener("ended", stop, { once: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setSharing(true);
    } catch (failure) {
      if (failure instanceof DOMException && failure.name === "NotAllowedError") {
        setError("Screen sharing was cancelled.");
      } else {
        setError("This screen could not be shared.");
      }
    }
  }, [stop, supported]);

  const capture = useCallback(async () => {
    const video = videoRef.current;
    if (!video || !streamRef.current || video.videoWidth === 0) {
      throw new Error("The shared screen is not ready.");
    }
    const scale = Math.min(1, MAX_CAPTURE_WIDTH / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("Capture failed."))),
        "image/jpeg",
        0.82,
      ),
    );
  }, []);

  return { supported, sharing, error, videoRef, start, stop, capture };
}
