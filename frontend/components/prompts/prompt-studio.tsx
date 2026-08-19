"use client";

import {
  Check,
  ChevronDown,
  Copy,
  LockKeyhole,
  Maximize2,
  Minimize2,
  RotateCcw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ARCHETYPE_LABELS,
  changedFields,
  DEPTH_LABELS,
  OPTIONAL_PLACEHOLDERS,
  PROMPT_FIELDS,
  profileIssues,
  REQUIRED_PLACEHOLDERS,
  textMetrics,
  type PromptFieldSpec,
} from "@/lib/prompt-profile";
import type {
  AnswerArchetype,
  PromptProfile,
  PromptSettingsResponse,
  ResponseDepth,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The prompt studio, as a page.
 *
 * It used to be a right-hand `Sheet`: five 8-row textareas, a locked prompt,
 * and two compiled previews stacked in a column narrower than the monospace
 * text inside it, over an opaque overlay that blacked out the app behind it.
 * Every one of those problems is the same problem — a screen's worth of
 * material in a drawer — so the fix is the screen, not more drawer.
 *
 * The route owns the network; this owns every edit and the two things a reader
 * can commit to (their default, or the open conversation). That seam is what
 * lets the whole composition be rendered against fixtures in a test.
 */

const ARCHETYPES: readonly AnswerArchetype[] = [
  "concept_explanation",
  "system_design",
  "chapter_review",
  "answer_transform",
];

const DEPTHS: readonly ResponseDepth[] = ["quick", "interview", "deep"];

/**
 * The editor's ground: a mono block on the quiet surface, never the canvas.
 *
 * `md:text-xs` is not redundant. `Textarea` sets `text-base md:text-sm`, and a
 * breakpoint variant outranks the unprefixed `text-xs` this would otherwise
 * carry — so the prompts rendered a step above the mono size everywhere except
 * a phone, and every 79-column source line wrapped an orphan word.
 */
const CODE_SURFACE = "bg-surface font-mono text-xs leading-6 md:text-xs";

type Commit = "save" | "apply";

export interface PromptStudioProps {
  /** Whether the first load has settled; separates "empty" from "not yet". */
  loaded: boolean;
  loadError: string;
  /** The account's saved profile, the project defaults, and the locked rules. */
  settings: PromptSettingsResponse | null;
  /**
   * What the editor opens on: the conversation's own profile when the reader
   * arrived from one, the account default otherwise.
   */
  initialProfile: PromptProfile | null;
  /** Set when a conversation is open, which is what enables applying to it. */
  conversationId: string | null;
  /** The depth the conversation is set to; the preview compiles against it. */
  responseDepth: ResponseDepth;
  compile: (
    profile: PromptProfile,
    archetype: AnswerArchetype,
    depth: ResponseDepth,
  ) => Promise<PromptSettingsResponse>;
  saveDefault: (profile: PromptProfile) => Promise<PromptSettingsResponse>;
  applyToConversation: (profile: PromptProfile) => Promise<void>;
}

/** One line of meta under a control, never carrying meaning on its own. */
function Meta({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2_000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-label={copied ? `${label} copied` : `Copy ${label}`}
      disabled={!value}
      onClick={() => {
        // jsdom, and any page served without a secure context, has no clipboard.
        const clipboard = navigator.clipboard;
        if (!clipboard) return;
        void clipboard.writeText(value).then(() => setCopied(true));
      }}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

/**
 * A read-only prompt, shown as text rather than in a disabled textarea.
 *
 * A textarea for output was the old screen's other readability problem: it
 * clipped to a fixed row count, wrapped mid-token at any width, and could not
 * be read past its scrollbar. A `pre` wraps on whitespace and can be copied
 * whole.
 */
function ReadOnlyPrompt({
  id,
  label,
  hint,
  value,
  pending,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  pending?: boolean;
}) {
  return (
    <section className="grid min-w-0 content-start gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="grid min-w-0 gap-1">
          <h3 className="text-sm font-medium">{label}</h3>
          {hint ? <Meta>{hint}</Meta> : null}
        </div>
        <div className="shrink-0">
          <CopyButton value={value} label={label.toLowerCase()} />
        </div>
      </div>
      {pending && !value ? (
        <Skeleton className="h-64 w-full rounded-lg" />
      ) : (
        <pre
          id={id}
          tabIndex={0}
          aria-label={label}
          className={cn(
            "max-h-72 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-divider p-4",
            CODE_SURFACE,
            pending && "text-muted-foreground",
          )}
        >
          {value || "—"}
        </pre>
      )}
    </section>
  );
}

/**
 * One editable prompt.
 *
 * The height is capped and the block scrolls inside itself, so five prompts of
 * unknown length still make a page a reader can navigate; the expand control is
 * there for the one they are actually working on.
 */
function PromptEditor({
  field,
  value,
  issue,
  customised,
  disabled,
  onChange,
  onReset,
}: {
  field: PromptFieldSpec;
  value: string;
  issue?: string;
  /** Differs from the project default, so a reset is worth offering. */
  customised: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
  onReset: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { lines, characters } = textMetrics(value);
  const issueId = `${field.anchor}-issue`;
  const helpId = `${field.anchor}-help`;

  return (
    <section
      aria-labelledby={field.anchor}
      className="grid content-start gap-3 border-t border-divider pt-6 first:border-t-0 first:pt-0"
    >
      {/*
        A column on a phone, a row from `sm`. `flex-wrap` was the third option
        and the wrong one: a long description pushed the controls onto their own
        line at *some* widths only, so the first prompt's buttons sat somewhere
        different from every other prompt's.
      */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="grid min-w-0 flex-1 gap-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h2 id={field.anchor} className="scroll-mt-24 text-lg font-semibold leading-snug">
              {field.label}
            </h2>
            {customised ? (
              <Badge variant="secondary" className="font-normal">
                Edited
              </Badge>
            ) : null}
          </div>
          <p id={helpId} className="text-xs leading-5 text-muted-foreground">
            {field.purpose} {field.usedFor}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
            {expanded ? "Collapse" : "Expand"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || !customised}
            onClick={onReset}
          >
            <RotateCcw aria-hidden />
            Reset
          </Button>
        </div>
      </div>

      <Textarea
        id={`${field.anchor}-input`}
        aria-label={field.label}
        aria-describedby={issue ? `${helpId} ${issueId}` : helpId}
        aria-invalid={issue ? true : undefined}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          "min-h-56 overflow-auto",
          CODE_SURFACE,
          expanded ? "max-h-none" : "max-h-96",
        )}
      />

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        {issue ? (
          <p id={issueId} className="text-xs font-medium text-destructive">
            {issue}
          </p>
        ) : (
          <Meta>
            {lines.toLocaleString()} line{lines === 1 ? "" : "s"} ·{" "}
            {characters.toLocaleString()} of {field.maxLength.toLocaleString()}{" "}
            characters
          </Meta>
        )}
        {field.key === "user_prompt_template" ? (
          <Meta>
            Required {REQUIRED_PLACEHOLDERS.map((name) => `{${name}}`).join(" ")} ·
            optional {OPTIONAL_PLACEHOLDERS.map((name) => `{${name}}`).join(" ")}
          </Meta>
        ) : null}
      </div>
    </section>
  );
}

export function PromptStudio({
  loaded,
  loadError,
  settings,
  initialProfile,
  conversationId,
  responseDepth,
  compile,
  saveDefault,
  applyToConversation,
}: PromptStudioProps) {
  const [draft, setDraft] = useState<PromptProfile | null>(null);
  /** What the editor opened on, so "unsaved" means something exact. */
  const [baseline, setBaseline] = useState<PromptProfile | null>(null);
  const [preview, setPreview] = useState<PromptSettingsResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [archetype, setArchetype] = useState<AnswerArchetype>("concept_explanation");
  const [depth, setDepth] = useState<ResponseDepth>(responseDepth);
  const [committing, setCommitting] = useState<Commit | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  // The profile arrives with the route's first load, and again if the reader
  // opens the studio from a different conversation without a full navigation.
  useEffect(() => {
    if (!initialProfile) return;
    setDraft(initialProfile);
    setBaseline(initialProfile);
  }, [initialProfile]);

  useEffect(() => setDepth(responseDepth), [responseDepth]);

  const issues = useMemo(() => (draft ? profileIssues(draft) : {}), [draft]);
  const blocked = Object.keys(issues).length > 0;
  /** The first prompt standing between the reader and a save, if any. */
  const blocker = useMemo(() => {
    const field = PROMPT_FIELDS.find((candidate) => issues[candidate.key]);
    return field ? { ...field, issue: issues[field.key] } : null;
  }, [issues]);

  const customised = useMemo(
    () => (draft && settings ? changedFields(draft, settings.defaults) : []),
    [draft, settings],
  );
  const unsaved = useMemo(
    () => (draft && baseline ? changedFields(draft, baseline) : []),
    [draft, baseline],
  );

  /*
    The compiled preview follows the editor rather than a button. It is the
    only thing on the screen that answers "what did that edit actually do", and
    making the reader ask for it after every change is what made the old panel
    feel stale. A short debounce keeps a burst of typing to one request, and a
    request counter means a slow one cannot overwrite a newer result.
  */
  const previewRequest = useRef(0);
  useEffect(() => {
    if (!draft || blocked) return;
    const request = ++previewRequest.current;
    const timer = window.setTimeout(() => {
      setPreviewing(true);
      compile(draft, archetype, depth)
        .then((compiled) => {
          if (request !== previewRequest.current) return;
          setPreview(compiled);
          setPreviewError("");
        })
        .catch((failure: Error) => {
          if (request !== previewRequest.current) return;
          setPreviewError(failure.message || "Could not compile the preview.");
        })
        .finally(() => {
          if (request === previewRequest.current) setPreviewing(false);
        });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [draft, archetype, depth, blocked, compile]);

  const update = useCallback((key: keyof PromptProfile, value: string) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setNotice("");
    setError("");
  }, []);

  async function commit(kind: Commit) {
    if (!draft || blocked) return;
    setCommitting(kind);
    setNotice("");
    setError("");
    try {
      if (kind === "save") {
        const saved = await saveDefault(draft);
        setDraft(saved.profile);
        setBaseline(saved.profile);
        setNotice("Saved. New conversations start from these prompts.");
      } else {
        await applyToConversation(draft);
        setBaseline(draft);
        setNotice("Applied to the conversation you came from.");
      }
    } catch (failure) {
      setError((failure as Error).message || "The change could not be saved.");
    } finally {
      setCommitting(null);
    }
  }

  const busy = committing !== null;

  if (loadError) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-8">
          <Alert variant="destructive">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        </div>
      </div>
    );
  }

  if (!loaded || !draft || !settings) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div
          className="mx-auto grid w-full max-w-3xl gap-4 px-4 py-6 sm:px-8"
          role="status"
          aria-live="polite"
        >
          <p className="text-sm text-muted-foreground">Loading your prompts…</p>
          <Skeleton className="h-24 w-full rounded-lg" />
          <Skeleton className="h-64 w-full rounded-lg" />
          <Skeleton className="h-64 w-full rounded-lg" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/*
        The commit bar is a sibling of the scrolling column, not a footer
        inside it. Both actions stay one glance away at any scroll position,
        which is the whole reason a five-prompt screen is bearable.
      */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-divider bg-background px-4 py-3 sm:px-8">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="font-serif text-xl font-semibold tracking-tight">
              Prompt studio
            </h1>
            <p className="text-xs text-muted-foreground">
              {conversationId
                ? "Editing the conversation you came from."
                : "Editing your default profile."}
            </p>
          </div>
          <p className="text-xs" role="status" aria-live="polite">
            {blocker ? (
              <span className="text-destructive">
                {blocker.label} — {blocker.issue}{" "}
                <a
                  href={`#${blocker.anchor}`}
                  className="font-medium underline underline-offset-4"
                >
                  Go to it
                </a>
              </span>
            ) : (
              <span className="text-muted-foreground">
                {error
                  ? "Nothing was saved."
                  : unsaved.length > 0
                    ? `${unsaved.length} unsaved change${
                        unsaved.length === 1 ? "" : "s"
                      }. Nothing is applied until you save.`
                    : notice || "Everything here is saved."}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || customised.length === 0}
            onClick={() => {
              setDraft(settings.defaults);
              setNotice("Project defaults loaded into the editor. Save to keep them.");
              setError("");
            }}
          >
            <RotateCcw aria-hidden />
            Restore project defaults
          </Button>
          {conversationId ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy || blocked}
              onClick={() => void commit("apply")}
            >
              {committing === "apply" ? "Applying…" : "Apply to conversation"}
            </Button>
          ) : null}
          <Button
            type="button"
            disabled={busy || blocked}
            onClick={() => void commit("save")}
          >
            {committing === "save" ? "Saving…" : "Save as my default"}
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-[92rem] gap-6 px-4 py-6 sm:px-8">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {/*
            Editors on the left, the compiled result on the right, both in view
            at once on a wide screen. Under `xl` the preview follows the prompts
            rather than competing with them for a narrow column.

            The preview column is a fixed measure and the editors take the rest,
            not the other way round: the default prompts are hard-wrapped near
            79 columns, and an editor narrower than that re-wraps an orphan word
            off the end of every long line. 26rem leaves ~77 columns beside an
            open rail on a 1512px laptop.
          */}
          <div className="grid items-start gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] 2xl:grid-cols-[minmax(0,1fr)_minmax(0,32rem)]">
            <div className="grid min-w-0 gap-6">
              <Collapsible className="grid gap-3 rounded-lg border border-divider p-4">
                <CollapsibleTrigger className="group/locked flex w-full items-center gap-2 text-left">
                  <LockKeyhole aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 grow">
                    <span className="block text-sm font-medium">
                      Required grounding rules
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      Citations and book-only answers. Readable, never editable.
                    </span>
                  </span>
                  <ChevronDown
                    aria-hidden
                    className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]/locked:rotate-180"
                  />
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <pre
                    tabIndex={0}
                    aria-label="Required grounding rules"
                    className={cn(
                      "max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-divider p-4",
                      CODE_SURFACE,
                    )}
                  >
                    {settings.locked_system_prompt}
                  </pre>
                </CollapsibleContent>
              </Collapsible>

              <div className="grid gap-6">
                {PROMPT_FIELDS.map((field) => (
                  <PromptEditor
                    key={field.key}
                    field={field}
                    value={draft[field.key]}
                    issue={issues[field.key]}
                    customised={customised.includes(field.key)}
                    disabled={busy}
                    onChange={(value) => update(field.key, value)}
                    onReset={() => update(field.key, settings.defaults[field.key])}
                  />
                ))}
              </div>
            </div>

            <div
              id="prompt-preview"
              className="grid min-w-0 content-start gap-4 xl:sticky xl:top-6 xl:max-h-[calc(100dvh-9rem)] xl:overflow-y-auto"
            >
              <div className="grid gap-1">
                <h2 className="text-lg font-semibold leading-snug">
                  Compiled preview
                </h2>
                <Meta>
                  Exactly what the model receives, minus the evidence — the server
                  inserts that per turn. It follows your edits as you type.
                </Meta>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid min-w-0 gap-2">
                  <Label htmlFor="preview-archetype">Question type</Label>
                  <Select
                    value={archetype}
                    onValueChange={(value) => setArchetype(value as AnswerArchetype)}
                  >
                    <SelectTrigger id="preview-archetype" className="w-full min-w-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ARCHETYPES.map((value) => (
                        <SelectItem key={value} value={value}>
                          {ARCHETYPE_LABELS[value]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid min-w-0 gap-2">
                  <Label htmlFor="preview-depth">Answer depth</Label>
                  <Select
                    value={depth}
                    onValueChange={(value) => setDepth(value as ResponseDepth)}
                  >
                    <SelectTrigger id="preview-depth" className="w-full min-w-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {DEPTHS.map((value) => (
                        <SelectItem key={value} value={value}>
                          {DEPTH_LABELS[value]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
                {blocked
                  ? "Paused until the highlighted prompt is valid."
                  : previewError
                    ? previewError
                    : previewing
                      ? "Compiling…"
                      : `Compiled for ${ARCHETYPE_LABELS[archetype].toLowerCase()}, ${DEPTH_LABELS[depth].toLowerCase()}.`}
              </p>

              <ReadOnlyPrompt
                id="compiled-system"
                label="System message"
                hint="Locked rules, then your instructions, then the template."
                value={preview?.preview_system_prompt ?? ""}
                pending={previewing}
              />
              <ReadOnlyPrompt
                id="compiled-user"
                label="User message"
                hint="The question and the retrieved evidence."
                value={preview?.preview_user_prompt ?? ""}
                pending={previewing}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
