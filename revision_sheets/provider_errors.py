"""Tell "the provider refused to spend" apart from "the provider is busy".

Both arrive as an HTTP error from the same client, and the difference decides
what a reader should be told and whether retrying can possibly work. Getting it
wrong is not cosmetic: an exhausted monthly key limit was reported as
"Generation could not finish. Check the worker/provider connection and retry",
and the reader retried twice, because the message named the two things that
were fine and hid the one that was not.

Duck-typed rather than importing the provider SDK. The status code and message
are what matter, every client exposes them somewhere, and this stays correct if
the transport underneath changes.
"""

import json
import re

# Phrases a provider uses when it is refusing on money rather than on load.
# Matched against the response body, not the status alone, because 403 also
# covers ordinary permission failures that have nothing to do with spend.
SPEND_MARKERS = (
    "key limit exceeded",
    "insufficient credit",
    "insufficient funds",
    "exceeded your current quota",
    "quota exceeded",
    "billing",
    "payment required",
    "add credits",
    "spending limit",
)

# 402 is unambiguous. 403 and 429 are only spend-related when the body says so:
# 403 is also plain authorization, and 429 is nearly always a rate limit, which
# *is* worth retrying and must not be reported as an exhausted budget.
ALWAYS_SPEND = {402}
SOMETIMES_SPEND = {403, 429}


def _status(error: object) -> int | None:
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _provider_message(text: str) -> str | None:
    """The provider's own sentence, dug out of the body it embedded it in."""

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0).replace("'", '"'))
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"].strip()
            if isinstance(error, str):
                return error.strip()
    return None


def spend_refusal(error: BaseException) -> str | None:
    """The reader-facing reason a provider refused on spend, or None.

    None means "not a spending problem" — including a 429 rate limit, which is
    transient and retryable and must keep the retry affordance it deserves.
    """

    text = str(error)
    lowered = text.casefold()
    status = _status(error)
    mentions_spend = any(marker in lowered for marker in SPEND_MARKERS)

    if status in ALWAYS_SPEND or (status in SOMETIMES_SPEND and mentions_spend):
        pass
    elif status is None and mentions_spend:
        # Some clients wrap the response away entirely and leave only the text.
        pass
    else:
        return None

    detail = _provider_message(text)
    # The provider's message names the account and usually links the key, which
    # is exactly where the reader has to go. Bounded so a long body cannot turn
    # into the whole notice.
    if detail and len(detail) > 400:
        detail = detail[:400].rstrip() + "…"
    return detail or "The AI provider refused the request on spending limits."
