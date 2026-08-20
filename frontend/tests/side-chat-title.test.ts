import { describe, expect, it } from "vitest";

import { MAXIMUM_TITLE_CHARS, clampTitle } from "@/hooks/use-side-chats";
import { errorDetail } from "@/lib/api";

describe("clampTitle", () => {
  it("keeps a short title as it is", () => {
    expect(clampTitle("Why is accuracy the wrong measure?")).toBe(
      "Why is accuracy the wrong measure?",
    );
  });

  it("cuts a highlighted paragraph down to something the server accepts", () => {
    // A window opened from a selection is named after that selection, and the
    // server caps a title at 200. Sending the whole paragraph was a 422 that
    // read "Request failed" — from the reader's side, a highlight that did
    // nothing at all.
    const paragraph = "Data synchronization: ".repeat(40);

    const clamped = clampTitle(paragraph)!;

    expect(clamped.length).toBeLessThanOrEqual(MAXIMUM_TITLE_CHARS);
    expect(clamped.endsWith("…")).toBe(true);
  });

  it("cuts on a word boundary when there is a reasonable one", () => {
    const clamped = clampTitle("word ".repeat(80))!;

    expect(clamped).not.toMatch(/\bwor…$/);
  });

  it("sends nothing rather than something the server would refuse", () => {
    // An empty title fails `min_length=1`, so absent beats blank.
    expect(clampTitle("   ")).toBeUndefined();
    expect(clampTitle(undefined)).toBeUndefined();
  });
});

describe("errorDetail", () => {
  function response(body: unknown, status = 422): Response {
    return {
      status,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  it("names the field a rejected request was rejected for", async () => {
    // Read as an object, a list of field errors rendered as "Request failed
    // (422)" — which says only that something was wrong, never what.
    const detail = await errorDetail(
      response({
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "title"],
            msg: "String should have at most 200 characters",
          },
        ],
      }),
    );

    expect(detail).toBe("title: String should have at most 200 characters");
  });

  it("joins several field errors", async () => {
    const detail = await errorDetail(
      response({
        detail: [
          { loc: ["body", "title"], msg: "too long" },
          { loc: ["body", "anchors", 0, "page"], msg: "must be positive" },
        ],
      }),
    );

    expect(detail).toBe("title: too long; anchors.0.page: must be positive");
  });

  it("still reads the simpler shapes", async () => {
    expect(await errorDetail(response({ detail: "side chat not found" }, 404))).toBe(
      "side chat not found",
    );
    expect(
      await errorDetail(response({ detail: { code: "x", message: "too large" } }, 413)),
    ).toBe("too large");
  });
});
