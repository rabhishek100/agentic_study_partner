"""Read-only source binding and native production calls for the five-flow suite.

These calls exercise generation, not API persistence or a connected UI journey.
Source material and artifacts belong in the private experiment directory.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from uuid import UUID

from psycopg import sql

from evals.budget import atomic_json
from evals.suite import fingerprint
from storage.database import connection, parse_owner_id

ROOT = Path(__file__).resolve().parents[1]


def read_dataset(path):
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT / "evaluation"):
        raise ValueError("Dataset must be inside evaluation/")
    return json.loads(target.read_text())


def table_digest(current, schema, table, where, parameters):
    """Hash complete rows without exporting canonical prose or embedding vectors."""
    rows = current.execute(sql.SQL("select md5(t::text) as digest from {}.{} t where {} order by digest")
                           .format(sql.Identifier(schema), sql.Identifier(table), sql.SQL(where)),
                           parameters).fetchall()
    return fingerprint([row["digest"] for row in rows])


def bind_book(current, owner, source):
    books = current.execute("select id, file_hash, title from public.books where owner_id=%s and file_hash=%s",
                            (owner, source["file_hash"])).fetchall()
    if len(books) != 1:
        raise ValueError("Gold source hash must identify exactly one owned book")
    book = books[0]
    nodes = current.execute("select id,toc_index,parent_id,node_type,title,start_page,end_page from public.nodes "
                            "where owner_id=%s and book_id=%s order by toc_index", (owner, book["id"])).fetchall()
    mapping = {str(row["toc_index"] + 1): row for row in nodes if row["toc_index"] is not None}
    if len(mapping) != len(nodes):
        raise ValueError("Gold TOC mapping is ambiguous or unavailable")
    digests = {table: table_digest(current, "public", table, "owner_id=%s and book_id=%s", (owner, book["id"]))
               for table in ("nodes", "content_blocks")}
    for table in ("chunks", "chunk_embeddings"):
        digests[table] = table_digest(current, "public", table, "owner_id=%s and source_book_id=%s", (owner, book["id"]))
    images = current.execute("select md5(i::text) as digest from public.image_blocks i "
                             "join public.content_blocks b on b.id=i.block_id and b.owner_id=i.owner_id "
                             "where b.owner_id=%s and b.book_id=%s order by digest", (owner, book["id"])).fetchall()
    digests["images"] = fingerprint(images)
    return {**book, "nodes": mapping, "fingerprint": fingerprint({"book": book, "tables": digests})}


def bind_video(current, owner, video_id):
    from video.repository import load_video
    from video.lecture import _published_version
    video = load_video(current, video_id, owner_id=owner)
    if video is None:
        raise ValueError("Owned lecture is unavailable")
    version = _published_version(current, owner=owner, video=UUID(str(video_id)))
    digests = {table: table_digest(current, "video", table,
                                   "owner_id=%s and video_id=%s and ingestion_version_id=%s", (owner, video_id, version))
               for table in ("evidence_units", "evidence_embeddings", "frames", "visual_events")}
    digests["resources"] = table_digest(current, "video", "video_resources", "owner_id=%s and video_id=%s", (owner, video_id))
    return {"id": str(video_id), "title": video["title"], "version": str(version),
            "fingerprint": fingerprint({"version": str(version), "title": video["title"], "tables": digests})}


def bind_sources(manifest, *, owner_id, database_url=None):
    """Bind the entire experiment up front, including dataset bytes and versions."""
    owner = parse_owner_id(owner_id)
    bindings = {"owner_id": str(owner), "books": {}, "videos": {}, "courses": {}, "datasets": {}}
    with connection(database_url, readonly=True) as current:
        for case in manifest.cases:
            dataset = case.inputs.get("dataset")
            if dataset and dataset not in bindings["datasets"]:
                bindings["datasets"][dataset] = fingerprint(read_dataset(dataset))
            source = case.source
            if source["kind"] == "book" and source["file_hash"] not in bindings["books"]:
                bindings["books"][source["file_hash"]] = bind_book(current, owner, source)
            if source["kind"] == "video" and source["video_id"] not in bindings["videos"]:
                bindings["videos"][source["video_id"]] = bind_video(current, owner, source["video_id"])
            if source["kind"] == "course" and source["title"] not in bindings["courses"]:
                from video.course_repository import list_course_lectures
                courses = current.execute("select id,title from video.courses where owner_id=%s and title=%s", (owner, source["title"])).fetchall()
                if len(courses) != 1:
                    raise ValueError("Course title must identify exactly one owned course")
                course = courses[0]
                lectures = list_course_lectures(current, course["id"], owner_id=owner)
                members = []
                for lecture in lectures:
                    # Match the serving layer: unpublished lectures cannot contribute evidence.
                    if lecture["readiness_status"] not in {"ready", "degraded"}:
                        continue
                    video_id = str(lecture["video_id"])
                    video = bind_video(current, owner, video_id)
                    bindings["videos"][video_id] = video
                    members.append({**video, "lecture_index": lecture["lecture_index"]})
                if not members:
                    raise ValueError("Course has no published members")
                bindings["courses"][source["title"]] = {"id": str(course["id"]), "title": course["title"],
                                                        "members": members, "fingerprint": fingerprint(members)}
    return bindings


def remap_gold(expected, book):
    """Old gold IDs are TOC ordinals; reject locators outside the matching source."""
    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            result = {key: visit(item) for key, item in value.items()}
            if value.get("node_id") is not None:
                node = book["nodes"].get(str(value["node_id"]))
                if node is None:
                    raise ValueError("Gold node is absent from the canonical TOC")
                pages = value.get("pages", [])
                if any(not node["start_page"] <= page <= node["end_page"] for page in pages):
                    raise ValueError("Gold evidence page is outside its canonical node")
                result["node_id"] = node["id"]
            if "book_id" in value:
                result["book_id"] = book["id"]
            return result
        if isinstance(value, str):
            def marker(match):
                node = book["nodes"].get(match[1])
                if node is None or not node["start_page"] <= int(match[2]) <= node["end_page"]:
                    raise ValueError("Gold citation is outside its canonical node")
                return f"[N{node['id']}:P{match[2]}]"
            return re.sub(r"\[N(\d+):P(\d+)\]", marker, value)
        return value
    return visit(deepcopy(expected))


def write_artifact(directory, case_id, name, data):
    path = Path(directory) / "artifacts" / case_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    from hashlib import sha256
    return {"path": path.relative_to(directory).as_posix(), "sha256": sha256(data).hexdigest(), "bytes": len(data)}


class NativeAdapters:
    def __init__(self, bindings, *, database_url=None, retrieval_mode="hybrid_rerank"):
        self.bindings, self.database_url, self.retrieval_mode = bindings, database_url, retrieval_mode
        self.owner = parse_owner_id(bindings["owner_id"])

    def registry(self):
        return {name: getattr(self, name) for name in ("book", "video", "course", "sheet", "interview_grade", "ideal")}

    def dataset(self, case):
        path = case.inputs["dataset"]
        data = read_dataset(path)
        if fingerprint(data) != self.bindings["datasets"][path]:
            raise ValueError("Frozen dataset changed")
        return data

    def book_binding(self, case):
        frozen = self.bindings["books"][case.source["file_hash"]]
        with connection(self.database_url, readonly=True) as current:
            if bind_book(current, self.owner, case.source) != frozen:
                raise ValueError("Canonical source or retrieval build changed")
        return frozen

    def book(self, case, parents, directory):
        from evals.multiturn import ProjectRunner, _score
        from study.contracts import ConversationState
        from study.conversation import new_conversation_state
        book = self.book_binding(case)
        self.dataset(case)
        state = ConversationState.model_validate(parents[-1]["state"]) if parents else new_conversation_state(
            book_ids=[book["id"]], conversation_id=case.inputs["conversation_id"])
        previous = state.model_dump(mode="json")
        result, state = ProjectRunner(self.owner, self.database_url, self.retrieval_mode)(case.inputs["question"], state)
        if result.outcome == "error":
            raise RuntimeError("Production book turn returned an error outcome")
        gold = remap_gold(case.expected, book)
        return {"output": result.model_dump(mode="json"), "state": state.model_dump(mode="json"),
                "evidence": {"references": [item.model_dump(mode="json") for item in result.evidence], "prior_state": previous,
                             "bound_expected": gold}, "checks": _score(gold, result, state), "source_fingerprint": book["fingerprint"]}

    def video(self, case, parents, directory):
        from api.video_chat import _answer_dependencies
        from evals.video import _score
        from video.contracts import VideoConversationState
        from video.conversation import execute_video_turn, new_video_conversation_state
        frozen = self.bindings["videos"][case.source["video_id"]]
        self.dataset(case)
        state = VideoConversationState.model_validate(parents[-1]["state"]) if parents else new_video_conversation_state(
            video_id=frozen["id"], conversation_id=case.inputs["conversation_id"])
        previous = state.model_dump(mode="json")
        with connection(self.database_url, readonly=True) as current:
            current.execute("set transaction isolation level repeatable read")
            if bind_video(current, self.owner, frozen["id"]) != frozen:
                raise ValueError("Published lecture version changed")
            dependencies = _answer_dependencies()
            if dependencies.media_store is None:
                raise ValueError("Production frame store unavailable; multimodal evaluation cannot fall back to text")
            result, state = execute_video_turn(current, case.inputs["question"], state, owner_id=self.owner,
                                               video_id=frozen["id"], video_title=frozen["title"], dependencies=dependencies)
        if result.outcome == "error":
            raise RuntimeError("Production video turn returned an error outcome")
        return {"output": result.model_dump(mode="json"), "state": state.model_dump(mode="json"),
                "evidence": {"references": [item.model_dump(mode="json") for item in result.evidence], "prior_state": previous},
                "checks": _score(case.expected, result), "source_fingerprint": frozen["fingerprint"]}

    def course(self, case, parents, directory):
        from api.video_chat import _answer_dependencies
        from video.course_contracts import CourseConversationState
        from video.course_conversation import execute_course_turn, new_course_conversation_state
        from video.course_repository import list_course_lectures
        frozen = self.bindings["courses"][case.source["title"]]
        state = CourseConversationState.model_validate(parents[-1]["state"]) if parents else new_course_conversation_state(course_id=frozen["id"])
        previous = state.model_dump(mode="json")
        with connection(self.database_url, readonly=True) as current:
            current.execute("set transaction isolation level repeatable read")
            members = list_course_lectures(current, frozen["id"], owner_id=self.owner)
            live = [{**bind_video(current, self.owner, str(member["video_id"])), "lecture_index": member["lecture_index"]}
                    for member in members if member["readiness_status"] in {"ready", "degraded"}]
            if live != frozen["members"]:
                raise ValueError("Published course membership or source versions changed")
            title_filter = case.inputs.get("lecture_title_filter")
            selected = [item for item in live if not title_filter or title_filter.casefold() in item["title"].casefold()]
            if not selected:
                raise ValueError("Selected course scope is empty")
            dependencies = _answer_dependencies()
            if dependencies.media_store is None:
                raise ValueError("Production frame store unavailable; course evaluation cannot fall back to text")
            result, state, versions = execute_course_turn(current, case.inputs["question"], state, owner_id=self.owner,
                course_id=frozen["id"], course_title=frozen["title"], video_ids=[UUID(item["id"]) for item in selected],
                dependencies=dependencies)
        if result.outcome == "error":
            raise RuntimeError("Production course turn returned an error outcome")
        allowed = {item["id"] for item in selected}
        checks = {"outcome": result.outcome == ("answer" if case.expected["answerable"] else "abstain"),
                  "selected_sources_only": all(item.video_id in allowed for item in [*result.evidence, *result.citations]),
                  "claim_support": None}
        # Course citations also bind the lecture identity, unlike video-only markers.
        from evals.scoring import video_citations
        checks["citations_valid"] = video_citations(result, required=case.expected["answerable"]) and all(
            any(item.rank == citation.evidence_rank and item.video_id == citation.video_id for item in result.evidence)
            for citation in result.citations) if result.citations else (False if case.expected["answerable"] else None)
        return {"output": result.model_dump(mode="json"), "state": state.model_dump(mode="json"), "checks": checks,
                "evidence": {"references": [item.model_dump(mode="json") for item in result.evidence], "prior_state": previous,
                             "published_versions": {str(key): str(value) for key, value in versions.items()}},
                "source_fingerprint": frozen["fingerprint"]}

    def sheet(self, case, parents, directory):
        from revision_sheets.contracts import ScopeRequest
        from revision_sheets.source import load_source
        from revision_sheets.generate import generate
        book = self.book_binding(case)
        self.dataset(case)
        gold = remap_gold(case.expected, book)
        if case.inputs.get("scope_kind") == "paper":
            request = ScopeRequest(scope_kind="paper", book_id=book["id"])
        else:
            chapters = [node for node in book["nodes"].values() if node["node_type"] == "chapter" and node["parent_id"] is None]
            matches = [node for node in chapters if re.match(rf"Chapter\s+{case.inputs['chapter']}[.\s]", node["title"], re.I)]
            if len(matches) != 1:
                raise ValueError("Chapter number must bind exactly one canonical chapter")
            request = ScopeRequest(scope_kind="chapter", book_id=book["id"], chapter_node_id=matches[0]["id"])
        with connection(self.database_url, readonly=True) as current:
            source = load_source(current, owner_id=self.owner, request=request)
        atomic_json(Path(directory) / "artifacts" / case.id / "source.json",
                    {"text": source.text, "references": source.references, "units": {key: sorted(value) for key,value in source.units.items()},
                     "fingerprint": source.fingerprint})
        sheet, pdf, provenance = generate(source,
            on_draft=lambda draft: atomic_json(Path(directory) / "artifacts" / case.id / "draft.json", draft.model_dump(mode="json")),
            on_review=lambda review: atomic_json(Path(directory) / "artifacts" / case.id / "production-review.json", review.model_dump(mode="json")))
        artifacts = {"pdf": write_artifact(directory, case.id, "sheet.pdf", pdf),
                     "provenance": write_artifact(directory, case.id, "provenance.json", json.dumps(provenance, default=str).encode())}
        return {"output": sheet.model_dump(mode="json"), "evidence": {"text": source.text, "references": source.references,
                                                                   "bound_expected": gold},
                "artifacts": artifacts, "source_fingerprint": source.fingerprint,
                "checks": {"production_findings_clear": not provenance.get("outstanding_findings"),
                           "independent_concept_coverage": None, "human_layout_review": None}}

    def interview_grade(self, case, parents, directory):
        from evals.interview_candidate_calibration import InterviewCandidateCalibrationDataset, evaluate_candidate_profile
        from interviews.contracts import AnswerEvaluation
        from interviews.models import structured_model
        data = InterviewCandidateCalibrationDataset.model_validate(self.dataset(case))
        profile = next(item for item in data.profiles if item.id == case.inputs["profile_id"])
        scenario = next(item for item in data.scenarios if item.id == profile.scenario_id)
        result = evaluate_candidate_profile(data, scenario, profile, model=structured_model(AnswerEvaluation))
        if result.get("provider_error"):
            raise RuntimeError(result["provider_error"])
        return {"output": {**result, "question": scenario.question.model_dump(mode="json")}, "checks": result["checks"],
                "evidence": {"text": scenario.evidence, "allowed_markers": scenario.allowed_markers},
                "source_fingerprint": fingerprint(scenario.model_dump(mode="json"))}

    def ideal(self, case, parents, directory):
        from scripts.evaluate_ideal_interview_flows import inventory
        from interviews.ideal_generation import generate_ideal_exchange
        from interviews.ideal_contracts import IdealInterviewExchangeDraft
        from interviews.models import structured_model
        seed = next(item for item in self.dataset(case)["cases"] if item["id"] == case.inputs["case_id"])
        inputs = seed["inputs"]
        source = inventory(inputs)
        exchanges = []
        model = structured_model(IdealInterviewExchangeDraft, temperature=0.25)
        for index, topic in enumerate(source.topics):
            exchange, _ = generate_ideal_exchange(inventory=source, topic=topic, interview_format=inputs["interview_format"],
                    target_level=inputs["target_level"], index=index, previous=exchanges, model=model)
            exchanges.append(exchange)
            atomic_json(Path(directory) / "artifacts" / case.id / "partial-exchanges.json",
                        [item.model_dump(mode="json") for item in exchanges])
        output = {"exchanges": [item.model_dump(mode="json") for item in exchanges]}
        from evals.ideal_interview import score_ideal_flow
        return {"output": output,
                "evidence": {"topics": inputs["topics"]}, "source_fingerprint": fingerprint(inputs),
                "checks": {**{key: bool(value) for key,value in score_ideal_flow(output, case.expected).items()},
                           "human_interview_usefulness": None}}
