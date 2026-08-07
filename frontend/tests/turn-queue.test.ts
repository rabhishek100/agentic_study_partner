import { describe, expect, it, vi } from "vitest";

import { createTurnQueue } from "@/lib/turn-queue";

/** Resolves once the microtask queue has drained. */
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("createTurnQueue", () => {
  it("runs up to the limit immediately", async () => {
    const queue = createTurnQueue(3);

    const slots = await Promise.all([
      queue.acquire("a"),
      queue.acquire("b"),
      queue.acquire("c"),
    ]);

    expect(slots).toHaveLength(3);
    expect(queue.running()).toBe(3);
    expect(queue.waiting()).toEqual([]);
  });

  it("makes the fourth request wait rather than refusing it", async () => {
    const queue = createTurnQueue(3);
    await Promise.all([queue.acquire("a"), queue.acquire("b")]);
    const third = await queue.acquire("c");

    const fourth = vi.fn();
    queue.acquire("d").then(fourth);
    await settle();

    expect(fourth).not.toHaveBeenCalled();
    expect(queue.waiting()).toEqual(["d"]);

    third();
    await settle();

    expect(fourth).toHaveBeenCalled();
    expect(queue.waiting()).toEqual([]);
  });

  it("starts waiting turns in the order they asked", async () => {
    const queue = createTurnQueue(1);
    const first = await queue.acquire("first");
    const started: string[] = [];
    queue.acquire("second").then(() => started.push("second"));
    queue.acquire("third").then(() => started.push("third"));

    first();
    await settle();
    expect(started).toEqual(["second"]);
  });

  it("releasing twice does not open a slot it never held", async () => {
    const queue = createTurnQueue(1);
    const slot = await queue.acquire("a");

    slot();
    slot();
    await settle();

    expect(queue.running()).toBe(0);
  });

  it("drops a queued turn that will never run", async () => {
    const queue = createTurnQueue(1);
    await queue.acquire("a");
    const abandoned = vi.fn();
    queue.acquire("closed").then(abandoned);

    queue.cancel("closed");
    await settle();

    expect(queue.waiting()).toEqual([]);
    expect(abandoned).not.toHaveBeenCalled();
  });

  it("cancelling something that is not queued changes nothing", async () => {
    const queue = createTurnQueue(1);
    const slot = await queue.acquire("a");

    queue.cancel("a");

    expect(queue.running()).toBe(1);
    slot();
  });

  it("notifies subscribers when the queue changes", async () => {
    const queue = createTurnQueue(1);
    const listener = vi.fn();
    const unsubscribe = queue.subscribe(listener);

    const slot = await queue.acquire("a");
    expect(listener).toHaveBeenCalled();

    listener.mockClear();
    unsubscribe();
    slot();
    await settle();

    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps the limit under a burst larger than it", async () => {
    const queue = createTurnQueue(3);
    const releases: (() => void)[] = [];
    for (let index = 0; index < 10; index += 1) {
      queue.acquire(`turn-${index}`).then((release) => releases.push(release));
    }
    await settle();

    expect(queue.running()).toBe(3);
    expect(queue.waiting()).toHaveLength(7);

    // Draining one slot at a time never exceeds the limit.
    while (releases.length > 0) {
      releases.shift()!();
      await settle();
      expect(queue.running()).toBeLessThanOrEqual(3);
    }
    expect(queue.waiting()).toEqual([]);
  });
});
