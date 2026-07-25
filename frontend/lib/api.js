"use client";

import { accessToken } from "./supabase";

const API_BASE = "/api";

async function detail(response) {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.detail === "string") return parsed.detail;
    // Ingestion errors carry {code, message}; the message is written for users.
    if (parsed.detail?.message) return parsed.detail.message;
  } catch {
    // fall through to the generic message
  }
  return `Request failed (${response.status})`;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch(path, { headers, ...options } = {}) {
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
  if (response.status === 204) return null;
  return response.json();
}
