"use client";

import { ArrowUp, Square } from "lucide-react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { MicButton } from "@/components/dictation/mic-button";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { spliceTranscript } from "@/lib/dictation";
import type { BookSummary, ResponseDepth } from "@/lib/types";
import { cn } from "@/lib/utils";

const MAX_TEXTAREA_HEIGHT_PX = 200;

export interface ComposerProps {
  disabled: boolean;
  isStreaming: boolean;
  placeholder: string;
  onSubmit: (question: string, mentionedBookIds?: number[]) => void;
  onStop: () => void;
  books?: BookSummary[];
  hasDefaultScope?: boolean;
  responseDepth?: ResponseDepth;
  onResponseDepthChange?: (depth: ResponseDepth) => void;
  settingsControl?: React.ReactNode;
}

export function Composer({
  disabled,
  isStreaming,
  placeholder,
  onSubmit,
  onStop,
  books = [],
  hasDefaultScope = true,
  responseDepth = "interview",
  onResponseDepthChange,
  settingsControl,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [caret, setCaret] = useState(0);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [mentionDismissed, setMentionDismissed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const mentionListRef = useRef<HTMLDivElement | null>(null);
  const mentionOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const mentionListId = useId();
  const mentionCandidate = useMemo(() => {
    const beforeCaret = value.slice(0, caret);
    const match = /(?:^|\s)@([^@[\]\n]*)$/.exec(beforeCaret);
    if (!match) return null;
    const query = match[1]?.trim().toLocaleLowerCase() ?? "";
    const at = beforeCaret.lastIndexOf("@");
    return {
      start: at,
      query,
      matches: books
        .filter((book) => book.title.toLocaleLowerCase().includes(query))
        .slice(0, 6),
    };
  }, [books, caret, value]);
  const mention = mentionDismissed ? null : mentionCandidate;
  const mentionedBookIds = useMemo(
    () =>
      books
        .filter((book) => value.includes(`@[${book.title}]`))
        .map((book) => book.book_id),
    [books, value],
  );
  const hasScope = hasDefaultScope || mentionedBookIds.length > 0;

  useEffect(() => {
    setActiveMentionIndex(0);
  }, [mentionCandidate?.query, mentionCandidate?.start]);

  useEffect(() => {
    const list = mentionListRef.current;
    const option = mentionOptionRefs.current[activeMentionIndex];
    if (!mention || !list || !option) return;
    const optionTop = option.offsetTop;
    const optionBottom = optionTop + option.offsetHeight;
    if (optionTop < list.scrollTop) list.scrollTop = optionTop;
    else if (optionBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = optionBottom - list.clientHeight;
    }
  }, [activeMentionIndex, mention]);

  // Grow with the question up to a bound, then scroll inside the field. A
  // fixed two-row box hides the end of anything longer than a sentence.
  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(
      textarea.scrollHeight,
      MAX_TEXTAREA_HEIGHT_PX,
    )}px`;
  }, [value]);

  function submit() {
    const question = value.trim();
    if (!question || disabled || isStreaming || !hasScope) return;
    if (mentionedBookIds.length > 0) onSubmit(question, mentionedBookIds);
    else onSubmit(question);
    setValue("");
    setCaret(0);
    setMentionDismissed(false);
  }

  /**
   * Place dictated words where the caret is and leave them there.
   *
   * The textarea's own selection is read rather than the tracked `caret`
   * because clicking the mic moves focus away, and the field keeps the
   * selection it had when it lost focus.
   */
  function insertDictation(transcript: string) {
    const textarea = textareaRef.current;
    const start = textarea?.selectionStart ?? value.length;
    const end = textarea?.selectionEnd ?? start;
    const spliced = spliceTranscript(value, transcript, start, end);
    setValue(spliced.value);
    setCaret(spliced.caret);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(spliced.caret, spliced.caret);
    });
  }

  function insertMention(book: BookSummary) {
    if (!mention) return;
    const token = `@[${book.title}]`;
    const next = `${value.slice(0, mention.start)}${token} ${value.slice(caret)}`;
    const nextCaret = mention.start + token.length + 1;
    setValue(next);
    setCaret(nextCaret);
    setMentionDismissed(false);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(nextCaret, nextCaret);
    });
  }

  return (
    <form
      className="relative"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Select
          value={responseDepth}
          onValueChange={(value) =>
            onResponseDepthChange?.(value as ResponseDepth)
          }
        >
          <SelectTrigger
            size="sm"
            aria-label="Response depth"
            className="border-0 text-xs text-muted-foreground shadow-none"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="start">
            <SelectItem value="quick">Quick answer</SelectItem>
            <SelectItem value="interview">Interview answer</SelectItem>
            <SelectItem value="deep">Deep dive</SelectItem>
          </SelectContent>
        </Select>
        {settingsControl}
      </div>
      <label className="sr-only" htmlFor="question">
        Ask about the book
      </label>
      <Textarea
        ref={textareaRef}
        id="question"
        rows={1}
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          setCaret(event.target.selectionStart);
          setMentionDismissed(false);
        }}
        onClick={(event) => {
          setCaret(event.currentTarget.selectionStart);
          setMentionDismissed(false);
        }}
        onKeyUp={(event) => setCaret(event.currentTarget.selectionStart)}
        onKeyDown={(event) => {
          if (mention && mention.matches.length > 0) {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActiveMentionIndex(
                (current) => (current + 1) % mention.matches.length,
              );
              return;
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveMentionIndex(
                (current) =>
                  (current - 1 + mention.matches.length) % mention.matches.length,
              );
              return;
            }
            if (event.key === "Escape") {
              event.preventDefault();
              setMentionDismissed(true);
              return;
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              const selectedBook =
                mention.matches[activeMentionIndex] ?? mention.matches[0];
              if (selectedBook) insertMention(selectedBook);
              return;
            }
          }
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={Boolean(mention && mention.matches.length > 0)}
        aria-controls={mention ? mentionListId : undefined}
        aria-activedescendant={
          mention && mention.matches.length > 0
            ? `${mentionListId}-option-${activeMentionIndex}`
            : undefined
        }
        placeholder={placeholder}
        disabled={disabled}
        className="max-h-[200px] resize-none rounded-xl bg-card py-3 pl-3.5 pr-21 text-[0.95rem] shadow-sm"
      />

      {mention && mention.matches.length > 0 && (
        <div
          ref={mentionListRef}
          id={mentionListId}
          role="listbox"
          aria-label="Tag a book"
          className="absolute bottom-[calc(100%-2.25rem)] left-0 z-20 max-h-56 w-full overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md sm:w-96"
        >
          {mention.matches.map((book, index) => (
            <button
              ref={(element) => {
                mentionOptionRefs.current[index] = element;
              }}
              id={`${mentionListId}-option-${index}`}
              key={book.book_id}
              type="button"
              role="option"
              aria-selected={index === activeMentionIndex}
              className={cn(
                "flex w-full flex-col rounded-md px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none",
                index === activeMentionIndex && "bg-accent",
              )}
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => setActiveMentionIndex(index)}
              onClick={() => insertMention(book)}
            >
              <span className="truncate font-medium">{book.title}</span>
              {book.author && (
                <span className="truncate text-xs text-muted-foreground">
                  {book.author}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="absolute bottom-2 right-2 flex items-center gap-1">
        <MicButton disabled={disabled} onTranscript={insertDictation} />
        {isStreaming ? (
          <Button
            type="button"
            size="icon-sm"
            variant="secondary"
            onClick={onStop}
            aria-label="Stop generating"
          >
            <Square aria-hidden />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon-sm"
            disabled={disabled || !value.trim() || !hasScope}
            aria-label="Send question"
          >
            <ArrowUp aria-hidden />
          </Button>
        )}
      </div>
      {!hasScope && value.trim() && (
        <p className="mt-1 text-xs text-muted-foreground">
          Tag a book with @ or select one from your library.
        </p>
      )}
    </form>
  );
}
