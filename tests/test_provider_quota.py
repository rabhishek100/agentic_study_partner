"""A spending refusal is not a failure, and must not offer a retry.

On 2026-09-07 the OpenRouter key's monthly limit was exhausted. Generation
reported "Generation could not finish. Check the worker/provider connection and
retry" — naming the worker and the connection, both of which were fine — and
the reader retried twice against a limit that no retry could move.
"""

import unittest

from revision_sheets.provider_errors import spend_refusal


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class ProviderError(Exception):
    """Shaped like the SDK's: a status code, a response, and the body in str()."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
            self.response = FakeResponse(status_code)


# Copied from the production traceback rather than paraphrased.
KEY_LIMIT = (
    "Error code: 403 - {'error': {'message': 'Key limit exceeded (monthly limit). "
    "Manage it using https://openrouter.ai/workspaces/default/keys/887b4bc6', "
    "'code': 403}}"
)


class SpendRefusalTests(unittest.TestCase):
    def test_the_real_production_error_is_recognised(self) -> None:
        refusal = spend_refusal(ProviderError(KEY_LIMIT, 403))

        self.assertIsNotNone(refusal)
        self.assertIn("Key limit exceeded", refusal)
        # The provider's own sentence carries the key's management URL, which is
        # exactly where the reader has to go; keep it rather than replacing it
        # with wording of our own that cannot link anywhere.
        self.assertIn("openrouter.ai", refusal)

    def test_payment_required_needs_no_corroborating_text(self) -> None:
        self.assertIsNotNone(spend_refusal(ProviderError("Error code: 402", 402)))

    def test_a_rate_limit_is_not_a_spending_refusal(self) -> None:
        """429 is transient and retryable, and must keep its retry button."""

        self.assertIsNone(
            spend_refusal(ProviderError("Error code: 429 - rate limit exceeded", 429))
        )

    def test_a_plain_authorization_failure_is_not_a_spending_refusal(self) -> None:
        """403 also means "this key may not do that", which retrying won't fix
        either — but it is a different thing to tell someone."""

        self.assertIsNone(
            spend_refusal(ProviderError("Error code: 403 - invalid api key", 403))
        )

    def test_a_429_that_really_is_about_credits_is_caught(self) -> None:
        """Some providers bill through 429; the body decides, not the status."""

        self.assertIsNotNone(
            spend_refusal(
                ProviderError("Error code: 429 - insufficient credits", 429)
            )
        )

    def test_an_ordinary_failure_is_left_alone(self) -> None:
        self.assertIsNone(spend_refusal(RuntimeError("connection reset")))
        self.assertIsNone(spend_refusal(ValueError("bad schema")))

    def test_a_message_with_no_status_still_counts_when_it_names_spend(self) -> None:
        """Some clients discard the response and leave only the text."""

        self.assertIsNotNone(spend_refusal(RuntimeError("Key limit exceeded")))

    def test_a_long_provider_body_is_bounded(self) -> None:
        error = ProviderError(
            "Error code: 402 - {'error': {'message': '" + "x" * 900 + "'}}", 402
        )

        refusal = spend_refusal(error)

        self.assertLessEqual(len(refusal), 401)
        self.assertTrue(refusal.endswith("…"))


if __name__ == "__main__":
    unittest.main()
