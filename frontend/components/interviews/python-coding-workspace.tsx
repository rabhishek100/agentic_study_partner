"use client";

import { python } from "@codemirror/lang-python";
import { EditorView } from "@codemirror/view";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { Braces, CircleHelp, Loader2, Play, RotateCcw, TestTube2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  PythonCodingExercise,
  PythonExecutionResult,
} from "@/lib/interview-types";
import { PythonRunner } from "@/lib/python-runner";
import { cn } from "@/lib/utils";

const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), {
  ssr: false,
  loading: () => <div className="h-72 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />,
});

const NOT_RUN: PythonExecutionResult = {
  status: "not_run",
  stdout: "",
  error: "",
  duration_ms: 0,
  official_tests_passed: null,
  scratch_tests_passed: null,
};

export function emptyPythonExecution(): PythonExecutionResult {
  return { ...NOT_RUN };
}

interface PythonCodingWorkspaceProps {
  exercise: PythonCodingExercise;
  code: string;
  scratchTests: string;
  execution: PythonExecutionResult;
  disabled?: boolean;
  hintsUsed: number;
  availableHints: number;
  hintLoading?: boolean;
  onCodeChange: (value: string) => void;
  onScratchTestsChange: (value: string) => void;
  onExecution: (value: PythonExecutionResult) => void;
  onRevealHint?: () => void;
}

