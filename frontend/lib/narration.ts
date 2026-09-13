/**
 * Turn a stored answer into a script a voice can read.
 *
 * This is rendering, not generation. The same boundary already puts citation
 * resolution and LaTeX normalisation in the client: taking grounded prose and
 * making it takeable-in is the interface's job, and doing it for the ear is
 * the same job as doing it for the eye. Nothing is added that the API did not
 * ground — the one thing spoken that is not on screen is a figure's
 * description, and that comes from the API too.
 *
 * What the transform removes is syntax, which is exactly what makes machine
 * reading intolerable: "asterisk asterisk", "backslash frac", "bracket S one".
 * What it adds is sentence structure, so the synthesiser has whole sentences
 * to give prosody to.
 */

import { figuresForMarker, resolveMarker, CITATION_PATTERN } from "./citations";
import { normalizeMath } from "./math";
import { speakMath } from "./spoken-math";
import type { CitationRef, EvidenceRef, FigureRef } from "./types";

/** A run of speech, and what it came from. */
export interface NarrationSegment {
  kind: "prose" | "figure";
  text: string;
  /** Stable rendered source for follow-along; never inferred from repeated text. */
  anchor?: NarrationAnchor;
  /** Present on a figure segment: which figure is being described. */
  figure?: FigureRef;
}

export type NarrationAnchor =
  | { type: "block"; key: string }
  | { type: "figure"; blockId: number };

export interface NarrationScript {
  segments: NarrationSegment[];
  /** Figures this script speaks, in the order it reaches them. */
  figures: FigureRef[];
}

/** One independently playable semantic passage in a narration script. */
export interface NarrationItem {
  /** Cleaned visible wording, used by follow-along and voice questions. */
  text: string;
  /** Provider wording with pronunciation hints that do not alter the answer. */
  speechText: string;
  kind: NarrationSegment["kind"];
  anchor?: NarrationAnchor;
}

export interface NarrationInput {
  answer: string;
  evidence?: EvidenceRef[];
  citations?: CitationRef[];
  figures?: FigureRef[];
  /** Spoken figure descriptions from the API, by block id. */
  descriptions?: Record<number, string>;
  /** Spoken before the answer, for "read the last exchange". */
  question?: string;
  /**
   * Visuals that already carry prose, spoken after the answer.
   *
   * This is the video surface, where a "figure" is a lecture frame and the
   * ingestion pipeline already wrote it a summary in sentences. There is
   * nothing to describe a second time, so those go in here whole rather than
   * through the figure path.
   */
  visuals?: { lead: string; description: string }[];
}

/**
 * The wait a listener notices is the wait for the first sound, so the opening
 * chunk is short and the rest are long enough to keep prosody intact. Both sit
 * well under the provider's own 2,500-character ceiling.
 */
export const FIRST_CHUNK_CHARACTERS = 320;
export const CHUNK_CHARACTERS = 800;
/** Enough context for natural prosody without making first playback sluggish. */
export const ITEM_CHARACTERS = 700;

/** Abbreviations whose full stop does not end a sentence. */
const ABBREVIATIONS = /(?:e\.g|i\.e|etc|vs|cf|approx|Fig|Eq|Dr|Prof|St|No|pp|p)\.$/i;

/** Split prose into sentences, tolerating the abbreviations textbooks use. */
export function splitSentences(text: string): string[] {
  const pieces = text.split(/(?<=[.!?])\s+/);
  const sentences: string[] = [];

  for (const piece of pieces) {
    const previous = sentences[sentences.length - 1];
    // "…as shown in Fig. 4" split after "Fig." — rejoin it, because a pause
    // there is a pause in the middle of a reference.
    if (previous !== undefined && ABBREVIATIONS.test(previous)) {
      sentences[sentences.length - 1] = `${previous} ${piece}`;
      continue;
    }
    if (piece.trim()) sentences.push(piece.trim());
  }
  return sentences;
}

/** Speak a maths span, or say nothing if it verbalises to nothing. */
function spokenMath(body: string): string {
  const spoken = speakMath(body);
  return spoken ? ` ${spoken} ` : " ";
}

/**
 * One markdown block as speech: syntax removed, maths in words.
 *
 * Citation markers are left in place here. They are located afterwards, on the
 * cleaned text, so a figure lands after the sentence that cites it.
 */
