import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnchorEditor } from "@/components/side-chat/anchor-editor";
import { MAXIMUM_ANCHORS } from "@/hooks/use-side-chats";
import type { QuoteAnchor } from "@/lib/types";

function anchor(id: string, text = `passage ${id}`): QuoteAnchor {
  return { anchor_id: id, parent_turn_index: 0, quoted_text: text };
}

function renderEditor({
  anchors = [anchor("a1", "the gap compounds")],
  resolveTurn = vi.fn(() => 2),
}: {
  anchors?: QuoteAnchor[];
  resolveTurn?: (text: string) => number | null;
} = {}) {
  const onChange = vi.fn();
  render(
    <AnchorEditor
      anchors={anchors}
      onChange={onChange}
      resolveTurn={resolveTurn}
    />,
  );
  return { onChange, resolveTurn };
}

const addButton = () =>
  screen.getByRole("button", { name: /Add a passage/ });

function openAndType(text: string) {
  fireEvent.click(addButton());
  fireEvent.change(screen.getByLabelText("Passage to reference"), {
    target: { value: text },
  });
}

describe("AnchorEditor", () => {
  it("shows every passage the side chat is anchored to", () => {
    renderEditor({ anchors: [anchor("a1", "first"), anchor("a2", "second")] });

    expect(screen.getAllByRole("blockquote")).toHaveLength(2);
  });

  it("removes the chip the reader dismissed, keeping the others", () => {
    const { onChange } = renderEditor({
      anchors: [anchor("a1", "first"), anchor("a2", "second")],
    });

    fireEvent.click(
      screen.getByRole("button", { name: /Remove the reference beginning "first"/ }),
    );

    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ anchor_id: "a2" }),
    ]);
  });

  it("says the side chat still searches the books when nothing is attached", () => {
    renderEditor({ anchors: [] });

    expect(
      screen.getByText(/still searches the same books/),
    ).toBeInTheDocument();
  });

  it("attaches a pasted passage to the turn it came from", () => {
    const resolveTurn = vi.fn(() => 4);
    const { onChange } = renderEditor({ resolveTurn });

    openAndType("features differ between training and serving");
    fireEvent.click(screen.getByRole("button", { name: "Add reference" }));

    expect(resolveTurn).toHaveBeenCalledWith(
      "features differ between training and serving",
    );
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ anchor_id: "a1" }),
      expect.objectContaining({
        parent_turn_index: 4,
        quoted_text: "features differ between training and serving",
      }),
    ]);
  });

  it("refuses a passage that is not from this conversation", () => {
    const { onChange } = renderEditor({ resolveTurn: vi.fn(() => null) });

    openAndType("something copied from a web page");
    fireEvent.click(screen.getByRole("button", { name: "Add reference" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /not from this conversation/,
    );
  });

  it("trims the pasted passage", () => {
    const { onChange } = renderEditor();

    openAndType("   padded passage   ");
    fireEvent.click(screen.getByRole("button", { name: "Add reference" }));

    expect(onChange).toHaveBeenCalledWith([
      expect.anything(),
      expect.objectContaining({ quoted_text: "padded passage" }),
    ]);
  });

  it("stops offering to add once the limit is reached", () => {
    renderEditor({
      anchors: Array.from({ length: MAXIMUM_ANCHORS }, (_, index) =>
        anchor(`a${index}`),
      ),
    });

    expect(addButton()).toBeDisabled();
  });
});
