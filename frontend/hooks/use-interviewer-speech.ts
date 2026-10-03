"use client";

import { trackedFetch } from "@/lib/analytics";

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
  const completionRef = useRef<(() => void) | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const completePlayback = useCallback(() => {
    const complete = completionRef.current;
    completionRef.current = null;
    complete?.();
  }, []);

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
    completePlayback();
    setSpeaking(false);
  }, [completePlayback]);

  const clear = useCallback(() => {
    stop();
    audioRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  }, [stop]);

  useEffect(() => clear, [clear]);

  const speakOnDevice = useCallback(async (text: string): Promise<boolean> => {
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
    return new Promise<boolean>((resolve) => {
      completionRef.current = () => resolve(true);
      utterance.onstart = () => setSpeaking(true);
      utterance.onend = () => {
        deviceUtteranceRef.current = null;
        setSpeaking(false);
        completePlayback();
      };
      utterance.onerror = () => {
        deviceUtteranceRef.current = null;
        setSpeaking(false);
        setError(
          "The interviewer voice could not be played. Read the response on screen.",
        );
        completePlayback();
      };
      deviceUtteranceRef.current = utterance;
      window.speechSynthesis.speak(utterance);
    });
  }, [completePlayback]);

  const play = useCallback(
    async (path: string, fallbackText: string) => {
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
        const response = await trackedFetch(uploadUrl(path), {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        });
        if (!response.ok) throw new ApiError(await errorDetail(response), response.status);
        const url = URL.createObjectURL(await response.blob());
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onplay = () => setSpeaking(true);
        const finished = new Promise<void>((resolve) => {
          completionRef.current = resolve;
          audio.onended = () => {
            setSpeaking(false);
            completePlayback();
          };
          audio.onerror = () => {
            setSpeaking(false);
            setError("The interviewer voice could not be played.");
            completePlayback();
          };
        });
        setLoading(false);
        await audio.play();
        await finished;
      } catch (failure) {
        completePlayback();
        setLoading(false);
        if (!(await speakOnDevice(fallbackText))) {
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
    [clear, completePlayback, speakOnDevice],
  );

  const speak = useCallback(
    (sessionId: string, turnIndex: number, fallbackText: string) =>
      play(`/interviews/${sessionId}/turns/${turnIndex}/speech`, fallbackText),
    [play],
  );

  const speakReaction = useCallback(
    (sessionId: string, turnIndex: number, fallbackText: string) =>
      play(
        `/interviews/${sessionId}/turns/${turnIndex}/reaction-speech`,
        fallbackText,
      ),
    [play],
  );

  const speakClarification = useCallback(
    (
      sessionId: string,
      turnIndex: number,
      clarificationIndex: number,
      fallbackText: string,
    ) =>
      play(
        `/interviews/${sessionId}/turns/${turnIndex}/clarifications/${clarificationIndex}/speech`,
        fallbackText,
      ),
    [play],
  );

  return {
    speaking,
    loading,
    error,
    speak,
    speakReaction,
    speakClarification,
    stop,
  };
}
