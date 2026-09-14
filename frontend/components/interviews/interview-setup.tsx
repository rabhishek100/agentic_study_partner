"use client";

import { BookOpen, ChevronDown, Code2, Video } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  LaunchPanel,
  type SetupOperation,
} from "@/components/interviews/launch-panel";
import { MicrophoneSetup } from "@/components/interviews/microphone-setup";
import {
  ChipChoices,
  OptionCards,
  SetupField,
  SetupStep,
  type SetupChoice,
} from "@/components/interviews/setup-controls";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ChapterSummary } from "@/lib/deck-types";
import {
  formatDuration,
  INTERVIEW_DURATIONS,
  type InterviewFormatChoice,
  type InterviewMode,
  type InterviewPreflight,
  type InterviewSetupPayload,
  type InterviewSourceKind,
  type TargetLevel,
} from "@/lib/interview-types";
import type { BookSummary } from "@/lib/types";
import type { VideoSummary } from "@/lib/video-types";
import { cn } from "@/lib/utils";

const SOURCE_KINDS: ReadonlyArray<SetupChoice<InterviewSourceKind>> = [
  {
    value: "book",
    label: "Book chapter",
    hint: "One chapter from a book you have ingested.",
    icon: BookOpen,
  },
  {
    value: "video",
    label: "Lecture",
    hint: "One processed lecture video.",
    icon: Video,
  },
];

const LEVELS: ReadonlyArray<SetupChoice<TargetLevel>> = [
  { value: "entry", label: "Entry", hint: "Foundations and clear explanations." },
  { value: "mid", label: "Mid-level", hint: "Depth, applications, and trade-offs." },
  { value: "senior", label: "Senior", hint: "Judgment, failure modes, and ambiguity." },
];

/**
 * Both modes were two lines inside a closed `Select` — the one control that
 * hides the difference between the options it is asking you to compare.
 */
const MODES: ReadonlyArray<SetupChoice<InterviewMode>> = [
  {
    value: "realistic",
    label: "Realistic",
    hint: "Natural acknowledgements only. The full report arrives at the end.",
  },
  {
    value: "guided",
    label: "Guided",
    hint: "A concise correction after every answer, as you go.",
  },
];

const FORMATS: ReadonlyArray<SetupChoice<InterviewFormatChoice>> = [
  {
    value: "auto",
    label: "Detect from the source",
    hint: "Recommended. The source check classifies it.",
  },
  {
    value: "concept",
    label: "Concept interview",
    hint: "Breadth, intuition, mechanics, and edge cases.",
  },
  {
    value: "system_design",
    label: "System design",
    hint: "Requirements, estimation, architecture, and failure modes.",
  },
  {
    value: "source_led",
    label: "Follow the source",
    hint: "Keep the order a source that already presents an interview uses.",
  },
];

const DURATIONS: ReadonlyArray<SetupChoice<string>> = INTERVIEW_DURATIONS.map(
  (value) => ({ value: String(value), label: formatDuration(value) }),
);

function labelOf<T extends string>(
  choices: ReadonlyArray<SetupChoice<T>>,
  value: T,
): string {
  return choices.find((choice) => choice.value === value)?.label ?? "—";
}

function hintOf<T extends string>(
  choices: ReadonlyArray<SetupChoice<T>>,
  value: T,
): string | undefined {
  return choices.find((choice) => choice.value === value)?.hint;
}

/** The same choices without their per-option prose, for a chip row. */
function bare<T extends string>(
  choices: ReadonlyArray<SetupChoice<T>>,
): ReadonlyArray<SetupChoice<T>> {
  return choices.map(({ value, label }) => ({ value, label }));
}

