"""A bounded, owner-scoped cache of synthesised speech.

TTS is priced per character, so replaying an answer costs exactly what the
first play cost unless the audio is kept. Keeping it is the whole point of a
read-aloud feature people use twice: a chapter answer re-heard on the walk
back is free.

The cache is bounded because it holds blobs on a small Postgres. Every insert
evicts the owner's least recently heard rows until the owner is back under
`NARRATION_AUDIO_CACHE_BYTES`. An unbounded cache on a free-tier disk is not a
cache, it is an outage with a delay.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from uuid import UUID

from psycopg import Connection


DEFAULT_CACHE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class CachedAudio:
    content: bytes
    media_type: str


def cache_key(text: str, *, model: str, voice: str) -> str:
    """Identity of one synthesis: the words, the model, and the voice.

    The model and voice are in the key so that changing either produces new
    audio instead of replaying the old voice from cache — a silent stale-read
    that would be very hard to explain to yourself later.
    """

    digest = hashlib.sha256()
    digest.update(model.encode())
    digest.update(b"\x00")
    digest.update(voice.encode())
    digest.update(b"\x00")
    digest.update(text.encode())
    return digest.hexdigest()


def budget_bytes() -> int:
    try:
        configured = int(os.getenv("NARRATION_AUDIO_CACHE_BYTES", "").strip() or 0)
    except ValueError:
        configured = 0
    return configured if configured > 0 else DEFAULT_CACHE_BYTES


def load(
    connection: Connection, *, owner_id: UUID, content_hash: str
) -> CachedAudio | None:
    """Return cached audio, recording that it was heard again."""

    row = connection.execute(
        """
        update narration_audio
           set last_used_at = now()
         where owner_id = %s and content_hash = %s
        returning audio, media_type
        """,
        (owner_id, content_hash),
    ).fetchone()
    if row is None:
        return None
    return CachedAudio(content=bytes(row["audio"]), media_type=row["media_type"])


def store(
    connection: Connection,
    *,
    owner_id: UUID,
    content_hash: str,
    audio: bytes,
    media_type: str,
    model_name: str,
    voice: str,
    character_count: int,
    cost_usd: float,
) -> None:
    connection.execute(
        """
        insert into narration_audio (
            owner_id, content_hash, model_name, voice, media_type,
            audio, character_count, cost_usd
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (owner_id, content_hash) do update
            set last_used_at = now()
        """,
        (
            owner_id,
            content_hash,
            model_name,
            voice,
            media_type,
            audio,
            character_count,
            round(max(0.0, cost_usd), 6),
        ),
    )
    evict(connection, owner_id=owner_id)


def evict(
    connection: Connection, *, owner_id: UUID, budget: int | None = None
) -> int:
    """Drop least recently heard rows until this owner fits the budget.

    Returns how many rows went, so a caller that cares can log it. Deleting in
    one statement rather than a loop keeps the whole eviction inside the
    transaction that caused it.
    """

    limit = budget if budget is not None else budget_bytes()
    result = connection.execute(
        """
        with ranked as (
            select content_hash,
                   sum(octet_length(audio)) over (
                       order by last_used_at desc, content_hash
                       rows between unbounded preceding and current row
                   ) as running_bytes
              from narration_audio
             where owner_id = %s
        )
        delete from narration_audio
         where owner_id = %s
           and content_hash in (
               select content_hash from ranked where running_bytes > %s
           )
        """,
        (owner_id, owner_id, limit),
    )
    return result.rowcount if result.rowcount and result.rowcount > 0 else 0
