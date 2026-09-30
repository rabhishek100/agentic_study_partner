"""Generate the endpoint catalog from FastAPI's live OpenAPI schema."""

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- BEGIN GENERATED: api-endpoints -->"
END = "<!-- END GENERATED: api-endpoints -->"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def endpoint_catalog(schema: dict) -> str:
    operations = [
        (path, method, operation)
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if method in HTTP_METHODS
    ]
    groups = {tag["name"]: [] for tag in schema["tags"]}
    for path, method, operation in operations:
        tag = operation["tags"][0]
        groups.setdefault(tag, []).append((path, method, operation))
    parts = [f"{len(operations)} operations, generated from the application OpenAPI schema.", ""]
    for tag, entries in groups.items():
        if not entries:
            continue
        parts.extend([f"### {tag}", "", "| Method | Path | Access | Success | Purpose |", "|---|---|---|---|---|"])
        for path, method, operation in sorted(entries):
            access = "Bearer JWT" if operation.get("security") else (
                "Signed playback token" if path.endswith("/stream") and method == "get" else "Public"
            )
            success = ", ".join(sorted(code for code in operation["responses"] if code.startswith("2")))
            purpose = " ".join(operation["description"].split("\n\n", 1)[0].split()).replace("|", "\\|")
            parts.append(f"| {method.upper()} | `{path}` | {access} | {success} | {purpose} |")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_reference(document: str, schema: dict) -> str:
    before, marker, rest = document.partition(BEGIN)
    if not marker:
        raise ValueError("API reference is missing its generated start marker")
    _, marker, after = rest.partition(END)
    if not marker:
        raise ValueError("API reference is missing its generated end marker")
    return before + BEGIN + "\n\n" + endpoint_catalog(schema) + "\n" + END + after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed endpoint catalog is stale; do not write")
    args = parser.parse_args()
    # Importing the app builds contracts without starting workers, connecting
    # to Postgres, or running startup/model warmup hooks.
    from api.main import app

    path = ROOT / "docs/api.md"
    original = path.read_text()
    updated = render_reference(original, app.openapi())
    if args.check:
        if updated != original:
            print("API catalog is stale. Run: python -m scripts.export_api_reference")
            return 1
        print("API endpoint catalog is current.")
        return 0
    path.write_text(updated)
    print("Updated docs/api.md from FastAPI OpenAPI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
