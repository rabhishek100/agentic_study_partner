/** Who owns the arrow keys while a document is open. Free of any pdf.js import.

Extracted from the viewer so it can be tested: importing the viewer outside a
browser fails at module scope, and this is the part with a decision in it.
*/

/** Elements whose own keyboard interaction must win over document shortcuts. */
export function ownsArrowKeys(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable="true"], [role="combobox"], [role="dialog"], [role="listbox"], [role="menu"], [role="separator"], [role="slider"], [role="spinbutton"], [role="tablist"]',
    ),
  );
}


/**
 * Whether something modal is on screen and should have the keys instead.
 *
 * Modal, not merely `role="dialog"`. The floating side-chat windows are
 * dialogs — non-modal ones, `aria-modal={false}`, several at a time, and a
 * minimized one stays mounted so its answer keeps streaming. Treating any
 * dialog as a reason to stop turning pages meant that asking one question
 * disabled the arrow keys for the rest of the session, which is exactly the
 * arrangement reading mode is built around.
 */
export function modalIsOpen(root: Document | HTMLElement): boolean {
  return Boolean(root.querySelector('[role="dialog"][aria-modal="true"]'));
}
