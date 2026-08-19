"use client";

import { Lock, LockOpen } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * One control, one meaning: answer from this source or say you cannot.
 *
 * Escalation is automatic by default, which is right for study and wrong for
 * verification. This is the opt-out, and it is deliberately a single switch
 * rather than a per-rung setting — a reader either wants their own material or
 * does not, and the ladder between those two states is the system's business.
 */
export function StayInSourceToggle({
  locked,
  onChange,
  noun,
}: {
  locked: boolean;
  onChange: (locked: boolean) => void;
  /** "book", "paper", "lecture" — whatever the reader has open. */
  noun: string;
}) {
  return (
    <Button
      type="button"
      variant={locked ? "secondary" : "ghost"}
      size="sm"
      aria-pressed={locked}
      title={
        locked
          ? `Questions this ${noun} does not answer will be refused rather than answered from elsewhere`
          : `Questions this ${noun} does not answer may be answered from your library, then from general knowledge`
      }
      onClick={() => onChange(!locked)}
    >
      {locked ? <Lock aria-hidden /> : <LockOpen aria-hidden />}
      {locked ? `Staying in this ${noun}` : `Stay in this ${noun}`}
    </Button>
  );
}
