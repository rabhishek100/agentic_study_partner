"""Explicit browser smoke on fake artifacts; never creates real human labels."""
from pathlib import Path
import secrets
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import time

from playwright.sync_api import sync_playwright
import uvicorn

from evals.review_server import create_app
from tests.test_eval_reviews import make_bundle


def main():
    with TemporaryDirectory(prefix="study-eval-review-check-") as temporary:
        directory = Path(temporary)
        make_bundle(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        server = uvicorn.Server(uvicorn.Config(create_app(directory, token=token, port=port),
                    host="127.0.0.1", port=port, access_log=False, log_level="error"))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                errors, dialogs, external = [], [], []
                page.on("pageerror", lambda value: errors.append(str(value)))
                page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                page.on("request", lambda request: external.append(request.url) if request.url.startswith("https://remote.example") else None)
                page.goto(f"http://127.0.0.1:{port}/?token={token}")
                page.locator("iframe[title='Generated revision sheet PDF']").wait_for()
                assert page.get_by_text("Fixture run: these are plumbing checks", exact=False).is_visible()
                assert not page.locator("#artifact script").count()
                assert page.get_by_text("1 / 1 LLM reviewed", exact=True).is_visible()
                assert page.locator("#automated-review").get_by_text("Correctness: 4 / 4", exact=True).is_visible()
                assert not page.locator("#reviewer").is_visible()
                page.get_by_text("Optional manual review", exact=True).click()
                page.locator("#reviewer").fill("Automation fixture")
                page.locator("#notes").fill("Fixture note to verify persistence")
                page.locator("#grounding").select_option("unsupported")
                page.locator("#correctness").select_option("2")
                page.get_by_role("button", name="Source evidence", exact=True).click()
                page.get_by_text("Full source & generation context", exact=False).wait_for()
                assert page.locator("#notes").input_value() == "Fixture note to verify persistence"
                assert page.locator("#correctness").input_value() == "2"
                assert not external and not dialogs
                page.get_by_role("button", name="Save", exact=True).click()
                page.get_by_text("Saved locally.", exact=True).wait_for()
                page.reload()
                page.get_by_text("1 / 1 LLM reviewed", exact=True).wait_for()
                page.get_by_text("Optional manual review", exact=True).click()
                assert page.locator("#notes").input_value() == "Fixture note to verify persistence"
                assert page.locator("#grounding").input_value() == "unsupported"
                with page.expect_download() as download:
                    page.get_by_role("button", name="Export reviews", exact=True).click()
                assert download.value.suggested_filename == "human-reviews.json"
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement !== document.body")
                assert not errors
                browser.close()
                print("PASS: browser save/reload/export, tab drafts, PDF, narrow layout, keyboard, unsafe-content and remote-image checks (fixture only)")
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
