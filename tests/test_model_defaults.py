"""No shipped code path may default to a model the project has not chosen.

Every generation default in this project is Luna. Revision sheets briefly was
not: its model lived only in a hardcoded fallback, `OPENROUTER_REVISION_MODEL`
was unset in production where every other model is pinned explicitly, and the
difference was invisible until it appeared as a spend spike that exhausted the
account's monthly key limit — which took chat, decks, interviews and captions
down with it, not just the feature that chose the model.

The lesson is not "Sol is bad". It is that a model choice hidden in a default
is a cost decision nobody reviewed, so this test makes the set of defaults
explicit and forces a deliberate edit here to change one.
"""

import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# Directories that never run in production: experiments are exploratory by
# definition, and tests name models to assert on them.
EXCLUDED = {"tests", "experiments", "evals", "evaluation", ".venv", "node_modules"}

MODEL_LITERAL = re.compile(r'"(?P<model>[a-z0-9-]+/[a-zA-Z0-9.\-]+)"')

# What production code is allowed to reach for without further discussion.
# Generation is Luna; the rest are cheap specialists doing a narrow job.
APPROVED = {
    "openai/gpt-6-luna",  # Lower token rates than 5.6 Luna; see design-decisions.md.
    "openai/text-embedding-3-large",
    "openai/text-embedding-3-small",
    "openai/whisper-1",
    "openai/whisper-large-v3-turbo",
    "openai/gpt-4o-transcribe",
    "google/gemini-2.5-flash-lite",
    "google/gemini-3-flash-preview",
    "google/gemini-embedding-2",
    "qwen/qwen3-vl-32b-instruct",
    "cohere/rerank-4-pro",
    "mistralai/voxtral-mini-tts-2603",
    "anthropic/claude-3.5-sonnet",
}

# These choices are authorized for bounded experiments only. This exact script
# is not copied into the image; none becomes a permitted application fallback.
EVALUATION_ONLY = {
    "scripts/screen_model_candidates.py": {
        "deepseek/deepseek-v4-flash", "qwen/qwen3.5-flash-02-23",
        "openai/gpt-6-luna-pro", "google/gemini-3.1-flash-lite",
    }
}

# Anything matching this is a model identifier rather than an incidental
# "namespace/thing" string, which keeps the scan from flagging file paths.
MODEL_VENDORS = (
    "openai/", "anthropic/", "google/", "qwen/", "cohere/", "mistralai/",
    "meta-llama/", "deepseek/", "x-ai/",
)


def source_files() -> list[Path]:
    return [
        path
        for path in REPOSITORY_ROOT.rglob("*.py")
        if not any(part in EXCLUDED or part.startswith(".") for part in path.parts)
    ]


class ModelDefaultTests(unittest.TestCase):
    def test_no_production_default_names_an_unapproved_model(self) -> None:
        unapproved: dict[str, list[str]] = {}
        for path in source_files():
            for match in MODEL_LITERAL.finditer(path.read_text()):
                model = match.group("model")
                if not model.startswith(MODEL_VENDORS):
                    continue
                if model in APPROVED:
                    continue
                if model in EVALUATION_ONLY.get(str(path.relative_to(REPOSITORY_ROOT)), set()):
                    continue
                unapproved.setdefault(model, []).append(
                    str(path.relative_to(REPOSITORY_ROOT))
                )

        self.assertEqual(
            unapproved,
            {},
            "unapproved model(s) referenced in shipped code. If this is a "
            "deliberate cost decision, add it to APPROVED above with a reason; "
            "if it is not, it is about to be billed for.",
        )

    def test_revision_sheets_generate_with_the_project_default(self) -> None:
        """Pinned by name because this is the one that got away."""

        from revision_sheets.generate import model_name

        self.assertEqual(model_name(), "openai/gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
