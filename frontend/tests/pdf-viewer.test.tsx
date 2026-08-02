import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PdfTarget } from "@/components/pdf/target";

const pdfHarness = vi.hoisted(() => ({
  onRenderTextLayerSuccess: null as null | (() => void),
}));
const apiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch }));
vi.mock("react-pdf", async () => {
  const React = await import("react");
  return {
    pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
    Document: ({
      children,
      onLoadSuccess,
    }: {
      children: React.ReactNode;
      onLoadSuccess: (value: { numPages: number }) => void;
    }) => {
      const loaded = React.useRef(false);
      React.useEffect(() => {
        if (loaded.current) return;
        loaded.current = true;
        onLoadSuccess({ numPages: 12 });
      }, [onLoadSuccess]);
      return React.createElement("div", null, children);
    },
    Page: ({
      onRenderTextLayerSuccess,
      pageNumber,
      width,
    }: {
      onRenderTextLayerSuccess: () => void;
      pageNumber: number;
      width: number;
    }) => {
      pdfHarness.onRenderTextLayerSuccess = onRenderTextLayerSuccess;
      return React.createElement(
        "div",
        {
          "data-testid": "pdf-page",
          "data-page": String(pageNumber),
          "data-width": String(width),
        },
        React.createElement(
          "div",
          { className: "react-pdf__Page__textContent" },
          React.createElement("span", null, "before "),
          React.createElement("span", null, "target passage"),
          React.createElement("span", null, " after"),
        ),
      );
    },
  };
});

import { PdfViewer } from "@/components/pdf/pdf-viewer";

const defaultResizeObserver = globalThis.ResizeObserver;
const target: PdfTarget = {
  bookId: 7,
  bookTitle: "A technical book",
  page: 4,
  excerpt: "target passage",
};
const noop = () => {};
const viewerProps = {
  page: 4,
  onPageChange: noop,
  zoom: 1,
  onZoomChange: noop,
  onMinimize: noop,
  onClose: noop,
};

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({ url: "https://example.test/book.pdf" });
  pdfHarness.onRenderTextLayerSuccess = null;
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      const passage = this.tagName === "SPAN" && this.textContent === "target passage";
      return {
        x: 0,
        y: passage ? 280 : 0,
        top: passage ? 280 : 0,
        left: 0,
        right: 600,
        bottom: passage ? 300 : 400,
        width: 600,
        height: passage ? 20 : 400,
        toJSON: () => ({}),
      };
    },
  );
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  globalThis.ResizeObserver = defaultResizeObserver;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("PdfViewer", () => {
  it("centres a cited passage inside only the PDF scroller", async () => {
    render(<PdfViewer target={target} {...viewerProps} />);
    await screen.findByTestId("pdf-page");

    act(() => pdfHarness.onRenderTextLayerSuccess?.());

    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({
      top: 90,
      left: 0,
      behavior: "auto",
    });
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("clears a stale no-match warning as soon as a new citation is selected", async () => {
    const { rerender } = render(
      <PdfViewer
        {...viewerProps}
        target={{ ...target, excerpt: "not present" }}
      />,
    );
    await screen.findByTestId("pdf-page");
    act(() => pdfHarness.onRenderTextLayerSuccess?.());
    expect(screen.getByText(/exact passage could not be located/i)).toBeVisible();

    rerender(
      <PdfViewer
        {...viewerProps}
        target={{ ...target, page: 5, excerpt: "target passage" }}
      />,
    );

    expect(
      screen.queryByText(/exact passage could not be located/i),
    ).not.toBeInTheDocument();
  });

  it("reuses the signed source while moving between citations in one book", async () => {
    const { rerender } = render(<PdfViewer target={target} {...viewerProps} />);
    await screen.findByTestId("pdf-page");
    expect(apiFetch).toHaveBeenCalledTimes(1);

    rerender(
      <PdfViewer {...viewerProps} target={{ ...target, page: 8 }} />,
    );
    expect(apiFetch).toHaveBeenCalledTimes(1);

    rerender(
      <PdfViewer
        {...viewerProps}
        target={{ ...target, bookId: 9, bookTitle: "Another book" }}
      />,
    );
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
  });

  it("debounces rapid pane resizes before asking pdf.js to redraw", async () => {
    let resize: ResizeObserverCallback | null = null;
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    };

    render(<PdfViewer target={target} {...viewerProps} />);
    const page = await screen.findByTestId("pdf-page");
    expect(page).toHaveAttribute("data-width", "568");

    vi.useFakeTimers();
    const notify = (width: number) =>
      resize?.(
        [{ contentRect: { width } } as ResizeObserverEntry],
        {} as ResizeObserver,
      );
    act(() => {
      notify(500);
      notify(520);
      notify(540);
      vi.advanceTimersByTime(99);
    });
    expect(page).toHaveAttribute("data-width", "568");

    act(() => vi.advanceTimersByTime(1));
    expect(page).toHaveAttribute("data-width", "508");
  });

  it("zooms the rendered page and resets from the percentage control", async () => {
    const onZoomChange = vi.fn();
    const { rerender } = render(
      <PdfViewer
        target={target}
        {...viewerProps}
        onZoomChange={onZoomChange}
      />,
    );
    const page = await screen.findByTestId("pdf-page");

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(onZoomChange).toHaveBeenCalledWith(1.25);

    rerender(
      <PdfViewer
        target={target}
        {...viewerProps}
        zoom={1.25}
        onZoomChange={onZoomChange}
      />,
    );
    expect(page).toHaveAttribute("data-width", "710");
    expect(screen.getByText("125%")).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Reset zoom to 100%. Current zoom 125%",
      }),
    );
    expect(onZoomChange).toHaveBeenLastCalledWith(1);
  });

  it("offers a distinct minimize action without closing the document", async () => {
    const onMinimize = vi.fn();
    const onClose = vi.fn();
    render(
      <PdfViewer
        target={target}
        {...viewerProps}
        onMinimize={onMinimize}
        onClose={onClose}
      />,
    );
    await screen.findByTestId("pdf-page");

    fireEvent.click(
      screen.getByRole("button", { name: "Minimize the document" }),
    );
    expect(onMinimize).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });
});
