import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn(), rooms: [] as unknown[] }));
vi.mock("@/lib/api", () => ({ apiFetch: mocks.apiFetch }));
vi.mock("livekit-client", () => ({
  ParticipantKind: { AGENT: "agent" },
  Track: { Kind: { Audio: "audio" } },
  RoomEvent: {
    TrackSubscribed: "track", TrackUnsubscribed: "untrack", DataReceived: "data",
    Reconnecting: "reconnecting", Reconnected: "reconnected", Disconnected: "disconnected",
    AudioPlaybackStatusChanged: "playback",
  },
  Room: class {
    state = "disconnected";
    canPlaybackAudio = true;
    callbacks = new Map<string, (...args: unknown[]) => void>();
    agent = { identity: "worker", kind: "agent", attributes: { "interview.voice.ready": "true" }, setVolume: vi.fn() };
    remoteParticipants = new Map([["worker", this.agent]]);
    localParticipant = {
      audioLevel: 0,
      setMicrophoneEnabled: vi.fn(async () => undefined),
      performRpc: vi.fn(async () => "ok"),
    };
    connect = vi.fn(async () => { this.state = "connected"; });
    disconnect = vi.fn(async () => { this.state = "disconnected"; this.callbacks.get("disconnected")?.(); });
    startAudio = vi.fn(async () => undefined);
    switchActiveDevice = vi.fn(async () => true);
    constructor() { mocks.rooms.push(this); }
    on(event: string, callback: (...args: unknown[]) => void) { this.callbacks.set(event, callback); return this; }
    packet(value: object) { this.callbacks.get("data")?.(new TextEncoder().encode(JSON.stringify(value)), this.agent, undefined, "interview.voice"); }
  },
}));

import { Room } from "livekit-client";
import { useLivekitInterview } from "@/hooks/use-livekit-interview";

type FakeRoom = Room & { packet(value: object): void; callbacks: Map<string, (...args: unknown[]) => void> };
const room = () => mocks.rooms.at(-1) as FakeRoom;
const options = () => ({ sessionId: "session", turnIndex: 0, onTranscript: vi.fn() });

beforeEach(() => {
  mocks.rooms.length = 0;
  mocks.apiFetch.mockReset().mockResolvedValue({ server_url: "wss://test.invalid", participant_token: "token" });
});
afterEach(() => vi.clearAllMocks());

describe("LiveKit media lifecycle", () => {
  it("rejects pre-reconnect transcripts and resumes only after an explicit start", async () => {
    const props = options();
    const { result, unmount } = renderHook(() => useLivekitInterview(props));
    await act(async () => result.current.voice.start());
    const initial = JSON.parse(vi.mocked(room().localParticipant.performRpc).mock.calls[0]![0].payload);
    await act(async () => {
      room().callbacks.get("reconnecting")?.();
      room().callbacks.get("reconnected")?.();
      room().packet({ type: "transcript", epoch: initial.epoch, sequence: 1, text: "Stale" });
    });
    expect(result.current.voice.status).toBe("idle");
    expect(props.onTranscript).not.toHaveBeenCalled();
    expect(vi.mocked(room().localParticipant.setMicrophoneEnabled).mock.calls.filter(([enabled]) => enabled)).toHaveLength(1);
    await act(async () => result.current.voice.start());
    const latest = JSON.parse(vi.mocked(room().localParticipant.performRpc).mock.calls.at(-1)![0].payload);
    act(() => room().packet({ type: "transcript", epoch: latest.epoch, sequence: 2, text: "New answer" }));
    expect(props.onTranscript).toHaveBeenCalledWith("New answer");
    unmount();
  });

  it("does not create a room when a token request completes after leaving the interview", async () => {
    let grant!: (value: object) => void;
    mocks.apiFetch.mockImplementationOnce(() => new Promise((resolve) => { grant = resolve; }));
    const { result, unmount } = renderHook(() => useLivekitInterview(options()));
    let starting!: Promise<void>;
    act(() => { starting = result.current.voice.start(); });
    unmount();
    await act(async () => {
      grant({ server_url: "wss://test.invalid", participant_token: "token" });
      await starting;
    });
    expect(mocks.rooms).toHaveLength(0);
  });

  it("does not start obsolete speech after a pending connection resolves", async () => {
    let grant!: (value: object) => void;
    mocks.apiFetch.mockImplementationOnce(() => new Promise((resolve) => { grant = resolve; }));
    const { result, unmount } = renderHook(() => useLivekitInterview(options()));
    let speaking!: Promise<void>;
    act(() => { speaking = result.current.speech.speak("session", 0, "Question"); });
    act(() => result.current.speech.stop());
    await act(async () => {
      grant({ server_url: "wss://test.invalid", participant_token: "token" });
      await speaking;
    });
    expect(room().localParticipant.performRpc).not.toHaveBeenCalled();
    expect(result.current.speech.loading).toBe(false);
    unmount();
  });

  it("keeps capture open while final transcripts arrive and rejects stale segments", async () => {
    const props = options();
    const { result, unmount } = renderHook(() => useLivekitInterview(props));
    await act(async () => result.current.voice.start());
    const calls = vi.mocked(room().localParticipant.performRpc).mock.calls;
    const listen = JSON.parse(calls[0]![0].payload);
    act(() => {
      room().packet({ type: "transcript", epoch: listen.epoch, sequence: 1, text: "First thought." });
      room().packet({ type: "transcript", epoch: listen.epoch, sequence: 2, text: "Second thought." });
      room().packet({ type: "transcript", epoch: listen.epoch, sequence: 2, text: "Duplicate." });
    });
    expect(props.onTranscript.mock.calls).toEqual([["First thought."], ["Second thought."]]);
    expect(room().localParticipant.setMicrophoneEnabled).toHaveBeenCalledTimes(1);
    act(() => result.current.voice.stop());
    act(() => room().packet({ type: "transcript", epoch: listen.epoch, sequence: 3, text: "Late text." }));
    expect(props.onTranscript).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("waits for matching playback completion before resolving a reaction", async () => {
    const { result, unmount } = renderHook(() => useLivekitInterview(options()));
    let speech!: Promise<void>;
    let settled = false;
    await act(async () => {
      speech = result.current.speech.speakReaction("session", 0, "Thanks.");
      void speech.then(() => { settled = true; });
      // Advance the connection and command promises without completing playback.
      for (let i = 0; i < 20; i++) await Promise.resolve();
    });
    const command = JSON.parse(vi.mocked(room().localParticipant.performRpc).mock.calls.at(-1)![0].payload);
    expect(command.utterance).toBe("reaction");
    expect(command).not.toHaveProperty("text");
    act(() => room().packet({ type: "speech_done", request_id: "old-request" }));
    expect(settled).toBe(false);
    await act(async () => {
      room().packet({ type: "speech_done", request_id: command.request_id });
      await speech;
    });
    expect(settled).toBe(true);
    unmount();
  });

  it("does not reopen the mic after a push-to-talk release during connection", async () => {
    let grant!: (value: object) => void;
    mocks.apiFetch.mockImplementationOnce(() => new Promise((resolve) => { grant = resolve; }));
    const { result, unmount } = renderHook(() => useLivekitInterview(options()));
    act(() => result.current.voice.setMode("push_to_talk"));
    let start!: Promise<void>;
    act(() => { start = result.current.voice.beginPush(); });
    act(() => result.current.voice.endPush());
    await act(async () => {
      grant({ server_url: "wss://test.invalid", participant_token: "token" });
      await start;
    });
    expect(room().localParticipant.setMicrophoneEnabled).not.toHaveBeenCalledWith(true, expect.anything());
    unmount();
  });
});