function speakableProse(block: string): string {
  let text = normalizeMath(block);

  text = text
    // Display maths first: the delimiters are longer and would otherwise be
    // eaten a dollar at a time by the inline rule.
    .replace(/\$\$([\s\S]*?)\$\$/g, (_, body: string) => spokenMath(body))
    .replace(/\$([^$\n]+)\$/g, (_, body: string) => spokenMath(body))
    // An image contributes its alt text; a link contributes its words, never
    // its URL, which is unspeakable and uninteresting.
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(?<!\w)([*_])(?=\S)(.*?)(?<=\S)\1(?!\w)/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    // Raw HTML is not rendered by react-markdown and must not be pronounced.
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, " and ")
    .replace(/&lt;/gi, " less than ")
    .replace(/&gt;/gi, " greater than ")
    // List controls are visual. Keep each item as a punctuated thought so a
    // compact Markdown list does not become one breathless run-on sentence.
    .replace(/^\s*[-*+]\s+\[[ xX]\]\s+(.+)$/gm, (_, item: string) => terminated(item))
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+(.+)$/gm, (_, item: string) => terminated(item))
    // A horizontal rule is a visual pause with nothing to say.
    .replace(/^\s*([-*_])\1{2,}\s*$/gm, "")
    .replace(/\s+/g, " ")
    .trim();

  return text;
}

/**
 * Make technical prose unambiguous to a speech model without changing claims.
 *
 * Letter sequences are spaced only where engineers conventionally spell them
 * out. Terms normally spoken as words (RAG, REST, JSON, CUDA, NumPy) stay
 * intact. The list is deliberately conservative: a wrong pronunciation hint
 * is worse than allowing a capable voice model to pronounce an unfamiliar
 * term from context.
 */
