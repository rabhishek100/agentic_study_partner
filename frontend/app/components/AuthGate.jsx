"use client";

import { useEffect, useState } from "react";

import { supabase, supabaseConfigured } from "../../lib/supabase";

export function useSession() {
  const [session, setSession] = useState(null);
  const [sessionLoading, setSessionLoading] = useState(true);

  useEffect(() => {
    if (!supabase) {
      setSessionLoading(false);
      return undefined;
    }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setSessionLoading(false);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => subscription.unsubscribe();
  }, []);

  return { session, sessionLoading };
}

export function signOut() {
  return supabase?.auth.signOut();
}

export default function AuthGate() {
  const [mode, setMode] = useState("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const credentials = { email: email.trim(), password };
      const { error: authError } =
        mode === "sign-in"
          ? await supabase.auth.signInWithPassword(credentials)
          : await supabase.auth.signUp(credentials);
      if (authError) setError(authError.message);
    } catch {
      setError("Could not reach the sign-in service.");
    } finally {
      setBusy(false);
    }
  }

  if (!supabaseConfigured) {
    return (
      <div className="auth-card" role="alert">
        <h2>Sign-in is not configured</h2>
        <p>
          Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
          <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>, then restart the app.
        </p>
      </div>
    );
  }

  return (
    <div className="auth-card">
      <p className="welcome-mark">ASP</p>
      <h2>{mode === "sign-in" ? "Sign in to your library" : "Create your library"}</h2>
      <p className="auth-note">
        Your books, uploads, and conversations are private to your account.
      </p>

      <form onSubmit={submit}>
        <label htmlFor="auth-email">Email</label>
        <input
          id="auth-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <label htmlFor="auth-password">Password</label>
        <input
          id="auth-password"
          type="password"
          autoComplete={
            mode === "sign-in" ? "current-password" : "new-password"
          }
          required
          minLength={8}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        {error && <div className="error">{error}</div>}

        <button className="send" type="submit" disabled={busy}>
          {busy
            ? "Working…"
            : mode === "sign-in"
              ? "Sign in"
              : "Create account"}
        </button>
      </form>

      <button
        className="auth-switch"
        type="button"
        onClick={() => {
          setMode(mode === "sign-in" ? "sign-up" : "sign-in");
          setError("");
        }}
      >
        {mode === "sign-in"
          ? "New here? Create an account"
          : "Already have an account? Sign in"}
      </button>
    </div>
  );
}
