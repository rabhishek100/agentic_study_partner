"use client";

import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The setup form's shared grammar.
 *
 * Two decisions are load-bearing here and both are corrections of what the
 * screen used to do.
 *
 * **Selection is a native radio, not `aria-pressed` on a button.** Six duration
 * buttons meant six tab stops and no group semantics; a radio group is one tab
 * stop, arrow-navigable, and announces "3 of 6". The input is visually hidden
 * and the label carries the appearance, so the keyboard and screen-reader
 * behaviour is the platform's rather than ours to re-implement.
 *
 * **One selection grammar for the whole form.** Cards for choices that need a
 * sentence of explanation, chips for choices whose label is self-evident. The
 * screen previously mixed a segmented control, filled chips, bordered cards,
 * and two `Select`s for four decisions of the same kind.
 */

export interface SetupChoice<T extends string> {
  value: T;
  label: string;
  hint?: string;
  icon?: LucideIcon;
  disabled?: boolean;
}

/**
 * Selected is `wash` ground plus a 3px marker, never the border: the design
 * system forbids a control border on the wash because it cannot reach 3:1
 * against it without becoming a highlight.
 */
const SELECTED_CARD = "border-transparent bg-wash";
const UNSELECTED_CARD = "border-border bg-surface hover:bg-surface-hover";
const FOCUS_WITHIN =
  "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-action";

/**
 * A numbered step. The count is the point — it says how much is left.
 *
 * The number sits inline with the title rather than in a gutter column. A
 * gutter costs 40px of every step's width on the one axis this screen is short
 * of, and it bought nothing the numeral itself was not already saying.
 */
export function SetupStep({
  index,
  title,
  hint,
  badge,
  children,
  className,
}: {
  index: number;
  title: string;
  hint?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("grid content-start gap-3", className)}>
      <div className="grid gap-1">
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className="flex size-7 shrink-0 items-center justify-center rounded-full border border-divider text-xs font-medium tabular-nums text-muted-foreground"
          >
            {index}
          </span>
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <h2 className="text-lg leading-snug font-semibold">{title}</h2>
            {badge}
          </div>
        </div>
        {hint ? (
          <p className="text-xs leading-5 text-muted-foreground sm:ps-10">{hint}</p>
        ) : null}
      </div>
      <div className="sm:ps-10">{children}</div>
    </section>
  );
}

/**
 * How a labelled group spends the width it is given.
 *
 * `stack` puts the label above the control; `row` puts it in a fixed column
 * beside it. `row` is the default for the settings groups because the label is
 * two words and the control is a row of chips — stacking them spent a whole
 * line of height on "Maximum time" and left the space beside it empty. Below
 * `sm` it collapses back to `stack`, where the width is not there to spend.
 */
export type GroupLayout = "stack" | "row";

/**
 * The label column. Wide enough for the longest legend and no wider — the six
 * duration chips need 490px to stay on one line and the control column had
 * 489, so every pixel spent here came straight out of that row.
 */
const ROW_GRID = "sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-x-4";

/**
 * The visible label is `aria-hidden` and the accessible name comes from an
 * `sr-only` legend, because a `<legend>` is laid out by the fieldset rather
 * than by its grid and cannot be placed in a column. The hint stays exposed and
 * is bound as the group's description, so nothing is announced twice and
 * nothing is lost.
 */
function GroupLabel({ legend }: { legend: string }) {
  return (
    <p aria-hidden className="text-sm font-medium sm:pt-2">
      {legend}
    </p>
  );
}

/**
 * Under the control, never in the label column. A sentence set in a 136px
 * column runs to five lines and makes the row taller than the control it
 * describes — which is the opposite of what putting the label beside the
 * control was for.
 */
function GroupHint({
  hint,
  hintId,
  layout,
}: {
  hint: string;
  hintId: string;
  layout: GroupLayout;
}) {
  return (
    <p
      id={hintId}
      className={cn(
        "text-xs leading-5 text-muted-foreground",
        layout === "row" && "sm:col-start-2",
      )}
    >
      {hint}
    </p>
  );
}

/**
 * The same label-beside-control row, for controls that are not a radio group.
 *
 * The visible text names the row; each control inside carries its own label, so
 * this one is context rather than the accessible name. That is what lets two
 * selects share a row without two more label lines above them.
 */
export function SetupField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("grid min-w-0 gap-2", ROW_GRID)}>
      <p className="text-sm font-medium sm:pt-2">{label}</p>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/**
 * Choices that need a sentence to be chosen well — source kind, target level,
 * feedback mode, interview format. The hint is not decoration: "Realistic" and
 * "Guided" are indistinguishable without it, and they were previously two lines
 * in a closed `Select`.
 */
