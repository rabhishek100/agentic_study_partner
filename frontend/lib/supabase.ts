"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const demoEmail = process.env.NEXT_PUBLIC_DEMO_LOGIN_EMAIL?.trim();
const demoPassword = process.env.NEXT_PUBLIC_DEMO_LOGIN_PASSWORD;

// The anon key is a publishable client credential; row-level security and the
// API's token verification are what actually protect data. The service-role
// key must never appear in this bundle.
export const supabaseConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient | null =
  url && anonKey
    ? createClient(url, anonKey, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false,
        },
      })
    : null;

export const supabaseUrl = url ?? "";

// These values are compiled into the browser bundle. They must identify only
// a disposable, tightly limited demo account; never configure a personal or
// operator account here.
export const demoLoginConfigured = Boolean(demoEmail && demoPassword);

export async function signInToDemo() {
  if (!supabase || !demoEmail || !demoPassword) {
    throw new Error("Demo sign-in is not configured.");
  }
  return supabase.auth.signInWithPassword({
    email: demoEmail,
    password: demoPassword,
  });
}

export async function accessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
