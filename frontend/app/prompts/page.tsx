"use client";

import { ArrowLeft, Loader2, LogOut } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { PromptStudio } from "@/components/prompts/prompt-studio";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { PROMPT_FIELDS } from "@/lib/prompt-profile";
import type {
  AnswerArchetype,
  ConversationDetail,
  PromptProfile,
  PromptSettingsResponse,
  ResponseDepth,
} from "@/lib/types";

const DEPTHS: readonly ResponseDepth[] = ["quick", "interview", "deep"];

/**
 * Where the studio was opened from, and for which conversation.
 *
 * Read from `window.location` rather than `useSearchParams`, which would force
 * the page under a Suspense boundary for three optional parameters. `from` is
 * checked before it is used as an href: a value that is not a single-slash
 * path is somebody else's origin, and the back link is not a redirector.
 */
interface StudioContext {
  conversationId: string | null;
  depth: ResponseDepth;
  from: string;
}

function readContext(): StudioContext {
  const params = new URLSearchParams(window.location.search);
  const depth = params.get("depth") as ResponseDepth | null;
  const from = params.get("from") ?? "";
  return {
    conversationId: params.get("conversation"),
    depth: depth && DEPTHS.includes(depth) ? depth : "interview",
    from: /^\/(?!\/)/.test(from) ? from : "/",
  };
}

export default function PromptsPage() {
  const { session, sessionLoading } = useSession();
  const [context, setContext] = useState<StudioContext>({
    conversationId: null,
    depth: "interview",
    from: "/",
  });
  const [settings, setSettings] = useState<PromptSettingsResponse | null>(null);
  const [initialProfile, setInitialProfile] = useState<PromptProfile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => setContext(readContext()), []);

  const load = useCallback(async (conversationId: string | null) => {
    try {
      const [account, conversation] = await Promise.all([
        apiFetch<PromptSettingsResponse>("/prompt-settings"),
        conversationId
          ? apiFetch<ConversationDetail>(`/conversations/${conversationId}`)
          : Promise.resolve(null),
      ]);
      setSettings(account);
      // A conversation carries its own profile once one has been applied to it;
      // opening the studio on the account default would quietly discard it.
      setInitialProfile(conversation?.prompt_profile ?? account.profile);
      setLoadError("");
    } catch (failure) {
      setLoadError((failure as Error).message || "Could not load your prompts.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) void load(context.conversationId);
  }, [session, context.conversationId, load]);

  const compile = useCallback(
    (profile: PromptProfile, archetype: AnswerArchetype, depth: ResponseDepth) =>
      apiFetch<PromptSettingsResponse>("/prompt-settings/preview", {
        method: "POST",
        body: JSON.stringify({
          profile,
          answer_archetype: archetype,
          response_depth: depth,
        }),
      }),
    [],
  );

  const saveDefault = useCallback(
    (profile: PromptProfile) =>
      apiFetch<PromptSettingsResponse>("/prompt-settings", {
        method: "PATCH",
        body: JSON.stringify({ profile }),
      }),
    [],
  );

  const applyToConversation = useCallback(
    async (profile: PromptProfile) => {
      if (!context.conversationId) return;
      await apiFetch(`/conversations/${context.conversationId}`, {
        method: "PATCH",
        body: JSON.stringify({ prompt_profile: profile }),
      });
    },
    [context.conversationId],
  );

  if (sessionLoading) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <div
          className="flex max-w-sm items-start gap-3 rounded-lg border border-divider bg-surface p-6"
          role="status"
          aria-live="polite"
        >
          <Loader2
            aria-hidden
            className="mt-1 size-5 shrink-0 animate-spin text-action motion-reduce:animate-none"
          />
          <div>
            <p className="font-medium">Checking your session</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Verifying sign-in before loading your prompts.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="relative grid h-dvh place-items-center p-6">
        <div className="absolute right-3 top-3">
          <ThemeToggle />
        </div>
        <AuthGate />
      </div>
    );
  }

  return (
    <AppShell
      nav={<SectionNav active="prompts" />}
      status={<span>Prompt studio</span>}
      account={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="max-w-44">
              <span className="truncate">{session.user.email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="font-normal text-muted-foreground">
              Signed in
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => signOut()}>
              <LogOut aria-hidden />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
      rail={
        <nav
          aria-label="Prompt studio contents"
          className="flex h-full flex-col gap-6 overflow-y-auto p-4"
        >
          <div className="sm:hidden">
            <SectionNav active="prompts" />
          </div>

          <Button variant="outline" size="sm" className="justify-start" asChild>
            <Link href={context.from}>
              <ArrowLeft aria-hidden />
              Back to the conversation
            </Link>
          </Button>

          <div className="grid gap-1">
            <p className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">
              On this page
            </p>
            <ul className="grid gap-1">
              {PROMPT_FIELDS.map((field) => (
                <li key={field.key}>
                  <a
                    href={`#${field.anchor}`}
                    className="block rounded-md px-2 py-2 text-sm text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground"
                  >
                    {field.label}
                  </a>
                </li>
              ))}
              <li>
                <a
                  href="#prompt-preview"
                  className="block rounded-md px-2 py-2 text-sm text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground"
                >
                  Compiled preview
                </a>
              </li>
            </ul>
          </div>
        </nav>
      }
    >
      <PromptStudio
        loaded={loaded}
        loadError={loadError}
        settings={settings}
        initialProfile={initialProfile}
        conversationId={context.conversationId}
        responseDepth={context.depth}
        compile={compile}
        saveDefault={saveDefault}
        applyToConversation={applyToConversation}
      />
    </AppShell>
  );
}
