import { describe, expect, it } from "vitest";

import { modalIsOpen, ownsArrowKeys } from "@/components/pdf/keyboard";

function mount(html: string): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = html;
  document.body.append(root);
  return root;
}

describe("modalIsOpen", () => {
  it("ignores a side chat, which is a dialog but not a modal one", () => {
    // The bug this fixes: asking one question left a window mounted — minimized
    // windows stay mounted so their answers keep streaming — and every arrow
    // key stopped turning pages for the rest of the session.
    const root = mount('<section role="dialog" aria-modal="false">a side chat</section>');

    expect(modalIsOpen(root)).toBe(false);
  });

  it("yields to a real modal", () => {
    const root = mount('<div role="dialog" aria-modal="true">prompt settings</div>');

    expect(modalIsOpen(root)).toBe(true);
  });

  it("says no when nothing is open at all", () => {
    expect(modalIsOpen(mount("<p>just the page</p>"))).toBe(false);
  });
});

describe("ownsArrowKeys", () => {
  it("leaves the arrows alone when focus is on the page itself", () => {
    const root = mount("<p>body text</p>");

    expect(ownsArrowKeys(root.querySelector("p"))).toBe(false);
  });

  it("gives them up to anything typed into", () => {
    const root = mount("<textarea></textarea><input />");

    expect(ownsArrowKeys(root.querySelector("textarea"))).toBe(true);
    expect(ownsArrowKeys(root.querySelector("input"))).toBe(true);
  });

  it("gives them up inside a side chat, where they mean something else", () => {
    // Focus *inside* a window is the window's business — its header moves it
    // with the arrows. That is different from a window merely existing.
    const root = mount(
      '<section role="dialog" aria-modal="false"><button id="handle">move</button></section>',
    );

    expect(ownsArrowKeys(root.querySelector("#handle"))).toBe(true);
  });

  it("gives them up on the resize divider, which nudges with them", () => {
    const root = mount('<div role="separator" tabindex="0"></div>');

    expect(ownsArrowKeys(root.querySelector('[role="separator"]'))).toBe(true);
  });
});
