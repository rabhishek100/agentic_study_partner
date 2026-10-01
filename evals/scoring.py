"""Deterministic locator checks; none of these establishes claim support."""
import re

SCORING_VERSION = "evidence-v2"
BOOK_ANSWER_ROUTES = {"retrieval_qa", "hierarchy_summary", "prior_answer_transform"}
VIDEO_ANSWER_ROUTES = {"evidence_qa", "lecture_summary", "prior_answer_transform"}


def mean(rows, field):
    values = [row.get("checks", {}).get(field) for row in rows]
    measured = [value for value in values if value is not None]
    return sum(measured) / len(measured) if measured else None


def metric_counts(rows):
    fields = sorted({field for row in rows for field in row.get("checks", {})})
    return {field: {"scored": sum(row.get("checks", {}).get(field) is not None for row in rows),
                    "unknown_or_ineligible": sum(row.get("checks", {}).get(field) is None for row in rows)}
            for field in fields}


def book_citations(result, *, required):
    if not result.citations:
        return False if required else None
    markers = set(re.findall(r"\[S\d+\]|\[N\d+:P\d+\]", result.answer))
    if markers != {citation.marker for citation in result.citations}:
        return False
    books = {item.book_id for item in result.evidence if item.book_id is not None}
    for citation in result.citations:
        if citation.book_id is None and len(books) > 1:
            return False  # an ambiguous cross-book locator is not verified
        matches = [item for index, item in enumerate(result.evidence, 1)
                   if item.node_id == citation.node_id and citation.page in item.pages
                   and (citation.book_id is None or item.book_id == citation.book_id)
                   and (citation.evidence_rank is None or (item.rank or index) == citation.evidence_rank)]
        node_marker = re.fullmatch(r"\[N(\d+):P(\d+)\]", citation.marker)
        rank_marker = re.fullmatch(r"\[S(\d+)\]", citation.marker)
        if not matches or not (node_marker or rank_marker):
            return False
        if node_marker and tuple(map(int, node_marker.groups())) != (citation.node_id, citation.page):
            return False
        if rank_marker and int(rank_marker.group(1)) != citation.evidence_rank:
            return False
    return True


def video_citations(result, *, required):
    if not result.citations:
        return False if required else None
    markers = set(re.findall(r"\[S\d+\]", result.answer))
    if markers != {citation.marker for citation in result.citations}:
        return False
    evidence = {item.rank: item for item in result.evidence}
    for citation in result.citations:
        item = evidence.get(citation.evidence_rank)
        if item is None or citation.marker != f"[S{citation.evidence_rank}]" or citation.modality != item.modality:
            return False
        for field in ("start_ms", "page_number", "frame_id", "resource_id"):
            if getattr(citation, field) != getattr(item, field):
                return False
    return True
