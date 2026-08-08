"""Web search retriever and model-knowledge sufficiency evaluation."""

import json
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from study.contracts import WebSourceRef

logger = logging.getLogger("study_partner.web_search")


class ChatModel(Protocol):
    def invoke(self, messages: Any) -> Any: ...


class DDGHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.in_title = False
        self.in_snippet = False
        self.curr: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "") or ""
        if tag == "a" and "result__a" in cls:
            href = attrs_dict.get("href", "") or ""
            self.curr = {"url": href, "title": "", "snippet": ""}
            self.in_title = True
        elif tag == "a" and "result__snippet" in cls:
            self.in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_title:
            self.in_title = False
        elif tag == "a" and self.in_snippet:
            self.in_snippet = False
            if self.curr.get("title"):
                self.results.append(self.curr)
                self.curr = {}

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.curr["title"] += data
        elif self.in_snippet:
            self.curr["snippet"] += data


def _clean_ddg_url(url: str) -> str:
    """Extract actual destination URL from DuckDuckGo redirect link."""
    if "duckduckgo.com/l/?" in url or "duckduckgo.com/d/?" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])
    return url


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def search_tavily(query: str, api_key: str, max_results: int = 5) -> list[WebSourceRef]:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    with httpx.Client(timeout=10.0) as client:
        res = client.post(url, json=payload)
        res.raise_for_status()
        data = res.json()

    results: list[WebSourceRef] = []
    for idx, item in enumerate(data.get("results", []), start=1):
        target_url = item.get("url", "")
        title = item.get("title", "") or target_url
        snippet = item.get("content", "") or ""
        results.append(
            WebSourceRef(
                url=target_url,
                title=title.strip(),
                snippet=snippet.strip(),
                domain=_extract_domain(target_url),
                rank=idx,
            )
        )
    return results


def search_duckduckgo(query: str, max_results: int = 5) -> list[WebSourceRef]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        res = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
        )
        res.raise_for_status()

    parser = DDGHTMLParser()
    parser.feed(res.text)

    results: list[WebSourceRef] = []
    for idx, item in enumerate(parser.results[:max_results], start=1):
        raw_url = item.get("url", "")
        clean_url = _clean_ddg_url(raw_url)
        title = item.get("title", "").strip() or clean_url
        snippet = item.get("snippet", "").strip()
        results.append(
            WebSourceRef(
                url=clean_url,
                title=title,
                snippet=snippet,
                domain=_extract_domain(clean_url),
                rank=idx,
            )
        )
    return results


def search_web_sources(query: str, max_results: int = 5) -> list[WebSourceRef]:
    """Retrieve top web search sources using Tavily (if key provided) or DuckDuckGo."""
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            return search_tavily(query, tavily_key, max_results=max_results)
        except Exception as err:
            logger.warning(f"Tavily search failed, falling back to DuckDuckGo: {err}")

    try:
        return search_duckduckgo(query, max_results=max_results)
    except Exception as err:
        logger.error(f"DuckDuckGo search failed: {err}")
        return []


def assess_model_knowledge_sufficiency(question: str, model: ChatModel) -> bool:
    """Reason whether model parametric knowledge is sufficient or if web search is needed.

    Returns True if LLM knowledge is sufficient to give a high-quality answer.
    Returns False if real-time web search is required.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    system_prompt = (
        "You are an AI reasoning evaluator. Evaluate if the user's question can be "
        "answered accurately and completely using standard AI general model knowledge, "
        "OR if it requires live web search (e.g., current real-time events, news, "
        "live data, specific live APIs, obscure specific website docs).\n\n"
        "Output ONLY a JSON object with schema:\n"
        '{"is_sufficient": boolean, "reason": "short explanation"}'
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ]
    try:
        response = model.invoke(messages)
        content = getattr(response, "content", str(response))
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            is_sufficient = parsed.get("is_sufficient")
            if isinstance(is_sufficient, bool):
                return is_sufficient
    except Exception as err:
        logger.warning(f"Sufficiency check failed, defaulting to True: {err}")

    return True
