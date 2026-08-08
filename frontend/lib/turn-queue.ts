/**
 * A limit on how many side-chat answers generate at once.
 *
 * Asking several questions in parallel is the point of the feature, so this is
 * a queue rather than a lock: nothing is refused, it just waits. The limit
 * exists because each turn is a retrieval pass plus a generation call, and ten
 * windows sending at once is ten simultaneous provider requests — a
 * rate-limit and cost spike with no upper bound.
 *
 * Deliberately a plain object rather than React state. Every window holds its
 * own chat hook, so the gate has to be shared outside the component tree;
 * subscribers exist only so a waiting window can say that it is waiting.
 */

export const DEFAULT_CONCURRENCY = 3;

export interface TurnQueue {
  /** Resolves when a slot is free. The caller must release it when done. */
  acquire(id: string): Promise<() => void>;
  /** Ids currently waiting for a slot, in the order they asked. */
  waiting(): readonly string[];
  running(): number;
  subscribe(listener: () => void): () => void;
  /** Drop a queued request that will never run, e.g. a closed window. */
  cancel(id: string): void;
}

export function createTurnQueue(limit = DEFAULT_CONCURRENCY): TurnQueue {
  let running = 0;
  const queue: { id: string; start: () => void }[] = [];
  const listeners = new Set<() => void>();

  const notify = () => listeners.forEach((listener) => listener());

  const pump = () => {
    while (running < limit && queue.length > 0) {
      const next = queue.shift()!;
      running += 1;
      next.start();
    }
    notify();
  };

  return {
    acquire(id) {
      return new Promise<() => void>((resolve) => {
        let released = false;
        const release = () => {
          // Guarded because a caller that releases twice would open a slot it
          // never held, which defeats the limit silently.
          if (released) return;
          released = true;
          running -= 1;
          pump();
        };
        queue.push({ id, start: () => resolve(release) });
        pump();
      });
    },
    waiting() {
      return queue.map((entry) => entry.id);
    },
    running() {
      return running;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    cancel(id) {
      const index = queue.findIndex((entry) => entry.id === id);
      if (index < 0) return;
      queue.splice(index, 1);
      notify();
    },
  };
}

/** The queue every side chat in this tab shares. */
export const sideChatQueue = createTurnQueue();
