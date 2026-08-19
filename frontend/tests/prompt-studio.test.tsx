import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PromptStudio,
  type PromptStudioProps,
} from "@/components/prompts/prompt-studio";
import { fieldIssue, PROMPT_FIELDS, placeholdersIn } from "@/lib/prompt-profile";
import type { PromptProfile, PromptSettingsResponse } from "@/lib/types";

const DEFAULTS: PromptProfile = {
  interview_instructions: "Teach the idea, then say what an interviewer listens for.",
  concept_template: "Definition, intuition, mechanics, failure modes, one example.",
  system_design_template: "Requirements, estimate, architecture, bottlenecks.",
  chapter_review_template: "Cover every section, then the questions it invites.",
  user_prompt_template: "Question: {question}\n\nEvidence:\n{evidence}",
};

function settings(profile: PromptProfile = DEFAULTS): PromptSettingsResponse {
  return {
    profile,
    defaults: DEFAULTS,
    locked_system_prompt: "Answer only from the evidence. Cite every claim.",
    preview_system_prompt: "LOCKED\n\n" + profile.interview_instructions,
    preview_user_prompt: "Question: What is a p-value?\n\nEvidence:\n<evidence>",
    profile_version: "v1",
  };
}

function studio(overrides: Partial<PromptStudioProps> = {}) {
  const props: PromptStudioProps = {
    loaded: true,
    loadError: "",
    settings: settings(),
    initialProfile: DEFAULTS,
    conversationId: null,
    responseDepth: "interview",
    compile: vi.fn().mockResolvedValue(settings()),
    saveDefault: vi.fn().mockImplementation((profile: PromptProfile) =>
      Promise.resolve(settings(profile)),
    ),
    applyToConversation: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(<PromptStudio {...props} />);
  return props;
}

function editor(label: string) {
  return screen.getByRole("textbox", { name: label });
}

beforeEach(() => {
  // jsdom's `navigator.clipboard` is a getter-only property, and the copy
  // controls read it on click.
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

describe("prompt profile validation", () => {
  it("reads placeholder names past a conversion or format spec", () => {
    expect(placeholdersIn("{question!r} {evidence:>4} {{literal}}").sort()).toEqual([
      "evidence",
      "question",
    ]);
  });

  it("names the placeholder an edit dropped", () => {
    const field = PROMPT_FIELDS.find((f) => f.key === "user_prompt_template")!;
    expect(fieldIssue(field, "Question: {question} and nothing else at all")).toBe(
      "Must keep {evidence}.",
    );
  });

  it("names a placeholder the server would reject", () => {
    const field = PROMPT_FIELDS.find((f) => f.key === "user_prompt_template")!;
    expect(fieldIssue(field, "{question} {evidence} {chapter} padding text here")).toBe(
      "Unsupported placeholder: {chapter}.",
    );
  });
});

describe("prompt studio", () => {
  it("renders every prompt as its own editor, with the locked rules read-only", () => {
    studio();
    for (const field of PROMPT_FIELDS) {
      expect(screen.getByRole("heading", { name: field.label })).toBeInTheDocument();
      expect(editor(field.label)).toHaveValue(DEFAULTS[field.key]);
    }
    expect(
      screen.queryByRole("textbox", { name: "Required grounding rules" }),
    ).not.toBeInTheDocument();
  });

  it("saves an edited profile as the account default", async () => {
    const user = userEvent.setup();
    const props = studio();

    await user.type(editor("Concept answer"), " Then a worked example.");
    expect(await screen.findByText(/1 unsaved change/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save as my default" }));

    await waitFor(() => expect(props.saveDefault).toHaveBeenCalledTimes(1));
    const saved = vi.mocked(props.saveDefault).mock.calls[0]![0];
    expect(saved.concept_template).toContain("Then a worked example.");
    expect(
      await screen.findByText("Saved. New conversations start from these prompts."),
    ).toBeInTheDocument();
  });

  it("applies to the conversation the reader came from, and only then", async () => {
    const user = userEvent.setup();
    expect(
      screen.queryByRole("button", { name: "Apply to conversation" }),
    ).not.toBeInTheDocument();

    const props = studio({ conversationId: "conv-1" });
    await user.click(screen.getByRole("button", { name: "Apply to conversation" }));

    await waitFor(() => expect(props.applyToConversation).toHaveBeenCalledTimes(1));
  });

  it("blocks a save that the contract would reject, and says why in place", async () => {
    const user = userEvent.setup();
    const props = studio();

    await user.clear(editor("User message template"));
    // `{{` is how userEvent types a literal brace; the field ends up holding
    // "Answer this question: {question} please", with no {evidence}.
    await user.type(
      editor("User message template"),
      "Answer this question: {{question} please",
    );

    expect(await screen.findByText("Must keep {evidence}.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as my default" })).toBeDisabled();
    expect(props.saveDefault).not.toHaveBeenCalled();
  });

  it("recompiles the preview from the edited draft", async () => {
    const user = userEvent.setup();
    const props = studio();

    await user.type(editor("Interview instructions"), " Be concrete.");

    await waitFor(() =>
      expect(props.compile).toHaveBeenCalledWith(
        expect.objectContaining({
          interview_instructions: expect.stringContaining("Be concrete."),
        }),
        "concept_explanation",
        "interview",
      ),
    );
  });

  it("restores one prompt to its project default", async () => {
    const user = userEvent.setup();
    studio({
      initialProfile: { ...DEFAULTS, concept_template: "Something else entirely." },
    });

    const section = screen.getByRole("region", { name: "Concept answer" });
    expect(section).toHaveTextContent("Edited");

    await user.click(within(section).getByRole("button", { name: "Reset" }));

    expect(editor("Concept answer")).toHaveValue(DEFAULTS.concept_template);
  });

  it("reports a failed load instead of an empty editor", () => {
    studio({ loaded: true, loadError: "Could not load your prompts.", settings: null });
    expect(screen.getByText("Could not load your prompts.")).toBeInTheDocument();
  });
});
