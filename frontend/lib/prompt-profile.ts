/**
 * What the prompt studio needs to know about a prompt profile.
 *
 * The field list and the validation here mirror `PromptProfile` in
 * `study/contracts.py`. The server is still the authority — this exists so an
 * edit that cannot be saved says so next to the field that broke, instead of
 * arriving as a 422 with a Pydantic sentence attached to nothing.
 */

import type { AnswerArchetype, PromptProfile, ResponseDepth } from "@/lib/types";

export interface PromptFieldSpec {
  key: keyof PromptProfile;
  /** Names the prompt in the editor, the contents rail, and the change list. */
  label: string;
  /** One line: what this prompt decides. */
  purpose: string;
  /** When the prompt is used, in the reader's terms. */
  usedFor: string;
  /** `min_length` / `max_length` on the contract. */
  maxLength: number;
  /** The heading's id, so the contents rail can jump to it. */
  anchor: string;
}

/** Every editable prompt, in the order the server composes them. */
export const PROMPT_FIELDS: readonly PromptFieldSpec[] = [
  {
    key: "interview_instructions",
    label: "Interview instructions",
    purpose: "Tone, teaching behaviour, and what makes an answer interview-ready.",
    usedFor: "Prepended to every answer, whichever template runs.",
    maxLength: 12_000,
    anchor: "prompt-interview-instructions",
  },
  {
    key: "concept_template",
    label: "Concept answer",
    purpose: "The shape of an explanation of one idea.",
    usedFor: "Questions such as “Explain logistic regression.”",
    maxLength: 8_000,
    anchor: "prompt-concept-template",
  },
  {
    key: "system_design_template",
    label: "System design",
    purpose: "The shape of a design answer: requirements, trade-offs, failure modes.",
    usedFor: "Design and architecture questions.",
    maxLength: 8_000,
    anchor: "prompt-system-design-template",
  },
  {
    key: "chapter_review_template",
    label: "Chapter and section review",
    purpose: "How a whole canonical scope is turned into interview material.",
    usedFor: "“Review chapter 5” and other whole-scope requests.",
    maxLength: 8_000,
    anchor: "prompt-chapter-review-template",
  },
  {
    key: "user_prompt_template",
    label: "User message template",
    purpose: "How the question and the retrieved evidence are handed to the model.",
    usedFor: "Every turn. The one prompt with required placeholders.",
    maxLength: 8_000,
    anchor: "prompt-user-prompt-template",
  },
];

/** `min_length` is the same 20 characters on every field of the contract. */
export const MIN_PROMPT_LENGTH = 20;

export const REQUIRED_PLACEHOLDERS = ["question", "evidence"] as const;
export const OPTIONAL_PLACEHOLDERS = [
  "answer_archetype",
  "response_depth",
  "request_context",
] as const;

/**
 * The placeholder names a `str.format` template would substitute.
 *
 * `{{` and `}}` are literal braces to Python's formatter, so they are stripped
 * before the names are read; a conversion or format spec (`{question!r:>10}`)
 * names the same field.
 */
export function placeholdersIn(template: string): string[] {
  const names = new Set<string>();
  for (const match of template.replace(/\{\{|\}\}/g, "").matchAll(/\{([^{}]*)\}/g)) {
    const name = ((match[1] ?? "").split(/[!:]/, 1)[0] ?? "").trim();
    if (name) names.add(name);
  }
  return [...names];
}

/**
 * Why this field cannot be saved, in one sentence, or `null` when it can.
 *
 * Deliberately the same checks and the same order as the contract's validators,
 * so the studio never blocks a save the server would have accepted.
 */
export function fieldIssue(
  field: PromptFieldSpec,
  value: string,
): string | null {
  const text = value.trim();
  if (text.length < MIN_PROMPT_LENGTH) {
    return `Needs at least ${MIN_PROMPT_LENGTH} characters.`;
  }
  if (text.length > field.maxLength) {
    return `Too long by ${(text.length - field.maxLength).toLocaleString()} characters.`;
  }
  if (field.key !== "user_prompt_template") return null;

  const used = placeholdersIn(text);
  const allowed: readonly string[] = [
    ...REQUIRED_PLACEHOLDERS,
    ...OPTIONAL_PLACEHOLDERS,
  ];
  const unsupported = used.filter((name) => !allowed.includes(name));
  if (unsupported.length > 0) {
    return `Unsupported placeholder${unsupported.length > 1 ? "s" : ""}: ${unsupported
      .map((name) => `{${name}}`)
      .join(", ")}.`;
  }
  const missing = REQUIRED_PLACEHOLDERS.filter((name) => !used.includes(name));
  if (missing.length > 0) {
    return `Must keep ${missing.map((name) => `{${name}}`).join(" and ")}.`;
  }
  return null;
}

export type PromptIssues = Partial<Record<keyof PromptProfile, string>>;

export function profileIssues(profile: PromptProfile): PromptIssues {
  const issues: PromptIssues = {};
  for (const field of PROMPT_FIELDS) {
    const issue = fieldIssue(field, profile[field.key]);
    if (issue) issues[field.key] = issue;
  }
  return issues;
}

/** Which fields differ, comparing the way the server does — trimmed. */
export function changedFields(
  profile: PromptProfile,
  against: PromptProfile,
): Array<keyof PromptProfile> {
  return PROMPT_FIELDS.filter(
    (field) => profile[field.key].trim() !== against[field.key].trim(),
  ).map((field) => field.key);
}

export const ARCHETYPE_LABELS: Record<AnswerArchetype, string> = {
  concept_explanation: "Concept answer",
  system_design: "System design",
  chapter_review: "Chapter review",
  answer_transform: "Answer transform",
};

export const DEPTH_LABELS: Record<ResponseDepth, string> = {
  quick: "Quick answer",
  interview: "Interview answer",
  deep: "Deep dive",
};

/** Lines and characters, for the meter under an editor. */
export function textMetrics(value: string): { lines: number; characters: number } {
  return { lines: value === "" ? 0 : value.split("\n").length, characters: value.length };
}
