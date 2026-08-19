import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  OutOfSourceNotice,
  RungBadge,
  WideningTrail,
} from "@/components/conversation/grounding";
import { isGrounded } from "@/lib/types";

describe("isGrounded", () => {
  it("draws the line between the library and everything above it", () => {
    // The one question the interface has to get right: a grounded answer
    // carries citations, an ungrounded one carries a notice, and nothing may
    // render as both.
    expect(isGrounded({ grounding_rung: "anchor" })).toBe(true);
    expect(isGrounded({ grounding_rung: "open_source" })).toBe(true);
    expect(isGrounded({ grounding_rung: "library" })).toBe(true);
    expect(isGrounded({ grounding_rung: "model_knowledge" })).toBe(false);
    expect(isGrounded({ grounding_rung: "web_search" })).toBe(false);
  });

  it("falls back to the source type for a turn recorded before the ladder", () => {
    expect(isGrounded({})).toBe(true);
    expect(isGrounded({ source_type: "model_knowledge" })).toBe(false);
  });
});

describe("RungBadge", () => {
  it("distinguishes the passage from the book it sits in", () => {
    // "Here" and "somewhere in this book" are different claims about the same
    // source.
    const { rerender } = render(<RungBadge rung="anchor" />);
    expect(screen.getByText("This passage")).toBeInTheDocument();

    rerender(<RungBadge rung="open_source" />);
    expect(screen.getByText("This source")).toBeInTheDocument();
  });

  it("says plainly when an answer left the reader's sources", () => {
    render(<RungBadge rung="model_knowledge" />);

    expect(screen.getByText("Outside your sources")).toBeInTheDocument();
  });

  it("shows nothing for a turn that ran under no policy", () => {
    const { container } = render(<RungBadge rung={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("OutOfSourceNotice", () => {
  it("stays out of the way of a grounded answer", () => {
    const { container } = render(<OutOfSourceNotice rung="library" />);

    expect(container).toBeEmptyDOMElement();
  });

  it("says what an ungrounded answer rests on, and why the search stopped", () => {
    render(
      <OutOfSourceNotice
        rung="model_knowledge"
        widenings={[
          {
            from_rung: "open_source",
            to_rung: "library",
            reason: "Insufficient evidence: not covered.",
          },
          {
            from_rung: "library",
            to_rung: "model_knowledge",
            reason: "Insufficient evidence: nothing on sklearn.",
          },
        ]}
      />,
    );

    expect(
      screen.getByText(/Outside your sources — general model knowledge/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Nothing below is cited/)).toBeInTheDocument();
    // The verdict that provoked the last widening, not the first.
    expect(screen.getByText(/nothing on sklearn/)).toBeInTheDocument();
  });

  it("offers the lock, and stops offering it once it is on", async () => {
    const onStayInSource = vi.fn();
    const { rerender } = render(
      <OutOfSourceNotice
        rung="model_knowledge"
        onStayInSource={onStayInSource}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /Stay in this source from now on/ }),
    );
    expect(onStayInSource).toHaveBeenCalled();

    rerender(
      <OutOfSourceNotice
        rung="model_knowledge"
        onStayInSource={onStayInSource}
        locked
      />,
    );
    expect(
      screen.queryByRole("button", { name: /Stay in this source from now on/ }),
    ).not.toBeInTheDocument();
  });

  it("names a web answer as a web answer", () => {
    render(<OutOfSourceNotice rung="web_search" />);

    expect(
      screen.getByText(/Outside your sources — from a web search/),
    ).toBeInTheDocument();
  });
});

describe("WideningTrail", () => {
  it("reads as a sequence of decisions with their causes", () => {
    render(
      <WideningTrail
        widenings={[
          {
            from_rung: "open_source",
            to_rung: "library",
            reason: "Insufficient evidence: not covered.",
          },
        ]}
      />,
    );

    expect(screen.getByText("open_source → library")).toBeInTheDocument();
    expect(screen.getByText(/not covered/)).toBeInTheDocument();
  });

  it("shows nothing for a turn that never widened", () => {
    const { container } = render(<WideningTrail widenings={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
