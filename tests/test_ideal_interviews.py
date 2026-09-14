from types import SimpleNamespace
from uuid import UUID

import jwt
import pytest

from decks.topics import ScopeInventory, Topic
from evals.ideal_interview import score_ideal_flow
from interviews.ideal_contracts import IdealInterviewExchange, IdealInterviewExchangeDraft, IdealInterviewFlow
from interviews.ideal_generation import generate_ideal_exchange
from interviews.ideal_livekit import IdealVoiceCommand, create_voice_connection, pronunciation_text
from interviews.ideal_voice_worker import spoken_sentences


def source() -> ScopeInventory:
    topics = (
        Topic(
            key="requirements",
            ordinal=0,
            label="Requirements",
            required=True,
            evidence_text="[N10:P3]\nClarify latency and durability requirements.",
            allowed_markers=frozenset({"[N10:P3]"}),
            node_id=10,
            start_page=3,
            end_page=3,
        ),
        Topic(
            key="architecture",
            ordinal=1,
            label="Architecture",
            required=True,
            evidence_text="[N11:P4]\nUse a durable queue to absorb bursts.",
            allowed_markers=frozenset({"[N11:P4]"}),
            node_id=11,
            start_page=4,
            end_page=4,
        ),
    )
    return ScopeInventory(
        source_kind="book",
        scope_key="book:1:9",
        title="Design a notification service",
        source_title="Systems",
        outline="Requirements\nArchitecture",
        topics=topics,
    )


class Model:
    def __init__(self, draft):
        self.draft = draft
        self.config = None

    def invoke(self, _messages, config=None):
        self.config = config
        return {"parsed": self.draft, "raw": SimpleNamespace(response_metadata={})}


def test_exchange_is_traced_and_resolves_only_its_topic_citations():
    model = Model(IdealInterviewExchangeDraft(
        interviewer_text="What would you clarify before choosing the architecture?",
        candidate_text=(
            "I'd start with the latency target and the durability guarantee because those "
            "constraints determine whether synchronous delivery is acceptable. I'd also make "
            "the expected request rate explicit, so we can separate normal load from bursts. "
            "If the product needs durable acceptance, then I'd acknowledge a request only after "
            "it reaches durable storage. That gives us a crisp boundary before we choose services."
        ),
        citation_markers=["[N10:P3]"],
    ))
    exchange, _ = generate_ideal_exchange(
        inventory=source(),
        topic=source().topics[0],
        interview_format="system_design",
        target_level="senior",
        index=0,
        previous=[],
        model=model,
    )
    assert exchange.topic_key == "requirements"
    assert exchange.citations[0].node_id == 10
    assert model.config["run_name"] == "ideal_interview_exchange"
    assert model.config["metadata"]["topic_key"] == "requirements"


def test_exchange_rejects_cross_topic_citations():
    model = Model(IdealInterviewExchangeDraft(
        interviewer_text="How would you scope this?",
        candidate_text="I'd clarify the contract because it drives the design. " * 12,
        citation_markers=["[N11:P4]"],
    ))
    with pytest.raises(ValueError, match="every marker"):
        generate_ideal_exchange(
            inventory=source(), topic=source().topics[0],
            interview_format="system_design", target_level="mid",
            index=0, previous=[], model=model,
        )


def test_exchange_strips_citation_markers_from_spoken_text():
    model = Model(IdealInterviewExchangeDraft(
        interviewer_text="What requirement would you clarify first [N10:P3]?",
        candidate_text=(
            "I'd start with latency and durability [N10:P3], because those constraints "
            "determine whether synchronous delivery is acceptable. I'd also make the "
            "expected request rate explicit, separating normal traffic from bursts. "
            "For durable acceptance, I'd acknowledge only after durable storage. "
        ) * 2,
        citation_markers=["[N10:P3]"],
    ))

    exchange, _ = generate_ideal_exchange(
        inventory=source(), topic=source().topics[0],
        interview_format="system_design", target_level="mid",
        index=0, previous=[], model=model,
    )

    assert "[N10:P3]" not in exchange.interviewer_text
    assert "[N10:P3]" not in exchange.candidate_text
    assert exchange.interviewer_text.endswith("?")
    assert exchange.citations[0].marker == "[N10:P3]"


