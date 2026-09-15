import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ accessToken: async () => "token" }));
vi.mock("@/lib/api", () => ({
  API_BASE: "/api",
  errorDetail: async () => "the reading voice is unavailable",
}));
vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: () => ({ status: "ready", url: "blob:figure" }),
}));
const narrationVoice = vi.hoisted(() => ({ setEnabled: vi.fn() }));
vi.mock("@/hooks/use-narration-voice", () => ({
  useNarrationVoice: () => ({
    enabled: false,
    setEnabled: narrationVoice.setEnabled,
    status: "off",
    error: "",
    dismissError: vi.fn(),
    startListening: vi.fn(),
    stopListening: vi.fn(async () => {}),
    speakAnswer: vi.fn(async () => {}),
    microphones: { selectedId: "", devices: [] },
    supported: true,
  }),
}));

import { ReadAloud } from "@/components/conversation/read-aloud";
import { Answer } from "@/components/conversation/answer";
import { NarrationPlayerBar } from "@/components/conversation/narration-player-bar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { playScript, reset, setPacing } from "@/lib/narration-player";
import type { FigureRef } from "@/lib/types";

/**
 * jsdom has no media element, so playback is a stub that ends immediately.
 * Every instance is recorded, which is how the tests see what was played and
 * at what speed.
 */
class AudioStub {
  static instances: AudioStub[] = [];
  static autoEnd = true;
  playbackRate = 1;
  duration = 100;
  currentTime = 0;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeupdate: (() => void) | null = null;
  onloadedmetadata: (() => void) | null = null;
  src = "";

  constructor(url: string) {
    this.src = url;
    AudioStub.instances.push(this);
  }

  play(): Promise<void> {
    // Resolved on a later tick, so the caller sees "speaking" before "ended".
    if (AudioStub.autoEnd) queueMicrotask(() => this.onended?.());
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
  window.localStorage.clear();
  setPacing({
    sentencePauseMs: 0,
    paragraphPauseMs: 0,
    headingPauseMs: 0,
    titlePauseMs: 0,
    headingRate: 1,
    titleRate: 1,
  });
  AudioStub.instances = [];
  AudioStub.autoEnd = true;
  narrationVoice.setEnabled.mockClear();
  vi.stubGlobal("Audio", AudioStub);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: () => "blob:narration",
    revokeObjectURL: () => {},
  });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
});

