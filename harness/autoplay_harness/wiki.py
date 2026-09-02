from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


class StardewWiki:
    API_URL = "https://stardewvalleywiki.com/mediawiki/api.php"

    def __init__(self) -> None:
        self.cache: dict[str, list[dict[str, Any]]] = {}

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        cache_key = query.strip().lower()
        if cache_key in self.cache:
            return self.cache[cache_key]
        pages = self._search_pages(query, limit)
        if not pages:
            stop_words = {"a", "an", "and", "for", "how", "in", "of", "the", "to", "where"}
            terms = [term for term in re.findall(r"[A-Za-z0-9']+", query) if term.lower() not in stop_words]
            for term in sorted(terms, key=len, reverse=True):
                pages = self._search_pages(term, limit)
                if pages:
                    break
        results = [self._read_page(page.get("title", "")) for page in pages]
        self.cache[cache_key] = results
        return results

    def _search_pages(self, query: str, limit: int) -> list[dict[str, Any]]:
        parameters = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
                "formatversion": 2,
            }
        )
        request = urllib.request.Request(
            f"{self.API_URL}?{parameters}",
            headers={"User-Agent": "Autoplay/0.1 local-game-agent"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        return payload.get("query", {}).get("search", [])

    def _read_page(self, title: str) -> dict[str, Any]:
        parameters = urllib.parse.urlencode(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "redirects": 1,
                "format": "json",
                "formatversion": 2,
            }
        )
        request = urllib.request.Request(
            f"{self.API_URL}?{parameters}",
            headers={"User-Agent": "Autoplay/0.1 local-game-agent"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        extractor = _TextExtractor()
        extractor.feed(payload.get("parse", {}).get("text", ""))
        extract = re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()[:1600]
        return {
            "title": title,
            "url": "https://stardewvalleywiki.com/" + urllib.parse.quote(title.replace(" ", "_")),
            "extract": extract,
        }


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())
