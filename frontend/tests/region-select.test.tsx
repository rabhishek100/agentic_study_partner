import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RegionSelect, textWithin } from "@/components/read/region-select";
import { useRef } from "react";

/** A text layer laid out the way pdf.js lays one out: absolute boxes. */
function layer(boxes: { text: string; left: number; top: number }[]) {
  const container = document.createElement("div");
  container.className = "react-pdf__Page__textContent";
  container.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 600, height: 800 }) as DOMRect;
  for (const box of boxes) {
    const span = document.createElement("span");
    span.textContent = box.text;
    span.getBoundingClientRect = () =>
      ({
        left: box.left,
        top: box.top,
        width: 40,
        height: 10,
        right: box.left + 40,
        bottom: box.top + 10,
      }) as DOMRect;
    container.append(span);
  }
  return container;
}

describe("textWithin", () => {
  it("takes the words the rectangle covers, in reading order", () => {
    const container = layer([
      { text: "first", left: 10, top: 10 },
      { text: "second", left: 10, top: 30 },
      { text: "far away", left: 400, top: 400 },
    ]);

    expect(textWithin(container, { left: 0, top: 0, width: 200, height: 100 })).toBe(
      "first second",
    );
  });

  it("judges a span by its centre, not by grazing it", () => {
    // Catching every span a rectangle touches at the edge is how a tight
    // selection ends up carrying half the paragraph beside it.
    const container = layer([{ text: "edge", left: 100, top: 10 }]);

    // The rectangle overlaps the span's first pixel but not its centre.
    expect(
      textWithin(container, { left: 0, top: 0, width: 105, height: 100 }),
    ).toBe("");
    expect(
      textWithin(container, { left: 0, top: 0, width: 130, height: 100 }),
    ).toBe("edge");
  });

  it("finds nothing where a figure is drawn rather than written", () => {
    const container = layer([]);

    expect(textWithin(container, { left: 0, top: 0, width: 300, height: 300 })).toBe(
      "",
    );
  });
});

function Harness({
  active,
  onRegion,
  onEmpty,
}: {
  active: boolean;
  onRegion: (selection: { text: string; x: number; y: number }) => void;
  onEmpty?: () => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  return (
    <div ref={container} data-testid="doc">
      <RegionSelect container={container} active={active} onRegion={onRegion} onEmpty={onEmpty} />
    </div>
  );
}

describe("RegionSelect", () => {
  it("stays out of the way until it is armed", () => {
    const { container } = render(
      <Harness active={false} onRegion={vi.fn()} />,
    );

    // Nothing over the page means ordinary reading — scrolling, selecting
    // text, following a citation — is untouched.
    expect(container.querySelector(".cursor-crosshair")).toBeNull();
  });

  it("covers the page once armed", () => {
    const { container } = render(<Harness active onRegion={vi.fn()} />);

    expect(container.querySelector(".cursor-crosshair")).not.toBeNull();
  });
});
