import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  signInToDemo: vi.fn(),
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  demoLoginConfigured: true,
  signInToDemo: auth.signInToDemo,
  supabaseConfigured: true,
  supabase: {
    auth: {
      signInWithPassword: auth.signInWithPassword,
      signUp: auth.signUp,
    },
  },
}));

import { AuthGate } from "@/components/auth-gate";

describe("AuthGate demo sign-in", () => {
  beforeEach(() => {
    auth.signInToDemo.mockReset();
    auth.signInWithPassword.mockReset();
    auth.signUp.mockReset();
  });

  it("opens the configured shared demo without filling the credential form", async () => {
    auth.signInToDemo.mockResolvedValue({ error: null });
    const user = userEvent.setup();
    render(<AuthGate />);

    await user.click(screen.getByRole("button", { name: "Explore the demo" }));

    expect(auth.signInToDemo).toHaveBeenCalledOnce();
    expect(auth.signInWithPassword).not.toHaveBeenCalled();
  });

  it("shows an actionable error when demo sign-in fails", async () => {
    auth.signInToDemo.mockResolvedValue({
      error: { message: "Demo account is unavailable" },
    });
    const user = userEvent.setup();
    render(<AuthGate />);

    await user.click(screen.getByRole("button", { name: "Explore the demo" }));

    expect(screen.getByText("Demo account is unavailable")).toBeInTheDocument();
  });
});
