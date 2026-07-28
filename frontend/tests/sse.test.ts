import { describe, expect, it } from "vitest";

import { drainSseEvents } from "@/lib/sse";

describe("drainSseEvents", () => {
  it("returns complete events and keeps the trailing partial frame", () => {
    const { events, rest } = drainSseEvents(
      'event: token\ndata: {"text":"Hello"}\n\nevent: token\ndata: {"text":" wo',
    );

    expect(events).toEqual([{ event: "token", data: '{"text":"Hello"}' }]);
    expect(rest).toBe('event: token\ndata: {"text":" wo');
  });

  it("reassembles an event split across two network reads", () => {
    const first = drainSseEvents('event: token\ndata: {"tex');
    expect(first.events).toHaveLength(0);

    const second = drainSseEvents(`${first.rest}t":"Hello"}\n\n`);
    expect(second.events).toEqual([
      { event: "token", data: '{"text":"Hello"}' },
    ]);
    expect(second.rest).toBe("");
  });

  it("drains several events queued in one read", () => {
    const { events, rest } = drainSseEvents(
      'event: token\ndata: {"text":"a"}\n\nevent: token\ndata: {"text":"b"}\n\nevent: final\ndata: {"ok":true}\n\n',
    );

    expect(events.map((entry) => entry.event)).toEqual([
      "token",
      "token",
      "final",
    ]);
    expect(rest).toBe("");
  });

  it("ignores heartbeat comments the API sends while retrieval runs", () => {
    const { events, rest } = drainSseEvents(
      ': heartbeat\n\n: heartbeat\n\nevent: token\ndata: {"text":"a"}\n\n',
    );

    expect(events).toEqual([{ event: "token", data: '{"text":"a"}' }]);
    expect(rest).toBe("");
  });

  it("drops frames that carry no data line", () => {
    const { events } = drainSseEvents("event: token\n\n");
    expect(events).toEqual([]);
  });

  it("defaults an unnamed event to `message` rather than dropping it", () => {
    const { events } = drainSseEvents('data: {"text":"a"}\n\n');
    expect(events).toEqual([{ event: "message", data: '{"text":"a"}' }]);
  });

  it("preserves JSON containing blank lines inside string values", () => {
    // A `\n\n` inside the payload is escaped as `\\n\\n` in JSON, so it must
    // not be mistaken for a frame boundary.
    const { events, rest } = drainSseEvents(
      'event: token\ndata: {"text":"line one\\n\\nline two"}\n\n',
    );

    expect(events).toHaveLength(1);
    expect(JSON.parse(events[0]!.data)).toEqual({
      text: "line one\n\nline two",
    });
    expect(rest).toBe("");
  });
});
