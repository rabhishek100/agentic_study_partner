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

  /*
   * No border and no legend box: whatever hosts this supplies the heading, and
   * a second framed panel inside a framed one is the nested-card pattern the
   * design brief rejects by name.
   *
   * The device row measures its *container*, not the viewport. It lives in the
   * launch panel's 288px content column on a 1440px screen, so a `sm:` query —
   * which asks about the window — put the select and the button side by side in
   * a space that fits neither, and the panel clipped 60px of both.
   */
  return (
    <fieldset className="@container/microphone grid gap-3">
      <legend className="sr-only">Microphone</legend>
      <div className="grid gap-2 @sm/microphone:grid-cols-[1fr_auto] @sm/microphone:items-end">
        <div className="grid gap-2">
          <Label htmlFor="interview-microphone" className="text-xs text-muted-foreground">
            Input device
          </Label>
          <Select
            value={microphones.selectedId ?? DEFAULT_DEVICE}
            onValueChange={choose}
            disabled={disabled || enabling || startingTest}
          >
            <SelectTrigger id="interview-microphone" className="w-full min-w-0 overflow-hidden">
              <SelectValue className="min-w-0 truncate" />
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
          variant={enabled ? "secondary" : "default"}
          className="w-full @sm/microphone:w-auto"
          disabled={disabled || enabling || startingTest || enabled}
          onClick={() => void enable()}
        >
          {enabling ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : enabled ? <CheckCircle2 aria-hidden /> : <Mic aria-hidden />}
          {enabling ? "Requesting access…" : enabled ? "Microphone enabled" : "Enable microphone"}
        </Button>
      </div>

      <div className="grid gap-2" aria-live="polite">
        {/*
          The meter appears only while a test is running. An empty track sitting
          permanently under the controls read as a broken progress bar — it has
          nothing to report until there is input to report.
        */}
        {testing ? (
          <div
            className="h-2 overflow-hidden rounded-full bg-surface-hover"
            role="meter"
            aria-label="Microphone input level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(level)}
          >
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-75",
                detected ? "bg-positive" : "bg-action",
              )}
              style={{ width: `${level}%` }}
            />
          </div>
        ) : null}
        {/*
          Stacked, not `justify-between`. In a 288px column the status text and
          the test button could not share a line, so they wrapped into a ragged
          two-row block with the button stranded mid-panel.
        */}
        <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
          {detected || enabled ? (
            <CheckCircle2 className="mt-1 size-4 shrink-0 text-positive" aria-hidden />
          ) : (
            <AudioLines className={cn("mt-1 size-4 shrink-0", testing && "text-action")} aria-hidden />
          )}
          <span className="min-w-0">
            {testing
              ? detected
                ? `Input detected from ${selectedLabel}.`
                : `Speak normally to test ${selectedLabel}.`
              : enabled
                ? "Microphone access is enabled. Testing the input is optional."
                : "Enable access for voice answers. The live input test is optional."}
          </span>
        </p>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="justify-self-start"
          disabled={disabled || !enabled || enabling || startingTest}
          onClick={() => void (testing ? stopTest() : startTest())}
        >
          {startingTest ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : testing ? <Square aria-hidden /> : <AudioLines aria-hidden />}
          {startingTest ? "Starting test…" : testing ? "Stop test" : "Test input (optional)"}
        </Button>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    </fieldset>
  );
}