export function PythonCodingWorkspace({
  exercise,
  code,
  scratchTests,
  execution,
  disabled = false,
  hintsUsed,
  availableHints,
  hintLoading = false,
  onCodeChange,
  onScratchTestsChange,
  onExecution,
  onRevealHint,
}: PythonCodingWorkspaceProps) {
  const { resolvedTheme } = useTheme();
  const runnerRef = useRef<PythonRunner | null>(null);
  const [runtime, setRuntime] = useState<"loading" | "ready" | "error">("loading");
  const [runtimeError, setRuntimeError] = useState("");
  const [running, setRunning] = useState(false);
  const extensions = useMemo(
    () => [
      python(),
      EditorView.contentAttributes.of({
        "aria-label": "Python answer editor",
        "aria-describedby": "python-editor-help",
      }),
      EditorView.lineWrapping,
    ],
    [],
  );
  const scratchExtensions = useMemo(
    () => [
      python(),
      EditorView.contentAttributes.of({
        "aria-label": "Scratch Python tests editor",
      }),
      EditorView.lineWrapping,
    ],
    [],
  );

  useEffect(() => {
    const runner = new PythonRunner();
    runnerRef.current = runner;
    void runner.prepare().then(
      () => setRuntime("ready"),
      (failure: Error) => {
        setRuntime("error");
        setRuntimeError(failure.message);
      },
    );
    return () => runner.reset();
  }, []);

  async function runCode() {
    if (!runnerRef.current || running || disabled) return;
    setRunning(true);
    setRuntimeError("");
    try {
      const result = await runnerRef.current.run({
        code,
        visibleTests: exercise.visible_tests,
        scratchTests,
      });
      onExecution(result);
      setRuntime(result.status === "timed_out" ? "loading" : "ready");
      if (result.status === "timed_out") {
        void runnerRef.current.prepare().then(
          () => setRuntime("ready"),
          (failure: Error) => {
            setRuntime("error");
            setRuntimeError(failure.message);
          },
        );
      }
    } catch (failure) {
      setRuntime("error");
      setRuntimeError((failure as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const passed = execution.status === "passed";
  const failed = ["failed", "error", "timed_out"].includes(execution.status);

  return (
    <section className="overflow-hidden rounded-xl border border-action bg-card" aria-labelledby="coding-workspace-title">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-surface px-4 py-3">
        <div>
          <div className="flex items-center gap-2">
            <Braces aria-hidden className="size-4 text-primary" />
            <h3 id="coding-workspace-title" className="text-sm font-semibold">Python coding workspace</h3>
            <Badge variant="outline">Runs locally</Badge>
          </div>
          <p id="python-editor-help" className="mt-1 text-xs leading-5 text-muted-foreground">
            Complete the TODOs or refactor the scaffold. Your code stays in this browser until you submit the answer.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {onRevealHint && hintsUsed < availableHints ? (
            <Button type="button" variant="outline" size="sm" disabled={disabled || hintLoading} onClick={onRevealHint}>
              {hintLoading ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <CircleHelp aria-hidden />}
              {hintLoading ? "Revealing…" : hintsUsed ? "Reveal next hint" : "Reveal a hint"}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || code === exercise.starter_code}
            onClick={() => {
              if (window.confirm("Reset your code to the original scaffold?")) {
                onCodeChange(exercise.starter_code);
                onExecution(emptyPythonExecution());
              }
            }}
          >
            <RotateCcw aria-hidden />Reset scaffold
          </Button>
          <Button type="button" size="sm" disabled={disabled || running || runtime !== "ready" || !code.trim()} onClick={() => void runCode()}>
            {running || runtime === "loading" ? <Loader2 aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Play aria-hidden />}
            {running ? "Running…" : runtime === "loading" ? "Loading Python…" : "Run code"}
          </Button>
        </div>
      </div>

      <div className="grid min-w-0 gap-0 xl:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <div className="min-w-0 border-b xl:border-b-0 xl:border-r">
          <CodeMirror
            value={code}
            onChange={(value) => {
              onCodeChange(value);
              onExecution(emptyPythonExecution());
            }}
            extensions={extensions}
            theme={resolvedTheme === "dark" ? "dark" : "light"}
            minHeight="360px"
            maxHeight="620px"
            editable={!disabled}
            readOnly={disabled}
            indentWithTab={false}
            basicSetup={{ autocompletion: true, foldGutter: true, highlightActiveLine: true }}
            className="text-sm [&_.cm-editor]:min-w-0 [&_.cm-editor]:outline-none [&_.cm-focused]:ring-2 [&_.cm-focused]:ring-action"
          />
        </div>

        <div className="min-w-0">
          <div className="border-b p-4">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <TestTube2 aria-hidden className="size-4" />Visible tests
            </p>
            <pre className="mt-3 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 font-mono text-xs leading-5" aria-label="Read-only official Python tests">
              {exercise.visible_tests}
            </pre>
          </div>
          <div className="border-b p-4">
            <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Your scratch tests</label>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">Optional checks run after the visible tests and are saved with your answer.</p>
            <CodeMirror
              value={scratchTests}
              onChange={(value) => {
                onScratchTestsChange(value);
                onExecution(emptyPythonExecution());
              }}
              placeholder="# Add your own assert statements"
              extensions={scratchExtensions}
              theme={resolvedTheme === "dark" ? "dark" : "light"}
              minHeight="110px"
              maxHeight="220px"
              editable={!disabled}
              readOnly={disabled}
              indentWithTab={false}
              basicSetup={{ lineNumbers: false, foldGutter: false, autocompletion: true }}
              className="mt-2 overflow-hidden rounded-lg border text-xs [&_.cm-editor]:outline-none [&_.cm-focused]:ring-2 [&_.cm-focused]:ring-action"
            />
          </div>
          <div className="p-4" role="status" aria-live="polite" aria-atomic="true">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Run result</p>
              {execution.status !== "not_run" ? (
                <Badge className={cn(passed && "bg-positive", failed && "bg-destructive")}>
                  {passed ? "Tests passed" : execution.status.replace("_", " ")}
                </Badge>
              ) : null}
            </div>
            {runtimeError ? <Alert variant="destructive" className="mt-3"><AlertDescription>{runtimeError}</AlertDescription></Alert> : null}
            {execution.status === "not_run" && !runtimeError ? <p className="mt-3 text-xs leading-5 text-muted-foreground">Run the scaffold when you are ready. A failed run does not prevent submission.</p> : null}
            {execution.stdout ? <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 font-mono text-xs leading-5">{execution.stdout}</pre> : null}
            {execution.error ? <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-destructive-wash p-3 font-mono text-xs leading-5 text-destructive">{execution.error}</pre> : null}
            {execution.duration_ms ? <p className="mt-2 text-xs text-muted-foreground">Completed in {execution.duration_ms} ms</p> : null}
          </div>
        </div>
      </div>

      {exercise.hints.length ? (
        <div className="border-t bg-surface p-4" aria-label="Revealed coding hints">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">Revealed hints</p>
          <ol className="mt-2 space-y-2 text-sm leading-6">
            {exercise.hints.map((hint, index) => <li key={`${index}-${hint}`}><span className="font-medium">Hint {index + 1}:</span> {hint}</li>)}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