export function OptionCards<T extends string>({
  name,
  legend,
  showLegend = false,
  layout = "row",
  hint,
  value,
  choices,
  onChange,
  disabled = false,
  className,
}: {
  name: string;
  legend: string;
  /** Visible when the group needs naming; `sr-only` when the step already does. */
  showLegend?: boolean;
  layout?: GroupLayout;
  hint?: string;
  value: T | "";
  choices: ReadonlyArray<SetupChoice<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}) {
  const hintId = `${name}-group-hint`;
  return (
    <fieldset
      disabled={disabled}
      aria-describedby={showLegend && hint ? hintId : undefined}
      className={cn(
        "grid min-w-0 gap-2",
        showLegend && layout === "row" && ROW_GRID,
      )}
    >
      <legend className="sr-only">{legend}</legend>
      {showLegend ? <GroupLabel legend={legend} /> : null}
      <div className={cn("grid gap-2", className)}>
        {choices.map((choice) => {
          const selected = choice.value === value;
          const Icon = choice.icon;
          const unavailable = disabled || choice.disabled;
          const choiceHintId = `${name}-${choice.value}-hint`;
          return (
            <label
              key={choice.value}
              className={cn(
                "relative grid min-w-0 gap-1 rounded-md border p-3 transition-colors",
                FOCUS_WITHIN,
                selected ? SELECTED_CARD : UNSELECTED_CARD,
                unavailable
                  ? "cursor-not-allowed border-divider bg-disabled-surface text-disabled-foreground hover:bg-disabled-surface"
                  : "cursor-pointer",
              )}
            >
              {/*
                The name is stated rather than inherited from the label's text.
                A wrapping label concatenates every descendant, so the option
                would otherwise announce as "Mid-levelDepth, applications, and
                trade-offs." — the hint belongs in the description, where it is
                read after the name instead of fused to it.
              */}
              <input
                type="radio"
                className="sr-only"
                name={name}
                value={choice.value}
                aria-label={choice.label}
                aria-describedby={choice.hint ? choiceHintId : undefined}
                checked={selected}
                disabled={choice.disabled}
                onChange={() => onChange(choice.value)}
              />
              {selected ? (
                <span
                  aria-hidden
                  className="absolute inset-y-2 start-0 w-[3px] rounded-full bg-action"
                />
              ) : null}
              <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                {Icon ? <Icon aria-hidden className="size-4 shrink-0" /> : null}
                <span className="min-w-0 truncate">{choice.label}</span>
              </span>
              {choice.hint ? (
                <span
                  id={choiceHintId}
                  className={cn(
                    "text-xs leading-5",
                    unavailable ? "text-disabled-foreground" : "text-muted-foreground",
                  )}
                >
                  {choice.hint}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
      {showLegend && hint ? (
        <GroupHint hint={hint} hintId={hintId} layout={layout} />
      ) : null}
    </fieldset>
  );
}

/**
 * Choices whose label already says everything — the duration ceiling. Selected
 * is the action fill, which inverts the text rather than tinting it, so the
 * choice survives greyscale and deuteranopia.
 */
export function ChipChoices<T extends string>({
  name,
  legend,
  showLegend = false,
  layout = "row",
  hint,
  value,
  choices,
  onChange,
  disabled = false,
  className,
}: {
  name: string;
  legend: string;
  showLegend?: boolean;
  layout?: GroupLayout;
  hint?: string;
  value: T;
  choices: ReadonlyArray<SetupChoice<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}) {
  const hintId = `${name}-group-hint`;
  return (
    <fieldset
      disabled={disabled}
      aria-describedby={showLegend && hint ? hintId : undefined}
      className={cn(
        "grid min-w-0 gap-2",
        showLegend && layout === "row" && ROW_GRID,
      )}
    >
      <legend className="sr-only">{legend}</legend>
      {showLegend ? <GroupLabel legend={legend} /> : null}
      {/*
        Wrap flow, not a column grid. Six equal columns forced "1½ hours" to
        break across two lines and made a row of chips as tall as a row of
        cards; a chip should be exactly as wide as its own label and no wider.
      */}
      <div className={cn("flex flex-wrap gap-2", className)}>
        {choices.map((choice) => {
          const selected = choice.value === value;
          return (
            <label
              key={choice.value}
              className={cn(
                "flex min-h-11 min-w-16 items-center justify-center rounded-md border px-3 py-2 text-sm whitespace-nowrap transition-colors",
                FOCUS_WITHIN,
                selected
                  ? "border-transparent bg-action font-medium text-action-on"
                  : UNSELECTED_CARD,
                disabled
                  ? "cursor-not-allowed border-divider bg-disabled-surface text-disabled-foreground hover:bg-disabled-surface"
                  : "cursor-pointer",
              )}
            >
              <input
                type="radio"
                className="sr-only"
                name={name}
                value={choice.value}
                aria-label={choice.label}
                checked={selected}
                onChange={() => onChange(choice.value)}
              />
              {choice.label}
            </label>
          );
        })}
      </div>
      {showLegend && hint ? (
        <GroupHint hint={hint} hintId={hintId} layout={layout} />
      ) : null}
    </fieldset>
  );
}

/**
 * What the reader has chosen, restated where they are about to commit to it.
 * A confirmation step is worth its space precisely because the choices are made
 * elsewhere.
 */
export function SummaryRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-xs font-medium">{value}</dd>
    </div>
  );
}
