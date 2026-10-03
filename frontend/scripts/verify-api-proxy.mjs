// Exercise the actual Next rewrite server past its former 30-second timeout.
// Local fixture only: no production credentials, database or paid provider calls.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const listen = async (server) => {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return server.address().port;
};
const upstream = createServer(async (req, res) => {
  if (req.url === "/api/proxy-ready") {
    res.end("ready");
    return;
  }
  if (req.method !== "POST" || req.url !== "/api/ideal-interviews") {
    res.writeHead(404).end();
    return;
  }
  let body = "";
  for await (const chunk of req) body += chunk;
  assert.equal(JSON.parse(body).book_id, 1);
  assert.equal(req.headers.authorization, "Bearer local-fixture");
  await wait(35_000);
  res.writeHead(201, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ flow_id: "local-proxy-regression" }));
});
const upstreamPort = await listen(upstream);
const reservation = createServer();
const webPort = await listen(reservation);
await new Promise((resolve) => reservation.close(resolve));
const web = spawn(process.execPath,
  ["node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1", "--port", String(webPort)],
  { env: { ...process.env, BACKEND_URL: `http://127.0.0.1:${upstreamPort}`, NEXT_TELEMETRY_DISABLED: "1" },
    stdio: ["ignore", "pipe", "pipe"] });
let logs = "";
for (const stream of [web.stdout, web.stderr]) stream.on("data", (data) => {
  logs = (logs + data).slice(-5_000);
});
const base = `http://127.0.0.1:${webPort}`;
try {
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`${base}/api/proxy-ready`, { signal: AbortSignal.timeout(2_000) });
      ready = res.ok && await res.text() === "ready";
    } catch { /* The child is still starting. */ }
    if (ready) break;
    if (web.exitCode !== null) throw new Error(`Next exited: ${logs}`);
    await wait(500);
  }
  assert.ok(ready, `Next did not become ready: ${logs}`);
  const started = performance.now();
  const response = await fetch(`${base}/api/ideal-interviews`, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer local-fixture" },
    body: JSON.stringify({ book_id: 1 }), signal: AbortSignal.timeout(50_000),
  });
  assert.equal(response.status, 201, `Delayed rewrite failed: ${logs}`);
  assert.deepEqual(await response.json(), { flow_id: "local-proxy-regression" });
  assert.ok(performance.now() - started >= 35_000);
  console.log("PASS: real Next POST rewrite returned 201 after 35 seconds; body and auth preserved.");
} finally {
  web.kill("SIGTERM");
  upstream.closeAllConnections();
  upstream.close();
  await Promise.race([once(web, "exit"), wait(5_000)]);
  if (web.exitCode === null) web.kill("SIGKILL");
}
