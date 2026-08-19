import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { MomentComposer, type Stretch } from "@/components/watch/moment-composer";

function Harness({
  onSubmit,
  atMs = 724_000,
}: {
  onSubmit: (question: string) => void;
  atMs?: number;
}) {
  const [inContext, setInContext] = useState(true);
  const [stretch, setStretch] = useState<Stretch | null>(null);
  return (
    <MomentComposer
      atMs={atMs}
      stretch={stretch}
      onMarkStretch={() => setStretch({ startMs: 700_000, endMs: 750_000 })}
      onClearStretch={() => setStretch(null)}
      momentInContext={inContext}
      onMomentInContextChange={setInContext}
      onSubmit={onSubmit}
    />
  );
}

describe("the composer under the player", () => {
  it("says where the lecture is without the viewer having to", () => {
    render(<Harness onSubmit={vi.fn()} />);

    expect(screen.getByText("12:04")).toBeInTheDocument();
    expect(
      screen.getByText("This moment and what is on screen are in context"),
    ).toBeInTheDocument();
  });

  it("carries a marked stretch instead of the playhead", async () => {
    // A viewer who marked a span meant that span, not wherever the lecture
    // has since reached.
    render(<Harness onSubmit={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Mark a stretch from here" }),
    );

    expect(screen.getByText("11:40 – 12:30")).toBeInTheDocument();
    expect(screen.getByText("This stretch is in context")).toBeInTheDocument();
  });

  it("asks the question the viewer typed", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await userEvent.type(
      screen.getByLabelText("Ask about this moment"),
      "Why divide by the square root?{Enter}",
    );

    expect(onSubmit).toHaveBeenCalledWith("Why divide by the square root?");
  });

  it("lets the viewer take the lecture's position out", async () => {
    render(<Harness onSubmit={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", {
        name: "Stop including 12:04 with this question",
      }),
    );

    expect(
      screen.getByText("Answering without the lecture's position"),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Ask anything about this lecture…"),
    ).toBeInTheDocument();
  });

  it("shows an hour field only once the lecture has one", () => {
    render(<Harness onSubmit={vi.fn()} atMs={4_360_000} />);

    expect(screen.getByText("1:12:40")).toBeInTheDocument();
  });
});
