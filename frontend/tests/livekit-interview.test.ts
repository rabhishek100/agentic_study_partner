import { describe, expect, it } from "vitest";
import { acceptsTranscript, parseVoiceEvent } from "@/lib/livekit-interview";

const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

describe("LiveKit draft boundaries", () => {
  it("accepts ordered final segments across thinking pauses", () => {
    const first = parseVoiceEvent(encode({ type: "transcript", epoch: 2, sequence: 1, text: "Start with requirements." }))!;
    const second = parseVoiceEvent(encode({ type: "transcript", epoch: 2, sequence: 2, text: "Then estimate traffic." }))!;
    expect(acceptsTranscript(first, 2, 0)).toBe(true);
    expect(acceptsTranscript(second, 2, 1)).toBe(true);
  });
  it("rejects duplicate and obsolete capture segments", () => {
    const event = { type: "transcript" as const, epoch: 2, sequence: 3, text: "Old answer" };
    expect(acceptsTranscript(event, 3, 2)).toBe(false);
    expect(acceptsTranscript(event, 2, 3)).toBe(false);
    expect(acceptsTranscript(event, 2, 4)).toBe(false);
  });
  it("rejects malformed packets and never appends interim speech", () => {
    expect(parseVoiceEvent(encode({ type: "transcript", epoch: "2", text: "Oops" }))).toBeNull();
    expect(parseVoiceEvent(encode({ type: "unknown" }))).toBeNull();
    expect(parseVoiceEvent(new Uint8Array([255]))).toBeNull();
    expect(acceptsTranscript({ type: "hearing", epoch: 2 }, 2, 0)).toBe(false);
  });
});
