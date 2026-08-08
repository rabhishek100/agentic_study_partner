import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/conversation/composer";
import { MicButton } from "@/components/dictation/mic-button";
import { transcribeRecording } from "@/lib/dictation";

vi.mock("@/lib/dictation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dictation")>()),
  transcribeRecording: vi.fn(),
}));

const transcribe = vi.mocked(transcribeRecording);

/** A recorder that produces one clip when stopped, like the browser's. */
class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static isTypeSupported = (type: string) => type.startsWith("audio/webm");

  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? "audio/webm";
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({
      data: new Blob(["spoken-audio"], { type: this.mimeType }),
    });
    this.onstop?.();
  }
}

const stoppedTracks: string[] = [];

function fakeStream(): MediaStream {
  return {
    getTracks: () => [{ stop: () => stoppedTracks.push("stopped") }],
  } as unknown as MediaStream;
}

function grantMicrophone(getUserMedia = vi.fn(async () => fakeStream())) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  return getUserMedia;
}

beforeEach(() => {
  FakeMediaRecorder.instances = [];
  stoppedTracks.length = 0;
  transcribe.mockReset();
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  grantMicrophone();
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "mediaDevices");
});

function composer(props: Partial<Parameters<typeof Composer>[0]> = {}) {
  return (
    <Composer
      disabled={false}
      isStreaming={false}
      placeholder="Ask about the book"
      onSubmit={() => {}}
      onStop={() => {}}
      {...props}
    />
  );
}

describe("MicButton", () => {
  it("records, transcribes, and hands the words back", async () => {
    transcribe.mockResolvedValue("What is a residual connection?");
    const onTranscript = vi.fn();
    const user = userEvent.setup();

    render(<MicButton onTranscript={onTranscript} />);
    await user.click(await screen.findByRole("button", { name: /dictate/i }));

    const stop = await screen.findByRole("button", {
      name: /stop recording and transcribe/i,
    });
    await user.click(stop);

    await waitFor(() =>
      expect(onTranscript).toHaveBeenCalledWith("What is a residual connection?"),
    );
    // The browser's recording indicator must go out with the recording.
    expect(stoppedTracks).toHaveLength(1);
  });

  it("discards a recording on Escape without spending a transcription", async () => {
    const onTranscript = vi.fn();
    const user = userEvent.setup();

    render(<MicButton onTranscript={onTranscript} />);
    await user.click(await screen.findByRole("button", { name: /dictate/i }));
    await screen.findByRole("button", { name: /stop recording/i });

    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /dictate/i })).toBeVisible(),
    );
    expect(transcribe).not.toHaveBeenCalled();
    expect(onTranscript).not.toHaveBeenCalled();
    expect(stoppedTracks).toHaveLength(1);
  });

  it("explains a blocked microphone instead of failing silently", async () => {
    grantMicrophone(
      vi.fn(async () => {
        throw new DOMException("denied", "NotAllowedError");
      }),
    );
    const user = userEvent.setup();

    render(<MicButton onTranscript={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /dictate/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Microphone access is blocked/,
    );
  });

  it("reports the server's own wording when transcription fails", async () => {
    transcribe.mockRejectedValue(
      Object.assign(new Error("no speech was recorded"), { name: "ApiError" }),
    );
    const user = userEvent.setup();

    render(<MicButton onTranscript={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /dictate/i }));
    await user.click(
      await screen.findByRole("button", { name: /stop recording/i }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent(
      /could not be transcribed|no speech was recorded/,
    );
  });

  it("stays out of the way where recording is impossible", async () => {
    vi.stubGlobal("MediaRecorder", undefined);

    render(<MicButton onTranscript={vi.fn()} />);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /dictate/i })).toBeNull(),
    );
  });
});

describe("dictation in the composer", () => {
  it("lands spoken words at the caret rather than replacing the question", async () => {
    transcribe.mockResolvedValue("gradient clipping");
    const user = userEvent.setup();

    render(composer());
    const field = screen.getByLabelText("Ask about the book");
    await user.type(field, "Explain in chapter 4");
    (field as HTMLTextAreaElement).setSelectionRange(7, 7);

    await user.click(screen.getByRole("button", { name: /dictate/i }));
    await user.click(
      await screen.findByRole("button", { name: /stop recording/i }),
    );

    await waitFor(() =>
      expect(field).toHaveValue("Explain gradient clipping in chapter 4"),
    );
  });

  it("sends a dictated question like a typed one", async () => {
    transcribe.mockResolvedValue("What is LoRA?");
    const onSubmit = vi.fn();
    const user = userEvent.setup();

    render(composer({ onSubmit }));
    await user.click(screen.getByRole("button", { name: /dictate/i }));
    await user.click(
      await screen.findByRole("button", { name: /stop recording/i }),
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Ask about the book")).toHaveValue(
        "What is LoRA?",
      ),
    );
    await user.click(screen.getByLabelText("Send question"));

    expect(onSubmit).toHaveBeenCalledWith("What is LoRA?");
  });
});
