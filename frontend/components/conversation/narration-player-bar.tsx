"use client";

import {
  Eye,
  EyeOff,
  Loader2,
  Mic,
  MicOff,
  Pause,
  Play,
  RotateCcw,
  SlidersHorizontal,
  SkipBack,
  SkipForward,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useReadAloud } from "@/hooks/use-read-aloud";
import { useNarrationVoice } from "@/hooks/use-narration-voice";
import { DEFAULT_PACING, SPEEDS, type NarrationPacing } from "@/lib/narration-player";
import {
  emitNarrationVoiceQuestion,
  NARRATION_VOICE_ANSWER,
  type NarrationVoiceAnswerDetail,
} from "@/lib/narration-voice-events";

const HIGHLIGHT_NAME = "narration-current";

type Position = { node: Text; offset: number };

function normalizedText(root: Element): { text: string; positions: Position[] } {
  const positions: Position[] = [];
  let text = "";
  let previousWhitespace = true;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode() as Text | null;
  while (node) {
    const parent = node.parentElement;
    const ignored = parent?.closest(
      "[data-citation], [aria-hidden='true'], pre, code, script, style",
    );
    if (!ignored) {
      for (let offset = 0; offset < node.data.length; offset += 1) {
        const character = node.data[offset]!;
        if (/\s/.test(character)) {
          if (!previousWhitespace) {
            text += " ";
            positions.push({ node, offset });
          }
          previousWhitespace = true;
        } else {
          text += character.toLocaleLowerCase();
          positions.push({ node, offset });
          previousWhitespace = false;
        }
      }
    }
    node = walker.nextNode() as Text | null;
  }
  return { text: text.trim(), positions };
}

function targetText(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function clearHighlight(): void {
  const css = globalThis.CSS as
    | (typeof CSS & { highlights?: Map<string, unknown> })
    | undefined;
  css?.highlights?.delete(HIGHLIGHT_NAME);
  document.querySelectorAll("[data-narration-fallback]").forEach((element) => {
    element.removeAttribute("data-narration-fallback");
  });
}

/** Highlight the exact rendered passage when the browser supports CSS ranges. */
function highlightSentence(root: Element, spoken: string): Range | null {
  clearHighlight();
  let target = targetText(spoken);
  if (!target || target.startsWith("you asked:")) return null;
  const stream = normalizedText(root);
  let start = stream.text.indexOf(target);
  // Headings and list labels gain punctuation for prosody that is not visible.
  if (start < 0 && /[.!?:;]$/.test(target)) {
    target = target.slice(0, -1).trimEnd();
    start = stream.text.indexOf(target);
  }
  if (start < 0) return null;
  const first = stream.positions[start];
  const last = stream.positions[start + target.length - 1];
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, first.offset);
  range.setEnd(last.node, Math.min(last.node.length, last.offset + 1));

  const HighlightConstructor = (globalThis as typeof globalThis & {
    Highlight?: new (...ranges: Range[]) => unknown;
  }).Highlight;
  const css = globalThis.CSS as
    | (typeof CSS & { highlights?: Map<string, unknown> })
    | undefined;
  if (HighlightConstructor && css?.highlights) {
    css.highlights.set(HIGHLIGHT_NAME, new HighlightConstructor(range));
  } else {
    const element = first.node.parentElement?.closest("p, li, h1, h2, h3, h4, blockquote");
    element?.setAttribute("data-narration-fallback", "");
  }
  return range;
}