def test_deterministic_eval_requires_exact_ordered_coverage():
    outputs = {"exchanges": [
        {
            "topic_key": "requirements", "phase": "requirements",
            "interviewer_text": "What constraints would you clarify before proposing this architecture?",
            "candidate_text": "I'd clarify latency and durability because those assumptions drive the architecture. " * 7,
            "citations": [{"marker": "[N10:P3]", "node_id": 10, "page": 3, "evidence_rank": None}],
        },
        {
            "topic_key": "architecture", "phase": "architecture",
            "interviewer_text": "How would you structure the request path after that?",
            "candidate_text": "I'd use a durable queue first because we should absorb bursts and isolate failures. " * 7,
            "citations": [{"marker": "[N11:P4]", "node_id": 11, "page": 4, "evidence_rank": None}],
        },
    ]}
    scores = score_ideal_flow(outputs, {
        "topic_keys": ["requirements", "architecture"],
        "citation_markers": ["[N10:P3]", "[N11:P4]"],
        "interview_format": "system_design",
    })
    assert scores["exact_topic_coverage"] == 1
    assert scores["citation_locator_validity"] == 1
    outputs["exchanges"].reverse()
    assert score_ideal_flow(outputs, {
        "topic_keys": ["requirements", "architecture"],
        "citation_markers": ["[N10:P3]", "[N11:P4]"],
        "interview_format": "system_design",
    })["exact_topic_coverage"] == 0


def test_pronunciation_map_uses_cartesia_inline_ipa(monkeypatch):
    monkeypatch.setenv(
        "LIVEKIT_IDEAL_PRONUNCIATIONS_JSON",
        '{"PostgreSQL":"ˈpoʊst|ɡrɛs|ˌkjuː|ˈɛl", "RAG":"ˈræɡ"}',
    )
    assert pronunciation_text("PostgreSQL backs the RAG service") == (
        "<<ˈpoʊst|ɡrɛs|ˌkjuː|ˈɛl>> backs the <<ˈræɡ>> service"
    )


def test_voice_command_validates_seek_speed_and_customization():
    command = IdealVoiceCommand(
        action="play",
        start_exchange=3,
        start_speaker="candidate",
        start_sentence=2,
        speed=1.25,
        interviewer_voice="voice_two",
        candidate_voice="voice_one",
        delivery="calm",
    )
    assert command.start_sentence == 2
    assert command.speed == 1.25
    assert spoken_sentences("First decision. Then the trade-off? Finally, validate it.") == [
        "First decision.",
        "Then the trade-off?",
        "Finally, validate it.",
    ]
    with pytest.raises(ValueError):
        IdealVoiceCommand(action="play", speed=2)


def test_listen_only_livekit_token_cannot_publish_a_microphone(monkeypatch):
    pytest.importorskip("livekit.api")
    for key, value in {
        "INTERVIEW_LIVEKIT_ENABLED": "true",
        "LIVEKIT_URL": "wss://test.invalid",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret-long-enough-for-hs256-tests",
    }.items():
        monkeypatch.setenv(key, value)
    exchange = IdealInterviewExchange(
        exchange_index=0,
        phase="requirements",
        topic_key="requirements",
        topic_label="Requirements",
        interviewer_text="What would you clarify first?",
        candidate_text="I'd clarify the latency target because it drives the design. " * 8,
        citations=[{"marker": "[N10:P3]", "node_id": 10, "page": 3}],
    )
    flow = IdealInterviewFlow(
        flow_id="00000000-0000-4000-8000-000000000099",
        book_id=1,
        node_id=9,
        scope_key="book:1:9",
        title="Ideal interview",
        source_title="Systems",
        interview_format="system_design",
        target_level="senior",
        topic_count=1,
        covered_topic_count=1,
        coverage_ratio=1,
        estimated_duration_seconds=60,
        exchanges=[exchange],
        generation_model="test",
        prompt_version="test",
    )
    connection = create_voice_connection(flow, UUID("00000000-0000-4000-8000-000000000001"))
    claims = jwt.decode(
        connection.participant_token,
        "test-secret-long-enough-for-hs256-tests",
        algorithms=["HS256"],
    )
    assert claims["video"]["canPublish"] is False
    assert claims["video"]["canSubscribe"] is True
    assert claims["exp"] - claims["nbf"] == 90 * 60
    assert claims["roomConfig"]["agents"][0]["agentName"] == "ideal-interview-voice"
