/**
 * Minimal server-sent-event framing for the study stream.
 *
 * Kept separate from the hook that consumes it because this is the part with
 * edge cases worth testing directly: partial frames split across network
 * reads, heartbeat comments, and events with no data line.
 */

export interface SseEvent {
  event: string;
  data: string;
}

/**
 * Split whatever has accumulated into complete events plus the remainder.
 *
 * The remainder is whatever follows the last `\n\n`; the caller keeps it and
 * prepends it to the next read. Comment-only frames (`: heartbeat`) and frames
 * with no `data:` line are dropped — they exist to keep the connection warm,
 * not to carry payload.
 */
export function drainSseEvents(buffer: string): {
  events: SseEvent[];
  rest: string;
} {
  const events: SseEvent[] = [];
  let rest = buffer;

  let boundary = rest.indexOf("\n\n");
  while (boundary !== -1) {
    const block = rest.slice(0, boundary);
    rest = rest.slice(boundary + 2);

    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    if (data) events.push({ event, data });

    boundary = rest.indexOf("\n\n");
  }

  return { events, rest };
}
