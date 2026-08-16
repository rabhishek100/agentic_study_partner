import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The states the design system specifies but nothing previously asserted.
 *
 * These are cheap to write and were the difference between a rule that is
 * written down and a rule that holds: every one of them regressed silently at
 * least once while the primitives were still carrying shadcn's defaults.
 */
describe("design system states", () => {
  describe("skeleton", () => {
    it("does not animate", () => {
      const { container } = render(<Skeleton className="h-4 w-32" />);
      const skeleton = container.querySelector('[data-slot="skeleton"]');

      // The motion rules allow exactly one looping animation in the product —
      // the spinner, for work of unknown duration. A pulsing block behind a
      // reading surface is the ambient motion the brief rules out.
      expect(skeleton?.className).not.toContain("animate-");
    });

    it("is hidden from assistive technology unless it announces itself busy", () => {
      const { container, rerender } = render(<Skeleton />);
      expect(container.querySelector('[data-slot="skeleton"]')).toHaveAttribute(
        "aria-hidden",
        "true",
      );

      rerender(<Skeleton busy />);
      const busy = container.querySelector('[data-slot="skeleton"]');
      expect(busy).toHaveAttribute("aria-busy", "true");
      expect(busy).not.toHaveAttribute("aria-hidden");
    });
  });

  describe("progress", () => {
    it("reports a percentage only when it has one", () => {
      const { rerender } = render(<Progress value={40} aria-label="Generating" />);
      expect(screen.getByRole("progressbar")).toHaveAttribute(
        "aria-valuenow",
        "40",
      );

      // Work has started but its extent is unknown. Inventing a number here
      // would be a more confident claim than the system can support.
      rerender(<Progress aria-label="Generating" />);
      const indeterminate = screen.getByRole("progressbar");
      expect(indeterminate).not.toHaveAttribute("aria-valuenow");
      expect(indeterminate).toHaveAttribute("data-state", "indeterminate");
    });

    it("draws no fill while indeterminate", () => {
      const { container } = render(<Progress aria-label="Generating" />);
      expect(
        container.querySelector('[data-slot="progress-indicator"]'),
      ).toBeNull();
    });
  });

  describe("focus and disabled", () => {
    it("leaves the one global focus treatment in place", () => {
      render(<Button>Ask</Button>);
      const button = screen.getByRole("button", { name: "Ask" });

      // Each primitive used to suppress the global rule and draw its own ring —
      // five different treatments, two of them at 20% and 50% opacity, which
      // cannot reach the 3:1 a focus indicator requires.
      expect(button.className).not.toContain("outline-none");
      expect(button.className).not.toContain("outline-hidden");
      expect(button.className).not.toMatch(/focus-visible:ring/);
    });

    it("expresses disabled with tokens rather than transparency", () => {
      render(<Button disabled>Ask</Button>);
      const button = screen.getByRole("button", { name: "Ask" });

      expect(button.className).not.toContain("opacity-50");
      expect(button.className).toContain("disabled:bg-disabled-surface");
      expect(button.className).toContain("disabled:text-disabled-foreground");
    });

    it("marks an invalid field with a border rather than a translucent ring", () => {
      render(<Input aria-invalid aria-label="Question" />);
      const input = screen.getByLabelText("Question");

      expect(input.className).toContain("aria-invalid:border-destructive");
      expect(input.className).not.toMatch(/ring-destructive\/\d/);
    });
  });
});
