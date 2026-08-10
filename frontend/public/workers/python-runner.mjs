const PYODIDE_VERSION = "314.0.3";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let pyodide;
try {
  const { loadPyodide } = await import(`${PYODIDE_BASE}pyodide.mjs`);
  pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });
  self.postMessage({ type: "ready" });
} catch (error) {
  self.postMessage({
    type: "startup_error",
    error: error instanceof Error ? error.message : "Could not load Python.",
  });
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

self.addEventListener("message", async (event) => {
  if (event.data?.type !== "run" || !pyodide) return;
  const { id, code, visibleTests, scratchTests } = event.data;
  const started = performance.now();
  const stdout = [];
  const stderr = [];
  pyodide.setStdout({ batched: (value) => stdout.push(value) });
  pyodide.setStderr({ batched: (value) => stderr.push(value) });
  const dict = pyodide.globals.get("dict");
  const globals = dict();
  let status = "passed";
  let error = "";
  let officialTestsPassed = null;
  let scratchTestsPassed = null;
  try {
    await pyodide.runPythonAsync(code, { globals });
    try {
      await pyodide.runPythonAsync(visibleTests, { globals });
      officialTestsPassed = true;
    } catch (testError) {
      officialTestsPassed = false;
      status = "failed";
      error = errorText(testError);
    }
    if (scratchTests.trim()) {
      try {
        await pyodide.runPythonAsync(scratchTests, { globals });
        scratchTestsPassed = true;
      } catch (scratchError) {
        scratchTestsPassed = false;
        status = "failed";
        error = [error, `Scratch tests: ${errorText(scratchError)}`]
          .filter(Boolean)
          .join("\n");
      }
    }
  } catch (codeError) {
    status = "error";
    error = errorText(codeError);
  } finally {
    globals.destroy();
    dict.destroy();
  }
  self.postMessage({
    type: "result",
    id,
    status,
    stdout: [...stdout, ...stderr].join("\n").slice(0, 12_000),
    error: error.slice(0, 12_000),
    duration_ms: Math.round(performance.now() - started),
    official_tests_passed: officialTestsPassed,
    scratch_tests_passed: scratchTestsPassed,
  });
});
