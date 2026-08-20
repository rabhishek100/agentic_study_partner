"""Sending the book's own pictures to the model.

A book of architecture diagrams answers half its questions in figures. They
were selected after generation, for the interface to display, so a reader who
asked about the diagram in front of them got an answer asking to be shown it.
"""

import unittest
from unittest.mock import MagicMock

from study.figures import load_figure_images
from study.contracts import FigureRef, PromptProfile
from study.prompts import DEFAULT_PROMPT_PROFILE, FIGURE_GROUNDING, build_answer_messages
from study.side_context import AnchoredSource, build_side_context

OWNER = "11111111-1111-4111-8111-111111111111"


def figure(block_id: int, page: int = 21) -> FigureRef:
    return FigureRef(
        book_id=523,
        node_id=9,
        block_id=block_id,
        page=page,
        mime_type="image/png",
        path="Chapter 1",
    )


class BuildMessagesTests(unittest.TestCase):
    def message_pair(self, **kwargs):
        return build_answer_messages(
            profile=DEFAULT_PROMPT_PROFILE,
            question="Explain the diagram.",
            evidence="[S1] a passage",
            archetype="concept_explanation",
            depth="quick",
            **kwargs,
        )

    def test_a_turn_with_no_figures_is_byte_for_byte_what_it_was(self):
        # The main chat's answers are measured against a frozen gold set.
        messages = self.message_pair()

        self.assertEqual([role for role, _ in messages], ["system", "human"])
        self.assertNotIn(FIGURE_GROUNDING, messages[0][1])

    def test_figures_ride_along_as_images_after_the_text(self):
        messages = self.message_pair(figures=[("image/png", "AAAA"), ("image/png", "BBBB")])

        human = messages[1]
        kinds = [part["type"] for part in human.content]
        self.assertEqual(kinds, ["text", "image_url", "image_url"])
        self.assertTrue(
            human.content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_the_model_is_told_the_images_are_citable_and_already_supplied(self):
        # The failure this fixes was an answer asking the reader to provide a
        # diagram they were looking at.
        messages = self.message_pair(figures=[("image/png", "AAAA")])

        self.assertIn(FIGURE_GROUNDING, messages[0].content)
        self.assertIn("[F1]", messages[1].content[0]["text"])
        self.assertIn("do not ask the reader to supply the diagram", FIGURE_GROUNDING)


class LoadFigureImagesTests(unittest.TestCase):
    def connection(self, rows):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = rows
        return connection

    def test_bytes_come_back_in_the_order_the_labels_name(self):
        # [F2] has to mean the second one.
        connection = self.connection(
            [
                {"block_id": 2, "mime_type": "image/png", "base64_content": "SECOND"},
                {"block_id": 1, "mime_type": "image/png", "base64_content": "FIRST"},
            ]
        )

        images = load_figure_images(
            connection,
            owner_id=OWNER,
            figures=[figure(1), figure(2)],
        )

        self.assertEqual([payload for _, payload in images], ["FIRST", "SECOND"])

    def test_a_figure_with_no_bytes_is_skipped_rather_than_sent_empty(self):
        # An empty image would shift every label after it under the model.
        connection = self.connection(
            [{"block_id": 2, "mime_type": "image/png", "base64_content": "SECOND"}]
        )

        images = load_figure_images(
            connection,
            owner_id=OWNER,
            figures=[figure(1), figure(2)],
        )

        self.assertEqual(images, [("image/png", "SECOND")])

    def test_only_the_first_few_are_sent(self):
        connection = self.connection(
            [
                {"block_id": index, "mime_type": "image/png", "base64_content": "X"}
                for index in range(1, 6)
            ]
        )

        images = load_figure_images(
            connection,
            owner_id=OWNER,
            figures=[figure(index) for index in range(1, 6)],
            limit=2,
        )

        self.assertEqual(len(images), 2)

    def test_no_figures_means_no_query_at_all(self):
        connection = self.connection([])

        self.assertEqual(load_figure_images(connection, owner_id=OWNER, figures=[]), [])
        connection.execute.assert_not_called()


class SideContextTests(unittest.TestCase):
    def test_an_anchored_source_is_what_turns_pictures_on(self):
        anchored = build_side_context(
            [],
            [],
            sources=[AnchoredSource(anchor_id="s1", label="p. 21", identities=("c1",))],
        )
        plain = build_side_context([], [])

        self.assertTrue(anchored.sources_present)
        self.assertFalse(plain.sources_present)


if __name__ == "__main__":
    unittest.main()


class AnchoredLocationRoutingTests(unittest.TestCase):
    """The reason "explain the diagram" came back as a question.

    A bare page anchor carries no quoted text, so the analyser saw a three-word
    message with no referent and asked which diagram was meant. The reader was
    looking at it. Retrieval never ran, so the figures never went anywhere —
    the vision path was correct and unreachable.
    """

    def test_a_page_anchor_tells_the_analyser_where_the_reader_is(self):
        from study.side_context import AnchoredSource, build_side_context

        context = build_side_context(
            [],
            [],
            sources=[
                AnchoredSource(
                    anchor_id="s1",
                    label="p. 21 · Chapter 1: Scale from zero to millions",
                    identities=("chunk-one",),
                )
            ],
        )

        self.assertEqual(
            context.anchored_locations,
            ("p. 21 · Chapter 1: Scale from zero to millions",),
        )

    def test_the_payload_carries_the_place_only_when_there_is_one(self):
        from study.analyze import _payload
        from study.contracts import ConversationState

        state = ConversationState(conversation_id="c1", book_ids=[523])
        plain = _payload("explain the diagram", state, [])
        anchored = _payload(
            "explain the diagram", state, [], (), ("p. 21 · Chapter 1",)
        )

        # Absent rather than empty for an ordinary turn: the main chat's
        # analyser payload is measured against a frozen routing gold set.
        self.assertNotIn("anchored_locations", plain)
        self.assertEqual(anchored["anchored_locations"], ["p. 21 · Chapter 1"])

    def test_the_instruction_forbids_asking_which_page_is_meant(self):
        from study.analyze import (
            ANCHORED_LOCATION_INSTRUCTIONS,
            SYSTEM_PROMPT,
        )

        self.assertIn("the diagram", ANCHORED_LOCATION_INSTRUCTIONS)
        self.assertIn("Never clarify", ANCHORED_LOCATION_INSTRUCTIONS)
        # Appended for anchored turns only, exactly as the quote block is.
        self.assertNotIn(ANCHORED_LOCATION_INSTRUCTIONS, SYSTEM_PROMPT)
