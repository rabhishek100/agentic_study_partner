"use client";

import { AudioLines, CheckCircle2, Loader2, Mic, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMicrophones } from "@/hooks/use-microphones";
import { microphoneProblem } from "@/lib/dictation";
import { openSpeechMicrophone } from "@/lib/microphone";
import { cn } from "@/lib/utils";

const DEFAULT_DEVICE = "__system_default__";

export function MicrophoneSetup({
  disabled = false,
  onAccessChange,
}: {
  disabled?: boolean;
  onAccessChange?: (granted: boolean) => void;
}) {
  const microphones = useMicrophones();
  const [enabled, setEnabled] = useState(false);
  const [testing, setTesting] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [startingTest, setStartingTest] = useState(false);
  const [detected, setDetected] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState("");
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const frameRef = useRef<number | null>(null);

  const stopTest = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
    setTesting(false);
    setDetected(false);
    setLevel(0);
  }, []);

  useEffect(() => stopTest, [stopTest]);
  useEffect(() => {
    if (disabled) stopTest();
  }, [disabled, stopTest]);

  const enable = useCallback(async () => {
    stopTest();
    setEnabling(true);
    setError("");
    onAccessChange?.(false);
    try {
      const stream = await openSpeechMicrophone();
      stream.getTracks().forEach((track) => track.stop());
      setEnabled(true);
      onAccessChange?.(true);
    } catch (failure) {
      setEnabled(false);
      setError(microphoneProblem(failure));
      onAccessChange?.(false);
    } finally {
      setEnabling(false);
    }
  }, [onAccessChange, stopTest]);

  const startTest = useCallback(async () => {
    stopTest();
    setStartingTest(true);
    setError("");
    try {
      const context = new AudioContext();
      await context.resume();
      const stream = await openSpeechMicrophone();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.35;
      source.connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      streamRef.current = stream;
      contextRef.current = context;
      setEnabled(true);
      setTesting(true);
      onAccessChange?.(true);

      const measure = () => {
        analyser.getFloatTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) energy += sample * sample;
        const rms = Math.sqrt(energy / samples.length);
        // Map roughly -60dB…-15dB to a useful visual range.
        const percent = Math.max(
          0,
          Math.min(100, ((20 * Math.log10(Math.max(rms, 0.001)) + 60) / 45) * 100),
        );
        setLevel(percent);
        if (rms >= 0.006) setDetected(true);
        frameRef.current = requestAnimationFrame(measure);
      };
      frameRef.current = requestAnimationFrame(measure);
    } catch (failure) {
      stopTest();
      setError(microphoneProblem(failure));
    } finally {
      setStartingTest(false);
    }
  }, [onAccessChange, stopTest]);

  const choose = (value: string) => {
    stopTest();
    microphones.select(value === DEFAULT_DEVICE ? null : value);
    setError("");
  };

  const selectedLabel =
    microphones.devices.find((device) => device.deviceId === microphones.selectedId)
      ?.label ?? "System default";

  return (
    <fieldset className="space-y-3 rounded-xl border bg-surface p-4">
      <legend className="px-1 text-sm font-medium">Microphone</legend>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <div className="space-y-2">
          <Label htmlFor="interview-microphone">Input device</Label>
          <Select
            value={microphones.selectedId ?? DEFAULT_DEVICE}
            onValueChange={choose}
            disabled={disabled || enabling || startingTest}
          >
            <SelectTrigger id="interview-microphone">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT_DEVICE}>System default</SelectItem>
              {microphones.devices.map((device) => (
                <SelectItem key={device.deviceId} value={device.deviceId}>
                  {device.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          type="button"
          variant={enabled ? "secondary" : "outline"}
          disabled={disabled || enabling || startingTest || enabled}
          onClick={() => void enable()}
        >
          {enabling ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : enabled ? <CheckCircle2 aria-hidden /> : <Mic aria-hidden />}
          {enabling ? "Requesting access…" : enabled ? "Microphone enabled" : "Enable microphone"}
        </Button>
      </div>

      <div className="space-y-2" aria-live="polite">
        <div
          className="h-2 overflow-hidden rounded-full bg-muted"
          role="meter"
          aria-label="Microphone input level"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(level)}
        >
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-75",
              detected ? "bg-positive" : "bg-primary",
            )}
            style={{ width: `${level}%` }}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            {detected || enabled ? (
              <CheckCircle2 className="size-4 shrink-0 text-positive" aria-hidden />
            ) : (
              <AudioLines className={cn("size-4 shrink-0", testing && "text-primary")} aria-hidden />
            )}
            <span>
              {testing
                ? detected
                  ? `Input detected from ${selectedLabel}.`
                  : `Speak normally to test ${selectedLabel}.`
                : enabled
                  ? "Microphone access is enabled. Testing the input is optional."
                  : "Enable access for voice answers. The live input test is optional."}
            </span>
          </div>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled || !enabled || enabling || startingTest}
            onClick={() => void (testing ? stopTest() : startTest())}
          >
            {startingTest ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : testing ? <Square aria-hidden /> : <AudioLines aria-hidden />}
            {startingTest ? "Starting test…" : testing ? "Stop test" : "Test input (optional)"}
          </Button>
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    </fieldset>
  );
}
