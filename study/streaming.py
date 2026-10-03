"""Optional token-callback streaming shared by every generation call site."""

import re
from typing import Callable, Protocol


TokenEventKind = str  # "token"
TokenCallback = Callable[[TokenEventKind, str], None]


class StreamableModel(Protocol):
    def invoke(self, messages): ...
    def stream(self, messages): ...


class _AccumulatedResponse:
    def __init__(self, content: str, response_metadata: dict) -> None:
        self.content = content
        self.response_metadata = response_metadata


# Chat-template control tokens ("<|im_end|>", "</|final|>") never belong in an
# answer. A provider occasionally emits one and then continues with its own
# scratch reasoning and a second draft; everything from the first token on is
# discarded rather than shown or saved.
LEAKED_CONTROL = re.compile(r"</?\|[a-z_]{2,24}\|>", re.IGNORECASE)


def strip_leaked_control(text: str) -> str:
    match = LEAKED_CONTROL.search(text)
    return text[: match.start()].rstrip() if match else text


def invoke_with_streaming(
    model: StreamableModel,
    messages: list[tuple[str, str]],
    *,
    token_callback: TokenCallback | None = None,
):
    """Invoke `model`, forwarding token deltas to `token_callback` if given.

    Returns an object exposing `.content` and `.response_metadata`, matching
    the shape callers already expect from a plain `model.invoke(...)`.
    """

    if token_callback is None:
        response = model.invoke(messages)
        content = getattr(response, "content", None)
        if isinstance(content, str) and LEAKED_CONTROL.search(content):
            return _AccumulatedResponse(strip_leaked_control(content),
                                        getattr(response, "response_metadata", {}) or {})
        return response

    pieces: list[str] = []
    metadata: dict = {}
    text, leaked = "", False
    for chunk in model.stream(messages):
        piece = chunk.content
        if piece:
            pieces.append(piece)
            text += piece
            # Keep reading so usage metadata still arrives, but forward nothing
            # once the text contains a control token, even one split across chunks.
            if not leaked and LEAKED_CONTROL.search(text[-(len(piece) + 32):]):
                leaked = True
            if not leaked:
                token_callback("token", piece)
        chunk_metadata = getattr(chunk, "response_metadata", None)
        if chunk_metadata:
            metadata.update(chunk_metadata)
    return _AccumulatedResponse(strip_leaked_control("".join(pieces)), metadata)
