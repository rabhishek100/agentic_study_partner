import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ accessToken: async () => "token" }));
vi.mock("@/lib/api", () => ({
  API_BASE: "/api",
  errorDetail: async () => "the reading voice is unavailable",
}));

import { ReadAloud } from "@/components/conversation/read-aloud";
import { reset } from "@/lib/narration-player";
import type { FigureRef } from "@/lib/types";

/**
 * jsdom has no media element, so playback is a stub that ends immediately.
 * Every instance is recorded, which is how the tests see what was played and
 * at what speed.
 */
class AudioStub {
  static instances: AudioStub[] = [];
  playbackRate = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  src = "";

  constructor(url: string) {
    this.src = url;
    AudioStub.instances.push(this);
  }

  play(): Promise<void> {
    // Resolved on a later tick, so the caller sees "speaking" before "ended".
    queueMicrotask(() => this.onended?.());
    return Promise.resolve();
  }

  pause(): void {}
}

function figure(blockId: number): FigureRef {
  return {
    book_id: 1,
    node_id: 7,
    block_id: blockId,
    page: 84,
    mime_type: "image/png",
    path: "Chapter 3 :: Regularisation",
    caption: "Validation error against complexity.",
    evidence_rank: 1,
  };
}

interface Call {
  url: string;
  body: Record<string, unknown>;
}

function stubFetch(calls: Call[], { speechFails = false } = {}) {
  return vi.fn(async (url: string, options: RequestInit) => {
    calls.push({ url, body: JSON.parse(String(options.body)) });
    if (url.endsWith("/narration/figures")) {
      return {
        ok: true,
        json: async () => ({ descriptions: { "42": "A U-shaped validation curve" } }),
      } as unknown as Response;
    }
    if (speechFails) return { ok: false, status: 502 } as unknown as Response;
    return { ok: true, blob: async () => new Blob(["mp3"]) } as unknown as Response;
  });
}

beforeEach(() => {
  reset();
  AudioStub.instances = [];
  vi.stubGlobal("Audio", AudioStub);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: () => "blob:narration",
    revokeObjectURL: () => {},
  });
});

const source = () => ({
  answer: "Error falls and then rises [S1].",
  evidence: [
    {
      node_id: 7,
      pages: [84],
      path: "Chapter 3",
      book_id: 1,
      book_title: "Sample Book",
      rank: 1,
      chunk_id: null,
      chunk_index: null,
      retrieval_method: null,
      score: null,
      excerpt: null,
    },
  ],
  citations: [],
  figures: [figure(42)],
});

describe("reading an answer aloud", () => {
  it("speaks the prose with the figure described inside it", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));

    render(<ReadAloud id="turn-1" source={source} />);
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() => expect(AudioStub.instances.length).toBeGreaterThan(0));

    // The figure is asked about by identity, never by caption text.
    expect(calls[0]!.url).toContain("/narration/figures");
    expect(calls[0]!.body).toEqual({ figures: [{ book_id: 1, block_id: 42 }] });

    const spoken = calls
      .slice(1)
      .map((call) => String(call.body.text))
      .join(" ");
    expect(spoken).toContain("Error falls and then rises.");
    expect(spoken).toContain("Figure, page 84.");
    expect(spoken).toContain("A U-shaped validation curve.");
    expect(spoken).not.toContain("S1");
  });

  it("plays at the speed the reader chose, without re-synthesising", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    window.localStorage.setItem("narration-speed", "1.5");

    render(<ReadAloud id="turn-1" source={source} />);
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() => expect(AudioStub.instances.length).toBeGreaterThan(0));
    expect(AudioStub.instances[0]!.playbackRate).toBe(1.5);
  });

  it("falls back to the browser's own voice and says that it did", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls, { speechFails: true }));
    const spoken: string[] = [];
    vi.stubGlobal("SpeechSynthesisUtterance", class {
      text: string;
      voice: unknown = null;
      rate = 1;
      onend: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
        spoken.push(text);
      }
    });
    vi.stubGlobal("speechSynthesis", {
      speak: () => {},
      cancel: () => {},
      pause: () => {},
      resume: () => {},
      getVoices: () => [],
    });

    render(<ReadAloud id="turn-1" source={source} />);
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() => expect(spoken.length).toBe(1));
    expect(spoken[0]).toContain("Error falls and then rises.");
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        /browser's own voice/,
      ),
    );
  });

  it("only ever speaks one passage at a time", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));

    render(
      <>
        <ReadAloud id="turn-1" source={source} label="Read first" />
        <ReadAloud id="turn-2" source={source} label="Read second" />
      </>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Read first" }));
    await userEvent.click(screen.getByRole("button", { name: "Read second" }));

    // The first control is back to offering, not paused mid-answer.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Read first" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Pause read first" })).toBeNull();
  });
});
