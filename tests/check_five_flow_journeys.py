"""Explicit Chromium -> real FastAPI -> isolated Postgres fixture journeys.

Requires a production frontend build and TEST_DATABASE_URL with a loopback
database whose name ends in _test. Auth identity and generation are fixtures;
SQL persistence, API/SSE transport, worker publication and the UI are real.
No credentials, hosted writes or paid provider calls are used.
"""
from contextlib import ExitStack
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from urllib.parse import urlparse
from unittest.mock import patch
from uuid import UUID, uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not parsed.path.endswith("_test"):
        raise SystemExit("Use an isolated loopback TEST_DATABASE_URL ending in _test")
    if not (ROOT / "frontend/.next/BUILD_ID").exists():
        raise SystemExit("Build the current frontend first: cd frontend && npm run build")
    public_url = dotenv_values(ROOT / "frontend/.env.local").get("NEXT_PUBLIC_SUPABASE_URL")
    if not public_url:
        raise SystemExit("The frontend build needs a public Supabase URL; fixture login uses its local storage key")
    import httpx
    from playwright.sync_api import sync_playwright
    import uvicorn
    with TemporaryDirectory(prefix="study-journeys-") as temporary, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"DATABASE_URL": url, "OPENROUTER_API_KEY": "",
            "LANGSMITH_TRACING": "false", "OTEL_ENABLED": "false", "VIDEO_MEDIA_BACKEND": "filesystem",
            "VIDEO_MEDIA_ROOT": temporary, "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}))
        from api.main import app
        from api.auth import current_owner
        from storage.database import connection
        from storage.postgres import ingest_book
        from tests.fixtures import sample_book
        from tests.video_fixtures import publish_video_with_evidence
        from tests.test_video_conversation import FakeAnswerModel
        from study.contracts import TurnResult, EvidenceRef, CitationRef
        from study.conversation import record_turn
        from revision_sheets import store
        from revision_sheets.contracts import Sheet
        from revision_sheets.render import render_pdf
        from revision_sheets.worker import RevisionWorker
        from video.answers import VideoAnswerDependencies
        from video.course_repository import create_course, attach_course_lecture
        from interviews.contracts import InterviewQuestion, AnswerEvaluation, ScoreCard
        owner = uuid4()
        with connection(url) as db:
            db.execute("insert into auth.users (id,email) values (%s,%s)", (owner, f"{owner}@journey.test"))
            parsed_book = sample_book()
            parsed_book.sections[0].texts[0].text = "Reliable event processing uses durable logs, consumer checkpoints and idempotent handlers. " * 25
            book = ingest_book(db, parsed_book, owner_id=owner, title="Journey fixture book", author="Fixture",
                              file_hash=uuid4().hex * 2, page_count=20, parser_version="fixture")
            db.execute("update books set status='ready',ready_at=now() where id=%s", (book,))
            node = db.execute("select id from nodes where book_id=%s order by toc_index limit 1", (book,)).fetchone()["id"]
            video = publish_video_with_evidence(db, owner_id=owner)
            course = create_course(db,owner_id=owner,creation_key=uuid4(),title="Journey fixture course")
            attach_course_lecture(db,course.course_id,video.video_id,owner_id=owner)
            excluded = publish_video_with_evidence(db,owner_id=owner,title="Excluded fixture lecture",url="https://youtu.be/lmnopqrstuv")
            attach_course_lecture(db,course.course_id,excluded.video_id,owner_id=owner)
        app.dependency_overrides[current_owner] = lambda: owner
        stopped = Event()
        server = None
        frontend = None
        worker_thread = None
        page = None
        errors = []
        try:
            def deny_provider(client, request, **kwargs):
                if request.url.host not in {"localhost", "127.0.0.1", "::1", "test"}:
                    raise AssertionError("Journey attempted an external provider")
                return original_send(client, request, **kwargs)
            original_send = httpx.Client.send
            stack.enter_context(patch.object(httpx.Client, "send", deny_provider))
            marker = f"[N{node}:P1]"
            def book_turn(question, state, **kwargs):
                summary = "summarize" in question.lower()
                answer = ("Fixture complete summary: reliable event processing." if summary else
                          "Fixture grounded answer: durable logs protect event processing.") + f" {marker}"
                result = TurnResult(question=question, answer=answer,
                    route="hierarchy_summary" if summary else "retrieval_qa", history_dependency="independent", outcome="answer",
                    evidence=[EvidenceRef(node_id=node,pages=[1],path="Chapter 1",book_id=book,book_title="Journey fixture book",
                                          rank=1,excerpt="Fixture source: reliable event processing.")],
                    citations=[CitationRef(marker=marker,node_id=node,page=1,book_id=book,evidence_rank=1)])
                if kwargs.get("token_callback"):
                    kwargs["token_callback"]("answer", answer)
                return result, record_turn(state, question, result)
            stack.enter_context(patch("api.main.execute_conversation_turn", book_turn))
            model = FakeAnswerModel(*(["Fixture lecture answer: attention weights values [S1]."] * 10),cost=0)
            stack.enter_context(patch("api.video_chat._answer_dependencies", lambda: VideoAnswerDependencies(model=model)))
            course_model = FakeAnswerModel(*(["Fixture course answer: attention weights values [S1]."] * 10),cost=0)
            stack.enter_context(patch("api.course_chat._course_answer_dependencies",lambda: VideoAnswerDependencies(model=course_model)))
            import io
            import wave
            from narration.synthesis import SpeechAudio
            wav = io.BytesIO()
            with wave.open(wav,"wb") as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b"\0\0"*1600)
            stack.enter_context(patch("api.interviews.synthesize_interviewer_speech",lambda *args,**kwargs:
                SpeechAudio(wav.getvalue(),"audio/wav",0,"fixture","fixture")))
            def revision(source, **kwargs):
                raw = (ROOT / "frontend/tests/fixtures/revision_sheet.json").read_text()
                selected = next(iter(source.references))
                sheet = Sheet.model_validate_json(raw.replace("[N1:P1]", selected))
                return sheet, render_pdf(sheet, source_title=source.title,scope_title=source.scope_title,references=source.references), {
                    "fixture": True, "inspected_figures": [], "uninspected_figures": [], "outstanding_findings": [],
                    "concepts": [], "coverage": [], "repairs": [], "figure_references": []}
            stack.enter_context(patch("revision_sheets.worker.generate", revision))
            def followup(source, question, **kwargs):
                return {"items": [{"id":"fixture","text":"Fixture source follow-up: retain durable events.",
                                    "citations":[next(iter(source.references))]}],"insufficient_evidence":"","source_references":source.references}
            stack.enter_context(patch("api.revision_sheets.ask", followup))
            def question(**kwargs):
                topic = kwargs["topic"]
                return InterviewQuestion(topic_key=topic.key,topic_label=topic.label,text="How would you recover a failed event consumer?",
                    expected_points=["Explain durable logs and checkpoints."],suggested_answer="Use durable logs and checkpoints.",
                    citation_markers=sorted(topic.allowed_markers),difficulty="mid"),0
            stack.enter_context(patch("interviews.service.generate_question", question))
            stack.enter_context(patch("interviews.graph.generate_question", question))
            stack.enter_context(patch("interviews.graph.structured_model", lambda *args: object()))
            stack.enter_context(patch("interviews.graph.invoke_structured", lambda *args,**kwargs: (
                AnswerEvaluation(classification="source_aligned",scores=ScoreCard(**{key:4 for key in ScoreCard.model_fields}),
                    concise_feedback="Fixture grade: recovery explanation is complete.",question_complete=True,topic_complete=True),0)))
            def work():
                worker = RevisionWorker(worker_id="journey-fixture",database_url=url)
                while not stopped.wait(0.1):
                    try:
                        with connection(url) as db:
                            job = store.claim(db,"journey-fixture",owner=owner)
                        if job:
                            worker.process(job)
                    except Exception as error:
                        errors.append(f"worker: {error}")
                        return
            worker_thread = Thread(target=work,daemon=True);worker_thread.start()
            api_port = port(); web_port = port()
            # Startup warms hosted embedding/rerank clients. Those are excluded
            # from this fixture transport check; native quality runs cover them.
            server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=api_port,access_log=False,log_level="error",lifespan="off"))
            server_thread = Thread(target=server.run,daemon=True);server_thread.start()
            frontend_log = stack.enter_context(open(Path(temporary)/"frontend.log","w+"))
            frontend = subprocess.Popen(["npm","run","start","--","--hostname","127.0.0.1","--port",str(web_port)],
                cwd=ROOT/"frontend",stdout=frontend_log,stderr=subprocess.STDOUT)
            api_url = f"http://127.0.0.1:{api_port}"; web_url = f"http://127.0.0.1:{web_port}"
            with httpx.Client(timeout=15) as client:
                for _ in range(150):
                    try:
                        if server.started and client.get(web_url).status_code==200: break
                    except httpx.HTTPError: pass
                    if frontend.poll() is not None:
                        frontend_log.seek(0);raise RuntimeError(frontend_log.read())
                    time.sleep(0.1)
                else: raise RuntimeError("Fixture servers did not start")
                created = client.post(api_url+"/api/interviews",json={"source_kind":"book","book_id":book,"node_id":node,
                    "maximum_duration_minutes":15,"target_level":"mid","interview_format":"concept","feedback_mode":"guided"})
                assert created.status_code==201, created.text
                interview = created.json()["session_id"]
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True)
                    context=browser.new_context(viewport={"width":1440,"height":1000},reduced_motion="reduce")
                    def encoded(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
                    now=int(time.time());token=encoded({"alg":"HS256","typ":"JWT"})+"."+encoded({"sub":str(owner),"exp":now+86400,"iat":now,"aud":"authenticated","role":"authenticated"})+".fixture"
                    session={"access_token":token,"refresh_token":"fixture","expires_at":now+86400,"expires_in":86400,"token_type":"bearer",
                             "user":{"id":str(owner),"email":"fixture@journey.test","aud":"authenticated","role":"authenticated","app_metadata":{},"user_metadata":{}}}
                    key="sb-"+urlparse(public_url).hostname.split(".")[0]+"-auth-token"
                    context.add_init_script(f"if(location.origin === {json.dumps(web_url)}) localStorage.setItem({json.dumps(key)},{json.dumps(json.dumps(session))});")
                    # Every API call reaches this real isolated API. Hosted auth,
                    # analytics, playback and other external requests are blocked.
                    def route(request):
                        target=urlparse(request.request.url)
                        if target.path.startswith("/api/"):
                            response=request.fetch(url=api_url+target.path+("?"+target.query if target.query else ""))
                            request.fulfill(response=response)
                        elif target.hostname in {"localhost","127.0.0.1","::1"}:
                            request.continue_()
                        else: request.abort()
                    context.route("**/*",route)
                    page=context.new_page();page.set_default_timeout(20000)
                    page.on("pageerror",lambda error:errors.append(str(error)))
                    page.on("response",lambda response:errors.append(f"HTTP {response.status}: {urlparse(response.url).path}")
                        if response.status>=500 else None)
                    page.goto(web_url)
                    composer=page.locator("textarea[role=combobox]");composer.wait_for()
                    composer.fill("Explain reliable event processing")
                    page.get_by_role("button",name="Send question",exact=True).click()
                    page.get_by_text("Fixture grounded answer: durable logs protect event processing.",exact=False).wait_for()
                    conversations=client.get(api_url+"/api/conversations").json()["conversations"]
                    conversation=conversations[0]["conversation_id"]
                    assert len(client.get(api_url+f"/api/conversations/{conversation}").json()["turns"])==1
                    composer.fill("Summarize Chapter 1")
                    page.get_by_role("button",name="Send question",exact=True).click()
                    page.get_by_text("Fixture complete summary: reliable event processing.",exact=False).wait_for()
                    assert len(client.get(api_url+f"/api/conversations/{conversation}").json()["turns"])==2
                    page.reload();page.get_by_role("button",name="Explain reliable event processing",exact=False).first.click()
                    page.get_by_text("Fixture complete summary: reliable event processing.",exact=False).wait_for()
                    print("PASS chat + summary: browser SSE, persisted turns and reopen",flush=True)
                    page.get_by_role("button",name="Revision sheet",exact=True).click()
                    page.locator("#revision-source").select_option(str(book))
                    page.locator("#revision-chapter").select_option(str(node))
                    page.get_by_role("button",name="Create sheet",exact=True).click()
                    page.get_by_role("link",name="Download PDF",exact=True).wait_for(timeout=30000)
                    with page.expect_download() as download:page.get_by_role("link",name="Download PDF",exact=True).click()
                    assert Path(download.value.path()).read_bytes().startswith(b"%PDF")
                    page.get_by_role("button",name="Ask about this",exact=True).click()
                    page.locator("#revision-question").fill("How do I recover?")
                    page.get_by_role("button",name="Ask",exact=True).click()
                    page.get_by_text("Fixture source follow-up: retain durable events.",exact=False).wait_for()
                    page.keyboard.press("Escape")
                    print("PASS revision: UI enqueue, worker publication, PDF bytes and source follow-up",flush=True)
                    page.goto(web_url+f"/videos/{video.video_id}")
                    page.get_by_role("textbox",name="Ask about this lecture").fill("What does attention do?")
                    page.get_by_role("button",name="Ask",exact=True).click()
                    page.get_by_text("Fixture lecture answer: attention weights values",exact=False).wait_for()
                    assert client.get(api_url+f"/api/videos/{video.video_id}/conversations").json()["conversations"][0]["turn_count"]==1
                    print("PASS lecture: UI native evidence generation, SSE and SQL persistence",flush=True)
                    page.goto(web_url+f"/courses/{course.course_id}")
                    page.get_by_role("checkbox",name="Include lecture 2: Excluded fixture lecture",exact=True).uncheck()
                    page.get_by_role("textbox",name="Ask across the selected course lectures").fill("What does attention do?")
                    page.get_by_role("button",name="Ask",exact=True).click()
                    page.get_by_text("Fixture course answer: attention weights values",exact=False).wait_for()
                    history=client.get(api_url+f"/api/courses/{course.course_id}/conversations").json()["conversations"]
                    detail=client.get(api_url+f"/api/course-conversations/{history[0]['conversation_id']}").json()
                    assert detail["selected_video_ids"]==[str(video.video_id)]
                    assert all(ref["video_id"]==str(video.video_id) for ref in detail["turns"][0]["result"]["evidence"])
                    page.reload()
                    page.get_by_role("button",name="What does attention do?",exact=False).first.click()
                    page.get_by_text("Fixture course answer: attention weights values",exact=False).wait_for()
                    print("PASS course: explicit exclusion, native SSE, SQL and browser reopen",flush=True)
                    page.goto(web_url+f"/interviews/{interview}")
                    page.get_by_role("button",name="Resume",exact=True).click()
                    page.get_by_text("How would you recover a failed event consumer?",exact=True).wait_for()
                    page.get_by_role("button",name="Pause",exact=True).click()
                    page.get_by_text("This interview is paused",exact=True).wait_for()
                    page.reload();page.get_by_role("button",name="Resume interview",exact=True).click()
                    page.get_by_placeholder("Speak or type your answer.",exact=False).fill("Read durable logs from the last committed checkpoint and use idempotent handlers.")
                    page.get_by_role("button",name="Send answer",exact=True).click()
                    page.get_by_text("Fixture grade: recovery explanation is complete.",exact=False).wait_for()
                    page.get_by_role("button",name="End interview",exact=True).click()
                    page.get_by_role("heading",name="Your interview report",exact=True).wait_for()
                    report=client.get(api_url+f"/api/interviews/{interview}/report").json()
                    assert report["session"]["metrics"]["questions_answered"]==1
                    print("PASS interview: real state service, pause/reload/resume, grade persistence and report",flush=True)
                    page.set_viewport_size({"width":390,"height":844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement !== document.body")
                    assert not errors, errors
                    browser.close()
        except Exception:
            print("Fixture journey diagnostics:", errors,flush=True)
            if page:
                try: print(page.locator("body").inner_text()[-6000:],flush=True)
                except Exception: pass
            raise
        finally:
            stopped.set()
            if worker_thread: worker_thread.join(timeout=10)
            if server: server.should_exit=True;server_thread.join(timeout=5)
            if frontend:
                frontend.terminate()
                try:frontend.wait(timeout=5)
                except subprocess.TimeoutExpired:frontend.kill();frontend.wait(timeout=5)
            app.dependency_overrides.pop(current_owner,None)
            with connection(url) as db:db.execute("delete from auth.users where id=%s",(owner,))


if __name__ == "__main__":
    main()
