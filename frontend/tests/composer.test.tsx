import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/conversation/composer";

function renderComposer(overrides: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const props = {
    disabled: false,
    isStreaming: false,
    placeholder: "Ask about the book…",
    onSubmit: vi.fn(),
    onStop: vi.fn(),
    responseDepth: "interview" as const,
    onResponseDepthChange: vi.fn(),
    ...overrides,
  };
  render(<Composer {...props} />);
  return props;
}

describe("Composer", () => {
  it("submits on Enter and clears the field", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderComposer();

    const textarea = screen.getByLabelText("Ask about the book");
    await user.type(textarea, "What is a feature store?{Enter}");

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith(
      "What is a feature store?",
    );
    expect(textarea).toHaveValue("");
  });

  it("inserts a newline on Shift+Enter instead of submitting", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderComposer();

    const textarea = screen.getByLabelText("Ask about the book");
    await user.type(textarea, "first{Shift>}{Enter}{/Shift}second");

    expect(onSubmit).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("first\nsecond");
  });

  it("does not submit whitespace", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderComposer();

    await user.type(screen.getByLabelText("Ask about the book"), "   {Enter}");

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("trims the question before submitting", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderComposer();

    await user.type(
      screen.getByLabelText("Ask about the book"),
      "  padded question  {Enter}",
    );

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith("padded question");
  });

  it("lets the reader choose a deep-dive response", async () => {
    const user = userEvent.setup();
    const { onResponseDepthChange } = renderComposer();

    await user.click(screen.getByLabelText("Response depth"));
    await user.click(screen.getByRole("option", { name: "Deep dive" }));

    expect(onResponseDepthChange).toHaveBeenCalledExactlyOnceWith("deep");
  });

  it("offers stop instead of send while streaming, and does not submit", async () => {
    const user = userEvent.setup();
    const { onStop, onSubmit } = renderComposer({ isStreaming: true });

    expect(screen.queryByLabelText("Send question")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Stop generating"));

    expect(onStop).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables input when there is nothing to ask about", () => {
    renderComposer({ disabled: true });

    expect(screen.getByLabelText("Ask about the book")).toBeDisabled();
    expect(screen.getByLabelText("Send question")).toBeDisabled();
  });
});
