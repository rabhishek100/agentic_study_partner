import { describe, expect, it } from "vitest";

import { PythonRunner } from "@/lib/python-runner";

class FakeWorker extends EventTarget {
  messages: unknown[] = [];
  terminated = false;

  postMessage(value: unknown) {
    this.messages.push(value);
  }

  terminate() {
    this.terminated = true;
  }
}

describe("browser-local Python runner", () => {
  it("waits for Pyodide readiness and returns structured test output", async () => {
    const worker = new FakeWorker();
    const runner = new PythonRunner(() => worker as unknown as Worker);
    const ready = runner.prepare();
    worker.dispatchEvent(new MessageEvent("message", { data: { type: "ready" } }));
    await ready;

    const resultPromise = runner.run({
      code: "def double(value): return value * 2",
      visibleTests: "assert double(3) == 6",
      scratchTests: "assert double(-2) == -4",
    });
    await Promise.resolve();
    const request = worker.messages[0] as { id: string };
    worker.dispatchEvent(new MessageEvent("message", {
      data: {
        type: "result",
        id: request.id,
        status: "passed",
        stdout: "",
        error: "",
        duration_ms: 9,
        official_tests_passed: true,
        scratch_tests_passed: true,
      },
    }));

    await expect(resultPromise).resolves.toMatchObject({
      status: "passed",
      official_tests_passed: true,
      scratch_tests_passed: true,
    });
    expect(worker.messages[0]).toMatchObject({
      type: "run",
      visibleTests: "assert double(3) == 6",
    });
    runner.reset();
    expect(worker.terminated).toBe(true);
  });
});
