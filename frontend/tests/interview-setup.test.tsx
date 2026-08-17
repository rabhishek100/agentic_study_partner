import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  LaunchPanel,
  type LaunchPanelProps,
} from "@/components/interviews/launch-panel";
import { OptionCards } from "@/components/interviews/setup-controls";
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
    stale: false,
    microphoneReady: false,
    codingUnavailable: false,
    operation: "idle",
    error: "",
    onCheck: vi.fn(),
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
  it("names every precondition before the reader presses anything", () => {
    panel({
      summary: { ...panelSummary(), sourceTitle: null, sourceContext: null },
    });

    expect(screen.getByText("Source selected")).toBeVisible();
    expect(screen.getByText("Source checked")).toBeVisible();
    // The old screen only revealed that the microphone was mandatory after the
    // preflight had already run, as a disabled button in a different column.
    expect(screen.getByText("Microphone ready")).toBeVisible();
    expect(screen.getByText(/Required — the interview is spoken/)).toBeVisible();
    expect(screen.getByRole("button", { name: /check this source/i })).toBeDisabled();
  });

  it("checks the source before it will start one", async () => {
    const user = userEvent.setup();
    const { props } = panel();

    await user.click(screen.getByRole("button", { name: /check this source/i }));

    expect(props.onCheck).toHaveBeenCalledOnce();
    expect(props.onStart).not.toHaveBeenCalled();
  });

  it("reports a setup that changed after the check instead of silently reverting", () => {
    panel({ preflight: PREFLIGHT, stale: true, microphoneReady: true });

    expect(screen.getByText(/your setup changed/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /re-check the source/i })).toBeEnabled();
    // The stale estimate is withdrawn rather than left standing as fact.
    expect(screen.queryByText(/18–26 min/)).toBeNull();
  });

  it("holds the start until the microphone is granted, and says so on the button", async () => {
    const user = userEvent.setup();
    const { props, update } = panel({ preflight: PREFLIGHT });

    expect(
      screen.getByRole("button", { name: /enable your microphone to start/i }),
    ).toBeDisabled();
    // The estimate is stated once, on the checklist line it belongs to.
    expect(screen.getByText(/9 topics · about 18–26 min/)).toBeVisible();

    update({ microphoneReady: true });
    await user.click(screen.getByRole("button", { name: /start interview/i }));
    expect(props.onStart).toHaveBeenCalledOnce();
  });

  it("carries the microphone controls on the line that requires them", () => {
    panel({
      microphoneControl: <button type="button">Enable microphone</button>,
    });

    // Not a pointer to somewhere else on the page: the control the requirement
    // describes sits inside the requirement.
    const requirement = screen.getByText("Microphone ready").closest("li");
    expect(requirement).not.toBeNull();
    expect(
      within(requirement as HTMLElement).getByRole("button", {
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

    await user.click(
      screen.getByRole("button", { name: /continue without the coding exercise/i }),
    );
    expect(props.onDropCodingExercise).toHaveBeenCalledOnce();
  });
});

describe("the setup choice groups", () => {
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
