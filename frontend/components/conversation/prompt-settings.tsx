"use client";

import { Eye, LockKeyhole, RotateCcw, Settings2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/api";
import type {
  AnswerArchetype,
  ConversationDetail,
  PromptProfile,
  PromptSettingsResponse,
  ResponseDepth,
} from "@/lib/types";

interface PromptSettingsProps {
  conversationId: string | null;
  responseDepth: ResponseDepth;
}

function PromptField({
  id,
  label,
  description,
  value,
  rows = 8,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  value: string;
  rows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <p className="text-xs text-muted-foreground">{description}</p>
      <Textarea
        id={id}
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="max-h-72 font-mono text-xs"
      />
    </div>
  );
}

export function PromptSettings({
  conversationId,
  responseDepth,
}: PromptSettingsProps) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<PromptSettingsResponse | null>(null);
  const [draft, setDraft] = useState<PromptProfile | null>(null);
  const [preview, setPreview] = useState<PromptSettingsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const buildPreview = useCallback(
    async (
      profile: PromptProfile,
      archetype: AnswerArchetype = "concept_explanation",
    ) =>
      apiFetch<PromptSettingsResponse>("/prompt-settings/preview", {
        method: "POST",
        body: JSON.stringify({
          profile,
          answer_archetype: archetype,
          response_depth: responseDepth,
        }),
      }),
    [responseDepth],
  );

  useEffect(() => {
    if (!open) return;
    let active = true;
    setBusy(true);
    setMessage("");
    Promise.all([
      apiFetch<PromptSettingsResponse>("/prompt-settings"),
      conversationId
        ? apiFetch<ConversationDetail>(`/conversations/${conversationId}`)
        : Promise.resolve(null),
    ])
      .then(async ([loaded, conversation]) => {
        const profile = conversation?.prompt_profile ?? loaded.profile;
        const compiled = await buildPreview(profile);
        if (!active) return;
        setSettings(loaded);
        setDraft(profile);
        setPreview(compiled);
      })
      .catch((error: Error) => {
        if (active) setMessage(error.message || "Could not load prompt settings.");
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [open, conversationId, buildPreview]);

  function update(field: keyof PromptProfile, value: string) {
    setDraft((current) => (current ? { ...current, [field]: value } : current));
    setMessage("");
  }

  async function saveDefault() {
    if (!draft) return;
    setBusy(true);
    setMessage("");
    try {
      const saved = await apiFetch<PromptSettingsResponse>("/prompt-settings", {
        method: "PATCH",
        body: JSON.stringify({ profile: draft }),
      });
      setSettings(saved);
      setPreview(saved);
      setMessage("Saved as your default for new conversations.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function applyConversation() {
    if (!draft || !conversationId) return;
    setBusy(true);
    setMessage("");
    try {
      await apiFetch(`/conversations/${conversationId}`, {
        method: "PATCH",
        body: JSON.stringify({ prompt_profile: draft }),
      });
      setPreview(await buildPreview(draft));
      setMessage("Applied to this conversation.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function previewDraft() {
    if (!draft) return;
    setBusy(true);
    setMessage("");
    try {
      setPreview(await buildPreview(draft));
      setMessage("Preview updated.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button type="button" variant="ghost" size="xs">
          <Settings2 aria-hidden />
          Prompt settings
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[min(94vw,48rem)] sm:max-w-3xl">
        <SheetHeader className="border-b">
          <SheetTitle>Interview prompt studio</SheetTitle>
          <SheetDescription>
            Edit interview behavior and answer templates. Grounding and citation
            rules remain visible but locked.
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 pb-6">
          {busy && !draft ? (
            <p className="text-sm text-muted-foreground" role="status">
              Loading prompt settings…
            </p>
          ) : draft && settings ? (
            <>
              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <LockKeyhole className="size-4 text-muted-foreground" aria-hidden />
                  <h3 className="font-medium">Required grounding rules</h3>
                </div>
                <p className="text-xs text-muted-foreground">
                  These protect citations and book-only answers, so they are
                  previewable but not editable.
                </p>
                <Textarea
                  aria-label="Required grounding rules"
                  readOnly
                  rows={10}
                  value={settings.locked_system_prompt}
                  className="max-h-64 bg-surface font-mono text-xs"
                />
              </section>

              <section className="space-y-5">
                <PromptField
                  id="interview-instructions"
                  label="Interview system instructions"
                  description="Controls tone, teaching behavior, and what makes an answer interview-ready."
                  value={draft.interview_instructions}
                  onChange={(value) => update("interview_instructions", value)}
                />
                <PromptField
                  id="concept-template"
                  label="Concept-answer template"
                  description="Used for questions such as “Explain logistic regression.”"
                  value={draft.concept_template}
                  onChange={(value) => update("concept_template", value)}
                />
                <PromptField
                  id="system-design-template"
                  label="System-design template"
                  description="Used for design and architecture interview questions."
                  value={draft.system_design_template}
                  onChange={(value) => update("system_design_template", value)}
                />
                <PromptField
                  id="chapter-review-template"
                  label="Chapter and section review template"
                  description="Extracts interview material from a complete canonical scope."
                  value={draft.chapter_review_template}
                  onChange={(value) => update("chapter_review_template", value)}
                />
                <PromptField
                  id="user-prompt-template"
                  label="User prompt template"
                  description={
                    "Must retain {question} and {evidence}. Optional: " +
                    "{answer_archetype}, {response_depth}, {request_context}."
                  }
                  value={draft.user_prompt_template}
                  rows={7}
                  onChange={(value) => update("user_prompt_template", value)}
                />
              </section>

              <section className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <h3 className="font-medium">Compiled prompt preview</h3>
                    <p className="text-xs text-muted-foreground">
                      Evidence is represented by a placeholder; it is inserted by
                      the server for each turn.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={previewDraft}
                    disabled={busy}
                  >
                    <Eye aria-hidden />
                    Preview changes
                  </Button>
                </div>
                <Label htmlFor="compiled-system">System message</Label>
                <Textarea
                  id="compiled-system"
                  readOnly
                  rows={12}
                  value={preview?.preview_system_prompt ?? ""}
                  className="max-h-72 bg-surface font-mono text-xs"
                />
                <Label htmlFor="compiled-user">User message</Label>
                <Textarea
                  id="compiled-user"
                  readOnly
                  rows={8}
                  value={preview?.preview_user_prompt ?? ""}
                  className="max-h-56 bg-surface font-mono text-xs"
                />
              </section>
            </>
          ) : null}

          {message && (
            <p className="text-xs text-muted-foreground" role="status">
              {message}
            </p>
          )}
        </div>

        <SheetFooter className="border-t sm:flex-row sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || !settings}
            onClick={() => {
              if (!settings) return;
              setDraft(settings.defaults);
              setMessage("Project defaults restored in the editor. Save to apply.");
            }}
          >
            <RotateCcw aria-hidden />
            Restore project defaults
          </Button>
          <div className="flex flex-wrap justify-end gap-2">
            {conversationId && (
              <Button
                type="button"
                variant="outline"
                disabled={busy || !draft}
                onClick={applyConversation}
              >
                Apply to conversation
              </Button>
            )}
            <Button
              type="button"
              disabled={busy || !draft}
              onClick={saveDefault}
            >
              Save as my default
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
