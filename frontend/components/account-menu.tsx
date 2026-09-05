"use client";

import { LogOut, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { signOut } from "@/hooks/use-session";

/**
 * Who is signed in, and how to stop being.
 *
 * This was thirteen copies of the same dropdown, one per route, each spending a
 * `max-w-44` slice of the masthead on an email address. On a phone that slice
 * was close to half the header, which is what squeezed the wordmark and the
 * status line down to a couple of clipped glyphs. Below `compact` the address
 * collapses to an icon and moves into the menu, where there is room for it.
 */
export function AccountMenu({ email }: { email?: string | null }) {
  const address = email ?? "your account";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="max-w-44 shrink-0 px-2 md:px-3"
          aria-label={`Account: ${address}`}
        >
          <UserRound aria-hidden className="md:hidden" />
          <span className="hidden truncate md:inline">{address}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel className="font-normal text-muted-foreground">
          <span className="block max-w-56 truncate">Signed in as {address}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => signOut()}>
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
