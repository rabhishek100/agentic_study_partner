"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { BrandLockup } from "@/components/brand-mark";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase, supabaseConfigured } from "@/lib/supabase";

type Mode = "sign-in" | "sign-up";

export function AuthGate() {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !supabase) return;
    setBusy(true);
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
      setBusy(false);
    }
  }

  if (!supabaseConfigured) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign-in is not configured</CardTitle>
          <CardDescription>
            Set <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>,
            then restart the app.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <BrandLockup className="-mb-2 w-64 max-w-full self-start" />
        <CardTitle className="font-heading text-xl font-medium">
          {mode === "sign-in"
            ? "Sign in to your library"
            : "Create your library"}
        </CardTitle>
        <CardDescription>
          The endless path to mastery. Your books, uploads, and conversations
          stay private to your account.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
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

          <div className="space-y-1.5">
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
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            {busy
              ? "Working…"
              : mode === "sign-in"
                ? "Sign in"
                : "Create account"}
          </Button>
        </form>

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
      </CardContent>
    </Card>
  );
}
