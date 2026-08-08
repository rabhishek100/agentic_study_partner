"""Tests for out-of-domain external QA and web search fallback."""

import pytest
from unittest.mock import MagicMock

from retrieval.web_search import (
    assess_model_knowledge_sufficiency,
    search_duckduckgo,
    search_web_sources,
)
from study.contracts import ConversationState, WebSourceRef
from study.external_qa import execute_external_qa


def test_search_duckduckgo_basic():
    """Verify DuckDuckGo search returns structured WebSourceRef results."""
    results = search_duckduckgo("python programming language", max_results=3)
    assert isinstance(results, list)
    if results:
        first = results[0]
        assert first.url.startswith("http")
        assert first.title
        assert first.rank == 1


def test_assess_model_knowledge_sufficiency_true():
    """Verify sufficiency evaluator returns True when LLM knowledge is sufficient."""
    mock_model = MagicMock()
    mock_model.invoke.return_value.content = '{"is_sufficient": true, "reason": "General CS topic"}'
    
    is_sufficient = assess_model_knowledge_sufficiency("What is quicksort?", mock_model)
    assert is_sufficient is True


def test_assess_model_knowledge_sufficiency_false():
    """Verify sufficiency evaluator returns False when web search is needed."""
    mock_model = MagicMock()
    mock_model.invoke.return_value.content = '{"is_sufficient": false, "reason": "Requires recent news"}'
    
    is_sufficient = assess_model_knowledge_sufficiency("What happened in tech yesterday?", mock_model)
    assert is_sufficient is False


def test_execute_external_qa_model_knowledge():
    """Verify execute_external_qa returns source_type='model_knowledge' when LLM knowledge is sufficient."""
    mock_model = MagicMock()
    # First call: sufficiency check -> True. Second call: answer generation.
    mock_model.invoke.side_effect = [
        MagicMock(content='{"is_sufficient": true, "reason": "General concept"}'),
        MagicMock(content="ℹ️ **General Model Knowledge**: Quicksort is a divide-and-conquer sorting algorithm."),
    ]

    state = ConversationState(conversation_id="test_conv")
    result = execute_external_qa(
        "What is quicksort?",
        state,
        model=mock_model,
        standalone_query="What is quicksort?",
    )

    assert result.route == "external_qa"
    assert result.source_type == "model_knowledge"
    assert result.outcome == "answer"
    assert "General Model Knowledge" in result.answer
    assert result.evidence == []
    assert result.web_sources == []


def test_execute_external_qa_web_search(monkeypatch):
    """Verify execute_external_qa returns source_type='web_search' and web_sources when search is needed."""
    mock_model = MagicMock()
    # First call: sufficiency check -> False. Second call: answer generation using web sources.
    mock_model.invoke.side_effect = [
        MagicMock(content='{"is_sufficient": false, "reason": "Requires live docs"}'),
        MagicMock(content="🌐 **Web Search Results**: According to [Web 1: Python Docs](https://docs.python.org), asyncio is an asynchronous library."),
    ]

    fake_web_sources = [
        WebSourceRef(url="https://docs.python.org", title="Python Docs", snippet="Asyncio documentation", domain="docs.python.org", rank=1)
    ]
    monkeypatch.setattr("study.external_qa.search_web_sources", lambda query, max_results=5: fake_web_sources)

    state = ConversationState(conversation_id="test_conv")
    result = execute_external_qa(
        "What is asyncio in Python 3.13?",
        state,
        model=mock_model,
    )

    assert result.route == "external_qa"
    assert result.source_type == "web_search"
    assert result.outcome == "answer"
    assert "Web Search Results" in result.answer
    assert result.web_sources == fake_web_sources
