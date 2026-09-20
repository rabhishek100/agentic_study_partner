import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Figures,
  InlineFigure,
  figureLabel,
  figureSource,
} from "@/components/conversation/figures";
import type { FigureRef } from "@/lib/types";

vi.mock("@/lib/supabase", () => ({
  accessToken: async () => "test-token",
}));

function figure(overrides: Partial<FigureRef> = {}): FigureRef {
  return {
    book_id: 530,
    node_id: 77,
    block_id: 1234,
    page: 78,
    mime_type: "image/png",
    path: "3 Linear Regression :: 3.1 Simple Linear Regression",
    caption: null,
    evidence_rank: 1,
    ...overrides,
  };
}

describe("figureSource / figureLabel", () => {
  it("uses the caption as alt text when one exists", () => {
    // A caption written at ingest says what the figure shows; that is real
    // alt text rather than a stand-in.
    expect(
      figureLabel(figure({ caption: "Residual plot showing non-linearity." })),
    ).toBe("Residual plot showing non-linearity.");
  });

  it("addresses the owner-scoped image endpoint", () => {
    expect(figureSource(figure())).toBe(
      "/api/books/530/blocks/1234/image",
    );
  });

  it("describes where the figure came from, not what it shows", () => {
    // Nothing in the pipeline knows what the image depicts; inventing a
    // description would be worse than stating its location.
    expect(figureLabel(figure())).toBe(
      "Figure from 3.1 Simple Linear Regression, page 78",
    );
  });
});

describe("Figures", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:figure"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends the bearer token that an <img> tag cannot carry", async () => {
    // The whole regression: a plain `<img src>` reaches this endpoint with no
    // Authorization header and is refused.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob([new Uint8Array([1])], { type: "image/png" }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Figures figures={[figure()]} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/books/530/blocks/1234/image");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer test-token",
    );
  });

  it("renders the fetched image once it arrives", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        blob: async () => new Blob([new Uint8Array([1])], { type: "image/png" }),
      })),
    );

    render(<Figures figures={[figure()]} />);

    const image = await screen.findByAltText(
      "Figure from 3.1 Simple Linear Regression, page 78",
    );
    expect(image).toHaveAttribute("src", "blob:figure");
    expect(image.closest("li")).toHaveAttribute(
      "data-narration-figure",
      "1234",
    );
  });

  it("explains a refused figure instead of showing a broken image", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401 })));

    render(<Figures figures={[figure()]} />);

    expect(
      await screen.findByText(/Could not load the figure from page 78/),
    ).toBeInTheDocument();
  });

  it("renders nothing when there are no figures", () => {
    const { container } = render(<Figures figures={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("labels the trailing gallery as supplementary", async () => {
    // Captioned figures are placed inline beside the passage that cites them;
    // this section is only what was on a cited page but never referred to.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        blob: async () => new Blob([new Uint8Array([1])], { type: "image/png" }),
      })),
    );

    const { rerender } = render(<Figures figures={[figure()]} />);
    expect(screen.getByText("Also on a cited page")).toBeInTheDocument();

    rerender(
      <Figures figures={[figure(), figure({ block_id: 9, page: 80 })]} />,
    );
    expect(screen.getByText("Also on cited pages")).toBeInTheDocument();
  });

  it("renders a supplied gallery in page and source-block order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        blob: async () => new Blob([new Uint8Array([1])], { type: "image/png" }),
      })),
    );

    render(
      <Figures
        figures={[
          figure({ block_id: 30, page: 82, caption: "Third" }),
          figure({ block_id: 20, page: 81, caption: "Second" }),
          figure({ block_id: 10, page: 81, caption: "First" }),
        ]}
      />,
    );

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(3));
    expect(screen.getAllByRole("img").map((image) => image.getAttribute("alt"))).toEqual([
      "First",
      "Second",
      "Third",
    ]);
  });
});

describe("figure captions", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:figure"),
      revokeObjectURL: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        blob: async () => new Blob([new Uint8Array([1])], { type: "image/png" }),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders LaTeX in a caption as maths, not as source", async () => {
    // 81 of the corpus captions name coefficients; `$\beta_0$` in a caption
    // is worse than useless.
    const { container } = render(
      <InlineFigure
        figure={figure({
          caption: "Contours of RSS against $\\beta_0$ and $\\beta_1$.",
        })}
      />,
    );

    await waitFor(() =>
      expect(container.querySelector(".katex")).not.toBeNull(),
    );
    const rendered = container.querySelector(".katex-html")?.textContent ?? "";
    expect(rendered).not.toContain("\\beta");
  });

  it("shows a plain caption unchanged", async () => {
    render(
      <InlineFigure
        figure={figure({ caption: "A scatter plot of sales against TV spend." })}
      />,
    );
    expect(
      await screen.findByText(/A scatter plot of sales against TV spend/),
    ).toBeInTheDocument();
  });

  it("can show the source caption while retaining the derived alt text", async () => {
    render(
      <InlineFigure
        figure={figure({ caption: "A generated accessibility description." })}
        visibleCaption="Figure 2.1: The source caption."
      />,
    );

    expect(await screen.findByText("Figure 2.1: The source caption.")).toBeInTheDocument();
    expect(screen.queryByText("A generated accessibility description.")).not.toBeInTheDocument();
    expect(screen.getByAltText("A generated accessibility description.")).toBeInTheDocument();
  });
});
