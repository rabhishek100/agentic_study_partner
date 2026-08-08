"use client";

import { accessToken } from "./supabase";

export const API_BASE = "/api";

/**
 * Where binary uploads go.
 *
 * Same-origin requests are proxied by Next, which buffers the whole request
 * body in memory and caps it at 10MB. A 220MB lecture was silently truncated
 * there and the connection reset, so the upload never reached the API at all.
 * Bytes therefore address the API directly when an origin is configured;
 * ordinary JSON stays same-origin, where the proxy is an asset.
 */
export const API_ORIGIN = (
  process.env.NEXT_PUBLIC_API_ORIGIN ?? ""
).replace(/\/$/, "");

export function uploadUrl(path: string): string {
  const suffix = path.startsWith("/api") ? path : `${API_BASE}${path}`;
  return `${API_ORIGIN}${suffix}`;
}

/** The server's own wording for a failure, for callers that fetch directly. */
export async function errorDetail(response: Response): Promise<string> {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw) as {
      detail?: string | { message?: string };
    };
    if (typeof parsed.detail === "string") return parsed.detail;
    // Ingestion errors carry {code, message}; the message is written for users.
    if (parsed.detail?.message) return parsed.detail.message;
  } catch {
    // fall through to the generic message
  }
  return `Request failed (${response.status})`;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiFetch<T>(
  path: string,
  { headers, ...options }: RequestInit = {},
): Promise<T> {
  const token = await accessToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status);
  }
  // 204 carries no body; the caller types these as void.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