export interface InterviewSetupProps {
  books: BookSummary[];
  videos: VideoSummary[];
  /** Whether the source lists have arrived; separates "empty" from "not yet". */
  loaded: boolean;
  fetchChapters: (bookId: string) => Promise<ChapterSummary[]>;
  runPreflight: (payload: InterviewSetupPayload) => Promise<InterviewPreflight>;
  /** Shown above the form; the sources it describes failed to arrive. */
  loadError?: string;
  /**
   * Creates the session, starts it, and navigates. Rejects with a message the
   * launch panel shows. `report` names the stage the reader is waiting on —
   * creating the session and generating the first question are separate waits
   * and the spec requires each to be labelled distinctly.
   */
  startInterview: (
    payload: InterviewSetupPayload,
    report: (stage: SetupOperation) => void,
  ) => Promise<void>;
  createIdealInterview: (
    payload: InterviewSetupPayload,
    report: (stage: SetupOperation) => void,
  ) => Promise<void>;
}

/**
 * The interview setup form.
 *
 * The screen owns every setup decision and the two-phase commit; the route owns
 * the network. That seam is what lets the composition be rendered against
 * fixtures for visual and accessibility review without a signed-in session.
 */
export function InterviewSetup({
  books,
  videos,
  loaded,
  loadError = "",
  fetchChapters,
  runPreflight,
  startInterview,
  createIdealInterview,
}: InterviewSetupProps) {
  const [sourceKind, setSourceKind] = useState<InterviewSourceKind>("book");
  const [bookId, setBookId] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [videoId, setVideoId] = useState("");
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [duration, setDuration] = useState(30);
  const [level, setLevel] = useState<TargetLevel>("mid");
  const [mode, setMode] = useState<InterviewMode>("realistic");
  const [format, setFormat] = useState<InterviewFormatChoice>("auto");
  const [codingExerciseRequested, setCodingExerciseRequested] = useState(false);
  const [tuning, setTuning] = useState(false);
  const [preview, setPreview] = useState<InterviewPreflight | null>(null);
  /** The setup the last successful inspection was run against. */
  const [checkedSetup, setCheckedSetup] = useState("");
  const [checkError, setCheckError] = useState("");
  const [microphoneAccess, setMicrophoneAccess] = useState(false);
  const [operation, setOperation] = useState<SetupOperation>("idle");
  const [error, setError] = useState("");
  const busy = operation !== "idle";

  useEffect(() => {
    if (!bookId) {
      setLoadingChapters(false);
      setChapters([]);
      setNodeId("");
      return;
    }
    setNodeId("");
    setLoadingChapters(true);
    let active = true;
    void fetchChapters(bookId)
      .then((loadedChapters) => {
        if (active) setChapters(loadedChapters);
      })
      .catch((failure: Error) => {
        if (active) setError(failure.message || "Could not load chapters.");
      })
      .finally(() => {
        if (active) setLoadingChapters(false);
      });
    return () => {
      active = false;
    };
  }, [bookId, fetchChapters]);

  /*
   * A different source makes the previous check meaningless rather than merely
   * out of date — its title and estimate describe material that is no longer
   * selected — so this one clears. Every other setting only makes it stale.
   */
  useEffect(() => {
    setPreview(null);
    setCheckedSetup("");
    setCheckError("");
  }, [sourceKind, bookId, nodeId, videoId]);

  const payload = useMemo<InterviewSetupPayload | null>(() => {
    if (sourceKind === "book" && (!bookId || !nodeId)) return null;
    if (sourceKind === "video" && !videoId) return null;
    return {
      source_kind: sourceKind,
      ...(sourceKind === "book"
        ? { book_id: Number(bookId), node_id: Number(nodeId) }
        : { video_id: videoId }),
      maximum_duration_minutes: duration,
      target_level: level,
      feedback_mode: mode,
      interview_format: format,
      coding_exercise_requested: codingExerciseRequested,
    };
  }, [
    bookId,
    codingExerciseRequested,
    duration,
    format,
    level,
    mode,
    nodeId,
    sourceKind,
    videoId,
  ]);

  const setupKey = useMemo(() => (payload ? JSON.stringify(payload) : ""), [payload]);
  const checking = Boolean(payload) && checkedSetup !== setupKey && !checkError;

  /*
   * The source inspection runs itself.
   *
   * It was a button — "Check this source" — that the reader had to press before
   * the start button would appear, which put a step in their head that exists
   * for no reason: the preflight is a deterministic read (a topic inventory and
   * a duration estimate, no model call), so nothing is spent by running it as
   * soon as there is something to run it on. Making them ask for it bought a
   * button with two meanings and a "your setup changed" state to explain when
   * it flipped back.
   *
   * The debounce is what makes that affordable while someone is still clicking
   * through levels and durations, and the flag closes over each run so a slow
   * response cannot overwrite a newer one.
   */
  useEffect(() => {
    if (!payload || checkError || checkedSetup === setupKey) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void runPreflight(payload)
        .then((result) => {
          if (cancelled) return;
          setPreview(result);
          setCheckedSetup(setupKey);
        })
        .catch((failure: Error) => {
          if (cancelled) return;
          setPreview(null);
          setCheckError(failure.message || "Could not inspect this source.");
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [payload, setupKey, checkedSetup, checkError, runPreflight]);

  const retryCheck = useCallback(() => setCheckError(""), []);

  const start = useCallback(async () => {
    if (!payload) return;
    setOperation("creating_session");
    setError("");
    try {
      await startInterview(payload, setOperation);
    } catch (failure) {
      setError((failure as Error).message || "Could not start the interview.");
      setOperation("idle");
    }
  }, [payload, startInterview]);

  const listen = useCallback(async () => {
    if (!payload || payload.source_kind !== "book") return;
    setOperation("generating_ideal_flow");
    setError("");
    try {
      await createIdealInterview(payload, setOperation);
    } catch (failure) {
      setError((failure as Error).message || "Could not create the ideal interview.");
      setOperation("idle");
    }
  }, [createIdealInterview, payload]);

  const chosenBook = books.find((book) => String(book.book_id) === bookId);
  const chosenChapter = chapters.find((chapter) => String(chapter.node_id) === nodeId);
  const chosenVideo = videos.find((video) => video.video_id === videoId);
  const sourceTitle =
    sourceKind === "book" ? (chosenChapter?.title ?? null) : (chosenVideo?.title ?? null);
  const sourceContext =
    sourceKind === "book" ? (chosenBook?.title ?? null) : "Lecture";
  const noSourcesReady =
    loaded && (sourceKind === "book" ? books.length === 0 : videos.length === 0);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto grid w-full max-w-[92rem] gap-6 px-4 py-6 sm:px-8">
        {/*
          One line, not a landing page. The masthead already says what this
          screen is, and a three-line serif headline over a three-line
          paragraph was spending a third of the first viewport restating it —
          on the one screen whose whole job is a form the reader wants to see
          all of at once. The subtitle sits beside the title rather than under
          it for the same reason.
        */}
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="font-serif text-xl font-semibold tracking-tight">
            Set up an interview
          </h1>
          <p className="text-xs text-muted-foreground">
            One chapter or one lecture, scored against its own evidence.
          </p>
        </div>

        {loadError ? (
          <Alert variant="destructive">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        ) : null}

        {/*
          Setup on the left, commitment on the right. The reader fills in a
          numbered sequence and watches one panel accumulate everything the
          start depends on, rather than meeting each precondition as a surprise
          at the moment they press the button.
        */}
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:gap-8">
          <div className="grid gap-6">
            <SetupStep
          index={1}
          title="Choose your source"
          hint="Exactly one chapter or one lecture. The interview does not widen beyond it."
        >
          <div className="grid gap-3">
            <OptionCards
              name="source-kind"
              legend="Type"
              showLegend
              value={sourceKind}
              choices={SOURCE_KINDS}
              onChange={setSourceKind}
              disabled={busy}
              className="sm:grid-cols-2"
            />

            {noSourcesReady ? (
              <p className="rounded-md border border-dashed border-divider p-4 text-sm leading-6 text-muted-foreground">
                {sourceKind === "book" ? (
                  <>
                    No books are ready yet.{" "}
                    <Link
                      href="/"
                      className="font-medium text-action underline underline-offset-4"
                    >
                      Ingest a book
                    </Link>{" "}
                    and its chapters will appear here.
                  </>
                ) : (
                  <>
                    No processed lectures yet.{" "}
                    <Link
                      href="/videos"
                      className="font-medium text-action underline underline-offset-4"
                    >
                      Add a lecture
                    </Link>{" "}
                    and it will appear here once processing finishes.
                  </>
                )}
              </p>
            ) : sourceKind === "book" ? (
              /*
                Both selects on one row under a single row label. Each keeps its
                own `sr-only` label for its accessible name; the placeholder
                carries it visually until a value is chosen, and once one is,
                the value names the field better than "Book" did.
              */
              <SetupField label="Source">
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="min-w-0">
                    <Label htmlFor="interview-book" className="sr-only">
                      Book
                    </Label>
                    <Select value={bookId} onValueChange={setBookId} disabled={busy}>
                      <SelectTrigger
                        id="interview-book"
                        className="w-full min-w-0 overflow-hidden"
                      >
                        <SelectValue
                          className="min-w-0 truncate"
                          placeholder={loaded ? "Choose a book" : "Loading…"}
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {books.map((book) => (
                          <SelectItem key={book.book_id} value={String(book.book_id)}>
                            {book.title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="min-w-0">
                    <Label htmlFor="interview-chapter" className="sr-only">
                      Chapter
                    </Label>
                    <Select
                      value={nodeId}
                      onValueChange={setNodeId}
                      disabled={
                        busy || !bookId || loadingChapters || chapters.length === 0
                      }
                    >
                      <SelectTrigger
                        id="interview-chapter"
                        className="w-full min-w-0 overflow-hidden"
                      >
                        <SelectValue
                          className="min-w-0 truncate"
                          placeholder={
                            loadingChapters
                              ? "Loading chapters…"
                              : bookId
                                ? "Choose a chapter"
                                : "Choose a book first"
                          }
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {chapters.map((chapter) => (
                          <SelectItem
                            key={chapter.node_id}
                            value={String(chapter.node_id)}
                          >
                            {chapter.title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </SetupField>
            ) : (
              <SetupField label="Source">
                <Label htmlFor="interview-video" className="sr-only">
                  Lecture
                </Label>
                <Select value={videoId} onValueChange={setVideoId} disabled={busy}>
                  <SelectTrigger
                    id="interview-video"
                    className="w-full min-w-0 overflow-hidden"
                  >
                    <SelectValue
                      className="min-w-0 truncate"
                      placeholder={loaded ? "Choose a processed lecture" : "Loading…"}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {videos.map((video) => (
                      <SelectItem key={video.video_id} value={video.video_id}>
                        {video.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </SetupField>
            )}
          </div>
        </SetupStep>

        <SetupStep
          index={2}
          title="Set the challenge"
          hint="Sensible defaults are already chosen. Change what matters to you."
        >
          <div className="grid gap-4">
            {/*
              Chips, not cards. Entry / Mid-level / Senior is an ordinal scale
              whose labels already say what they mean, so three bordered boxes
              of prose bought nothing and added three more outlines to a column
              that had thirteen. The description of the *chosen* level sits
              under the row instead, where it is bound as the group's
              description rather than repeated three times.
            */}
            <ChipChoices
              name="target-level"
              legend="Target level"
              showLegend
              hint={hintOf(LEVELS, level)}
              value={level}
              choices={bare(LEVELS)}
              onChange={setLevel}
              disabled={busy}
            />
            <ChipChoices
              name="maximum-time"
              legend="Maximum time"
              showLegend
              hint="A ceiling, not a quota. The session ends when meaningful coverage is complete."
              value={String(duration)}
              choices={DURATIONS}
              onChange={(value) => setDuration(Number(value))}
              disabled={busy}
            />
            <OptionCards
              name="feedback-mode"
              legend="Feedback"
              showLegend
              value={mode}
              choices={MODES}
              onChange={setMode}
              disabled={busy}
              className="sm:grid-cols-2"
            />
          </div>
        </SetupStep>

              {/*
                Unnumbered on purpose: two expert overrides sitting in the same
                rhythm as the source made a four-decision form look like a
                seven-decision one. The trigger states the values in effect, so
                collapsing them never hides what they are.
              */}
              <Collapsible
                open={tuning}
                onOpenChange={setTuning}
                className="grid content-start gap-5 border-t border-divider pt-6"
              >
                {/*
                  Same spine as a step: the marker in the label column, the
                  heading where the controls start. "Optional" rather than a
                  number, because it is not one of the things you must do.
                */}
                <CollapsibleTrigger className="group grid w-full gap-1 rounded-md text-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-x-4">
                  <span className="text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground sm:pt-2">
                    Optional
                  </span>
                  <span className="grid gap-1">
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-lg leading-snug font-semibold">
                        Fine-tune
                      </span>
                      <ChevronDown
                        aria-hidden
                        className={cn(
                          "size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
                          tuning && "rotate-180",
                        )}
                      />
                    </span>
                    <span className="text-xs leading-5 text-muted-foreground">
                      {labelOf(FORMATS, format)} ·{" "}
                      {codingExerciseRequested
                        ? "coding exercise included"
                        : "no coding exercise"}
                    </span>
                  </span>
                </CollapsibleTrigger>
                <CollapsibleContent className="grid gap-4">
                  <OptionCards
                    name="interview-format"
                    legend="Interview format"
                    showLegend
                    layout="stack"
                    hint="Leave this on detection unless the source is classified wrongly."
                    value={format}
                    choices={FORMATS}
                    onChange={setFormat}
                    disabled={busy}
                    className="sm:grid-cols-2"
                  />
                  <label
                    className={cn(
                      "flex items-start gap-3 rounded-md border p-3 transition-colors",
                      "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-action",
                      codingExerciseRequested
                        ? "border-transparent bg-wash"
                        : "border-border bg-surface hover:bg-surface-hover",
                      busy ? "cursor-not-allowed" : "cursor-pointer",
                    )}
                  >
                    <Checkbox
                      id="coding-exercise-requested"
                      className="mt-1"
                      checked={codingExerciseRequested}
                      disabled={busy}
                      onCheckedChange={(checked) =>
                        setCodingExerciseRequested(checked === true)
                      }
                    />
                    <span className="grid min-w-0 gap-1">
                      <span className="flex items-center gap-2 text-sm font-medium">
                        <Code2 aria-hidden className="size-4 shrink-0" />
                        Open with a Python exercise
                      </span>
                      <span className="text-xs leading-5 text-muted-foreground">
                        One source-grounded scaffold in an embedded editor, then
                        the adaptive interview continues. The source check
                        confirms whether this source has executable material.
                      </span>
                    </span>
                  </label>
                </CollapsibleContent>
              </Collapsible>
          </div>

          <LaunchPanel
            summary={{
              sourceTitle,
              sourceContext,
              level: labelOf(LEVELS, level),
              duration: `${formatDuration(duration)} max`,
              feedback: labelOf(MODES, mode),
              format: labelOf(FORMATS, format),
              coding: codingExerciseRequested ? "Included" : "Not included",
            }}
            preflight={preview}
            checking={checking}
            checkError={checkError}
            microphoneReady={microphoneAccess}
            microphoneControl={
              <MicrophoneSetup disabled={busy} onAccessChange={setMicrophoneAccess} />
            }
            codingUnavailable={Boolean(
              preview && codingExerciseRequested && preview.coding_topic_count === 0,
            )}
            operation={operation}
            error={error}
            onRetryCheck={retryCheck}
            onStart={() => void start()}
            onListen={() => void listen()}
            listenAvailable={sourceKind === "book"}
            onDropCodingExercise={() => setCodingExerciseRequested(false)}
          />
        </div>
      </div>
    </div>
  );
}
