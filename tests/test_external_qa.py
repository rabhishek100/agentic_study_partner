"""Tests for out-of-domain external QA and web search fallback."""

import pytest
from unittest.mock import MagicMock

from retrieval.web_search import (
    assess_model_knowledge_sufficiency,
    search_duckduckgo,
    search_web_sources,
)
from study.contracts import ConversationMessage, ConversationState, WebSourceRef
from study.external_qa import EXPLICIT_WEB_REQUEST, execute_external_qa


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
    """Verify execute_external_qa returns source_type='model_knowledge' when answering from model knowledge."""
    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(
        content="ℹ️ **General Model Knowledge**: Quicksort is a divide-and-conquer sorting algorithm."
    )

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
    """Verify execute_external_qa returns source_type='web_search' and web_sources when search is triggered by query."""
    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(
        content="🌐 **Web Search Results**: According to [Web 1: Python Docs](https://docs.python.org), asyncio is an asynchronous library."
    )

    fake_web_sources = [
        WebSourceRef(url="https://docs.python.org", title="Python Docs", snippet="Asyncio documentation", domain="docs.python.org", rank=1)
    ]
    monkeypatch.setattr("study.external_qa.search_web_sources", lambda query, max_results=5: fake_web_sources)

    state = ConversationState(conversation_id="test_conv")
    result = execute_external_qa(
        "What are the latest news updates today for Python asyncio?",
        state,
        model=mock_model,
    )

    assert result.route == "external_qa"
    assert result.source_type == "web_search"
    assert result.outcome == "answer"
    assert "Web Search Results" in result.answer
    assert result.web_sources == fake_web_sources


# --- Follow-up context ---------------------------------------------------
#
# The bug these cover: `execute_external_qa` accepted a conversation and never
# read it, so every branch sent the raw follow-up text to the model with no
# history. Asked "add more detail on this part", the system replied asking
# which part was meant — the one thing the reader had already said.


def _conversation_with_history() -> ConversationState:
    return ConversationState(
        conversation_id="test_conv",
        messages=[
            ConversationMessage(
                role="user",
                content="Explain chapter 12 of System Design Interview.",
                turn_id="t1",
            ),
            ConversationMessage(
                role="assistant",
                content=(
                    "Chapter 12 designs a chat system: it covers the WebSocket "
                    "handshake, the chat service, and message synchronization."
                ),
                turn_id="t1",
            ),
        ],
        previous_answer=(
            "Chapter 12 designs a chat system: it covers the WebSocket "
            "handshake, the chat service, and message synchronization."
        ),
    )


def test_model_knowledge_answer_replays_conversation_history():
    """The prior turn reaches the model, so a follow-up has a referent."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")

    execute_external_qa(
        "add more detail on this part",
        _conversation_with_history(),
        model=mock_model,
    )

    sent = mock_model.invoke.call_args_list[-1].args[0]
    replayed = " ".join(str(message.content) for message in sent)
    assert "Explain chapter 12 of System Design Interview." in replayed
    assert "message synchronization" in replayed
    # Prior turns are replayed as real user/assistant messages, not pasted
    # into the system prompt, so the model reads a conversation it continues.
    roles = [message.type for message in sent]
    assert roles == ["system", "human", "ai", "human"]


def test_prior_answer_is_context_not_citable_evidence():
    """Rungs 0-2 and 3-4 never mix: history informs, it is never cited."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")

    result = execute_external_qa(
        "add more detail on this part",
        _conversation_with_history(),
        model=mock_model,
    )

    system_prompt = str(mock_model.invoke.call_args_list[-1].args[0][0].content)
    assert "<previous_answer>" in system_prompt
    assert "context, not a source" in system_prompt
    assert result.evidence == []
    assert result.citations == []


# --- Deciding to search --------------------------------------------------
#
# The old trigger required the literal adjacency "search web", which matched
# none of the ways a reader actually asks. An explicit request therefore fell
# into the model-knowledge branch — the feature failed exactly when it had
# been asked for by name.


@pytest.mark.parametrize(
    "message",
    [
        "can you use web search to add more details on this part",
        "search the web for this",
        "look it up online",
        "check the internet for current best practice",
        "please google this",
        "add some online sources",
    ],
)
def test_explicit_web_request_is_recognised(message):
    assert EXPLICIT_WEB_REQUEST.search(message)


@pytest.mark.parametrize(
    "message",
    [
        "Summarize Batch Prediction Versus Online Prediction in Chapter 7.",
        "Does the online mode you just described necessarily use streaming features?",
        "How does the book describe research on search relevance?",
    ],
)
def test_book_questions_are_not_mistaken_for_web_requests(message):
    """'online prediction' and 'search relevance' are book topics, not requests."""

    assert EXPLICIT_WEB_REQUEST.search(message) is None


def test_explicit_request_forces_web_search_without_a_recency_signal(monkeypatch):
    """The reader asked in so many words; nothing else gets a vote."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="🌐 **Web Search Results**: ...")
    sufficiency = MagicMock(return_value=True)
    monkeypatch.setattr("study.external_qa.assess_model_knowledge_sufficiency", sufficiency)
    fake = [WebSourceRef(url="https://example.com", title="T", snippet="S", rank=1)]
    monkeypatch.setattr(
        "study.external_qa.search_web_sources",
        lambda query, max_results=5: fake,
    )

    result = execute_external_qa(
        "can you use web search to add more details on this part",
        _conversation_with_history(),
        model=mock_model,
    )

    assert result.source_type == "web_search"
    assert result.web_sources == fake
    # An explicit request short-circuits the sufficiency call rather than
    # spending one to be told what the reader already said.
    sufficiency.assert_not_called()


def test_insufficient_model_knowledge_escalates_to_web_search(monkeypatch):
    """The sufficiency check decides model vs. web; it used to be dead code."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="🌐 **Web Search Results**: ...")
    monkeypatch.setattr(
        "study.external_qa.assess_model_knowledge_sufficiency",
        lambda question, model: False,
    )
    fake = [WebSourceRef(url="https://example.com", title="T", snippet="S", rank=1)]
    monkeypatch.setattr(
        "study.external_qa.search_web_sources",
        lambda query, max_results=5: fake,
    )

    result = execute_external_qa(
        "What does the Ray Serve autoscaler do about cold starts?",
        ConversationState(conversation_id="c"),
        model=mock_model,
    )

    assert result.source_type == "web_search"


