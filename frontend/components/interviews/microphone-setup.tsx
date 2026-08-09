"use client";

import { AudioLines, CheckCircle2, Mic, Square } from "lucide-react";
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
  onReadyChange,
}: {
  disabled?: boolean;
  onReadyChange?: (ready: boolean) => void;
}) {
  const microphones = useMicrophones();
  const [testing, setTesting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [detected, setDetected] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState("");
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const frameRef = useRef<number | null>(null);

  const stop = useCallback(() => {
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

  useEffect(() => stop, [stop]);
  useEffect(() => {
    if (disabled) stop();
  }, [disabled, stop]);

  const start = useCallback(async () => {
    stop();
    setStarting(true);
    setError("");
    onReadyChange?.(false);
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
      setTesting(true);
      onReadyChange?.(true);

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
      stop();
      setError(microphoneProblem(failure));
      onReadyChange?.(false);
    } finally {
      setStarting(false);
    }
  }, [onReadyChange, stop]);

  const choose = (value: string) => {
    stop();
    microphones.select(value === DEFAULT_DEVICE ? null : value);
    setError("");
    onReadyChange?.(false);
  };

  const selectedLabel =
    microphones.devices.find((device) => device.deviceId === microphones.selectedId)
      ?.label ?? "System default";

  return (
    <fieldset className="space-y-3 rounded-xl border bg-muted/25 p-4">
      <legend className="px-1 text-sm font-medium">Microphone check <span className="font-normal text-muted-foreground">· optional</span></legend>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="interview-microphone">Input device</Label>
          <Select
            value={microphones.selectedId ?? DEFAULT_DEVICE}
            onValueChange={choose}
            disabled={disabled || starting}
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
          variant={testing ? "secondary" : "outline"}
          disabled={disabled || starting}
          onClick={() => void (testing ? stop() : start())}
        >
          {testing ? <Square aria-hidden /> : <Mic aria-hidden />}
          {starting ? "Enabling…" : testing ? "Stop test" : "Test microphone"}
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
              detected ? "bg-emerald-500" : "bg-primary",
            )}
            style={{ width: `${level}%` }}
          />
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {detected ? (
            <CheckCircle2 className="size-4 text-emerald-600" aria-hidden />
          ) : (
            <AudioLines className={cn("size-4", testing && "text-primary")} aria-hidden />
          )}
          <span>
            {testing
              ? detected
                ? `Input detected from ${selectedLabel}. This microphone is ready.`
                : `Speak normally to test ${selectedLabel}.`
              : "Optional: test voice now, or start the interview and answer by typing."}
          </span>
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    </fieldset>
  );
}
