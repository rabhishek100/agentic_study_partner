const backendUrl = (
  process.env.BACKEND_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Every other local address in this project is 127.0.0.1 — .env, the README,
  // the API origin — but Next 16 serves dev assets only to origins it has been
  // told about, and answers 403 to the rest. Opening the dev server at
  // 127.0.0.1 therefore loaded the HTML and then failed every chunk, leaving a
  // skeleton that never resolved and no error a reader could act on. Only
  // localhost worked, which is not something anyone would guess.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Next's gzip layer buffers proxied responses, which defeats SSE
  // token-by-token delivery on /api/chat/stream (see next.config rewrite
  // below) by holding the whole reply until the stream ends.
  compress: false,
  ...(process.env.NEXT_OUTPUT === "standalone"
    ? { output: "standalone" }
    : {}),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
