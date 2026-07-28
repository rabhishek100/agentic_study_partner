/**
 * Normalize LaTeX delimiters so `remark-math` can see them.
 *
 * Models writing about a statistics textbook emit `\(m \approx \sqrt{p}\)`
 * and `\[ … \]`, which markdown treats as escaped parentheses and brackets —
 * the delimiters vanish and the raw TeX is printed as prose. `remark-math`
 * only recognises `$…$` and `$$…$$`, so the two conventions are reconciled
 * here rather than by constraining the prompt, which would make rendering
 * depend on the model obeying an instruction.
 *
 * Code is left exactly as written: a fenced block or inline span discussing
 * LaTeX source must not have its delimiters rewritten.
 */

/** Split text into code and non-code runs, preserving the code verbatim. */
function splitPreservingCode(text: string): { code: boolean; value: string }[] {
  const parts: { code: boolean; value: string }[] = [];
  // Fenced blocks first (``` or ~~~), then inline spans (`…`).
  const pattern = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ code: false, value: text.slice(lastIndex, match.index) });
    }
    parts.push({ code: true, value: match[0] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push({ code: false, value: text.slice(lastIndex) });
  }
  return parts;
}

export function normalizeMath(text: string): string {
  return splitPreservingCode(text)
    .map(({ code, value }) => {
      if (code) return value;
      return (
        value
          // Display math first. `remark-math` only treats `$$` as display
          // when it stands as its own block, so the delimiters go on their
          // own lines; inline `$$…$$` would render as cramped inline maths.
          .replace(
            /\\\[([\s\S]*?)\\\]/g,
            (_, body: string) => `\n\n$$\n${body.trim()}\n$$\n\n`,
          )
          // Inline math: \( … \) becomes $ … $.
          .replace(/\\\(([\s\S]*?)\\\)/g, (_, body: string) => `$${body}$`)
      );
    })
    .join("");
}
