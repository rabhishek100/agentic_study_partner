from study.content import ContentBlock, EvidenceBundle, NodeContent
from study.scope import ResolvedScope, ScopeNode
from decks.topics import book_inventory
from interviews.planning import LoadedInterviewSource, initial_checkpoint, preflight


def flat_chapter(*, pages: int = 4, characters_per_page: int = 2_100) -> EvidenceBundle:
    node = ScopeNode(
        id=18,
        book_id=7,
        parent_id=None,
        toc_index=1,
        level=1,
        node_type="chapter",
        title="Chapter 18: Microservices",
        path_text="Chapter 18: Microservices",
        start_page=346,
        end_page=346 + pages - 1,
    )
    blocks: list[ContentBlock] = []
    block_id = 1
    for offset in range(pages):
        page = 346 + offset
        blocks.extend([
            ContentBlock(
                id=block_id,
                node_id=node.id,
                block_index=block_id,
                block_type="text",
                category="Title",
                page_number=page,
                text_content=f"Design area {offset + 1}",
                table_text=None,
                table_html=None,
                image_mime_type=None,
                has_image_payload=False,
            ),
            ContentBlock(
                id=block_id + 1,
                node_id=node.id,
                block_index=block_id + 1,
                block_type="text",
                category="NarrativeText",
                page_number=page,
                text_content=(f"page-{page} evidence " + "x" * characters_per_page),
                table_text=None,
                table_html=None,
                image_mime_type=None,
                has_image_payload=False,
            ),
        ])
        block_id += 2
    return EvidenceBundle(
        scope=ResolvedScope(
            kind="chapter",
            book_id=7,
            book_title="System Design",
            root_node_id=node.id,
            display_path=node.path_text,
            start_page=node.start_page,
            end_page=node.end_page,
            nodes=(node,),
        ),
        nodes=(NodeContent(node=node, blocks=tuple(blocks)),),
    )


def test_large_flat_chapter_becomes_page_grounded_coverage_units():
    inventory = book_inventory(flat_chapter())

    assert len(inventory.topics) == 4
    assert [topic.key for topic in inventory.topics] == [
        "node:18:page:346",
        "node:18:page:347",
        "node:18:page:348",
        "node:18:page:349",
    ]
    assert all(topic.required for topic in inventory.topics)
    assert [topic.allowed_markers for topic in inventory.topics] == [
        frozenset({f"[N18:P{page}]"}) for page in range(346, 350)
    ]
    combined = "\n".join(topic.evidence_text for topic in inventory.topics)
    assert all(f"page-{page} evidence" in combined for page in range(346, 350))


def test_small_flat_section_remains_one_canonical_topic():
    inventory = book_inventory(flat_chapter(pages=2, characters_per_page=500))

    assert len(inventory.topics) == 1
    assert inventory.topics[0].key == "node:18"
    assert inventory.topics[0].allowed_markers == frozenset({"[N18:P346]", "[N18:P347]"})


def test_live_interview_plans_multiple_areas_across_a_flat_chapter():
    inventory = book_inventory(flat_chapter(pages=10))

    preview = preflight(
        LoadedInterviewSource(inventory=inventory, ingestion_version_id=None),
        target_level="mid",
    )
    checkpoint = initial_checkpoint(
        inventory,
        maximum_duration_minutes=15,
        target_level="mid",
    )

    assert preview.topic_count == 10
    assert preview.required_topic_count == 10
    assert not preview.warnings
    planned = [topic for topic in checkpoint.topics if topic.required]
    assert len(planned) == 4
    assert len({topic.key for topic in planned}) == 4
