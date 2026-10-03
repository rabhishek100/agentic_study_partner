"""Isolated, opt-in round-three changes; never enabled by application imports.

Apply once in a fresh evaluation process before importing NativeAdapters. Each
candidate changes one stage, retaining canonical source and ownership checks.
"""
from dataclasses import replace
from hashlib import sha256
import re

VERSION = "flow-candidates-v1"
CHOICES = ("09", "10", "11", "13", "14", "15", "19", "20")
_applied = None


def normalized(value):
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def explicit_title(title, question):
    title = re.sub(r"^(?:chapter\s+)?\d+(?:\s+\d+)*\s*", "", normalized(title))
    return title if len(title.split()) >= 2 and f" {title} " in f" {normalized(question)} " else None


def protect_terms(question, decision, candidates):
    if decision.route != "retrieval_qa":
        return decision
    query = decision.standalone_query
    terms = list(dict.fromkeys(term for candidate in candidates
                 if (term := explicit_title(candidate.title, question))))[:3]
    missing = [term for term in terms if f" {term} " not in f" {normalized(query)} "]
    return decision.model_copy(update={"standalone_query": query + " " + " ".join(missing)}) if missing else decision


def title_rank(results, query, *, limit, unique_nodes):
    ranked = [replace(row, score=row.score * 1.25) if explicit_title(row.section_title, query) else row
              for row in results]
    ranked.sort(key=lambda row: (-row.score, row.toc_index, row.chunk_index))
    selected, seen = [], set()
    for row in ranked:
        if unique_nodes and row.source_node_id in seen:
            continue
        seen.add(row.source_node_id)
        selected.append(row)
        if len(selected) == limit:
            break
    return selected


