import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RevisionSheets } from "@/components/revision/revision-sheets";
import { RevisionPage } from "@/components/revision/revision-page";
import type { BookSummary } from "@/lib/types";
import type { RevisionSheet } from "@/lib/revision-types";
import content from "./fixtures/revision_sheet.json";

const fetchApi = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch: fetchApi, errorDetail: async () => "Download failed" }));
vi.mock("@/lib/supabase", () => ({ accessToken: async () => "token" }));
vi.mock("@/components/pdf", () => ({ PdfViewer: () => <div>Source PDF viewer</div> }));
vi.mock("@/components/conversation/figures", () => ({ InlineFigure: () => <div>Original figure</div> }));

const books = [{ book_id: 1, title: "Design book" }, { book_id: 2, title: "Other book" }] as BookSummary[];
const id = "11111111-1111-4111-8111-111111111111";
const summary = { id, book_id: 1, chapter_node_id: 10, scope_kind: "chapter", scope_key: "book:1:chapter:10", source_title: "Design book", scope_title: "Chapter one", version: 1, created_at: "2026-09-05T10:00:00Z" };
const sheet = { ...summary, content, source_references: { "[N1:P1]": { book_id: 1, node_id: 1, page: 1, path: "Chapter one", section_title: "Chapter one", section_number: "1" } },
  source_changed: false, settings_changed: false, provenance: { model: "test", prompt_version: "test", content_repairs: 0, fit_repairs: 0, inspected_figures: [], uninspected_figures: [] },
  diagram_layout: { width: 520, height: 90, nodes: content.diagram.nodes.map((n, i) => ({ id: n.id, x: i * 130 + 5, y: 15, width: 110, height: 43 })), edges: [] },
} as unknown as RevisionSheet;

beforeEach(() => {
  fetchApi.mockReset();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["pdf"], { type: "application/pdf" }) }));
  URL.createObjectURL = vi.fn(() => "blob:revision"); URL.revokeObjectURL = vi.fn();
  window.history.replaceState(null, "", "/");
});
afterEach(() => { window.history.replaceState(null, "", "/"); vi.unstubAllGlobals(); });

