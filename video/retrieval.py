"""Owner-scoped hybrid retrieval with timeline expansion across modalities."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id
from video.embeddings import (
    IMAGE_DOCUMENT_FORMAT_VERSION,
    TEXT_DOCUMENT_FORMAT_VERSION,
    ImageEmbedder,
    TextEmbedder,
)


VideoModality = Literal[
    "transcript", "visual_frame", "visual_event", "resource_page"
]
RetrievalMethod = Literal[
    "fts", "text_vector", "image_vector", "hybrid", "timeline_expansion"
]
RRF_RANK_CONSTANT = 60
# Asking about the deck should put the deck in front of the model. Frames of a
# screen-shared slide carry richer text than the page itself, so without this
# a question naming the document is answered almost entirely from frames.
#
# "Slide" is deliberately absent. In a lecture it almost always means the thing
# on the screen — "what is shown on the slide", "the slide he was on" — and
# treating it as a request for the attached file spends three of eight evidence
# slots on pages when the reader asked what was projected. A reader who wants
# the file says deck, PDF, document, handout, or page. "Slide deck" still
# matches, on "deck".
DOCUMENT_FOCUS = re.compile(
    r"\b(deck|pdf|document|handout|notes|page|pages)\b", re.IGNORECASE
)
DOCUMENT_FOCUS_FLOOR = 3
# The share of an answer's evidence each modality is guaranteed when it has
# candidates to fill it. Weighted towards the transcript because that is what
# the lecture *is*; the rest supports it. See `_balanced_direct`.
MODALITY_SHARE = {"transcript": 0.5, "visual": 0.375, "resource_page": 0.125}
# How much lecture a retrieved caption cue is widened to. Long enough to carry
# a complete thought at speaking pace — roughly a paragraph of speech — and
# short enough that the citation still lands the reader on the claim rather
# than somewhere in the vicinity of it.
TRANSCRIPT_PASSAGE_MS = 45_000
TRANSCRIPT_PASSAGE_CHARACTERS = 900
QUERY_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


class VideoNotReadyError(LookupError):
    """The requested video has no published evidence version for this owner."""


@dataclass(frozen=True)
class VideoEvidence:
    id: str
    modality: VideoModality
    text: str
    start_ms: int | None
    end_ms: int | None
    page_number: int | None
    transcript_segment_id: int | None
    frame_id: int | None
    visual_event_id: int | None
    resource_page_id: int | None
    score: float
    retrieval_method: RetrievalMethod
    rank: int = 0

    @property
    def is_visual(self) -> bool:
        return self.modality in {"visual_frame", "visual_event"}


def retrieve_video_evidence(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
    query: str,
    limit: int = 8,
    timeline_window_ms: int = 60_000,
    text_embedder: TextEmbedder | None = None,
    image_embedder: ImageEmbedder | None = None,
) -> tuple[UUID, tuple[VideoEvidence, ...]]:
    """Retrieve a mixed evidence set from the current published version.

    Lexical matches remain the baseline because lecture questions often reuse
    the lecturer's exact wording. Evidence-text vectors recover paraphrases,
    and diagram-region vectors recover drawings whose surrounding words never
    named them. The three rankings are fused by reciprocal rank rather than by
    comparing incomparable raw scores.

    Timeline expansion then adds nearby evidence from any modality still
    missing, preventing the transcript from silently becoming the only
    modality that can reach answer generation.
    """

    owner, video = parse_owner_id(owner_id), UUID(str(video_id))
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("video retrieval query cannot be blank")
    if len(cleaned) > 2_000:
        raise ValueError("video retrieval query is too long")
    if not 2 <= limit <= 20:
        raise ValueError("video retrieval limit must be between 2 and 20")
    if not 0 <= timeline_window_ms <= 10 * 60_000:
        raise ValueError("timeline window is outside the supported range")

    version = connection.execute(
        """
        select version.id
        from video.videos as video
        join video.ingestion_versions as version
          on version.id = video.current_ingestion_version_id
         and version.video_id = video.id and version.owner_id = video.owner_id
        where video.id = %s and video.owner_id = %s
          and video.readiness_status in ('ready', 'degraded')
          and version.status in ('ready', 'degraded')
        """,
        (video, owner),
    ).fetchone()
    if version is None:
        raise VideoNotReadyError("video has no published evidence")
    version_id = version["id"]

    # Four times the answer's size. Eight was measured and is worse: a longer
    # shortlist lets more weakly-matching frames into the fusion, and the
    # budget then spends its visual share on them instead of on the frames the
    # ranking actually liked.
    candidate_limit = limit * 4
    lexical = _lexical_query(cleaned)
    rows = (
        connection.execute(
            """
            with query as (
                select to_tsquery('english', %s) as value
            ),
            scored as (
                select evidence.*,
                       ts_rank_cd(evidence.search_vector, query.value, 32)
                           as score,
                       row_number() over (
                           partition by evidence.modality
                           order by ts_rank_cd(
                               evidence.search_vector, query.value, 32
                           ) desc, evidence.start_ms nulls last, evidence.id
                       ) as position
                from video.evidence_units as evidence
                cross join query
                where evidence.owner_id = %s and evidence.video_id = %s
                  and evidence.ingestion_version_id = %s
                  and evidence.search_vector @@ query.value
            )
            select * from scored where position <= %s
            order by score desc, start_ms nulls last, id
            """,
            (lexical, owner, video, version_id, candidate_limit),
        ).fetchall()
        if lexical
        else []
    )
    ranked = [[_evidence(row, method="fts") for row in rows]]
    if text_embedder is not None:
        ranked.append(
            _vector_candidates(
                connection,
                owner=owner,
                video=video,
                version_id=version_id,
                query=cleaned,
                embedder=text_embedder,
                embedding_kind="text",
                document_format_version=TEXT_DOCUMENT_FORMAT_VERSION,
                method="text_vector",
                limit=candidate_limit,
            )
        )
    if image_embedder is not None:
        ranked.append(
            _vector_candidates(
                connection,
                owner=owner,
                video=video,
                version_id=version_id,
                query=cleaned,
                embedder=image_embedder,
                embedding_kind="image",
                document_format_version=IMAGE_DOCUMENT_FORMAT_VERSION,
                method="image_vector",
                limit=max(2, limit * 2),
            )
        )
    direct = (
        _reciprocal_rank_fusion(ranked, limit=candidate_limit)
        if len(ranked) > 1
        else ranked[0][:candidate_limit]
    )
    selected = _balanced_direct(
        direct,
        limit=limit,
        document_floor=(
            DOCUMENT_FOCUS_FLOOR if DOCUMENT_FOCUS.search(cleaned) else 1
        ),
    )

    anchor_timestamps = [
        item.start_ms
        for item in selected
        if item.start_ms is not None
    ]
    expansion_modalities: list[str] = []
    if not any(item.modality == "transcript" for item in selected):
        expansion_modalities.append("transcript")
    if not any(item.is_visual for item in selected):
        expansion_modalities.extend(("visual_frame", "visual_event"))
    if anchor_timestamps and expansion_modalities and timeline_window_ms:
        expanded_rows = connection.execute(
            """
            select evidence.*,
                   min(abs(evidence.start_ms - anchor.value)) as distance_ms
            from video.evidence_units as evidence
            cross join unnest(%s::bigint[]) as anchor(value)
            where evidence.owner_id = %s and evidence.video_id = %s
              and evidence.ingestion_version_id = %s
              and evidence.modality = any(%s::text[])
              and abs(evidence.start_ms - anchor.value) <= %s
            group by evidence.id, evidence.owner_id
            order by distance_ms, evidence.start_ms, evidence.id
            limit %s
            """,
            (
                anchor_timestamps,
                owner,
                video,
                version_id,
                expansion_modalities,
                timeline_window_ms,
                max(2, limit // 2),
            ),
        ).fetchall()
        existing = {item.id for item in selected}
        for row in expanded_rows:
            if row["id"] in existing:
                continue
            distance = int(row["distance_ms"])
            score = max(0.0, 1.0 - distance / max(1, timeline_window_ms))
            selected.append(
                _evidence(row, method="timeline_expansion", score=score)
            )
            existing.add(row["id"])

    selected = _trim_mixed(selected, limit=limit)
    selected = _expand_transcript(
        connection,
        selected,
        owner=owner,
        video=video,
        version_id=version_id,
        candidates=direct,
        limit=limit,
    )
    return version_id, tuple(
        replace(item, rank=index) for index, item in enumerate(selected, start=1)
    )


def _expand_transcript(
    connection: Connection,
    items: list[VideoEvidence],
    *,
    owner: UUID,
    video: UUID,
    version_id: UUID,
    candidates: list[VideoEvidence],
    limit: int,
) -> list[VideoEvidence]:
    """Widen each retrieved caption cue into the passage around it.

    A cue averages thirty characters — "models.", "in the back." — because a
    caption file is cut for reading speed, not for meaning. Retrieval uses it
    to find the right moment and then hands the answer six words as the
    evidence for a claim, which is not evidence at all. Match on the cue,
    return the passage: the same small-to-big move the book pipeline gets for
    free by chunking, which the transcript never had done to it.

    Cues whose passages overlap are merged instead of both being returned, and
    the slot that frees goes to the next-best candidate — so reading further
    around a moment never costs the answer a distinct source.
    """

    spoken = [item for item in items if item.modality == "transcript"]
    if not spoken:
        return items

    half = TRANSCRIPT_PASSAGE_MS // 2
    ordered = sorted(
        (
            (
                max(0, (item.start_ms + (item.end_ms or item.start_ms)) // 2 - half),
                (item.start_ms + (item.end_ms or item.start_ms)) // 2 + half,
                position,
                item,
            )
            for position, item in enumerate(spoken)
            if item.start_ms is not None
        ),
    )
    if not ordered:
        return items

    # Coalesce in time order rather than pairwise on arrival: a window can
    # overlap two earlier ones and merging it into whichever it met first
    # would leave those two overlapping each other, which is the duplication
    # this exists to prevent. The best-ranked cue in each run stays as its
    # representative, so the passage keeps the fused position that earned it.
    windows: list[tuple[int, int, VideoEvidence]] = []
    for start, end, position, item in ordered:
        if windows and start < windows[-1][1]:
            previous_start, previous_end, previous_item = windows[-1]
            best = (
                previous_item
                if spoken.index(previous_item) <= position
                else item
            )
            windows[-1] = (previous_start, max(previous_end, end), best)
        else:
            windows.append((start, end, item))

    rows = connection.execute(
        """
        select start_ms, end_ms, retrieval_text
        from video.evidence_units
        where owner_id = %s and video_id = %s and ingestion_version_id = %s
          and modality = 'transcript' and start_ms is not null
          and start_ms < %s and coalesce(end_ms, start_ms) > %s
        order by start_ms
        """,
        (
            owner,
            video,
            version_id,
            max(end for _, end, _ in windows),
            min(start for start, _, _ in windows),
        ),
    ).fetchall()

    expanded: list[VideoEvidence] = []
    for start, end, anchor in windows:
        inside = [
            row
            for row in rows
            if row["start_ms"] < end and (row["end_ms"] or row["start_ms"]) > start
        ]
        if not inside:
            expanded.append(anchor)
            continue
        text, used = [], 0
        for row in inside:
            piece = " ".join((row["retrieval_text"] or "").split())
            if used + len(piece) > TRANSCRIPT_PASSAGE_CHARACTERS and text:
                break
            text.append(piece)
            used += len(piece) + 1
        covered = inside[: len(text)]
        expanded.append(
            replace(
                anchor,
                text=" ".join(text),
                start_ms=int(covered[0]["start_ms"]),
                end_ms=int(covered[-1]["end_ms"] or covered[-1]["start_ms"]),
                # One cue no longer stands for the passage, so the pointer to a
                # single segment row would be a claim about which cue mattered.
                transcript_segment_id=(
                    anchor.transcript_segment_id if len(covered) == 1 else None
                ),
            )
        )

    kept = [item for item in items if item.modality != "transcript"] + expanded
    # `replace` keeps each passage's id, so the original fused order survives
    # the widening and the merge.
    order = {item.id: position for position, item in enumerate(items)}
    kept.sort(key=lambda item: order.get(item.id, limit))
    chosen = {item.id for item in kept}
    covered = [(item.start_ms, item.end_ms) for item in expanded]
    for item in candidates:
        if len(kept) >= limit:
            break
        if item.id in chosen:
            continue
        if item.modality == "transcript":
            # A cue already inside a passage would return the same words a
            # second time. One from elsewhere in the lecture is a real source,
            # and preferring a frame over it just because merging freed the
            # slot would undo the balance the budget was for.
            if item.start_ms is None or any(
                item.start_ms < end and (item.end_ms or item.start_ms) > start
                for start, end in covered
            ):
                continue
        kept.append(item)
        chosen.add(item.id)
    return kept[:limit]


def _lexical_query(query: str) -> str:
    """Match any meaningful word, the way the book pipeline's BM25 does.

    Requiring every word would make an ordinary spoken question match nothing;
    the 'english' configuration then drops the stopwords that carry no signal
    and stems the rest, so "what did he draw" reaches a frame described as
    "drawn".
    """

    terms = list(dict.fromkeys(QUERY_TOKEN.findall(query.casefold())))
    return " | ".join(f"'{term}'" for term in terms)


def _vector_candidates(
    connection: Connection,
    *,
    owner: UUID,
    video: UUID,
    version_id: UUID,
    query: str,
    embedder: TextEmbedder | ImageEmbedder,
    embedding_kind: Literal["text", "image"],
    document_format_version: str,
    method: RetrievalMethod,
    limit: int,
) -> list[VideoEvidence]:
    """Rank evidence by cosine distance in exactly one embedding space.

    An evidence unit can carry several diagram-region vectors, so the closest
    region represents the unit and the rest are dropped before fusion.
    """

    vector = list(embedder.embed_query(query).vectors[0])
    rows = connection.execute(
        """
        select distinct on (evidence.id) evidence.*,
               1 - (embedding.embedding <=> %s::extensions.vector) as score
        from video.evidence_embeddings as embedding
        join video.evidence_units as evidence
          on evidence.id = embedding.evidence_id
         and evidence.ingestion_version_id = embedding.ingestion_version_id
         and evidence.video_id = embedding.video_id
         and evidence.owner_id = embedding.owner_id
        where embedding.owner_id = %s and embedding.video_id = %s
          and embedding.ingestion_version_id = %s
          and embedding.embedding_kind = %s
          and embedding.model_name = %s
          and embedding.model_revision = %s
          and embedding.dimension = %s
          and embedding.document_format_version = %s
        order by evidence.id, embedding.embedding <=> %s::extensions.vector
        """,
        (
            vector,
            owner,
            video,
            version_id,
            embedding_kind,
            embedder.model_name,
            embedder.model_revision,
            embedder.dimension,
            document_format_version,
            vector,
        ),
    ).fetchall()
    candidates = [_evidence(row, method=method) for row in rows]
    candidates.sort(key=lambda item: (-item.score, item.start_ms or 0, item.id))
    return _per_modality(candidates, limit=limit)


def _per_modality(
    candidates: list[VideoEvidence], *, limit: int
) -> list[VideoEvidence]:
    """Keep the best `limit` of each modality rather than the best overall.

    A global cut hands the shortlist to whichever modality embeds best, which
    for this corpus is always the frames: 258 paragraph-length descriptions
    against 2,436 caption fragments and 135 short deck pages. The modality
    budget downstream then has a share to fill and nothing to fill it from —
    measured on the gold set as questions naming the deck coming back with no
    deck page in them at all.

    Ranking within a modality is untouched; only the cut is per modality.
    """

    kept: list[VideoEvidence] = []
    taken: dict[str, int] = {}
    for item in candidates:
        kind = _kind(item)
        if taken.get(kind, 0) >= limit:
            continue
        taken[kind] = taken.get(kind, 0) + 1
        kept.append(item)
    return kept


def _reciprocal_rank_fusion(
    ranked_lists: list[list[VideoEvidence]], *, limit: int
) -> list[VideoEvidence]:
    """Fuse rankings from incomparable scoring spaces by rank position.

    The cut here is global, and stays global. Two alternatives were measured
    and neither earned its place: cutting per modality as well — the shortlists
    feeding it already are — cost 3 points of anchor recall, because the fused
    order is what tells the budget which frames are the *right* frames; and
    topping each modality up to a floor after the global cut changed which
    turns failed without changing how many. Per-modality shortlists get every
    modality into the pool; the fused ranking orders them once they are there.
    """

    scores: dict[str, float] = {}
    best: dict[str, VideoEvidence] = {}
    methods: dict[str, set[RetrievalMethod]] = {}
    for ranked in ranked_lists:
        for position, item in enumerate(ranked, start=1):
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (
                RRF_RANK_CONSTANT + position
            )
            best.setdefault(item.id, item)
            methods.setdefault(item.id, set()).add(item.retrieval_method)
    fused = [
        replace(
            item,
            score=scores[item.id],
            retrieval_method=(
                item.retrieval_method
                if len(methods[item.id]) == 1
                else "hybrid"
            ),
        )
        for item in best.values()
    ]
    fused.sort(key=lambda item: (-item.score, item.start_ms or 0, item.id))
    return fused[:limit]


def _kind(item: VideoEvidence) -> str:
    """The modality for budgeting: frames and events are one thing to a reader."""

    return "visual" if item.is_visual else item.modality


def _balanced_direct(
    candidates: list[VideoEvidence], *, limit: int, document_floor: int = 1
) -> list[VideoEvidence]:
    """Give each modality a share of the answer's evidence, then rank the rest.

    A lecture is a spoken artifact: what the lecturer said is the evidence, and
    what was on screen supports it. Ranking alone inverts that for a mechanical
    reason rather than a helpful one — a frame's description runs to about
    1,600 characters and a caption cue to about 30, so the frame wins on
    lexical and vector scores almost regardless of the question.

    The rule this replaces guaranteed one item of each modality and gave every
    remaining slot to the ranking. Measured over the gold set, that sent 133 of
    184 evidence slots to frames and left almost exactly one transcript cue per
    answer — six words, from which no claim can honestly be built.

    A share a modality cannot fill goes back to the ranking, so a question
    about something drawn still comes back mostly visual when that is what the
    lecture has. The shares are floors against starvation, not a fixed recipe.
    """

    quotas = {
        name: max(1, round(limit * share)) for name, share in MODALITY_SHARE.items()
    }
    quotas["resource_page"] = max(quotas["resource_page"], document_floor)
    # Whatever the reader named goes first, so its floor survives a limit too
    # small to satisfy every quota at once.
    order = (
        ("resource_page", "transcript", "visual")
        if document_floor > 1
        else ("transcript", "visual", "resource_page")
    )

    selected: list[VideoEvidence] = []
    chosen: set[str] = set()
    for kind in order:
        taken = 0
        for item in candidates:
            if len(selected) >= limit or taken >= quotas.get(kind, 0):
                break
            if _kind(item) == kind and item.id not in chosen:
                selected.append(item)
                chosen.add(item.id)
                taken += 1
    for item in candidates:
        if len(selected) >= limit:
            break
        if item.id not in chosen:
            selected.append(item)
            chosen.add(item.id)
    # Back into fused-rank order: the budget decides what is present, not what
    # the model reads first.
    selected.sort(key=lambda item: candidates.index(item))
    return selected[:limit]


def _trim_mixed(values: list[VideoEvidence], *, limit: int) -> list[VideoEvidence]:
    if len(values) <= limit:
        return values
    # Preserve at least one transcript and one visual when both exist, then
    # keep the highest-ranked remaining direct/expanded evidence.
    keep: list[VideoEvidence] = []
    for predicate in (
        lambda item: item.modality == "transcript",
        lambda item: item.is_visual,
    ):
        match = next((item for item in values if predicate(item)), None)
        if match is not None and match not in keep:
            keep.append(match)
    keep.extend(item for item in values if item not in keep)
    return keep[:limit]


def _evidence(
    row: dict,
    *,
    method: RetrievalMethod,
    score: float | None = None,
) -> VideoEvidence:
    return VideoEvidence(
        id=row["id"],
        modality=row["modality"],
        text=row["retrieval_text"],
        start_ms=(int(row["start_ms"]) if row["start_ms"] is not None else None),
        end_ms=(int(row["end_ms"]) if row["end_ms"] is not None else None),
        page_number=(
            int(row["page_number"]) if row["page_number"] is not None else None
        ),
        transcript_segment_id=row["transcript_segment_id"],
        frame_id=row["frame_id"],
        visual_event_id=row["visual_event_id"],
        resource_page_id=row["resource_page_id"],
        score=float(row.get("score") if score is None else score),
        retrieval_method=method,
    )
