"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const OPTIONS = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const;

/**
 * The three choices, as menu items.
 *
 * Exported so the account menu can carry them at `compact`, where the masthead
 * has no room for a control of its own: the reading route alone was asking for
 * eight, and the ones at the end of the row were rendered off the side of the
 * screen.
 */
export function ThemeMenuItems() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <>
      {OPTIONS.map(({ value, label, Icon }) => (
        <DropdownMenuItem key={value} onSelect={() => setTheme(value)}>
          <Icon aria-hidden />
          {label}
          {mounted && theme === value && (
            <span className="ml-auto text-xs text-muted-foreground">Active</span>
          )}
        </DropdownMenuItem>
      ))}
    </>
  );
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // The resolved theme is unknown until the client mounts; rendering the real
  // icon before then would mismatch the server HTML.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const active = OPTIONS.find((option) => option.value === theme) ?? OPTIONS[2];
  const Icon = mounted ? active.Icon : Monitor;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Change theme">
          <Icon aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {OPTIONS.map(({ value, label, Icon: OptionIcon }) => (
          <DropdownMenuItem key={value} onSelect={() => setTheme(value)}>
            <OptionIcon aria-hidden />
            {label}
            {mounted && theme === value && (
              <span className="ml-auto text-xs text-muted-foreground">
                Active
              </span>
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