describe("revision sheets", () => {
  it("queues an entire paper without a chapter selector or chapter request", async () => {
    fetchApi.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith("/revision-sheets?")) return { sheets: [], jobs: [] };
      if (options?.method === "POST") return { job: { id: "paper-job", status: "failed", scope_title: "Paper", error_detail: "Test" } };
    });
    render(<RevisionSheets books={books} selectedBookIds={[1]} noun="paper" />);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create sheet" })).toBeEnabled());
    expect(screen.queryByLabelText("Chapter")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create sheet" }));
    await waitFor(() => expect(fetchApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
    const call = fetchApi.mock.calls.find(([, options]) => options?.method === "POST")!;
    expect(JSON.parse(call[1].body)).toEqual({ scope_kind: "paper", book_id: 1 });
    expect(fetchApi.mock.calls.some(([path]) => path.includes("/chapters"))).toBe(false);
  });

  it("reopens a saved version without a generation call and keeps the chat form idle", async () => {
    fetchApi.mockImplementation(async (path: string) => {
      if (path.startsWith("/revision-sheets?")) return { sheets: [summary], jobs: [] };
      if (path.includes("/chapters")) return { chapters: [{ node_id: 10, title: "Chapter one", start_page: 1, end_page: 8 }] };
      return sheet;
    });
    const submit = vi.fn(e => e.preventDefault());
    render(<form onSubmit={submit}><RevisionSheets books={books} selectedBookIds={[1]} noun="book" /></form>);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    fireEvent.click(await screen.findByRole("button", { name: /Chapter one.*Version 1/ }));
    await screen.findByRole("heading", { name: content.title });
    expect(fetchApi.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
    expect(submit).not.toHaveBeenCalled();
    expect(window.location.hash).toBe(`#revision=${id}`);
    fireEvent.click(screen.getByRole("button", { name: "Saved sheets" }));
    await screen.findByRole("heading", { name: "Saved sheets" });
  });

  it("requires one explicit chapter and queues it with an idempotency key", async () => {
    fetchApi.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith("/revision-sheets?")) return { sheets: [], jobs: [] };
      if (path.includes("/chapters")) return { chapters: [{ node_id: 10, title: "Chapter one", start_page: 1, end_page: 8 }] };
      if (options?.method === "POST") return { job: { id: "job1", scope_key: summary.scope_key, status: "failed", scope_title: "Chapter one", error_detail: "Test failure" } };
    });
    render(<RevisionSheets books={books} selectedBookIds={[1, 2]} noun="book" />);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    expect(screen.getByRole("button", { name: "Create sheet" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "1" } });
    await screen.findByRole("option", { name: /Chapter one/ });
    fireEvent.change(screen.getByLabelText("Chapter"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Create sheet" }));
    await waitFor(() => expect(fetchApi).toHaveBeenCalledWith("/revision-sheets", expect.objectContaining({ method: "POST" })));
    const call = fetchApi.mock.calls.find(([, options]) => options?.method === "POST")!;
    expect(JSON.parse(call[1].body)).toEqual({ scope_kind: "chapter", book_id: 1, chapter_node_id: 10 });
    expect(call[1].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("offers no retry when the provider refused on spending", async () => {
    // Retrying cannot move an exhausted monthly limit, and offering the button
    // is what sent the reader round the loop twice.
    fetchApi.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith("/revision-sheets?")) return { sheets: [], jobs: [] };
      if (path.includes("/chapters")) return { chapters: [{ node_id: 10, title: "Chapter one", start_page: 1, end_page: 8 }] };
      if (options?.method === "POST") return { job: { id: "job1", scope_key: summary.scope_key, status: "failed", scope_title: "Chapter one", error_code: "provider_quota_exhausted", error_detail: "Key limit exceeded (monthly limit). Raise the limit or add credit." } };
    });
    render(<RevisionSheets books={books} selectedBookIds={[1, 2]} noun="book" />);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "1" } });
    await screen.findByRole("option", { name: /Chapter one/ });
    fireEvent.change(screen.getByLabelText("Chapter"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Create sheet" }));

    await screen.findByText(/Key limit exceeded/);
    expect(screen.queryByRole("button", { name: "Retry generation" })).not.toBeInTheDocument();
  });

  it("still offers a retry for an ordinary failure", async () => {
    fetchApi.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith("/revision-sheets?")) return { sheets: [], jobs: [] };
      if (path.includes("/chapters")) return { chapters: [{ node_id: 10, title: "Chapter one", start_page: 1, end_page: 8 }] };
      if (options?.method === "POST") return { job: { id: "job1", scope_key: summary.scope_key, status: "failed", scope_title: "Chapter one", error_code: "generation_failed", error_detail: "Generation could not finish." } };
    });
    render(<RevisionSheets books={books} selectedBookIds={[1, 2]} noun="book" />);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "1" } });
    await screen.findByRole("option", { name: /Chapter one/ });
    fireEvent.change(screen.getByLabelText("Chapter"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Create sheet" }));

    expect(await screen.findByRole("button", { name: "Retry generation" })).toBeInTheDocument();
  });

  it("ignores an old chapter request after changing books", async () => {
    let resolveOld!: (value: unknown) => void;
    fetchApi.mockImplementation((path: string) => {
      if (path.startsWith("/revision-sheets?")) return Promise.resolve({ sheets: [], jobs: [] });
      if (path === "/books/1/chapters") return new Promise(resolve => { resolveOld = resolve; });
      return Promise.resolve({ chapters: [{ node_id: 20, title: "New chapter", start_page: 1, end_page: 2 }] });
    });
    render(<RevisionSheets books={books} selectedBookIds={[1]} noun="book" />);
    fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
    await waitFor(() => expect(resolveOld).toBeDefined());
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "2" } });
    await screen.findByRole("option", { name: /New chapter/ });
    await act(async () => resolveOld({ chapters: [{ node_id: 10, title: "Old chapter", start_page: 1, end_page: 2 }] }));
    expect(screen.queryByRole("option", { name: /Old chapter/ })).not.toBeInTheDocument();
  });

  it("routes citation clicks to the original source page", () => {
    const onSource = vi.fn();
    render(<RevisionPage sheet={sheet} onSource={onSource} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Open Chapter one, PDF page 1" })[0]!);
    expect(onSource).toHaveBeenCalledWith(expect.objectContaining({ book_id: 1, node_id: 1, page: 1 }));
    expect(screen.getByRole("img")).toHaveAccessibleName(/relationships are listed immediately below/);
  });
});


it("opens reviewed HTML and retains every original figure in the gallery", async () => {
  const score = { score: 4, rationale: "Reviewed against rendered pages" };
  const upgraded = { ...sheet, provenance: { ...sheet.provenance,
    html: "<!doctype html><html><body><h1>Reviewed summary</h1></body></html>",
    review: { beauty: score, presentation: score, concept_coverage: score, conciseness: score, coverage: [] },
    inspected_figures: [10, 11], figure_references: [{block_id: 10}, {block_id: 11}],
  }};
  fetchApi.mockImplementation(async (path: string) => path.startsWith("/revision-sheets?") ? {sheets:[summary], jobs:[]} : path.includes("/chapters") ? {chapters:[]} : upgraded);
  render(<RevisionSheets books={books} selectedBookIds={[1]} noun="book" />);
  fireEvent.click(screen.getByRole("button", { name: "Revision sheet" }));
  fireEvent.click(await screen.findByRole("button", {name: /Chapter one.*Version 1/}));
  expect(await screen.findByTitle(/printable revision summary/)).toHaveAttribute("sandbox", "allow-same-origin");
  expect(await screen.findByRole("link", {name: "Download HTML"})).toBeInTheDocument();
  fireEvent.click(screen.getByText("All original figures · 2"));
  expect(screen.getAllByText("Original figure")).toHaveLength(2);
  fireEvent.click(screen.getByText("Independent quality review"));
  expect(screen.getByText("Visual design · 4/5")).toBeInTheDocument();
  expect(fetchApi.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
});
