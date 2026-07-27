"use client";

import { AlertCircle, Check, Copy, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ChatTurn } from "@/lib/types";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <Button
      variant="ghost"
      size="xs"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
      }}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

/** Three dots that read as "working" without claiming a percentage. */
export function ThinkingIndicator({ label }: { label: string }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-muted-foreground"
      role="status"
    >
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="size-1.5 animate-bounce rounded-full bg-primary"
            style={{ animationDelay: `${index * 0.15}s` }}
          />
        ))}
      </span>
      {label}
    </p>
  );
}

export interface TurnViewProps {
  turn: ChatTurn;
  isLast: boolean;
  canRetry: boolean;
  onRetry: () => void;
}

export function TurnView({ turn, isLast, canRetry, onRetry }: TurnViewProps) {
  const showThinking = turn.status === "streaming" && !turn.answer;

  return (
    <article className="space-y-4" aria-labelledby={`question-${turn.id}`}>
      <div className="flex justify-end">
        <h3
          id={`question-${turn.id}`}
          className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-[0.95rem] font-normal text-secondary-foreground"
        >
          {turn.question}
        </h3>
      </div>

      <div className="space-y-3">
        {showThinking && <ThinkingIndicator label="Checking the book…" />}

        {turn.answer && (
          <div className="answer-prose">
            <ReactMarkdown>{turn.answer}</ReactMarkdown>
          </div>
        )}

        {turn.status === "stopped" && (
          <p className="text-xs text-muted-foreground">
            Stopped before the answer finished. This turn was not added to the
            conversation, so a follow-up question will not see it.
          </p>
        )}

        {turn.status === "failed" && turn.error && (
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertDescription>{turn.error}</AlertDescription>
          </Alert>
        )}

        {turn.status !== "streaming" && (
          <div className="flex items-center gap-1">
            {turn.answer && <CopyButton text={turn.answer} />}
            {isLast && canRetry && (
              <Button variant="ghost" size="xs" onClick={onRetry}>
                <RotateCcw aria-hidden />
                {turn.status === "failed" ? "Try again" : "Regenerate"}
              </Button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