afterEach(() => {
  Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
  vi.unstubAllGlobals();
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

  it("opens one global media bar with follow, seek and stop controls", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;

    render(
      <>
        <ReadAloud id="turn-1" source={source} />
        <NarrationPlayerBar />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    expect(await screen.findByRole("region", { name: "Read-aloud player" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Reading position" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous passage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rewind 10 seconds" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reading pacing controls" })).toBeInTheDocument();

    // The media metadata says 100 seconds, but the logical timeline stays
    // fixed instead of stretching and flickering when metadata arrives.
    const slider = screen.getByRole("slider", { name: "Reading position" });
    expect(Number(slider.getAttribute("max"))).toBeLessThan(10);

    await userEvent.click(screen.getByRole("button", { name: "Turn off auto-follow" }));
    expect(window.localStorage.getItem("narration-auto-follow-v2")).toBe("false");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    expect(screen.queryByRole("region", { name: "Read-aloud player" })).toBeNull();
  });

  it("stores independent pause and heading pace controls", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;

    render(
      <>
        <ReadAloud id="turn-1" source={() => ({ answer: "## Architecture\n\nA request flows." })} />
        <NarrationPlayerBar />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await userEvent.click(await screen.findByRole("button", { name: "Reading pacing controls" }));

    fireEvent.change(screen.getByRole("slider", { name: "After each sentence" }), {
      target: { value: "700" },
    });
    fireEvent.change(screen.getByRole("slider", { name: "Heading voice pace" }), {
      target: { value: "0.75" },
    });

    const stored = JSON.parse(window.localStorage.getItem("narration-pacing") ?? "{}");
    expect(stored.sentencePauseMs).toBe(700);
    expect(stored.headingRate).toBe(0.75);
    expect(AudioStub.instances[0]!.playbackRate).toBe(0.75);
  });

  it("commits a pointer scrub once instead of restarting audio on every move", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;

    render(
      <>
        <ReadAloud id="turn-1" source={source} />
        <NarrationPlayerBar />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(AudioStub.instances).toHaveLength(1));

    const slider = screen.getByRole("slider", { name: "Reading position" });
    fireEvent.pointerDown(slider);
    fireEvent.input(slider, { target: { value: "0.5" } });
    fireEvent.change(slider, { target: { value: "0.8" } });
    expect(AudioStub.instances).toHaveLength(1);
    fireEvent.pointerUp(slider, { target: { value: "0.8" } });
    await waitFor(() => expect(AudioStub.instances).toHaveLength(2));
  });

  it("follows stable blocks when identical text appears more than once", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const repeated = "Repeated phrase.\n\nRepeated phrase.";

    const view = render(
      <TooltipProvider>
        <Answer
          narrationId="turn-1"
          text={repeated}
          evidence={[]}
          citations={[]}
        />
        <ReadAloud id="turn-1" source={() => ({ answer: repeated })} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    const paragraphs = view.container.querySelectorAll("p");
    const firstScroll = vi.fn();
    const secondScroll = vi.fn();
    Object.defineProperty(paragraphs[0]!, "scrollIntoView", { value: firstScroll });
    Object.defineProperty(paragraphs[1]!, "scrollIntoView", { value: secondScroll });

    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(firstScroll).toHaveBeenCalledOnce());
    expect(secondScroll).not.toHaveBeenCalled();

    act(() => AudioStub.instances[0]!.onended?.());
    await waitFor(() => expect(secondScroll).toHaveBeenCalledOnce());
  });

  it("tracks repeated sentences within the same normal answer paragraph", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) { this.ranges = ranges; }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 200, bottom: 230, left: 0, right: 200, width: 200, height: 30 }),
    });
    const repeated = "Same sentence. Same sentence.";

    render(
      <TooltipProvider>
        <Answer narrationId="turn-1" text={repeated} evidence={[]} citations={[]} />
        <ReadAloud id="turn-1" source={() => ({ answer: repeated })} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() => expect(highlights.has("narration-current")).toBe(true));
    const first = highlights.get("narration-current")!.ranges[0]!.startOffset;
    act(() => AudioStub.instances[0]!.onended?.());
    await waitFor(() =>
      expect(highlights.get("narration-current")!.ranges[0]!.startOffset).toBeGreaterThan(first),
    );
  });

  it("keeps normal-answer speech aligned through entities, emphasis and maths", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) { this.ranges = ranges; }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 200, bottom: 250, left: 0, right: 400, width: 400, height: 50 }),
    });
    const answer = "Use **replication** &amp; $x^2$ carefully. Then verify.";

    render(
      <TooltipProvider>
        <Answer narrationId="turn-1" text={answer} evidence={[]} citations={[]} />
        <ReadAloud id="turn-1" source={() => ({ answer })} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() => expect(highlights.has("narration-current")).toBe(true));
    const highlighted = highlights.get("narration-current")!.ranges[0]!.toString();
    expect(highlighted).toContain("replication");
    expect(highlighted).toContain("carefully.");
    expect(highlighted).not.toContain("Then verify");
  });

  it("tracks unpunctuated list items as separate spoken passages", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) { this.ranges = ranges; }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 200, bottom: 230, left: 0, right: 200, width: 200, height: 30 }),
    });
    const answer = "- First item\n- Second item";

    render(
      <TooltipProvider>
        <Answer narrationId="turn-1" text={answer} evidence={[]} citations={[]} />
        <ReadAloud id="turn-1" source={() => ({ answer })} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() =>
      expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("First item"),
    );

    act(() => AudioStub.instances[0]!.onended?.());
    await waitFor(() =>
      expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("Second item"),
    );
  });

  it("keeps cited list speech on the same visible sentence through the final item", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) { this.ranges = ranges; }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 200, bottom: 230, left: 0, right: 200, width: 200, height: 30 }),
    });
    const answer = "- First cited item. [S1]\n- Second cited item. [S1]\n- Third cited item. [S1]";
    const input = { ...source(), answer, figures: [] };
    const view = render(
      <TooltipProvider>
        <Answer
          narrationId="turn-1"
          text={answer}
          evidence={input.evidence}
          citations={input.citations}
        />
        <ReadAloud id="turn-1" source={() => input} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));

    await waitFor(() =>
      expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("First cited item."),
    );
    act(() => AudioStub.instances[0]!.onended?.());
    await waitFor(() =>
      expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("Second cited item."),
    );
    act(() => AudioStub.instances[1]!.onended?.());
    await waitFor(() =>
      expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("Third cited item."),
    );
    expect(view.container.querySelector("ul")).not.toHaveAttribute("data-narration-fallback");
  });

  it("waits for audio playback before advancing the highlight", async () => {
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) { this.ranges = ranges; }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top: 200, bottom: 230, left: 0, right: 200, width: 200, height: 30 }),
    });

    let releaseSpeech!: (response: Response) => void;
    const speech = new Promise<Response>((resolve) => { releaseSpeech = resolve; });
    const fetch = vi.fn(() => speech);
    vi.stubGlobal("fetch", fetch);

    render(
      <TooltipProvider>
        <Answer narrationId="turn-1" text="Sound starts here." evidence={[]} citations={[]} />
        <ReadAloud id="turn-1" source={() => ({ answer: "Sound starts here." })} />
        <NarrationPlayerBar />
      </TooltipProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(highlights.has("narration-current")).toBe(false);

    releaseSpeech({ ok: true, blob: async () => new Blob(["mp3"]) } as Response);
    await waitFor(() => expect(highlights.has("narration-current")).toBe(true));
    expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe("Sound starts here.");
  });

  it("never highlights an entire list when a sentence anchor cannot resolve", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;

    const view = render(
      <>
        <div data-narration-anchor="turn-1">
          <ul data-narration-block="line-1">
            <li>Only visible sentence.</li>
          </ul>
        </div>
        <NarrationPlayerBar />
      </>,
    );
    act(() => {
      void playScript("turn-1", {
        segments: [{
          kind: "list",
          text: "A sentence that is not rendered.",
          anchor: { type: "block", key: "line-1", sentence: 99 },
        }],
        figures: [],
      });
    });
    await waitFor(() => expect(AudioStub.instances).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/passage 1 of 1/i)).toBeInTheDocument());

    expect(view.container.querySelector("ul")).not.toHaveAttribute("data-narration-fallback");
  });

  it("highlights and scrolls to the exact sentence in a long verbatim block", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    const highlights = new Map<string, { ranges: Range[] }>();
    class HighlightStub {
      ranges: Range[];
      constructor(...ranges: Range[]) {
        this.ranges = ranges;
      }
    }
    vi.stubGlobal("Highlight", HighlightStub);
    vi.stubGlobal("CSS", { highlights });
    const scrollBy = vi.fn();
    vi.stubGlobal("scrollBy", scrollBy);
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({
        top: 1_400,
        bottom: 1_430,
        left: 100,
        right: 500,
        width: 400,
        height: 30,
        x: 100,
        y: 1_400,
        toJSON: () => ({}),
      }),
    });

    render(
      <>
        <div data-narration-anchor="reading-1">
          <div data-narration-passage="7">
            <p>First sentence. The exact second sentence. Third sentence.</p>
          </div>
        </div>
        <NarrationPlayerBar />
      </>,
    );

    act(() => {
      void playScript(
        "reading-1",
        {
          segments: [
            {
              kind: "prose",
              text: "The exact second sentence.",
              anchor: { type: "passage", index: 7 },
            },
          ],
          figures: [],
        },
        { anchorId: "reading-1" },
      );
    });

    await waitFor(() => expect(highlights.has("narration-current")).toBe(true));
    expect(highlights.get("narration-current")?.ranges[0]?.toString()).toBe(
      "The exact second sentence.",
    );
    expect(scrollBy).toHaveBeenCalledOnce();
    expect(scrollBy.mock.calls[0]?.[0]).toMatchObject({ behavior: "auto" });
  });

  it("turns auto-follow off when the reader manually scrolls", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;

    render(
      <>
        <div data-narration-anchor="reading-1">
          <p data-narration-passage="1">A passage the reader leaves.</p>
        </div>
        <NarrationPlayerBar />
      </>,
    );
    act(() => {
      void playScript(
        "reading-1",
        {
          segments: [
            {
              kind: "prose",
              text: "A passage the reader leaves.",
              anchor: { type: "passage", index: 1 },
            },
          ],
          figures: [],
        },
        { anchorId: "reading-1" },
      );
    });

    await screen.findByRole("button", { name: "Turn off auto-follow" });
    fireEvent.wheel(document);

    await screen.findByRole("button", { name: "Turn on auto-follow" });
    expect(window.localStorage.getItem("narration-auto-follow-v2")).toBeNull();

    act(() => {
      void playScript("reading-2", {
        segments: [{ kind: "prose", text: "Another reading." }],
        figures: [],
      });
    });
    await screen.findByRole("button", { name: "Turn off auto-follow" });
  });

  it("refocuses the active sentence after browser zoom reflows the page", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    let top = 200;
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ top, bottom: top + 30, left: 0, right: 200, width: 200, height: 30 }),
    });
    const windowScrollBy = vi.fn();
    vi.stubGlobal("scrollBy", windowScrollBy);

    const view = render(
      <>
        <div data-narration-scroll-container="">
          <div data-narration-anchor="reading-1">
            <p data-narration-passage="1">The active sentence.</p>
          </div>
        </div>
        <NarrationPlayerBar />
      </>,
    );
    const paneScrollBy = vi.fn();
    const pane = view.container.querySelector("[data-narration-scroll-container]")!;
    Object.defineProperty(pane, "scrollBy", { value: paneScrollBy });
    Object.defineProperty(pane, "getBoundingClientRect", {
      value: () => ({ top: 0, bottom: 800, left: 0, right: 800, width: 800, height: 800 }),
    });
    act(() => {
      void playScript("reading-1", {
        segments: [{
          kind: "prose",
          text: "The active sentence.",
          anchor: { type: "passage", index: 1, sentence: 0 },
        }],
        figures: [],
      }, { anchorId: "reading-1" });
    });
    await screen.findByRole("region", { name: "Read-aloud player" });
    expect(paneScrollBy).not.toHaveBeenCalled();

    fireEvent.wheel(document, { ctrlKey: true });
    expect(screen.getByRole("button", { name: "Turn off auto-follow" })).toBeInTheDocument();

    top = 1_400;
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(paneScrollBy).toHaveBeenCalledOnce());
    expect(paneScrollBy.mock.calls[0]?.[0]).toMatchObject({ behavior: "auto" });
    expect(windowScrollBy).not.toHaveBeenCalled();
  });

  it("offers the guarded voice-question mode only for a saved book turn", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    AudioStub.autoEnd = false;
    process.env.NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED = "true";

    render(
      <>
        <ReadAloud
          id="turn-1"
          source={source}
          voiceContext={{ conversationId: "conversation-1", turnIndex: 0 }}
        />
        <NarrationPlayerBar />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await userEvent.click(await screen.findByRole("button", { name: "Voice questions" }));

    expect(narrationVoice.setEnabled).toHaveBeenCalledWith(true);
    delete process.env.NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED;
  });
});
