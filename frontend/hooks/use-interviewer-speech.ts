"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, errorDetail, uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";

export function useInterviewerSpeech() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setSpeaking(false);
  }, []);

  const clear = useCallback(() => {
    stop();
    audioRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  }, [stop]);

  useEffect(() => clear, [clear]);

  const speak = useCallback(
    async (sessionId: string, turnIndex: number) => {
      clear();
      setLoading(true);
      setError("");
      try {
        const token = await accessToken();
        const response = await fetch(
          uploadUrl(`/interviews/${sessionId}/turns/${turnIndex}/speech`),
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        );
        if (!response.ok) throw new ApiError(await errorDetail(response), response.status);
        const url = URL.createObjectURL(await response.blob());
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onplay = () => setSpeaking(true);
        audio.onended = () => setSpeaking(false);
        audio.onerror = () => {
          setSpeaking(false);
          setError("The interviewer voice could not be played.");
        };
        await audio.play();
      } catch (failure) {
        const blocked =
          failure instanceof DOMException && failure.name === "NotAllowedError";
        setError(
          blocked
            ? "Your browser blocked automatic audio. Select Hear question once to enable it."
            : (failure as Error).message || "Interviewer voice is unavailable.",
        );
      } finally {
        setLoading(false);
      }
    },
    [clear],
  );

  return { speaking, loading, error, speak, stop };
}