export function pronunciationText(value: string): string {
  const spelled: Record<string, string> = {
    AI: "A I", ML: "M L", LLM: "L L M", LLMs: "L L M's",
    API: "A P I", APIs: "A P I's", GPU: "G P U", GPUs: "G P U's",
    CPU: "C P U", CPUs: "C P U's", TPU: "T P U", TPUs: "T P U's",
    UI: "U I", UX: "U X", CLI: "C L I", SDK: "S D K",
    HTTP: "H T T P", HTTPS: "H T T P S", TCP: "T C P", UDP: "U D P",
    SQL: "S Q L", FTS: "F T S", ANN: "A N N", HNSW: "H N S W",
    NLP: "N L P", OCR: "O C R", PDF: "P D F", PDFs: "P D F's",
    URL: "U R L", URLs: "U R L's", UUID: "U U I D", JWT: "J W T",
    CI: "C I", CD: "C D", AWS: "A W S", GCP: "G C P",
  };

  return value
    // Identifiers are read as words, not as one invented word or punctuation.
    .replace(/\b([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b/g, (identifier) =>
      identifier.replace(/_/g, " "),
    )
    .replace(/\b[a-z]+(?:[A-Z][a-z0-9]*)+\b/g, (identifier) =>
      identifier.replace(/([a-z0-9])([A-Z])/g, "$1 $2"),
    )
    .replace(/\b[A-Za-z]+\d+(?:\.\d+)?\b/g, (term) => {
      const match = /^([A-Za-z]+)(\d+(?:\.\d+)?)$/.exec(term);
      if (!match) return term;
      const prefix = spelled[match[1]!] ?? match[1]!.split("").join(" ");
      return `${prefix} ${match[2]}`;
    })
    .replace(/\b(?:AI|ML|LLMs?|APIs?|GPUs?|CPUs?|TPUs?|UI|UX|CLI|SDK|HTTPS?|TCP|UDP|SQL|FTS|ANN|HNSW|NLP|OCR|PDFs?|URLs?|UUID|JWT|CI|CD|AWS|GCP)\b/g,
      (term) => spelled[term] ?? term,
    )
    .replace(/\s*&\s*/g, " and ")
    .replace(/\s*(?:→|->|=>)\s*/g, " goes to ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Ensure a spoken line ends in a full stop, so the voice actually stops. */
function terminated(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  return /[.!?:;]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

/** Remove citation markers and the whitespace they leave behind. */
function withoutMarkers(text: string): string {
  return text
    .replace(new RegExp(CITATION_PATTERN.source, "g"), "")
    .replace(/\s+([.,;:!?])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** How a figure is introduced, whether or not it can be described. */
export function figureLead(figure: FigureRef): string {
  return figure.page ? `Figure, page ${figure.page}.` : "Figure.";
}

interface Block {
  kind: "prose" | "heading" | "list" | "code" | "table";
  lines: string[];
  startLine: number;
}

/**
 * Split markdown into blocks, keeping fenced code and tables whole.
 *
 * Both are announced rather than read. Reading a code block aloud gives "def
 * train open paren capital X comma y close paren colon", and reading a table
 * row by row gives a column header before every cell — neither is narration,
 * and both are on screen for the reader who wants them.
 */
function blocks(markdown: string): Block[] {
  const found: Block[] = [];
  const lines = markdown.split("\n");
  let current: Block | null = null;
  let fence: string | null = null;

  const flush = () => {
    if (current && current.lines.some((line) => line.trim())) found.push(current);
    current = null;
  };

  for (const [lineIndex, line] of lines.entries()) {
    const fenceMatch = /^\s*(```|~~~)/.exec(line);
    if (fence) {
      current?.lines.push(line);
      if (fenceMatch && line.trim().startsWith(fence)) {
        fence = null;
        flush();
      }
      continue;
    }
    if (fenceMatch) {
      flush();
      fence = fenceMatch[1]!;
      current = { kind: "code", lines: [line], startLine: lineIndex + 1 };
      continue;
    }

    const isHeading = /^\s{0,3}#{1,6}\s+/.test(line);
    if (isHeading) {
      flush();
      found.push({ kind: "heading", lines: [line], startLine: lineIndex + 1 });
      continue;
    }

    const isListItem = /^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)/.test(line);
    if (isListItem) {
      if (current?.kind !== "list") {
        flush();
        current = { kind: "list", lines: [], startLine: lineIndex + 1 };
      }
      current.lines.push(line);
      continue;
    }

    const isTableRow = /^\s*\|.*\|\s*$/.test(line);
    if (isTableRow) {
      if (current?.kind !== "table") {
        flush();
        current = { kind: "table", lines: [], startLine: lineIndex + 1 };
      }
      current.lines.push(line);
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }
    if (current?.kind !== "prose") {
      flush();
      current = { kind: "prose", lines: [], startLine: lineIndex + 1 };
    }
    current.lines.push(line);
  }
  flush();
  return found;
}

function announceCode(block: Block): string {
  // The fences themselves are not lines of code.
  const count = Math.max(0, block.lines.length - 2);
  const language = /^\s*(?:```|~~~)\s*([A-Za-z0-9+#-]+)/.exec(block.lines[0] ?? "")?.[1];
  const named = language ? `${language} code block` : "Code block";
  return count > 0
    ? `${named}, ${count} ${count === 1 ? "line" : "lines"}, shown on screen.`
    : `${named}, shown on screen.`;
}

function announceTable(block: Block): string {
  // A separator row (`|---|---|`) is formatting, and the first row is headers.
  const rows = block.lines.filter((line) => !/^\s*\|[\s:|-]+\|\s*$/.test(line));
  const count = Math.max(0, rows.length - 1);
  return count > 0
    ? `Table with ${count} ${count === 1 ? "row" : "rows"}, shown on screen.`
    : "Table, shown on screen.";
}

/**
 * Build the script for one answer.
 *
 * Called twice in practice: once to learn which figures the script reaches,
 * and again once their descriptions have been fetched. It is a pure function
 * over its input, so the second call costs nothing.
 */
export function buildNarrationScript(input: NarrationInput): NarrationScript {
  const evidence = input.evidence ?? [];
  const citations = input.citations ?? [];
  const figures = input.figures ?? [];
  const descriptions = input.descriptions ?? {};

  const segments: NarrationSegment[] = [];
  const spokenFigures: FigureRef[] = [];
  const alreadySpoken = new Set<number>();

  const speakFigure = (figure: FigureRef) => {
    if (alreadySpoken.has(figure.block_id)) return;
    alreadySpoken.add(figure.block_id);
    spokenFigures.push(figure);
    const description = (descriptions[figure.block_id] ?? "").trim();
    segments.push({
      kind: "figure",
      figure,
      anchor: { type: "figure", blockId: figure.block_id },
      text: description
        ? `${figureLead(figure)} ${terminated(description)}`
        : figureLead(figure),
    });
  };

  if (input.question?.trim()) {
    segments.push({
      kind: "prose",
      text: `You asked: ${terminated(withoutMarkers(speakableProse(input.question)))}`,
    });
  }

  for (const block of blocks(input.answer)) {
    const anchor: NarrationAnchor = { type: "block", key: `line-${block.startLine}` };
    if (block.kind === "code") {
      segments.push({ kind: "prose", text: announceCode(block), anchor });
      continue;
    }
    if (block.kind === "table") {
      segments.push({ kind: "prose", text: announceTable(block), anchor });
      continue;
    }

    const prose = speakableProse(block.lines.join("\n"));
    if (!prose) continue;

    for (const sentence of splitSentences(prose)) {
      const spoken = withoutMarkers(sentence);
      if (spoken) segments.push({ kind: "prose", text: terminated(spoken), anchor });

      // Whatever this sentence cited, described right after it — which is
      // where the reader's eye would have gone.
      const pattern = new RegExp(CITATION_PATTERN.source, "g");
      let match: RegExpExecArray | null;
      while ((match = pattern.exec(sentence)) !== null) {
        const marker = resolveMarker(match[0], evidence, citations);
        for (const figure of figuresForMarker(figures, marker)) speakFigure(figure);
      }
    }
  }

  // Figures no marker claimed still exist on screen below the prose, so they
  // are spoken there too rather than dropped.
  const unclaimed = figures.filter((figure) => !alreadySpoken.has(figure.block_id));
  const visuals = (input.visuals ?? []).filter((visual) => visual.description.trim());
  if (unclaimed.length > 0 || visuals.length > 0) {
    segments.push({
      kind: "prose",
      text: unclaimed.length + visuals.length === 1 ? "Also shown." : "Also shown, in order.",
    });
    for (const figure of unclaimed) speakFigure(figure);
    for (const visual of visuals) {
      segments.push({
        kind: "figure",
        text: `${terminated(visual.lead)} ${terminated(visual.description)}`,
      });
    }
  }

  return { segments, figures: spokenFigures };
}

/**
 * Pack a script into requests, cut only at segment or sentence ends.
 *
 * A chunk boundary is a boundary the synthesiser hears: cut mid-clause and the
 * voice drops pitch as if the sentence had ended. So a long sentence is only
 * ever split when it alone exceeds the ceiling, which for real prose it does
 * not.
 */
export function narrationChunks(
  script: NarrationScript,
  { first = FIRST_CHUNK_CHARACTERS, rest = CHUNK_CHARACTERS } = {},
): string[] {
  const pieces: string[] = [];
  for (const segment of script.segments) {
    for (const sentence of splitSentences(segment.text)) pieces.push(sentence);
  }

  const chunks: string[] = [];
  let current = "";
  const ceiling = () => (chunks.length === 0 ? first : rest);

  for (const piece of pieces) {
    if (!current) {
      current = piece;
      continue;
    }
    if (current.length + 1 + piece.length <= ceiling()) {
      current = `${current} ${piece}`;
      continue;
    }
    chunks.push(current);
    current = piece;
  }
  if (current) chunks.push(current);

  return chunks.filter((chunk) => /[a-z0-9]/i.test(chunk));
}

/**
 * Addressable playback units for the media player.
 *
 * TTS is requested per rendered passage. This gives the voice enough context
 * for stable prosody while pause, rewind, scrubbing and follow-along retain a
 * deterministic source target. The server cache keeps replay inexpensive.
 */
export function narrationItems(script: NarrationScript): NarrationItem[] {
  const items: NarrationItem[] = [];
  for (const segment of script.segments) {
    for (const sentence of splitSentences(segment.text)) {
      if (!/[a-z0-9]/i.test(sentence)) continue;
      const previous = items.at(-1);
      const sameAnchor = JSON.stringify(previous?.anchor) === JSON.stringify(segment.anchor);
      // One rendered paragraph (or one figure description) is one voice take.
      // Keeping its neighbouring sentences together prevents the voice from
      // resetting pitch, pace and timbre after every full stop.
      if (
        previous &&
        previous.kind === segment.kind &&
        sameAnchor &&
        previous.text.length + 1 + sentence.length <= ITEM_CHARACTERS
      ) {
        previous.text = `${previous.text} ${sentence}`;
        previous.speechText = pronunciationText(previous.text);
      } else {
        items.push({
          text: sentence,
          speechText: pronunciationText(sentence),
          kind: segment.kind,
          anchor: segment.anchor,
        });
      }
    }
  }
  return items;
}

/** The figures a script will reach, so their descriptions can be fetched. */
export function citedFigures(input: NarrationInput): FigureRef[] {
  return buildNarrationScript({ ...input, descriptions: {} }).figures;
}
