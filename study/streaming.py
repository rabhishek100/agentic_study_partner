"""Optional token-callback streaming shared by every generation call site."""

from typing import Callable, Protocol


TokenEventKind = str  # "token" | "restart"
TokenCallback = Callable[[TokenEventKind, str], None]


class StreamableModel(Protocol):
    def invoke(self, messages): ...
    def stream(self, messages): ...


class _AccumulatedResponse:
    def __init__(self, content: str, response_metadata: dict) -> None:
        self.content = content
        self.response_metadata = response_metadata


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
        return model.invoke(messages)

    pieces: list[str] = []
    metadata: dict = {}
    for chunk in model.stream(messages):
        piece = chunk.content
        if piece:
            token_callback("token", piece)
            pieces.append(piece)
        chunk_metadata = getattr(chunk, "response_metadata", None)
        if chunk_metadata:
            metadata.update(chunk_metadata)
    return _AccumulatedResponse("".join(pieces), metadata)
