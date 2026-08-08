"use client";

import { AlertCircle } from "lucide-react";

import { VideoAnswer } from "@/components/video/video-answer";
import { VideoInspector } from "@/components/video/video-inspector";
import { VideoReferences } from "@/components/video/video-references";
import { ThinkingIndicator } from "@/components/video/video-turn";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { SideChatTurn } from "@/lib/side-chat";
import type { VideoDocumentTarget, VideoTurnResult } from "@/lib/video-types";

export interface VideoSideChatTurnsProps {
  videoId: string;
  turns: SideChatTurn<VideoTurnResult>[];
  isLoading: boolean;
  isQueued: boolean;
  onSeek: (milliseconds: number) => void;
  onOpenDocument: (target: VideoDocumentTarget) => void;
}

/**
 * A lecture side chat's exchanges, rendered narrow.
 *
 * The answer body, its timestamp and page markers, and the reference list are
 * the same components the main lecture chat uses, so a side answer is a
 * first-class grounded answer: its markers still seek the player and still open
 * the slide they cite. What is left out is what a small window has no room for —
 * the visual evidence strip and the document page gallery, both of which are
 * better read in the main column.
 */
export function VideoSideChatTurns({
  videoId,
  turns,
  isLoading,
  isQueued,
  onSeek,
  onOpenDocument,
}: VideoSideChatTurnsProps) {
  if (isLoading && turns.length === 0) {
    return (
      <p className="side-chat-ui px-3 py-4 text-muted-foreground" role="status">
        Loading this side chat…
      </p>
    );
  }

  if (turns.length === 0) {
    return (
      <p className="side-chat-ui px-3 py-4 text-muted-foreground">
        Ask about the highlighted passage. Answers search the same lecture as the
        main conversation, so this can go beyond what the passage itself says.
      </p>
    );
  }

  return (
    <div className="space-y-4 px-3 py-3">
      {turns.map((turn) => (
        <article key={turn.id} className="space-y-2">
          <p className="side-chat-ui rounded-lg rounded-br-sm bg-secondary px-2.5 py-1.5 text-secondary-foreground">
            {turn.question}
          </p>

          {turn.status === "streaming" &&
            !turn.answer &&
            (isQueued ? (
              <p className="side-chat-ui text-muted-foreground" role="status">
                Waiting for the other side chats to finish…
              </p>
            ) : (
              <ThinkingIndicator label="Searching the lecture…" />
            ))}

          {turn.answer &&
            (turn.result ? (
              <VideoAnswer
                videoId={videoId}
                answer={turn.answer}
                evidence={turn.result.evidence}
                citations={turn.result.citations}
                onSeek={onSeek}
                onOpenDocument={onOpenDocument}
              />
            ) : (
              // Mid-stream there are no citations to resolve yet, so markers
              // would render as literal "[S1]" text.
              <p className="side-chat-prose whitespace-pre-wrap">
                {turn.answer}
              </p>
            ))}

          {turn.result && turn.result.evidence.length > 0 && (
            <VideoReferences
              evidence={turn.result.evidence}
              citations={turn.result.citations}
              onSeek={onSeek}
              onOpenDocument={onOpenDocument}
            />
          )}

          {turn.status === "stopped" && (
            <p className="side-chat-ui text-muted-foreground">
              Stopped before the answer finished, so this turn was not recorded.
            </p>
          )}

          {turn.status === "failed" && turn.error && (
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription className="side-chat-ui">
                {turn.error}
              </AlertDescription>
            </Alert>
          )}

          {turn.result && <VideoInspector result={turn.result} />}
        </article>
      ))}
    </div>
  );
}