function anchoredElement(root: Element, narration: ReturnType<typeof useReadAloud>): Element | null {
  const anchor = narration.currentAnchor;
  if (!anchor) return null;
  if (anchor.type === "figure") {
    return root.parentElement?.querySelector(
      `[data-narration-figure="${anchor.blockId}"]`,
    ) ?? null;
  }
  if (anchor.type === "passage") {
    return root.querySelector(`[data-narration-passage="${anchor.index}"]`);
  }
  return root.querySelector(`[data-narration-block="${anchor.key}"]`);
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function FollowAlong() {
  const narration = useReadAloud();
  const suppressScrollAt = useRef<number | null>(null);

  useEffect(() => {
    if (!narration.activeId || !narration.anchorId || !narration.currentText) {
      clearHighlight();
      return;
    }
    const root = [...document.querySelectorAll("[data-narration-anchor]")].find(
      (element) => element.getAttribute("data-narration-anchor") === narration.anchorId,
    );
    if (!root) return;
    const anchored = anchoredElement(root, narration);
    const range = highlightSentence(anchored ?? root, narration.currentText);
    if (!range && anchored) anchored.setAttribute("data-narration-fallback", "");
    const scrollTarget = anchored ?? range?.startContainer.parentElement;
    if (!scrollTarget || !narration.autoFollow) return;
    if (suppressScrollAt.current === narration.chunkIndex) return;
    suppressScrollAt.current = null;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scrollTarget.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
  }, [
    narration.activeId,
    narration.anchorId,
    narration.autoFollow,
    narration.chunkIndex,
    narration.currentAnchor,
    narration.currentText,
  ]);

  useEffect(() => {
    if (!narration.autoFollow) return;
    const suspend = () => { suppressScrollAt.current = narration.chunkIndex; };
    document.addEventListener("wheel", suspend, { passive: true });
    document.addEventListener("touchstart", suspend, { passive: true });
    return () => {
      document.removeEventListener("wheel", suspend);
      document.removeEventListener("touchstart", suspend);
    };
  }, [narration.autoFollow, narration.chunkIndex]);

  useEffect(() => clearHighlight, []);
  // Kept out of the Tailwind/PostCSS input: its optimizer currently warns on
  // the standards-track Custom Highlight pseudo-element even though browsers
  // support it and the rule is valid at runtime.
  return (
    <style>{`::highlight(${HIGHLIGHT_NAME}) {
      background: var(--wash);
      color: inherit;
    }`}</style>
  );
}

type QuestionPhase = "idle" | "capturing" | "reviewing" | "asking";

interface PacingSliderProps {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: (value: number) => string;
  onChange: (value: number) => void;
}

function PacingSlider({
  label,
  hint,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: PacingSliderProps) {
  return (
    <label className="block space-y-2">
      <span className="flex items-baseline justify-between gap-3 text-xs">
        <span className="font-medium text-foreground">{label}</span>
        <span className="tabular-nums text-muted-foreground">{display(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="block h-5 w-full cursor-pointer accent-primary"
        aria-label={label}
      />
      <span className="block text-eyebrow leading-snug text-muted-foreground">{hint}</span>
    </label>
  );
}

function PacingControls() {
  const narration = useReadAloud();
  const update = (key: keyof NarrationPacing, value: number) => {
    narration.setPacing({ ...narration.pacing, [key]: value });
  };
  const seconds = (value: number) => `${(value / 1000).toFixed(1)} s`;
  const rate = (value: number) => `${value.toFixed(2)}×`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Reading pacing controls">
          <SlidersHorizontal aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={10} className="w-[min(22rem,calc(100vw-1.5rem))] space-y-4 p-4">
        <PopoverHeader>
          <PopoverTitle>Reading rhythm</PopoverTitle>
          <PopoverDescription>
            Pauses are real silence, never markup sent to the voice.
          </PopoverDescription>
        </PopoverHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <PacingSlider label="After each sentence" hint="A brief moment to absorb one thought." value={narration.pacing.sentencePauseMs} min={0} max={1500} step={100} display={seconds} onChange={(value) => update("sentencePauseMs", value)} />
          <PacingSlider label="After each paragraph" hint="Separates related groups of ideas." value={narration.pacing.paragraphPauseMs} min={0} max={2500} step={100} display={seconds} onChange={(value) => update("paragraphPauseMs", value)} />
          <PacingSlider label="After each heading" hint="Signals that a new section is beginning." value={narration.pacing.headingPauseMs} min={0} max={3500} step={100} display={seconds} onChange={(value) => update("headingPauseMs", value)} />
          <PacingSlider label="After the title" hint="Creates a clear opening before the chapter." value={narration.pacing.titlePauseMs} min={0} max={4500} step={100} display={seconds} onChange={(value) => update("titlePauseMs", value)} />
          <PacingSlider label="Heading voice pace" hint="Relative to the main reading speed." value={narration.pacing.headingRate} min={0.65} max={1.15} step={0.05} display={rate} onChange={(value) => update("headingRate", value)} />
          <PacingSlider label="Title voice pace" hint="A deliberate pace helps establish context." value={narration.pacing.titleRate} min={0.6} max={1.1} step={0.05} display={rate} onChange={(value) => update("titleRate", value)} />
        </div>
        <div className="flex items-center justify-between border-t border-border pt-3">
          <span className="text-eyebrow text-muted-foreground">Saved on this device</span>
          <Button variant="ghost" size="xs" onClick={() => narration.setPacing(DEFAULT_PACING)}>
            Reset rhythm
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function VoiceQuestions() {
  const narration = useReadAloud();
  const [draft, setDraft] = useState("");
  const [phase, setPhase] = useState<QuestionPhase>("idle");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [questionError, setQuestionError] = useState("");
  const draftRef = useRef("");
  const stopRef = useRef<() => Promise<void>>(async () => {});
  const submitRef = useRef<() => void>(() => {});
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reviewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finalizing = useRef(false);

  const clearTimers = useCallback(() => {
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    if (reviewTimer.current) clearTimeout(reviewTimer.current);
    silenceTimer.current = null;
    reviewTimer.current = null;
  }, []);

  const onHearing = useCallback(() => {
    narration.pause();
    setPhase("capturing");
  }, [narration]);

  const onTranscript = useCallback((text: string) => {
    const joined = `${draftRef.current} ${text}`.trim();
    draftRef.current = joined;
    setDraft(joined);
    setPhase("capturing");
    if (finalizing.current) return;
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    silenceTimer.current = setTimeout(() => {
      finalizing.current = true;
      void stopRef.current().finally(() => {
        setPhase("reviewing");
        finalizing.current = false;
        reviewTimer.current = setTimeout(() => submitRef.current(), 2_000);
      });
    }, 1_200);
  }, []);

  const voice = useNarrationVoice({
    conversationId: narration.voiceContext?.conversationId ?? null,
    onHearing,
    onTranscript,
  });
  stopRef.current = voice.stopListening;

  const submit = useCallback(() => {
    const context = narration.voiceContext;
    const question = draftRef.current.trim();
    if (!context || !question) return;
    clearTimers();
    const id = crypto.randomUUID();
    setRequestId(id);
    setQuestionError("");
    setPhase("asking");
    emitNarrationVoiceQuestion({
      requestId: id,
      conversationId: context.conversationId,
      parentTurnIndex: context.turnIndex,
      quotedText: narration.currentText,
      question,
    });
  }, [clearTimers, narration]);
  submitRef.current = submit;

  const cancelQuestion = useCallback(() => {
    clearTimers();
    draftRef.current = "";
    setDraft("");
    setPhase("idle");
    setRequestId(null);
    setQuestionError("");
  }, [clearTimers]);

  useEffect(() => {
    const hearAnswer = (raw: Event) => {
      const detail = (raw as CustomEvent<NarrationVoiceAnswerDetail>).detail;
      if (!requestId || detail.requestId !== requestId) return;
      setPhase("idle");
      setRequestId(null);
      draftRef.current = "";
      setDraft("");
      if (detail.error || detail.turnIndex == null) {
        setQuestionError(detail.error ?? "The grounded voice question failed.");
        return;
      }
      void voice.speakAnswer(detail.sideChatId, detail.turnIndex);
    };
    window.addEventListener(NARRATION_VOICE_ANSWER, hearAnswer);
    return () => window.removeEventListener(NARRATION_VOICE_ANSWER, hearAnswer);
  }, [requestId, voice]);

  useEffect(() => clearTimers, [clearTimers]);

  if (!narration.voiceContext || !voice.supported) return null;

  if (!voice.enabled) {
    return (
      <div className="mt-2 flex items-center justify-between gap-3 border-t border-border pt-2">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          Ask a grounded question without leaving your place.
        </p>
        <Button variant="ghost" size="sm" onClick={() => voice.setEnabled(true)}>
          <Mic aria-hidden /> Voice questions
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border pt-2 text-xs">
      {phase === "reviewing" ? (
        <>
          <label className="sr-only" htmlFor="narration-voice-question">Recognized question</label>
          <input
            id="narration-voice-question"
            value={draft}
            onChange={(event) => {
              clearTimers();
              draftRef.current = event.currentTarget.value;
              setDraft(event.currentTarget.value);
            }}
            className="min-w-48 flex-1 rounded-md border border-input bg-background px-2 py-2 text-sm"
          />
          <Button size="sm" onClick={submit}>Ask now</Button>
          <Button variant="ghost" size="sm" onClick={cancelQuestion}>Cancel</Button>
        </>
      ) : (
        <>
          <span className="flex min-w-0 flex-1 items-center gap-2 text-muted-foreground" role="status">
            {phase === "asking" || voice.status === "answering" || voice.status === "connecting" ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Mic className="size-3.5" aria-hidden />
            )}
            <span className="truncate">
              {phase === "asking"
                ? "Searching the selected book…"
                : voice.status === "answering"
                  ? "Answering from the grounded side chat…"
                  : phase === "capturing" || voice.status === "hearing"
                    ? draft || "Listening to your question…"
                    : voice.status === "listening"
                      ? "Listening — speak to interrupt"
                      : "Voice answer complete. Resume when ready."}
            </span>
          </span>
          {voice.status === "ready" && phase === "idle" ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                narration.resume();
                void voice.startListening();
              }}
            >
              <Play aria-hidden /> Resume reading
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              clearTimers();
              voice.setEnabled(false);
              cancelQuestion();
            }}
            aria-label="Turn off voice questions"
          >
            <MicOff aria-hidden />
          </Button>
        </>
      )}
      {voice.error || questionError ? (
        <button
          type="button"
          className="w-full text-left text-destructive"
          onClick={() => {
            voice.dismissError();
            setQuestionError("");
          }}
        >
          {voice.error || questionError}
        </button>
      ) : null}
    </div>
  );
}

export function NarrationPlayerBar() {
  const narration = useReadAloud();
  const [scrubValue, setScrubValue] = useState<number | null>(null);
  const scrubbing = useRef(false);

  useEffect(() => {
    scrubbing.current = false;
    setScrubValue(null);
  }, [narration.activeId]);

  if (!narration.activeId) return <FollowAlong />;

  const playing = narration.status === "speaking" || narration.status === "preparing";
  const progress = scrubValue ?? Math.min(narration.currentTime, narration.duration || 0);

  const commitScrub = (value: number) => {
    scrubbing.current = false;
    narration.seekTo(value);
    setScrubValue(null);
  };

  return (
    <>
      <FollowAlong />
      <section
        aria-label="Read-aloud player"
        className="fixed inset-x-3 bottom-3 z-drawer mx-auto w-auto max-w-3xl rounded-2xl border border-border bg-popover p-3 text-popover-foreground shadow-xl sm:bottom-5 sm:px-4"
      >
        <div className="flex flex-wrap items-center justify-center gap-2 sm:flex-nowrap sm:gap-3">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={narration.previous}
            aria-label="Previous passage"
          >
            <SkipBack aria-hidden />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={playing ? narration.pause : narration.resume}
            aria-label={playing ? "Pause reading" : "Resume reading"}
          >
            {narration.status === "preparing" ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : playing ? (
              <Pause aria-hidden />
            ) : (
              <Play aria-hidden />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => narration.seekBy(-10)}
            aria-label="Rewind 10 seconds"
          >
            <RotateCcw aria-hidden />
          </Button>

          <div className="order-first w-full min-w-0 sm:order-none sm:flex-1">
            <div className="mb-1 flex items-center justify-between gap-3 text-xs">
              <span className="truncate font-medium">
                {narration.label} · passage {narration.chunkIndex + 1} of {narration.chunkCount}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatTime(progress)} / {narration.durationEstimated ? "≈" : ""}
                {formatTime(narration.duration)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={Math.max(0.01, narration.duration)}
              step={0.1}
              value={progress}
              onPointerDown={(event) => {
                scrubbing.current = true;
                setScrubValue(Number(event.currentTarget.value));
              }}
              onInput={(event) => {
                if (scrubbing.current) setScrubValue(Number(event.currentTarget.value));
              }}
              onChange={(event) => {
                const value = Number(event.currentTarget.value);
                if (scrubbing.current) setScrubValue(value);
                else narration.seekTo(value); // Keyboard changes are discrete.
              }}
              onPointerUp={(event) => commitScrub(Number(event.currentTarget.value))}
              onPointerCancel={() => {
                scrubbing.current = false;
                setScrubValue(null);
              }}
              aria-label="Reading position"
              className="block h-5 w-full cursor-pointer accent-primary"
            />
          </div>

          <Button
            variant="ghost"
            size="icon-sm"
            onClick={narration.next}
            aria-label="Next passage"
          >
            <SkipForward aria-hidden />
          </Button>
          <Select
            value={String(narration.speed)}
            onValueChange={(value) => narration.setSpeed(Number(value))}
          >
            <SelectTrigger size="sm" className="h-8 w-[4.5rem]" aria-label="Reading speed">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SPEEDS.map((speed) => (
                <SelectItem key={speed} value={String(speed)}>{speed}×</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <PacingControls />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={narration.autoFollow ? "Turn off auto-follow" : "Turn on auto-follow"}
            aria-pressed={narration.autoFollow}
            onClick={() => narration.setAutoFollow(!narration.autoFollow)}
          >
            {narration.autoFollow ? <Eye aria-hidden /> : <EyeOff aria-hidden />}
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={narration.stop} aria-label="Stop reading">
            <Square aria-hidden />
          </Button>
        </div>
        {narration.error ? (
          <p className="mt-1 truncate text-xs text-muted-foreground" role="status">
            {narration.error}
          </p>
        ) : null}
        {process.env.NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED === "true" ? (
          <VoiceQuestions />
        ) : null}
      </section>
    </>
  );
}
