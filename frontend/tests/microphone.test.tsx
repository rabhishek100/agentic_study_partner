import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MicButton } from "@/components/dictation/mic-button";
import { transcribeRecording } from "@/lib/dictation";
import {
  getMicrophoneSnapshot,
  refreshMicrophones,
  resetMicrophones,
  selectMicrophone,
} from "@/lib/microphone";

vi.mock("@/lib/dictation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dictation")>()),
  transcribeRecording: vi.fn(),
}));

class FakeMediaRecorder {
  static isTypeSupported = (type: string) => type.startsWith("audio/webm");

  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"]) });
    this.onstop?.();
  }
}

function stream(): MediaStream {
  return { getTracks: () => [{ stop: () => {} }] } as unknown as MediaStream;
}

function input(deviceId: string, label: string): Partial<MediaDeviceInfo> {
  return { deviceId, kind: "audioinput", label };
}

const BUILT_IN = input("built-in", "MacBook Microphone");
const HEADSET = input("headset", "USB Headset");

function stubDevices(
  devices: Array<Partial<MediaDeviceInfo>>,
  getUserMedia = vi.fn<(constraints: MediaStreamConstraints) => Promise<MediaStream>>(
    async () => stream(),
  ),
) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia,
      enumerateDevices: vi.fn(async () => devices as MediaDeviceInfo[]),
      addEventListener: () => {},
      removeEventListener: () => {},
    },
  });
  return getUserMedia;
}

beforeEach(() => {
  resetMicrophones();
  vi.mocked(transcribeRecording).mockResolvedValue("a spoken question");
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "mediaDevices");
});

describe("the microphone list", () => {
  it("drops the aliases Chrome lists alongside the real inputs", async () => {
    stubDevices([
      input("default", "Default - MacBook Microphone"),
      input("communications", "Communications"),
      BUILT_IN,
      HEADSET,
      { deviceId: "speaker", kind: "audiooutput", label: "MacBook Speakers" },
    ]);

    await refreshMicrophones();

    expect(getMicrophoneSnapshot().devices).toEqual([
      { deviceId: "built-in", label: "MacBook Microphone" },
      { deviceId: "headset", label: "USB Headset" },
    ]);
  });

  it("names inputs by position while the browser withholds labels", async () => {
    stubDevices([input("a", ""), input("b", "   ")]);

    await refreshMicrophones();

    expect(getMicrophoneSnapshot().devices.map((d) => d.label)).toEqual([
      "Microphone 1",
      "Microphone 2",
    ]);
  });

  it("stops claiming a device that has been unplugged", async () => {
    stubDevices([BUILT_IN, HEADSET]);
    await refreshMicrophones();
    selectMicrophone("headset");

    stubDevices([BUILT_IN]);
    await refreshMicrophones();

    expect(getMicrophoneSnapshot().selectedId).toBeNull();
  });
});

describe("choosing a microphone", () => {
  it("stays hidden while there is nothing to choose between", async () => {
    stubDevices([BUILT_IN]);

    render(<MicButton onTranscript={vi.fn()} />);

    await screen.findByRole("button", { name: /dictate/i });
    expect(
      screen.queryByRole("button", { name: /choose a microphone/i }),
    ).toBeNull();
  });

  it("records from the chosen input rather than the default", async () => {
    const getUserMedia = stubDevices([BUILT_IN, HEADSET]);
    const user = userEvent.setup();

    render(<MicButton onTranscript={vi.fn()} />);
    await user.click(
      await screen.findByRole("button", { name: /choose a microphone/i }),
    );
    await user.click(
      await screen.findByRole("menuitemradio", { name: "USB Headset" }),
    );
    await user.click(screen.getByRole("button", { name: /dictate/i }));

    await waitFor(() =>
      expect(getUserMedia).toHaveBeenCalledWith({
        audio: { deviceId: { exact: "headset" } },
      }),
    );
  });

  it("shares one choice across every composer on the page", async () => {
    stubDevices([BUILT_IN, HEADSET]);
    const user = userEvent.setup();

    render(
      <>
        <MicButton label="Dictate in the main composer" onTranscript={vi.fn()} />
        <MicButton label="Dictate in the side chat" onTranscript={vi.fn()} />
      </>,
    );

    const [mainPicker, sidePicker] = await screen.findAllByRole("button", {
      name: /choose a microphone/i,
    });
    await user.click(mainPicker!);
    await user.click(
      await screen.findByRole("menuitemradio", { name: "USB Headset" }),
    );

    await user.click(sidePicker!);
    await waitFor(() =>
      expect(
        screen.getByRole("menuitemradio", { name: "USB Headset" }),
      ).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("falls back to the default when the chosen input has gone away", async () => {
    const getUserMedia = vi.fn(async (constraints: MediaStreamConstraints) => {
      if (constraints.audio !== true) {
        throw new DOMException("gone", "OverconstrainedError");
      }
      return stream();
    });
    stubDevices([BUILT_IN, HEADSET], getUserMedia);
    await refreshMicrophones();
    selectMicrophone("headset");
    const user = userEvent.setup();

    render(<MicButton onTranscript={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /dictate/i }));

    // It recorded rather than failing, and the picker no longer claims a
    // device that is not there.
    await screen.findByRole("button", { name: /stop recording/i });
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getMicrophoneSnapshot().selectedId).toBeNull();
  });
});