def two_section_context(documents, *, database_url, owner, scope, limit):
    from study import query
    if not documents or not documents[0].metadata.get("chunk_id"):
        return documents
    seeds, seen_nodes = [], set()
    for item in documents:
        key = (item.metadata.get("book_id"), item.metadata.get("node_id"))
        if key not in seen_nodes and item.metadata.get("chunk_id"):
            seeds.append(item)
            seen_nodes.add(key)
        if len(seeds) == 2:
            break
    pack_limit = min(5, max(1, (limit - 2) // len(seeds)))
    packs = []
    with query.database_connection(database_url, readonly=True) as source:
        for seed in seeds:
            rows = source.execute(
                """select neighbor.* from chunks as seed join chunks as neighbor
                on neighbor.owner_id = seed.owner_id
                  and neighbor.source_book_id = seed.source_book_id
                  and neighbor.source_node_id = seed.source_node_id
                  and neighbor.build_id = seed.build_id
                where seed.owner_id = %s and seed.id = %s
                  and (%s::bigint[] is null or seed.source_book_id = any(%s))
                order by abs(neighbor.chunk_index - seed.chunk_index), neighbor.chunk_index
                limit %s""", (owner, seed.metadata["chunk_id"], scope, scope, pack_limit),
            ).fetchall()
            packs.append([seed, *[query.document_from_result(query.search_result_from_row(row,
                score=seed.metadata.get("score") or 0, retrieval_method="hierarchy_expansion"))
                for row in rows if row["id"] != seed.metadata["chunk_id"]]])
    expanded, seen = [], set()
    # Seed priority, then alternate nearest neighbors so a long first section
    # cannot consume the other section's share. Remaining slots retain ranking.
    for item in [*seeds, *[pack[index] for index in range(1, pack_limit)
                          for pack in packs if index < len(pack)], *documents]:
        key = item.metadata.get("chunk_id")
        if key is not None and key in seen:
            continue
        seen.add(key)
        expanded.append(item)
    return expanded[:limit]


PHASE_GUIDANCE = {
    "opening": "Explain the source's mental model and why its main mechanism is needed.",
    "requirements": "Connect source-stated constraints to the requirements they imply.",
    "estimation": "Explain only source-stated estimates and their assumptions; if no numbers are supplied, discuss the qualitative constraint without inventing a value.",
    "architecture": "Explain a source-supported component choice and its consequence; compare an alternative only if the source supplies it.",
    "deep_dive": "Explain cause, mechanism and consequence, preserving the source's conditions.",
    "tradeoffs": "Connect a source-stated benefit to its cost or limitation and the condition under which the choice works.",
    "reliability": "Explain a source-stated failure and its recovery or prevention; if no failure is supplied, explain the mechanism without inventing one.",
    "evaluation": "Explain what the source measures or verifies, its conditions, and what the result does and does not establish.",
    "closing": "Connect the supplied source ideas into a concise recap without adding outside claims.",
}


def apply(candidate):
    global _applied
    if candidate is None:
        return
    if candidate not in CHOICES or _applied is not None:
        raise ValueError("One known candidate per fresh evaluation process is required")
    _applied = candidate
    if candidate == "09":
        from study import analyze
        original = analyze._clarification_fallback
        def fallback(question, decision, **kwargs):
            return protect_terms(question, original(question, decision, **kwargs), kwargs["candidates"])
        analyze._clarification_fallback = fallback
    elif candidate == "10":
        from retrieval import postgres, search
        original = postgres.search
        def boosted(connection, query, *, limit=5, unique_nodes=False, **kwargs):
            rows = original(connection, query, limit=max(20, limit * 4), unique_nodes=False, **kwargs)
            return title_rank(rows, query, limit=limit, unique_nodes=unique_nodes)
        postgres.search = search.bm25_search = boosted
    elif candidate == "11":
        from study import query
        query._section_neighborhood = two_section_context
    elif candidate == "13":
        from video import course_retrieval
        original = course_retrieval.retrieve_video_evidence
        def adjacent(connection, **kwargs):
            return original(connection, **{**kwargs, "timeline_window_ms": 180_000})
        course_retrieval.retrieve_video_evidence = adjacent
    elif candidate == "14":
        from study import summarize, query
        original = summarize.build_summary_messages
        def allocated(scope, context, **kwargs):
            messages = original(scope, context, **kwargs)
            optional = summarize._optional_coverage_node_ids(scope, context, kwargs.get("response_depth", "interview"))
            count = max(1, len(context.expected_node_ids.difference(optional)))
            instruction = (f"Plan the essential explanation before writing: reserve approximately 1/{count} of the required-section explanation budget for each required section, adjusting for source complexity. "
                           "For each section first state its mechanism and source-stated conditions, limitations or experimental setup; then spend remaining space on examples or interview advice. "
                           "A citation alone does not spend that section's explanation allowance. Retain the existing total output limit and complete canonical evidence.")
            return [*messages, ("human", instruction)]
        summarize.build_summary_messages = query.build_summary_messages = allocated
    elif candidate == "15":
        from pydantic import Field
        from revision_sheets import review, generate
        class QualifiedConcept(review.EvidenceConcept):
            qualification: str = Field(max_length=240, description="Source-stated condition or limitation supported by this concept's citations; empty when none is stated.")
        class QualifiedInventory(review.Inventory):
            concepts: list[QualifiedConcept] = Field(min_length=1, max_length=80)
        review.Inventory = generate.Inventory = QualifiedInventory
        review.INVENTORY_PROMPT += "\nSeparate each source-stated condition or limitation into qualification as well as the concept explanation. Leave it empty when the source states none; do not supply generic qualifications. Use the concept's exact citations to support it."
        generate.PROMPT += "\nCarry each essential concept's nonempty qualification into its actual printed note. Preserve its source meaning and supporting citations; naming the concept or asking a recall question does not print its condition."
        generate.PROMPT_VERSION += ":qualification-v1"
        review.RUBRIC_VERSION += ":qualification-v1"
        generate.RUBRIC_VERSION = review.RUBRIC_VERSION
    elif candidate == "19":
        _assessment_grader()
    elif candidate == "20":
        from langchain_core.messages import SystemMessage
        from interviews import ideal_generation
        original = ideal_generation.build_exchange_messages
        version = ideal_generation.prompt_version()
        def reasoned(**kwargs):
            return [*original(**kwargs), SystemMessage(content=PHASE_GUIDANCE[kwargs["phase"]] +
                " Use only supplied source evidence. Keep original word limits and citation contracts; do not invent numbers, alternatives or failure modes to fill this guidance.")]
        ideal_generation.build_exchange_messages = reasoned
        ideal_generation.prompt_version = lambda: version + ":reasoning-" + sha256(repr(PHASE_GUIDANCE).encode()).hexdigest()[:12]


def _assessment_grader():
    from pydantic import Field
    from interviews import models, prompts
    from interviews.contracts import AnswerEvaluation, ContractModel
    from langchain_core.messages import SystemMessage
    class Assessment(ContractModel):
        requested_points: list[str] = Field(max_length=8, description="What this literal interviewer question asks; private expected points not asked are optional, never required.")
        answer_quotes: list[str] = Field(max_length=8, description="Exact short quotations from Candidate answer demonstrating what was actually said, never from private points or recommended answers.")
        grade: AnswerEvaluation
    original = models.structured_model
    def assessed(schema, **kwargs):
        if schema is not AnswerEvaluation:
            return original(schema, **kwargs)
        chosen = models.model_name(AnswerEvaluation)
        old_name = models.model_name
        try:
            models.model_name = lambda requested=None: chosen if requested is Assessment else old_name(requested)
            client = original(Assessment, **kwargs)
        finally:
            models.model_name = old_name
        class Grader:
            def invoke(self, messages, **options):
                texts = "\n".join(str(getattr(message, "content", message)) for message in messages)
                answer = texts.partition("Candidate answer:\n")[2].partition("\n\nSubmitted Python artifact")[0]
                if not answer:
                    raise ValueError("Candidate answer field is required for assessment")
                result = client.invoke([*messages, SystemMessage(content=
                    "First record requested_points and exact answer_quotes, then score only the requested work against what the candidate actually said. Never credit private expected points or your recommended answer as candidate evidence. Unasked private details are optional future questions. Return JSON matching the supplied schema.")], **options)
                parsed = result.get("parsed") if isinstance(result, dict) else result
                parsed = parsed if isinstance(parsed, Assessment) else Assessment.model_validate(parsed)
                if any(not quote.strip() or quote not in answer for quote in parsed.answer_quotes):
                    raise ValueError("Assessment quoted content outside the candidate answer")
                return {**result, "parsed": parsed.grade} if isinstance(result, dict) else parsed.grade
        return Grader()
    models.structured_model = assessed
    prompts.PROMPT_VERSION += ":assessment-v1"