def test_sufficient_model_knowledge_spends_no_search(monkeypatch):
    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")
    monkeypatch.setattr(
        "study.external_qa.assess_model_knowledge_sufficiency",
        lambda question, model: True,
    )
    searched = MagicMock()
    monkeypatch.setattr("study.external_qa.search_web_sources", searched)

    result = execute_external_qa(
        "What is quicksort?",
        ConversationState(conversation_id="c"),
        model=mock_model,
    )

    assert result.source_type == "model_knowledge"
    searched.assert_not_called()


# --- The search query itself ---------------------------------------------


def test_referential_query_is_rewritten_before_searching(monkeypatch):
    """"this part" retrieves nothing on any engine; it is resolved first."""

    mock_model = MagicMock()
    mock_model.invoke.side_effect = [
        MagicMock(content="WebSocket handshake chat system message synchronization"),
        MagicMock(content="🌐 **Web Search Results**: ..."),
    ]
    queries: list[str] = []

    def capture(query, max_results=5):
        queries.append(query)
        return [WebSourceRef(url="https://example.com", title="T", snippet="S", rank=1)]

    monkeypatch.setattr("study.external_qa.search_web_sources", capture)

    execute_external_qa(
        "can you use web search to add more details on this part",
        _conversation_with_history(),
        model=mock_model,
    )

    assert queries == ["WebSocket handshake chat system message synchronization"]


def test_search_query_rewrite_is_skipped_without_history(monkeypatch):
    """No conversation, nothing to resolve against — do not spend the call."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="🌐 **Web Search Results**: ...")
    queries: list[str] = []

    def capture(query, max_results=5):
        queries.append(query)
        return [WebSourceRef(url="https://example.com", title="T", snippet="S", rank=1)]

    monkeypatch.setattr("study.external_qa.search_web_sources", capture)

    execute_external_qa(
        "search the web for the latest Kubernetes autoscaling guidance",
        ConversationState(conversation_id="c"),
        model=mock_model,
    )

    assert queries == ["search the web for the latest Kubernetes autoscaling guidance"]


def test_web_search_failure_falls_back_and_says_so(monkeypatch):
    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")
    monkeypatch.setattr("study.external_qa.search_web_sources", lambda q, max_results=5: [])

    result = execute_external_qa(
        "search the web for Kubernetes autoscaling guidance",
        ConversationState(conversation_id="c"),
        model=mock_model,
    )

    assert result.source_type == "model_knowledge"
    assert result.web_sources == []
    assert "returned 0 results" in result.routing_reason


def test_routing_reason_keeps_both_causes():
    """Why the turn left the library, and why it landed where it did."""

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")

    result = execute_external_qa(
        "What is quicksort?",
        ConversationState(conversation_id="c"),
        model=mock_model,
        routing_reason="Indexed evidence was evaluated as insufficient.",
    )

    assert "Indexed evidence was evaluated as insufficient" in result.routing_reason
    assert "model knowledge" in result.routing_reason


def test_an_earlier_web_request_does_not_stick_to_later_turns(monkeypatch):
    """An explicit request applies to the turn that made it, and no other.

    The current question is not yet in `state.messages` when this route runs —
    it is recorded after the turn completes — so scanning history for "use web
    search" would read the *previous* turn's request and search the web on
    every question after it.
    """

    conversation = ConversationState(
        conversation_id="c",
        messages=[
            ConversationMessage(role="user", content="search the web for chat systems"),
            ConversationMessage(role="assistant", content="🌐 Web Search Results: ..."),
        ],
        previous_answer="🌐 Web Search Results: ...",
    )
    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="ℹ️ **General Model Knowledge**: ...")
    monkeypatch.setattr(
        "study.external_qa.assess_model_knowledge_sufficiency",
        lambda question, model: True,
    )
    searched = MagicMock()
    monkeypatch.setattr("study.external_qa.search_web_sources", searched)

    result = execute_external_qa(
        "and what is a heap?",
        conversation,
        model=mock_model,
    )

    assert result.source_type == "model_knowledge"
    searched.assert_not_called()


def test_explicit_request_survives_an_analyser_rewrite(monkeypatch):
    """A rewrite drops "use web search" with the rest of the meta-instruction.

    The subject matter is what the analyser is asked for, so the request lives
    only in the reader's own words by the time it arrives here.
    """

    mock_model = MagicMock()
    mock_model.invoke.return_value = MagicMock(content="🌐 **Web Search Results**: ...")
    monkeypatch.setattr(
        "study.external_qa.assess_model_knowledge_sufficiency",
        lambda question, model: True,
    )
    monkeypatch.setattr(
        "study.external_qa.search_web_sources",
        lambda query, max_results=5: [
            WebSourceRef(url="https://example.com", title="T", snippet="S", rank=1)
        ],
    )

    result = execute_external_qa(
        "can you use web search to add more details on this part",
        _conversation_with_history(),
        model=mock_model,
        standalone_query="chat system inter-server protocol",
    )

    assert result.source_type == "web_search"
