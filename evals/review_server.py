"""Loopback-only review of already generated artifacts; no inference endpoint."""
from hmac import compare_digest
import json
from pathlib import Path
import re
import secrets
from threading import RLock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from evals.reviews import HumanReview, load_bundle, valid_reviews, save_review, review_stats


def create_app(directory, *, token, port):
    directory = Path(directory).resolve()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = RLock()
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in allowed}
    nonce = secrets.token_urlsafe(24)

    @app.middleware("http")
    async def secure(request, call_next):
        if request.headers.get("host") not in allowed:
            return JSONResponse({"detail": "Loopback host required"}, status_code=403)
        if request.url.path != "/":
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            # PDFs/images load through same-origin URLs with the session token.
            if not supplied:
                supplied = request.query_params.get("token", "")
            if not compare_digest(supplied, token):
                return JSONResponse({"detail": "Review session token required"}, status_code=401)
        if request.method not in {"GET", "HEAD"} and request.headers.get("origin") not in origins:
            return JSONResponse({"detail": "Same-origin review write required"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                                      "img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"})
        return response

    def bundle():
        try:
            return load_bundle(directory)
        except (ValueError, OSError, KeyError) as error:
            raise HTTPException(409, "Bundle unavailable or changed; reload after checking the experiment") from error

    def case(case_id):
        row = next((row for row in bundle()["cases"] if row["id"] == case_id), None)
        if row is None:
            raise HTTPException(404, "Unknown case")
        return row

    def reviews(data):
        try:
            return valid_reviews(directory, data)
        except (ValueError, OSError, KeyError) as error:
            raise HTTPException(409, "Review labels changed or belong to another experiment") from error

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).with_name("review_ui.html").read_text()).replace("__SCRIPT_NONCE__", nonce)

    @app.get("/api/bundle")
    def inventory():
        data = bundle()
        labels = reviews(data)
        return {"experiment": directory.name, "fingerprint": data["fingerprint"], "config": {
                    key: data["config"].get(key) for key in ("execution_layer", "retrieval_mode")},
                "budget": data.get("budget"), "stats": review_stats(data, labels), "reviews": labels,
                "cases": [{**{key: row.get(key) for key in ("id", "flow", "title", "tier", "status", "output_hash")},
                           "automated_reviewed": row.get("judgment") is not None} for row in data["cases"]]}

    @app.get("/api/cases/{case_id}")
    def detail(case_id: str, reveal: bool = False):
        row = dict(case(case_id))
        if not reveal:
            for key in ("judgment", "metrics", "checks", "trace_url"):
                row.pop(key, None)
        images, contexts = [], []
        for relative in row.get("request_capture", []):
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory / "requests"):
                raise HTTPException(409, "Invalid capture path")
            from hashlib import sha256
            expected_hash = row.get("evidence", {}).get("generation_capture_hashes", {}).get(relative)
            if expected_hash and sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise HTTPException(409, "Frozen generation capture changed")
            payload = json.loads(path.read_text())
            for message in payload.get("messages", []):
                content = message.get("content")
                if isinstance(content, str):
                    contexts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if part.get("type") == "text":
                            contexts.append(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            # No remote image fetch and no SVG/scriptable data.
                            if re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+", url):
                                images.append(url)
        row["source_contexts"] = contexts
        row["source_images"] = list(dict.fromkeys(images))
        row["partial_artifacts"] = {}
        if row.get("output") is None:
            for name in ("source.json", "draft.json", "production-review.json", "partial-exchanges.json"):
                path = (directory / "artifacts" / case_id / name).resolve()
                if path.is_relative_to(directory / "artifacts") and path.is_file():
                    row["partial_artifacts"][name] = json.loads(path.read_text())
        captures = row.get("request_capture", [])
        row["capture_integrity"] = ("verified" if all(relative in row.get("evidence", {}).get("generation_capture_hashes", {})
            for relative in captures) else "unverified_legacy_snapshot") if captures else "not_available"
        return row

    @app.get("/api/cases/{case_id}/artifacts/{name}")
    def artifact(case_id: str, name: str):
        descriptor = case(case_id).get("artifacts", {}).get(name)
        if not isinstance(descriptor, dict):
            raise HTTPException(404, "Unknown artifact")
        path = (directory / descriptor["path"]).resolve()
        if not path.is_relative_to(directory / "artifacts") or not path.is_file():
            raise HTTPException(404, "Artifact unavailable")
        from hashlib import sha256
        if sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]:
            raise HTTPException(409, "Artifact bytes changed")
        # Never execute generated HTML, even if a future adapter registers it.
        return FileResponse(path, media_type="application/pdf" if name == "pdf" else "text/plain")

    @app.post("/api/reviews")
    def save(review: HumanReview):
        with lock:
            data = bundle()
            try:
                label = save_review(directory, data, review)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            from evals.reporting import write_report
            write_report(data, directory)
        return label

    @app.get("/api/export")
    def export():
        data = bundle()
        labels = reviews(data)
        return JSONResponse({"experiment_fingerprint": data["fingerprint"], "reviews": labels,
                             "stats": review_stats(data, labels)},
                            headers={"Content-Disposition": 'attachment; filename="human-reviews.json"'})

    return app
