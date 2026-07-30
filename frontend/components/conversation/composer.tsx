"use client";

import { ArrowUp, Square } from "lucide-react";
import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { BookSummary, ResponseDepth } from "@/lib/types";

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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const mention = useMemo(() => {
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
  const mentionedBookIds = useMemo(
    () =>
      books
        .filter((book) => value.includes(`@[${book.title}]`))
        .map((book) => book.book_id),
    [books, value],
  );
  const hasScope = hasDefaultScope || mentionedBookIds.length > 0;

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
  }

  function insertMention(book: BookSummary) {
    if (!mention) return;
    const token = `@[${book.title}]`;
    const next = `${value.slice(0, mention.start)}${token} ${value.slice(caret)}`;
    const nextCaret = mention.start + token.length + 1;
    setValue(next);
    setCaret(nextCaret);
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
        }}
        onClick={(event) => setCaret(event.currentTarget.selectionStart)}
        onKeyUp={(event) => setCaret(event.currentTarget.selectionStart)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        disabled={disabled}
        className="max-h-[200px] resize-none rounded-xl bg-card py-3 pl-3.5 pr-13 text-[0.95rem] shadow-sm"
      />

      {mention && mention.matches.length > 0 && (
        <div
          role="listbox"
          aria-label="Tag a book"
          className="absolute bottom-[calc(100%-2.25rem)] left-0 z-20 max-h-56 w-full overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md sm:w-96"
        >
          {mention.matches.map((book) => (
            <button
              key={book.book_id}
              type="button"
              role="option"
              aria-selected={false}
              className="flex w-full flex-col rounded-md px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
              onMouseDown={(event) => event.preventDefault()}
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

      <div className="absolute bottom-2 right-2">
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
