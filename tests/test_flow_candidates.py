"""Behavioral boundaries for isolated candidates, without provider calls."""
import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from evals.flow_candidates import CHOICES, explicit_title, protect_terms, title_rank, two_section_context
from retrieval.postgres import SearchResult
from study.contracts import ScopeCandidate, TurnDecision


def candidate(title):
    return ScopeCandidate(book_id=1, node_id=2, kind="section", title=title,
        display_path=title, start_page=1, end_page=2, match_reason="title phrase")


def test_rewrite_preserves_only_explicit_canonical_terms_and_route():
    decision = TurnDecision(route="retrieval_qa", history_dependency="dependent",
                            standalone_query="audit the deployment", reason="follow-up")
    changed = protect_terms("What about responsible-AI?", decision,
                            [candidate("7.4 Responsible AI"), candidate("Data leakage")])
    assert changed.standalone_query == "audit the deployment responsible ai"
    assert changed.history_dependency == decision.history_dependency
    assert "data leakage" not in changed.standalone_query
    assert protect_terms("How does that work?", decision, [candidate("Data leakage")]) == decision
    clarify = TurnDecision(route="clarify", history_dependency="independent", reason="missing",
                           clarification_question="Which chapter?")
    assert protect_terms("responsible AI", clarify, [candidate("Responsible AI")]) == clarify
    assert explicit_title("Data leakage", "metadata leakage") is None


def test_title_boost_does_not_force_irrelevant_titles_or_duplicate_sections():
    first = SearchResult("a", 1, 1, 1, 0, "Deployment", "", 1, 1, "", (), 1.1)
    named = replace(first, chunk_id="b", source_node_id=2, section_title="7.4 Responsible AI", score=1)
    rows = title_rank([first, named, replace(named, chunk_id="c")], "responsible-AI audit", limit=2, unique_nodes=True)
    assert [r.chunk_id for r in rows] == ["b", "a"]
    assert title_rank([first, named], "production safety", limit=1, unique_nodes=False)[0].chunk_id == "a"
    assert first.score == 1.1 and named.score == 1


def test_two_section_expansion_preserves_owner_build_scope_and_bounded_diversity(monkeypatch):
    from study import query
    def doc(identifier, node):
        return Document(page_content=identifier, metadata={"chunk_id": identifier, "book_id": 1,
                        "node_id": node, "score": 1})
    queries = []
    class DB:
        def execute(self, sql, parameters):
            queries.append((sql, parameters))
            seed = parameters[1]
            return SimpleNamespace(fetchall=lambda: [{"id": seed + "n"}])
    @contextmanager
    def connection(url, *, readonly):
        assert readonly and url == "source"
        yield DB()
    monkeypatch.setattr(query, "database_connection", connection)
    monkeypatch.setattr(query, "search_result_from_row", lambda row, **kw: row)
    monkeypatch.setattr(query, "document_from_result", lambda row: doc(row["id"], 1 if row["id"].startswith("a") else 2))
    results = two_section_context([doc("a", 1), doc("a2", 1), doc("b", 2), doc("c", 3)],
        database_url="source", owner="owner", scope=[1], limit=5)
    assert [d.metadata["chunk_id"] for d in results] == ["a", "b", "a2", "c"]
    assert len(queries) == 2
    for sql, params in queries:
        assert "neighbor.build_id = seed.build_id" in sql
        assert "neighbor.owner_id = seed.owner_id" in sql
        assert "neighbor.source_book_id = seed.source_book_id" in sql
        assert params[0] == "owner" and params[2:4] == ([1], [1])
    assert len(results) <= 5


@pytest.mark.parametrize("name", CHOICES)
def test_each_candidate_can_load_in_a_fresh_process_without_network(name):
    result = subprocess.run([sys.executable, "-c", f"from evals.flow_candidates import apply; apply('{name}')"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_assessment_quotes_must_come_from_candidate_and_keep_grader_role(monkeypatch):
    from evals import flow_candidates
    from interviews import models, prompts
    from interviews.contracts import AnswerEvaluation
    from unittest.mock import Mock
    client = Mock()
    chosen = []
    def factory(schema, **kwargs):
        chosen.append((schema, models.model_name(schema)))
        return client
    monkeypatch.setenv("OPENROUTER_INTERVIEW_GRADER_MODEL", "grader/model")
    monkeypatch.setenv("OPENROUTER_INTERVIEW_MODEL", "interviewer/model")
    monkeypatch.setattr(models, "structured_model", factory)
    monkeypatch.setattr(prompts, "PROMPT_VERSION", prompts.PROMPT_VERSION)
    monkeypatch.setattr(flow_candidates, "_applied", None)
    flow_candidates.apply("19")
    grader = models.structured_model(AnswerEvaluation)
    assert chosen[-1][1] == "grader/model"
    schema = chosen[-1][0]
    grade = Mock(spec=AnswerEvaluation)
    # Construct is deliberate: this test concerns quoted evidence isolation,
    # not duplicating the existing AnswerEvaluation contract tests.
    parsed = schema.model_construct(requested_points=["routing"], answer_quotes=["retrieval"], grade=grade)
    client.invoke.return_value = {"parsed": parsed, "raw": "provider-usage"}
    messages = [HumanMessage(content="Private expected points:\nmagic\nCandidate answer:\nI would use retrieval.\n\nSubmitted Python artifact and browser-reported execution result:\nNone")]
    response = grader.invoke(messages)
    assert response == {"parsed": grade, "raw": "provider-usage"}
    parsed.answer_quotes = ["magic"]
    with pytest.raises(ValueError, match="outside"):
        grader.invoke(messages)
    with pytest.raises(ValueError, match="required"):
        grader.invoke([HumanMessage(content="Private expected points only")])


def test_ideal_guidance_covers_all_contract_phases():
    from typing import get_args
    from interviews.ideal_contracts import IdealPhase
    from evals.flow_candidates import PHASE_GUIDANCE
    assert set(get_args(IdealPhase)) == set(PHASE_GUIDANCE)


def test_repeat_plan_covers_every_affected_sheet_and_summary_without_model_bundles():
    from scripts.repeat_round_candidates import TRIALS
    from scripts.screen_model_candidates import candidate_environment, LUNA, GEMINI
    manifest = json.loads(open("evaluation/five_flow_manifest.json").read())
    sheets = {case["id"] for case in manifest["cases"] if case["flow"] == "revision_sheet"}
    summaries = {case["id"] for case in manifest["cases"] if case["flow"] == "summary"}
    assert set(TRIALS["figures-all"][2]) == sheets
    assert set(TRIALS["summary-a"][2]) == summaries
    assert set(TRIALS["summary-b"][2]) == summaries
    figure_env = candidate_environment(TRIALS["figures-all"][0], base={})
    assert figure_env["OPENROUTER_REVISION_FIGURE_MODEL"] == GEMINI
    assert figure_env["OPENROUTER_REVISION_AUTHOR_MODEL"] == LUNA
    assert figure_env["OPENROUTER_REVISION_INVENTORY_MODEL"] == LUNA
    assert figure_env["OPENROUTER_REVISION_JUDGE_MODEL"] == LUNA
