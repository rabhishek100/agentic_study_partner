"use client";

import { DropdownMenuCheckboxItem } from "@/components/ui/dropdown-menu";

/**
 * One control, one meaning: answer from this source or say you cannot.
 *
 * Escalation is automatic by default, which is right for study and wrong for
 * verification. This is the opt-out, and it is deliberately a single switch
 * rather than a per-rung setting — a reader either wants their own material or
 * does not, and the ladder between those two states is the system's business.
 *
 * It lives in the session menu, which is where the specification puts it and
 * which keeps the composer's own edge clear. What may not live in a menu is the
 * *engaged* state: the lock persists between visits and its effect is a refusal
 * that looks like a bad answer, so the panel says so on its surface whenever it
 * is on.
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
    <DropdownMenuCheckboxItem
      checked={locked}
      // The menu stays open: this is a setting the reader may want to read the
      // consequence of, and closing on the tick hides the sentence below it.
      onSelect={(event) => event.preventDefault()}
      onCheckedChange={onChange}
    >
      <span className="flex flex-col gap-1">
        <span>{locked ? `Staying in this ${noun}` : `Stay in this ${noun}`}</span>
        <span className="text-xs text-muted-foreground">
          {locked
            ? `Questions this ${noun} does not answer are refused rather than answered from elsewhere`
            : `Questions this ${noun} does not answer may be answered from your library, then from general knowledge`}
        </span>
      </span>
    </DropdownMenuCheckboxItem>
  );
}
