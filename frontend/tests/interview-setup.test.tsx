import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  LaunchPanel,
  type LaunchPanelProps,
} from "@/components/interviews/launch-panel";
import { ChipChoices, OptionCards } from "@/components/interviews/setup-controls";
import type { InterviewPreflight } from "@/lib/interview-types";

const PREFLIGHT: InterviewPreflight = {
  source_kind: "book",
  scope_key: "book:1:5",
  title: "5. Functions",
  source_title: "Fluent Python",
  detected_format: "concept",
  selected_format: "concept",
  format_source: "detected",
  topic_count: 14,
  required_topic_count: 9,
  coding_topic_count: 3,
  estimated_min_minutes: 18,
  estimated_max_minutes: 26,
  warnings: [],
};

function panelSummary(): LaunchPanelProps["summary"] {
  return {
    sourceTitle: "5. Functions",
    sourceContext: "Fluent Python",
    level: "Mid-level",
    duration: "30 min max",
    feedback: "Realistic",
    format: "Detect from the source",
    coding: "Not included",
  };
}

function panel(overrides: Partial<LaunchPanelProps> = {}) {
  const props: LaunchPanelProps = {
    summary: panelSummary(),
    preflight: null,
    checking: false,
    checkError: "",
    microphoneReady: false,
    codingUnavailable: false,
    operation: "idle",
    error: "",
    onRetryCheck: vi.fn(),
    onStart: vi.fn(),
    onDropCodingExercise: vi.fn(),
    ...overrides,
  };
  const { rerender } = render(<LaunchPanel {...props} />);
  return {
    props,
    update: (next: Partial<LaunchPanelProps>) =>
      rerender(<LaunchPanel {...props} {...next} />),
  };
}

describe("the interview launch panel", () => {
  it("offers one action, and says underneath it what is still missing", () => {
    panel({
      summary: { ...panelSummary(), sourceTitle: null, sourceContext: null },
    });

    // One button, one meaning. It used to be a single control that renamed
    // itself between "Review setup" and "Start interview" with nothing saying
    // a check had to happen first.
    const actions = screen
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(actions).toEqual(["Start interview"]);
    expect(screen.getByRole("button", { name: /start interview/i })).toBeDisabled();
    expect(screen.getByText(/choose a book chapter or a lecture in step 1/i)).toBeVisible();
  });

  it("reports the inspection it runs on its own", () => {
    const { update } = panel({ checking: true });

    expect(screen.getByText(/checking the evidence in this source/i)).toBeVisible();

    update({ checking: false, preflight: PREFLIGHT });
    expect(screen.getByText(/9 topics/)).toBeVisible();
    expect(screen.getByText(/about 18–26 min/)).toBeVisible();
    expect(screen.queryByText(/checking the evidence/i)).toBeNull();
  });

  it("lets the reader ask again when the inspection fails", async () => {
    const user = userEvent.setup();
    const { props } = panel({ checkError: "network is down" });

    expect(screen.getByText(/could not inspect this source/i)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(props.onRetryCheck).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /start interview/i })).toBeDisabled();
  });

  it("holds the start until the microphone is granted", async () => {
    const user = userEvent.setup();
    const { props, update } = panel({ preflight: PREFLIGHT });

    expect(screen.getByRole("button", { name: /start interview/i })).toBeDisabled();
    expect(screen.getByText(/turn on your microphone above/i)).toBeVisible();

    update({ microphoneReady: true });
    await user.click(screen.getByRole("button", { name: /start interview/i }));
    expect(props.onStart).toHaveBeenCalledOnce();
  });

  it("offers listen-only ideal flow without requiring microphone access", async () => {
    const user = userEvent.setup();
    const onListen = vi.fn();
    panel({ preflight: PREFLIGHT, onListen, listenAvailable: true });

    expect(screen.getByRole("button", { name: /start interview/i })).toBeDisabled();
    const listen = screen.getByRole("button", { name: /listen to ideal interview/i });
    expect(listen).toBeEnabled();
    await user.click(listen);
    expect(onListen).toHaveBeenCalledOnce();
  });

  it("limits ideal flows to complete book chapters", () => {
    panel({ preflight: PREFLIGHT, onListen: vi.fn(), listenAvailable: false });

    expect(screen.getByRole("button", { name: /listen to ideal interview/i })).toBeDisabled();
    expect(screen.getByText(/currently require a book chapter/i)).toBeVisible();
  });

  it("carries the microphone controls in the section that requires them", () => {
    panel({
      microphoneControl: <button type="button">Enable microphone</button>,
    });

    // Not a pointer to somewhere else on the page: the control the requirement
    // describes sits inside the requirement.
    const section = screen.getByText("Microphone").closest("div");
    expect(section).not.toBeNull();
    expect(
      within(section?.parentElement as HTMLElement).getByRole("button", {
        name: "Enable microphone",
      }),
    ).toBeVisible();
  });

  it("offers the fix when the source cannot ground the requested coding exercise", async () => {
    const user = userEvent.setup();
    const { props } = panel({
      preflight: { ...PREFLIGHT, coding_topic_count: 0 },
      microphoneReady: true,
      codingUnavailable: true,
    });

    expect(
      screen.getByText(/no executable material for a grounded coding exercise/i),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /start interview/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /continue without it/i }));
    expect(props.onDropCodingExercise).toHaveBeenCalledOnce();
  });
});

describe("the setup choice groups", () => {
  it("describes the chosen option once, as the group's description", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    // Three bordered cards of prose became three chips plus one line about the
    // level actually selected — and that line is bound to the group rather than
    // repeated per option.
    render(
      <ChipChoices
        name="target-level"
        legend="Target level"
        showLegend
        hint="Depth, applications, and trade-offs."
        value="mid"
        choices={[
          { value: "entry", label: "Entry" },
          { value: "mid", label: "Mid-level" },
          { value: "senior", label: "Senior" },
        ]}
        onChange={onChange}
      />,
    );

    const group = screen.getByRole("group", { name: "Target level" });
    expect(group).toHaveAccessibleDescription("Depth, applications, and trade-offs.");
    expect(screen.getByRole("radio", { name: "Mid-level" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: "Senior" }));
    expect(onChange).toHaveBeenCalledWith("senior");
  });

  it("is one radio group rather than a row of independent toggles", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <OptionCards
        name="target-level"
        legend="Target level"
        showLegend
        value="mid"
        choices={[
          { value: "entry", label: "Entry" },
          { value: "mid", label: "Mid-level" },
          { value: "senior", label: "Senior" },
        ]}
        onChange={onChange}
      />,
    );

    const group = screen.getByRole("group", { name: "Target level" });
    expect(group).toBeVisible();
    expect(screen.getByRole("radio", { name: "Mid-level" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: "Senior" }));
    expect(onChange).toHaveBeenCalledWith("senior");
  });
});
