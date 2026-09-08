"""Citation identity and inventory consistency; not semantic entailment."""

import re

from .html_render import max_pages

# Per A4 page, from the tuning recorded in docs/revision-sheets-spec.md: the
# two-page sheet targeted 450-550 words with a hard maximum of 650.
WORDS_LOW, WORDS_HIGH, WORDS_CEILING = 225, 275, 325
from .contracts import RevisionError, Sheet


def resolve_disposition_concepts(sheet: Sheet) -> int:
    """Resolve unambiguous inventory references through their explicit item map.

    Models sometimes put a concept ID in a source-unit ledger. This is an
    existing reference chain, not a reason to invent or drop coverage. Unknown
    IDs and invalid mappings remain untouched so validation still rejects them.
    """
    rendered = {item.id for item in sheet.items()}
    concepts = {c.id: c.item_ids for c in sheet.essential_concepts}
    if len(concepts) != len(sheet.essential_concepts):
        return 0
    resolved = 0
    for disposition in sheet.source_dispositions:
        ids = []
        for item_id in disposition.item_ids:
            targets = concepts.get(item_id, [])
            if item_id not in rendered and targets and set(targets) <= rendered:
                ids.extend(targets)
                resolved += 1
            else:
                ids.append(item_id)
        disposition.item_ids = list(dict.fromkeys(ids))
    return resolved


def validate_sheet(sheet: Sheet, *, allowed: set[str], units: dict[str, set[str]],
                   figure_ids: set[int], scope_kind: str) -> None:
    errors: list[str] = []
    items = sheet.items()
    ids = [item.id for item in items]
    by_id = {item.id: item for item in items}
    if len(ids) != len(set(ids)):
        errors.append("Every rendered item needs a unique ID.")
    if sheet.template_kind != scope_kind:
        errors.append("Template must match the selected source type.")
    for item in items:
        if not set(item.citations) <= allowed:
            errors.append(f"{item.id}: citations must be exact supplied markers.")
    nodes = {n.id for n in sheet.diagram.nodes}
    for edge in sheet.diagram.edges:
        if edge.source not in nodes or edge.target not in nodes:
            errors.append(f"{edge.id}: unknown diagram endpoint.")
    if not set(sheet.diagram.source_figure_ids) <= figure_ids:
        errors.append("Diagram references a figure that was not inspected.")
    concept_ids = [c.id for c in sheet.essential_concepts]
    if len(concept_ids) != len(set(concept_ids)):
        errors.append("Essential concept IDs must be unique.")
    for concept in sheet.essential_concepts:
        mapped = [by_id[i] for i in concept.item_ids if i in by_id]
        if len(mapped) != len(concept.item_ids) or not set(concept.citations) <= allowed:
            errors.append(f"Concept {concept.id}: unknown items or citations.")
        # Repeated explanations may cite different pages. Exact marker
        # identity and item existence are deterministic; the independent judge
        # checks actual support and coverage against complete source evidence.
    dispositions = [d.source_unit for d in sheet.source_dispositions]
    if len(dispositions) != len(set(dispositions)) or set(dispositions) != set(units):
        errors.append("Account for every source unit exactly once.")
    for d in sheet.source_dispositions:
        if any(i not in by_id for i in d.item_ids):
            unknown = [i for i in d.item_ids if i not in by_id]
            errors.append(f"{d.source_unit}: unknown rendered items {unknown}. Map only to existing rendered item IDs: {ids}. Concept inventory IDs are not rendered item IDs.")
        if not d.item_ids and not d.reason.strip():
            errors.append(f"{d.source_unit}: explain why supporting material was omitted.")
        # A source unit can repeat an idea whose strongest printed citation
        # is on another page. The ledger records the model's semantic mapping;
        # requiring every mapped page in print turns compression into a
        # bibliography. Exact citation identity is enforced per claim above.
    visible = [sheet.title]
    for item in items:
        visible += [getattr(item, "heading", ""), getattr(item, "text", ""),
                    getattr(item, "label", "")]
    words = len(re.findall(r"\S+", " ".join(visible)))
    # Derived from the page allowance rather than fixed. These per-page figures
    # are the ones the two-page sheet was tuned to in real source trials; the
    # budget moves with the paper instead of being a second, stale constant
    # that silently contradicts `REVISION_MAX_PAGES`.
    pages = max_pages()
    low, high, ceiling = pages * WORDS_LOW, pages * WORDS_HIGH, pages * WORDS_CEILING
    if words > ceiling:
        errors.append(
            f"The visible sheet has {words} words. Compress it to {low}-{high} words "
            f"(hard maximum {ceiling}) across at most {pages} A4 pages, including headings "
            "and diagram labels. Use 20-35 words per essential note and 15-25 words per "
            "trade-off/result: breadth now comes from having more notes, not from "
            "writing longer ones."
        )
    if errors:
        raise RevisionError("invalid_content", "\n".join(errors[:30]))
