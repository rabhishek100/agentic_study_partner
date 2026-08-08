"use client";

/**
 * Carrying one question from a failed card into a grounded conversation.
 *
 * A card you keep failing should become a real explanation, not a reread. The
 * question is handed over in session storage rather than in the URL: a card
 * front plus its answer runs to a few hundred characters, which is a hostile
 * URL and a worse browser history entry. It is read exactly once, so a later
 * refresh does not silently re-ask it.
 */

const KEY = "deck-handoff-question";

export interface DeckHandoff {
  question: string;
  /** Book cards narrow the conversation to the book they came from. */
  bookIds?: number[];
}

export function stashQuestion(handoff: DeckHandoff): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(handoff));
  } catch {
    // Private browsing, or storage disabled. The navigation still works; the
    // reader just lands on an empty composer instead of a seeded one.
  }
}

export function takeQuestion(): DeckHandoff | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    const parsed = JSON.parse(raw) as DeckHandoff;
    return parsed.question?.trim() ? parsed : null;
  } catch {
    return null;
  }
}

/** The question a card becomes when you could not recall it. */
export function questionForCard(front: string, deckTitle: string): string {
  return (
    `I could not recall this while revising ${deckTitle}: "${front.trim()}" ` +
    `Explain it properly, with the reasoning and the trade-offs, and tell me ` +
    `what an interviewer would follow up with.`
  );
}
