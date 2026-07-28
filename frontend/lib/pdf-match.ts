/**
 * Locate a stored excerpt inside a rendered PDF page's text layer.
 *
 * The parser keeps no geometry — a canonical block records its page and
 * nothing about where on the page it sits — so highlighting a cited passage
 * means finding its text again in what pdf.js renders. That text differs from
 * the stored excerpt in predictable ways: ligatures, soft hyphens, words
 * broken across line ends, and whitespace that reflects column layout rather
 * than the sentence.
 *
 * Everything here is pure and tested directly, including the failure path.
 * When no acceptable match exists the caller highlights the whole page and
 * says so, which is much better than highlighting the wrong paragraph.
 */

/** Similarity below which a candidate is not considered the excerpt. */
export const MATCH_THRESHOLD = 0.62;

export interface TextItem {
  /** One text run as pdf.js reports it. */
  str: string;
}

export interface MatchRange {
  /** Index of the first matching item, inclusive. */
  start: number;
  /** Index of the last matching item, inclusive. */
  end: number;
  score: number;
}

/**
 * Reduce text to what two renderings of the same sentence share.
 *
 * NFKC folds ligatures (ﬁ → fi). Soft hyphens and hyphens at line ends are
 * dropped so "regres-\nsion" matches "regression". Case and punctuation are
 * discarded because neither survives column layout reliably.
 */
export function normalizeForMatch(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/­/g, "")
    .replace(/-\s*\n\s*/g, "")
    .replace(/[‐-―]/g, "-")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Word-level Jaccard-with-order: cheap, and robust to reflowed whitespace. */
export function similarity(left: string, right: string): number {
  const a = left.split(" ").filter(Boolean);
  const b = right.split(" ").filter(Boolean);
  if (a.length === 0 || b.length === 0) return 0;

  const counts = new Map<string, number>();
  for (const word of a) counts.set(word, (counts.get(word) ?? 0) + 1);

  let shared = 0;
  for (const word of b) {
    const remaining = counts.get(word) ?? 0;
    if (remaining > 0) {
      shared += 1;
      counts.set(word, remaining - 1);
    }
  }
  // Against the excerpt's length, so a page containing the excerpt plus much
  // more still scores highly rather than being penalised for the surplus.
  return shared / a.length;
}

/**
 * Find the run of text items that best matches `excerpt`.
 *
 * Returns null when nothing clears `MATCH_THRESHOLD`, which is the signal to
 * fall back to a page-level highlight.
 */
export function findExcerptRange(
  items: TextItem[],
  excerpt: string,
  threshold: number = MATCH_THRESHOLD,
): MatchRange | null {
  const target = normalizeForMatch(excerpt);
  if (!target || items.length === 0) return null;

  const normalized = items.map((item) => normalizeForMatch(item.str));
  const targetWords = target.split(" ").filter(Boolean).length;

  let best: MatchRange | null = null;
  let bestWidth = Number.POSITIVE_INFINITY;

  for (let start = 0; start < items.length; start += 1) {
    if (!normalized[start]) continue;
    const parts: string[] = [];
    let words = 0;

    for (let end = start; end < items.length; end += 1) {
      const piece = normalized[end];
      if (piece) {
        parts.push(piece);
        words += piece.split(" ").filter(Boolean).length;
      }
      // Once the window is half again as long as the excerpt, extending it
      // can only dilute the match.
      if (words > targetWords * 1.5 && end > start) break;

      const score = similarity(target, parts.join(" "));
      // Ties go to the tighter window. Several windows can contain the whole
      // excerpt — the widest of them starts a run or two early — and
      // highlighting text the excerpt does not include is a worse answer than
      // highlighting exactly what it does.
      if (!best || score > best.score || (score === best.score && words < bestWidth)) {
        best = { start, end, score };
        bestWidth = words;
      }
    }
  }

  return best && best.score >= threshold ? best : null;
}
