"use client";

import { accessToken } from "./supabase";

export const API_BASE = "/api";

async function detail(response: Response): Promise<string> {
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
    throw new ApiError(await detail(response), response.status);
  }
  // 204 carries no body; the caller types these as void.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
