/**
 * Turn LaTeX into words a voice can say.
 *
 * KaTeX renders `\frac{\partial L}{\partial w}` into something the eye reads
 * instantly. A synthesiser handed the same string says "backslash frac open
 * brace backslash partial", which is the single fastest way to make narration
 * unlistenable — and the answers this app produces are full of maths.
 *
 * The vocabulary below is deliberately small and deterministic: the common
 * notation of a statistics or machine-learning textbook, in the words a person
 * reading aloud would actually use. Anything unrecognised degrades to its bare
 * symbols rather than to backslashes, so an unusual formula is quiet rather
 * than absurd.
 */

/** Greek letters, said the way a lecturer says them. */
const GREEK: Record<string, string> = {
  alpha: "alpha", beta: "beta", gamma: "gamma", delta: "delta",
  epsilon: "epsilon", varepsilon: "epsilon", zeta: "zeta", eta: "eta",
  theta: "theta", vartheta: "theta", iota: "iota", kappa: "kappa",
  lambda: "lambda", mu: "mu", nu: "nu", xi: "xi", pi: "pi", rho: "rho",
  sigma: "sigma", tau: "tau", upsilon: "upsilon", phi: "phi", varphi: "phi",
  chi: "chi", psi: "psi", omega: "omega",
  Gamma: "capital gamma", Delta: "delta", Theta: "capital theta",
  Lambda: "capital lambda", Xi: "capital xi", Pi: "capital pi",
  Sigma: "capital sigma", Phi: "capital phi", Psi: "capital psi",
  Omega: "omega",
};

/** Commands that stand alone, in the order they must be replaced. */
const WORDS: [RegExp, string][] = [
  [/\\arg\s*\\?max/g, " arg max "],
  [/\\arg\s*\\?min/g, " arg min "],
  [/\\operatorname\{([^{}]*)\}/g, " $1 "],
  [/\\(?:mathbb|mathcal|mathrm|mathbf|mathit|boldsymbol|text|textrm|textbf)\{([^{}]*)\}/g, "$1"],
  [/\\(?:left|right|big|Big|bigg|Bigg)\b/g, " "],
  [/\\(?:quad|qquad|,|;|:|!)/g, " "],
  [/\\sum/g, " the sum of "],
  [/\\prod/g, " the product of "],
  [/\\int/g, " the integral of "],
  [/\\lim/g, " the limit of "],
  [/\\infty/g, " infinity "],
  [/\\partial/g, " partial "],
  [/\\nabla/g, " the gradient of "],
  [/\\approx/g, " approximately "],
  [/\\sim/g, " distributed as "],
  [/\\propto/g, " proportional to "],
  [/\\neq/g, " is not equal to "],
  [/\\leq|\\le\b/g, " is less than or equal to "],
  [/\\geq|\\ge\b/g, " is greater than or equal to "],
  [/\\ll\b/g, " is much less than "],
  [/\\gg\b/g, " is much greater than "],
  [/\\times/g, " times "],
  [/\\cdot/g, " times "],
  [/\\div/g, " divided by "],
  [/\\pm/g, " plus or minus "],
  [/\\(?:rightarrow|to|mapsto)/g, " goes to "],
  [/\\(?:leftarrow|gets)/g, " comes from "],
  [/\\(?:Rightarrow|implies)/g, " implies "],
  [/\\(?:iff|Leftrightarrow)/g, " if and only if "],
  [/\\in\b/g, " in "],
  [/\\notin\b/g, " not in "],
  [/\\subseteq|\\subset/g, " is a subset of "],
  [/\\cup/g, " union "],
  [/\\cap/g, " intersect "],
  [/\\forall/g, " for all "],
  [/\\exists/g, " there exists "],
  [/\\log/g, " log "],
  [/\\ln/g, " natural log "],
  [/\\exp/g, " exp "],
  [/\\sin/g, " sine "],
  [/\\cos/g, " cosine "],
  [/\\tan/g, " tangent "],
  [/\\max/g, " max "],
  [/\\min/g, " min "],
  [/\\dots|\\ldots|\\cdots/g, " and so on "],
  [/\\%/g, " percent "],
];

/** `{…}` with balanced braces, starting at `open`. Null when unbalanced. */
function balanced(text: string, open: number): { body: string; end: number } | null {
  if (text[open] !== "{") return null;
  let depth = 0;
  for (let index = open; index < text.length; index += 1) {
    if (text[index] === "{") depth += 1;
    else if (text[index] === "}") {
      depth -= 1;
      if (depth === 0) return { body: text.slice(open + 1, index), end: index + 1 };
    }
  }
  return null;
}

