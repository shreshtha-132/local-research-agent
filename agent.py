"""Core research pipeline: search -> fetch -> clean -> summarize locally -> save.

Shared by the CLI (research_agent.py) and the web UI (app.py).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import ollama
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_RESULTS = 5
# Cap each page so every source fits in the model's context window.
DEFAULT_MAX_CHARS = 6000
# Ollama's default context is small and it silently truncates longer prompts.
DEFAULT_NUM_CTX = 16384
OUTPUT_DIR = Path(__file__).parent / "research"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SYSTEM_PROMPT = """You are a careful research assistant. Today's date is {today}.
Answer the user's request using ONLY the numbered sources provided.
- Write in Markdown with clear headings and bullet points.
- Cite sources inline with their number, like [1] or [2][4], after each claim.
- If sources disagree, say so. If the sources don't cover part of the request, say that plainly.
- Do not invent facts, numbers, dates or links that are not in the sources.
- Do not add a sources list at the end; it is added automatically."""


@dataclass
class Source:
    n: int
    title: str
    url: str
    snippet: str
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text)


def search_web(query: str, max_results: int = DEFAULT_RESULTS) -> list[Source]:
    """Search DuckDuckGo (free, no API key)."""
    results = DDGS().text(query, max_results=max_results) or []
    sources = []
    for r in results:
        url = r.get("href")
        if url:
            sources.append(Source(len(sources) + 1, r.get("title", ""), url, r.get("body", "")))
    return sources


def fetch_text(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, str]:
    """Return (text, error). Exactly one of them is non-empty."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        response.raise_for_status()
    except requests.RequestException as e:
        status = getattr(e.response, "status_code", None)
        return "", f"HTTP {status}" if status else e.__class__.__name__
    if "html" not in response.headers.get("Content-Type", "html"):
        return "", f"not HTML ({response.headers.get('Content-Type')})"

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) < 200:
        return "", "no readable text (page may need JavaScript)"
    return text[:max_chars], ""


def fetch_all(sources: list[Source], max_chars: int = DEFAULT_MAX_CHARS) -> Iterator[Source]:
    """Fetch pages in parallel, yielding each source as it finishes (in order)."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_text, s.url, max_chars) for s in sources]
        for source, future in zip(sources, futures):
            source.text, source.error = future.result()
            yield source


def build_prompt(query: str, sources: list[Source]) -> str:
    """Plain text (not JSON) so more of the context goes to actual content."""
    blocks = []
    for s in sources:
        body = s.text or f"(Page could not be fetched; search snippet only.)\n{s.snippet}"
        blocks.append(f"### Source [{s.n}]: {s.title}\nURL: {s.url}\n\n{body}")
    return f"Research request: {query}\n\n" + "\n\n---\n\n".join(blocks)


def stream_digest(
    query: str,
    sources: list[Source],
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
) -> Iterator[str]:
    """Stream the digest text from the local model, chunk by chunk."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=datetime.now().strftime("%B %d, %Y"))},
        {"role": "user", "content": build_prompt(query, sources)},
    ]
    stream = ollama.chat(
        model=model,
        messages=messages,
        stream=True,
        think=think,
        options={"num_ctx": num_ctx},
    )
    for chunk in stream:
        if chunk.message.content:
            yield chunk.message.content


def format_digest(query: str, body: str, sources: list[Source], model: str) -> str:
    """Add a header and a sources list built from real URLs (not model output)."""
    lines = [f"- [{s.n}] [{s.title or s.url}]({s.url})" + ("" if s.ok else " _(snippet only)_") for s in sources]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"> **Query:** {query}  \n> **Generated:** {stamp} with `{model}`\n\n"
        f"{body.strip()}\n\n## Sources\n" + "\n".join(lines) + "\n"
    )


def readable_filename(query: str, when: datetime) -> str:
    """e.g. 'What is Apache Iceberg - 2 Nov 2026, 5.11 PM.md' (safe on macOS, Linux, Windows)."""
    title = re.sub(r'[\\/:*?"<>|\s]+', " ", query).strip().rstrip(".?!")
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0]
    title = title[:1].upper() + title[1:] if title else "Research"
    stamp = f"{when.day} {when:%b %Y}, {when.hour % 12 or 12}.{when:%M} {when:%p}"
    return f"{title} - {stamp}.md"


def save_digest(query: str, digest: str, out_dir: Path = OUTPUT_DIR) -> Path:
    out_dir.mkdir(exist_ok=True)
    path = out_dir / readable_filename(query, datetime.now())
    n = 2
    while path.exists():  # two runs in the same minute
        path = path.with_name(f"{path.stem.split(' (')[0]} ({n}).md")
        n += 1
    path.write_text(digest, encoding="utf-8")
    return path


def saved_reports(out_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Saved reports, newest first."""
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def installed_models() -> list[str]:
    """Names of models pulled in Ollama, or [] if Ollama isn't reachable."""
    try:
        return sorted(m.model for m in ollama.list().models)
    except Exception:
        return []
