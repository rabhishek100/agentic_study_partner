"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { BrandMark } from "@/components/brand-mark";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Panel,
  PanelContent,
  PanelDescription,
  PanelHeader,
  PanelTitle,
} from "@/components/ui/panel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  demoLoginConfigured,
  signInToDemo,
  supabase,
  supabaseConfigured,
} from "@/lib/supabase";

type Mode = "sign-in" | "sign-up";
type PendingAction = "credentials" | "demo" | null;

export function AuthGate() {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const busy = pending !== null;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !supabase) return;
    setPending("credentials");
    setError("");
    setNotice("");
    try {
      const credentials = { email: email.trim(), password };
      if (mode === "sign-in") {
        const { error: authError } =
          await supabase.auth.signInWithPassword(credentials);
        if (authError) setError(authError.message);
        return;
      }

      const { data, error: authError } = await supabase.auth.signUp(credentials);
      if (authError) {
        setError(authError.message);
        return;
      }
      // A project that requires email confirmation returns a user with no
      // session. Without this the form would just sit there looking broken.
      if (!data.session) {
        setNotice(
          `Check ${credentials.email} for a confirmation link, then sign in.`,
        );
        setMode("sign-in");
      }
    } catch {
      setError("Could not reach the sign-in service.");
    } finally {
      setPending(null);
    }
  }

  async function openDemo() {
    if (busy || !demoLoginConfigured) return;
    setPending("demo");
    setError("");
    setNotice("");
    try {
      const { error: authError } = await signInToDemo();
      if (authError) setError(authError.message);
    } catch {
      setError("Could not reach the demo sign-in service.");
    } finally {
      setPending(null);
    }
  }

  if (!supabaseConfigured) {
    return (
      <Panel className="w-full max-w-md">
        <PanelHeader>
          <PanelTitle>Sign-in is not configured</PanelTitle>
          <PanelDescription>
            Set <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>,
            then restart the app.
          </PanelDescription>
        </PanelHeader>
      </Panel>
    );
  }

  return (
    <Panel className="w-full max-w-md">
      <PanelHeader>
        <BrandMark className="mb-3" />
        <PanelTitle className="font-serif text-xl font-medium">
          {mode === "sign-in"
            ? "Sign in to your library"
            : "Create your library"}
        </PanelTitle>
        <PanelDescription>
          Your books, uploads, and conversations are private to your account.
        </PanelDescription>
      </PanelHeader>

      <PanelContent className="space-y-4">
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="auth-email">Email</Label>
            <Input
              id="auth-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="auth-password">Password</Label>
            <Input
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
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertTitle>Sign-in failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {notice && (
            <Alert>
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          )}

          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {pending === "credentials" && (
              <Loader2 className="animate-spin" aria-hidden />
            )}
            {pending === "credentials"
              ? "Working…"
              : mode === "sign-in"
                ? "Sign in"
                : "Create account"}
          </Button>
        </form>

        {mode === "sign-in" && demoLoginConfigured && (
          <div className="space-y-3">
            <div className="flex items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-border" />
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                or
              </span>
              <span className="h-px flex-1 bg-border" />
            </div>
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="w-full"
              onClick={openDemo}
              disabled={busy}
            >
              {pending === "demo" ? "Opening demo…" : "Explore the demo"}
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              Opens a shared guest workspace. Its activity may be reset.
            </p>
          </div>
        )}

        <Button
          type="button"
          variant="link"
          className="w-full"
          onClick={() => {
            setMode(mode === "sign-in" ? "sign-up" : "sign-in");
            setError("");
            setNotice("");
          }}
        >
          {mode === "sign-in"
            ? "New here? Create an account"
            : "Already have an account? Sign in"}
        </Button>
      </PanelContent>
    </Panel>
  );
}
