"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, errorDetail, uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";

const REMOTE_SPEECH_TIMEOUT_MS = 20_000;

export function primeInterviewerSpeech(): void {
  if (
    typeof window === "undefined" ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) return;
  const utterance = new SpeechSynthesisUtterance(" ");
  utterance.volume = 0;
  window.speechSynthesis.speak(utterance);
}

export function useInterviewerSpeech() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const deviceUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
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
    if (deviceUtteranceRef.current && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      deviceUtteranceRef.current = null;
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

  const speakOnDevice = useCallback((text: string): boolean => {
    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance === "undefined"
    ) return false;
    const utterance = new SpeechSynthesisUtterance(text);
    const voices = window.speechSynthesis.getVoices();
    utterance.voice =
      voices.find(
        (voice) =>
          voice.lang.toLowerCase().startsWith("en") &&
          /(premium|enhanced|natural|google|microsoft|samantha)/i.test(voice.name),
      ) ?? voices.find((voice) => voice.lang.toLowerCase().startsWith("en")) ?? null;
    utterance.rate = 0.96;
    utterance.pitch = 1;
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => {
      deviceUtteranceRef.current = null;
      setSpeaking(false);
    };
    utterance.onerror = () => {
      deviceUtteranceRef.current = null;
      setSpeaking(false);
      setError("The interviewer voice could not be played. Read the question on screen.");
    };
    deviceUtteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance);
    return true;
  }, []);

  const speak = useCallback(
    async (sessionId: string, turnIndex: number, fallbackText: string) => {
      clear();
      setLoading(true);
      setError("");
      const controller = new AbortController();
      const timeout = window.setTimeout(
        () => controller.abort(),
        REMOTE_SPEECH_TIMEOUT_MS,
      );
      try {
        const token = await accessToken();
        const response = await fetch(
          uploadUrl(`/interviews/${sessionId}/turns/${turnIndex}/speech`),
          {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: controller.signal,
          },
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
        if (!speakOnDevice(fallbackText)) {
          const blocked =
            failure instanceof DOMException && failure.name === "NotAllowedError";
          setError(
            blocked
              ? "Your browser blocked automatic audio. Select Hear question once to enable it."
              : (failure as Error).message || "Interviewer voice is unavailable.",
          );
        }
      } finally {
        window.clearTimeout(timeout);
        setLoading(false);
      }
    },
    [clear, speakOnDevice],
  );

  return { speaking, loading, error, speak, stop };
}
