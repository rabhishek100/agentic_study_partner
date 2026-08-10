import type { PythonExecutionResult } from "@/lib/interview-types";

const STARTUP_TIMEOUT_MS = 30_000;
export const PYTHON_RUN_TIMEOUT_MS = 6_000;

type WorkerMessage =
  | { type: "ready" }
  | { type: "startup_error"; error: string }
  | ({ type: "result"; id: string } & PythonExecutionResult);

export interface PythonRunRequest {
  code: string;
  visibleTests: string;
  scratchTests: string;
}

type WorkerFactory = () => Worker;

export class PythonRunner {
  private worker: Worker | null = null;
  private ready: Promise<void> | null = null;
  private resolveReady: (() => void) | null = null;
  private rejectReady: ((reason: Error) => void) | null = null;
  private pending: {
    id: string;
    resolve: (result: PythonExecutionResult) => void;
    timer: number;
  } | null = null;

  constructor(
    private readonly workerFactory: WorkerFactory = () =>
      new Worker("/workers/python-runner.mjs", { type: "module" }),
  ) {}

  prepare(): Promise<void> {
    if (this.ready) return this.ready;
    this.worker = this.workerFactory();
    this.worker.addEventListener("message", this.onMessage);
    this.worker.addEventListener("error", this.onWorkerError);
    this.ready = new Promise<void>((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
      window.setTimeout(
        () => reject(new Error("The local Python runtime did not finish loading.")),
        STARTUP_TIMEOUT_MS,
      );
    });
    return this.ready;
  }

  async run(request: PythonRunRequest): Promise<PythonExecutionResult> {
    await this.prepare();
    if (!this.worker) throw new Error("The local Python runtime is unavailable.");
    if (this.pending) throw new Error("Python is already running.");
    const id = crypto.randomUUID();
    return new Promise<PythonExecutionResult>((resolve) => {
      const timer = window.setTimeout(() => {
        this.reset();
        resolve({
          status: "timed_out",
          stdout: "",
          error: `Execution exceeded ${PYTHON_RUN_TIMEOUT_MS / 1000} seconds. The Python worker was reset.`,
          duration_ms: PYTHON_RUN_TIMEOUT_MS,
          official_tests_passed: null,
          scratch_tests_passed: null,
        });
      }, PYTHON_RUN_TIMEOUT_MS);
      this.pending = { id, resolve, timer };
      this.worker?.postMessage({ type: "run", id, ...request });
    });
  }

  reset(): void {
    if (this.pending) window.clearTimeout(this.pending.timer);
    this.pending = null;
    if (this.worker) {
      this.worker.removeEventListener("message", this.onMessage);
      this.worker.removeEventListener("error", this.onWorkerError);
      this.worker.terminate();
    }
    this.worker = null;
    this.ready = null;
    this.resolveReady = null;
    this.rejectReady = null;
  }

  private onMessage = (event: MessageEvent<WorkerMessage>) => {
    const message = event.data;
    if (message.type === "ready") {
      this.resolveReady?.();
      this.resolveReady = null;
      this.rejectReady = null;
      return;
    }
    if (message.type === "startup_error") {
      this.rejectReady?.(new Error(message.error));
      this.reset();
      return;
    }
    if (!this.pending || message.id !== this.pending.id) return;
    window.clearTimeout(this.pending.timer);
    const { type: _type, id: _id, ...result } = message;
    this.pending.resolve(result);
    this.pending = null;
  };

  private onWorkerError = () => {
    const error = new Error("The local Python worker stopped unexpectedly.");
    this.rejectReady?.(error);
    if (this.pending) {
      window.clearTimeout(this.pending.timer);
      this.pending.resolve({
        status: "error",
        stdout: "",
        error: error.message,
        duration_ms: 0,
        official_tests_passed: null,
        scratch_tests_passed: null,
      });
    }
    this.reset();
  };
}