/**
 * Rewrite every `\command{a}{b}` occurrence, innermost arguments first.
 *
 * A regex over `\{([^{}]*)\}` cannot see `\frac{\frac{a}{b}}{c}`, which is not
 * exotic — it is what a chain rule looks like. Walking the string and matching
 * braces handles nesting at the cost of a loop.
 */
function expand(
  text: string,
  command: string,
  arity: number,
  render: (parts: string[]) => string,
): string {
  const token = `\\${command}`;
  let result = "";
  let cursor = 0;

  while (cursor < text.length) {
    const found = text.indexOf(token, cursor);
    if (found === -1) break;
    // `\subset` must not be eaten by a rule for `\sub`.
    const after = text[found + token.length];
    if (after !== undefined && /[a-zA-Z]/.test(after) && after !== "{" && after !== "[") {
      result += text.slice(cursor, found + token.length);
      cursor = found + token.length;
      continue;
    }

    const parts: string[] = [];
    let position = found + token.length;
    // `\sqrt[3]{x}`: the optional index is read but not spoken as a bracket.
    const optional = /^\[([^\]]*)\]/.exec(text.slice(position));
    if (optional) position += optional[0].length;

    let complete = true;
    for (let index = 0; index < arity; index += 1) {
      while (text[position] === " ") position += 1;
      const group = balanced(text, position);
      if (!group) {
        complete = false;
        break;
      }
      parts.push(speakMath(group.body));
      position = group.end;
    }

    if (!complete) {
      result += text.slice(cursor, found + token.length);
      cursor = found + token.length;
      continue;
    }

    result += text.slice(cursor, found) + render(parts);
    cursor = position;
  }

  return result + text.slice(cursor);
}

/** A superscript said as a word: squared, cubed, or "to the power of n". */
function power(exponent: string): string {
  const trimmed = exponent.trim();
  if (trimmed === "2") return " squared ";
  if (trimmed === "3") return " cubed ";
  if (trimmed === "T") return " transpose ";
  if (trimmed === "-1") return " inverse ";
  return ` to the power of ${trimmed} `;
}

export function speakMath(latex: string): string {
  let text = latex;

  text = expand(text, "frac", 2, ([a, b]) => ` ${a} over ${b} `);
  text = expand(text, "dfrac", 2, ([a, b]) => ` ${a} over ${b} `);
  text = expand(text, "tfrac", 2, ([a, b]) => ` ${a} over ${b} `);
  text = expand(text, "sqrt", 1, ([a]) => ` the square root of ${a} `);
  text = expand(text, "hat", 1, ([a]) => ` ${a} hat `);
  text = expand(text, "bar", 1, ([a]) => ` ${a} bar `);
  text = expand(text, "tilde", 1, ([a]) => ` ${a} tilde `);
  text = expand(text, "vec", 1, ([a]) => ` vector ${a} `);

  for (const [pattern, replacement] of WORDS) {
    text = text.replace(pattern, replacement);
  }

  text = text.replace(/\\([A-Za-z]+)/g, (whole, name: string) =>
    name in GREEK ? ` ${GREEK[name]} ` : ` ${name} `,
  );

  // Sub- and superscripts, braced or bare.
  text = text.replace(/\^\{([^{}]*)\}/g, (_, body: string) => power(body));
  text = text.replace(/\^(-?\w)/g, (_, body: string) => power(body));
  text = text.replace(/_\{([^{}]*)\}/g, (_, body: string) => ` sub ${body.trim()} `);
  text = text.replace(/_(\w)/g, (_, body: string) => ` sub ${body} `);

  // Relations and arithmetic. A hyphen between word characters is left alone:
  // it is a hyphenated name far more often than a minus sign.
  text = text
    .replace(/&/g, " ")
    .replace(/\\\\/g, ". ")
    .replace(/={2,}/g, " equals ")
    .replace(/=/g, " equals ")
    .replace(/(?<![\w])-(?![\w-])/g, " minus ")
    .replace(/\+/g, " plus ")
    .replace(/(?<=[\w\s)])\/(?=[\w\s(])/g, " over ")
    .replace(/</g, " less than ")
    .replace(/>/g, " greater than ")
    // Brackets are grouping, not words. Spoken, they are a pause at most.
    .replace(/[{}()[\]|]/g, " ");

  return text.replace(/\s+/g, " ").trim();
}
