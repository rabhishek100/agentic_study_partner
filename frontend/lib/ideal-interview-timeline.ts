import type { IdealInterviewExchange } from "@/lib/interview-types";

export type IdealSpeaker = "interviewer" | "candidate";

export interface IdealTimelineSegment {
  exchangeIndex: number;
  speaker: IdealSpeaker;
  sentenceIndex: number;
  start: number;
  end: number;
}

const WORDS_PER_MINUTE = 155;

export function spokenSentences(text: string): string[] {
  const sentences = text.split(/(?<=[.!?])\s+/).map((part) => part.trim()).filter(Boolean);
  return sentences.length ? sentences : [text];
}

function speechSeconds(text: string): number {
  return Math.max(0.25, text.trim().split(/\s+/).filter(Boolean).length / WORDS_PER_MINUTE * 60);
}

export function buildIdealTimeline(exchanges: IdealInterviewExchange[]): IdealTimelineSegment[] {
  const segments: IdealTimelineSegment[] = [];
  let cursor = 0;
  for (const exchange of exchanges) {
    const speakers: Array<[IdealSpeaker, string, number]> = [
      ["interviewer", exchange.interviewer_text, exchange.pause_after_question_ms / 1_000],
      ["candidate", exchange.candidate_text, exchange.pause_after_answer_ms / 1_000],
    ];
    for (const [speaker, text, finalPause] of speakers) {
      const sentences = spokenSentences(text);
      sentences.forEach((sentence, sentenceIndex) => {
        const start = cursor;
        cursor += speechSeconds(sentence);
        if (sentenceIndex === sentences.length - 1) cursor += finalPause;
        segments.push({
          exchangeIndex: exchange.exchange_index,
          speaker,
          sentenceIndex,
          start,
          end: cursor,
        });
      });
    }
  }
  return segments;
}

export function segmentAt(
  timeline: IdealTimelineSegment[],
  seconds: number,
): IdealTimelineSegment | null {
  if (!timeline.length) return null;
  const bounded = Math.max(0, Math.min(seconds, timeline.at(-1)!.end));
  return [...timeline].reverse().find((segment) => segment.start <= bounded) ?? timeline[0]!;
}

export function formatInterviewTime(seconds: number): string {
  const whole = Math.max(0, Math.round(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
