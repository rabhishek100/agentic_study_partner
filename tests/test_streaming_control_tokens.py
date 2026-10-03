"""Leaked chat-template tokens never reach the reader or the saved answer."""
import unittest
from types import SimpleNamespace

from study.streaming import invoke_with_streaming, strip_leaked_control

ANSWER = "Split by time and fit preprocessing on training data only. [S1]"
LEAK = " \n</|im_end|>\n</|final|> assistant_lazy We need cite every paragraph. Second draft [S1]"


class Model:
    def __init__(self, pieces):
        self.pieces = pieces

    def invoke(self, messages):
        return SimpleNamespace(content="".join(self.pieces), response_metadata={"finish_reason": "stop"})

    def stream(self, messages):
        for piece in self.pieces:
            yield SimpleNamespace(content=piece, response_metadata={})
        yield SimpleNamespace(content="", response_metadata={"finish_reason": "stop"})


class ControlTokenTests(unittest.TestCase):
    def test_text_after_a_control_token_is_discarded(self):
        self.assertEqual(strip_leaked_control(ANSWER + LEAK), ANSWER)
        self.assertEqual(strip_leaked_control(ANSWER), ANSWER)
        self.assertEqual(strip_leaked_control("Compare a | b and x|y."), "Compare a | b and x|y.")

    def test_invoke_returns_the_clean_answer(self):
        response = invoke_with_streaming(Model([ANSWER + LEAK]), [])
        self.assertEqual(response.content, ANSWER)
        self.assertEqual(response.response_metadata, {"finish_reason": "stop"})
        untouched = invoke_with_streaming(Model([ANSWER]), [])
        self.assertEqual(untouched.content, ANSWER)

    def test_streaming_stops_forwarding_even_when_the_token_is_split(self):
        pieces = [ANSWER, " \n<", "/", "|im", "_e", "nd", "|>", " scratch reasoning", " second draft"]
        forwarded = []
        response = invoke_with_streaming(Model(pieces), [], token_callback=lambda kind, text: forwarded.append(text))
        self.assertEqual(response.content, ANSWER)
        self.assertEqual(response.response_metadata, {"finish_reason": "stop"})
        self.assertNotIn(" scratch reasoning", forwarded)
        self.assertNotIn("|>", forwarded)


if __name__ == "__main__":
    unittest.main()
