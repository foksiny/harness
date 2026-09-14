"""
Exa Web Search tool for Harness (Free, No API Key required).
Aggressively searches the live web: tries multiple free engines in a chain
(DuckDuckGo HTML + Lite + Instant Answers, Wikipedia search API, Bing) and
refuses to give up on the first empty result — it reformulates the query and
retries, then enriches thin snippets by peeking at the page body.
"""
import base64
import html
import json
import re
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from harness.tools.base import Tool

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
HTML_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ExaSearchTool(Tool):
    name = "exa_search"
    description = (
        "Search the live web for documentation, errors, or current facts (free, no API key). "
        "Aggressively retries across DuckDuckGo, Wikipedia, and Bing engines with query "
        "reformulation until a result is found."
    )
    action_type = "web_search"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query keywords."},
            "num_results": {"type": "integer", "description": "Number of results to return (default: 5, max: 10)."},
        },
        "required": ["query"],
    }

    # ── Core entry point ────────────────────────────────────────────────────

    def execute(self, query: str, num_results: int = 5, **kwargs) -> str:
        q = (query or "").strip()
        if not q:
            return "Error: Empty search query."
        try:
            limit = min(max(1, int(num_results)), 10)
        except (TypeError, ValueError):
            limit = 5

        seen_urls: set = set()
        collected: List[Dict[str, str]] = []
        engines_tried: List[str] = []

        # Round 1: original query across every engine in the chain.
        self._run_chain(q, limit, seen_urls, collected, engines_tried)

        # Round 2+: aggressive reformulation — never settle for an empty bag.
        if not collected:
            for variant in self._query_variants(q):
                before = len(collected)
                self._run_chain(variant, limit, seen_urls, collected, engines_tried)
                if collected:  # any result from a reformulation is a win
                    break
                if len(collected) != before:  # pragma: no cover - safety
                    break

        if not collected:
            tried = ", ".join(dict.fromkeys(engines_tried)) or "all"
            return (
                f"No results found for '{q}' after searching {tried} engines "
                f"with {len(self._query_variants(q)) + 1} query formulations."
            )

        self._enrich(collected[:limit])

        lines = [f"Exa Web Search Results for '{q}' ({len(collected)} found):\n"]
        for i, it in enumerate(collected[:limit], 1):
            lines.append(f"{i}. **{it['title']}**")
            lines.append(f"   URL: {it['url']}")
            if it.get("engine"):
                lines.append(f"   Source: {it['engine']}")
            snippet = it.get("snippet") or "No snippet available"
            lines.append(f"   Snippet: {snippet[:400]}\n")
        return "\n".join(lines)

    # ── Engine chain ────────────────────────────────────────────────────────

    def _run_chain(self, query: str, limit: int, seen: set, collected: List, engines_tried: List) -> None:
        """Run the engine chain for one query, stopping once we have enough."""
        engines = (
            (self._search_ddg_html, "DuckDuckGo"),
            (self._search_ddg_lite, "DuckDuckGo Lite"),
            (self._search_wikipedia, "Wikipedia"),
            (self._search_ddg_instant, "DuckDuckGo Instant Answer"),
            (self._search_bing, "Bing"),
        )
        for fetch_fn, engine in engines:
            engines_tried.append(engine)
            try:
                results = fetch_fn(query, limit - len(collected))
            except Exception:
                results = []  # an engine failure must never abort the chain
            added = 0
            for r in results:
                url = (r.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                r["engine"] = engine
                collected.append(r)
                added += 1
            if len(collected) >= limit:
                return

    # ── Engines ─────────────────────────────────────────────────────────────

    def _search_ddg_html(self, query: str, limit: int) -> List[Dict[str, Any]]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        html_text = self._fetch(url, timeout=9)
        items = []
        blocks = html_text.split('<div class="result results_links')
        for b in blocks[1:]:
            link_m = re.search(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
            if not link_m:
                continue
            raw_link = self._unpack_redirect(link_m.group(1))
            title = self._strip_tags(link_m.group(2)) or raw_link
            snippet_m = re.search(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', b, re.DOTALL)
            snippet = self._strip_tags(snippet_m.group(1)) if snippet_m else ""
            items.append({"title": title, "url": raw_link, "snippet": snippet})
            if len(items) >= limit:
                break
        return items

    def _search_ddg_lite(self, query: str, limit: int) -> List[Dict[str, Any]]:
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote_plus(query)}"
        html_text = self._fetch(url, timeout=8)
        items = []
        for link_m in re.finditer(r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', html_text, re.DOTALL):
            items.append({
                "title": self._strip_tags(link_m.group(2)),
                "url": self._unpack_redirect(link_m.group(1)),
                "snippet": "",
            })
            if len(items) >= limit:
                break
        return items

    def _search_ddg_instant(self, query: str, limit: int) -> List[Dict[str, Any]]:
        url = ("https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query, "format": "json", "no_html": 1, "skip_disambig": 1, "t": "harness",
        }))
        data = json.loads(self._fetch(url, timeout=8))
        items: List[Dict[str, Any]] = []
        abstract = (data.get("AbstractText") or "").strip()
        if abstract:
            items.append({
                "title": (data.get("Heading") or query).strip(),
                "url": data.get("AbstractURL") or "",
                "snippet": abstract,
            })

        def walk(topics) -> None:
            for t in topics or []:
                if not isinstance(t, dict):
                    continue
                nested = t.get("Topics")
                if isinstance(nested, list):
                    walk(nested)
                    continue
                text = (t.get("Text") or "").strip()
                if text:
                    items.append({
                        "title": text.split(" - ")[0],
                        "url": t.get("FirstURL") or "",
                        "snippet": text,
                    })
                if len(items) >= limit:
                    return

        walk(data.get("RelatedTopics"))
        if not items and data.get("Answer"):
            items.append({"title": query, "url": "", "snippet": data["Answer"]})
        return items[:limit]

    def _search_wikipedia(self, query: str, limit: int) -> List[Dict[str, Any]]:
        url = ("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "format": "json",
            "srsearch": query, "srlimit": str(min(limit, 10)),
        }))
        data = json.loads(self._fetch(url, timeout=8))
        items = []
        for hit in data.get("query", {}).get("search", [])[:limit]:
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            snippet = self._strip_tags(hit.get("snippet") or "")
            items.append({
                "title": title,
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                "snippet": snippet,
            })
        return items

    def _search_bing(self, query: str, limit: int) -> List[Dict[str, Any]]:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
        html_text = self._fetch(url, timeout=8)
        items = []
        for b in html_text.split('<li class="b_algo"')[1:]:
            link_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
            if not link_m:
                continue
            snip_m = re.search(r'<p[^>]*>(.*?)</p>', b, re.DOTALL)
            items.append({
                "title": self._strip_tags(link_m.group(2)),
                "url": link_m.group(1),
                "snippet": self._strip_tags(snip_m.group(1)) if snip_m else "",
            })
            if len(items) >= limit:
                break
        return items

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _query_variants(self, query: str) -> List[str]:
        """Reformulations tried before the tool gives up on a query."""
        variants = []
        cleaned = re.sub(r"""["'()\[\]{}]""", " ", query)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned != query:
            variants.append(cleaned)

        short = re.sub(
            r"^(how (do|to|can|does|is)|what (is|are|does|can|is meant)|why (does|is|can)|find|help with)\s+(?:i\s+)?",
            "", query, flags=re.IGNORECASE,
        ).strip("?.!,\s ")
        short = re.sub(r"[?.!,\s]+$", "", short).strip()
        if short and short != query:
            variants.append(short)

        words = query.split()
        if len(words) > 2:
            variants.append(" ".join(words[:-1]))

        # Dedupe and drop anything that produced no real signal.
        seen = set()
        out = []
        for v in variants:
            if v and v != query and v not in seen:
                seen.add(v)
                out.append(v)
            if len(out) >= 3:
                break
        return out

    def _enrich(self, items: List[Dict[str, Any]]) -> None:
        """When a snippet is missing, peek at the page body and extract real text."""
        for it in items:
            if it.get("snippet") or not it.get("url") or not str(it["url"]).startswith("http"):
                continue
            try:
                body = self._fetch(it["url"], timeout=4, max_bytes=120_000)
                it["snippet"] = self._extract_page_text(body)
            except Exception:
                continue

    def _extract_page_text(self, body: str) -> str:
        desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', body, re.IGNORECASE)
        desc = self._strip_tags(desc_m.group(1)) if desc_m else ""
        if len(desc) >= 80:
            return desc
        text_only = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.DOTALL | re.IGNORECASE)
        text = self._strip_tags(text_only)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:500] or desc

    def _strip_tags(self, raw: str) -> str:
        clean = re.sub(r"<[^>]+>", "", raw or "")
        return html.unescape(clean).strip()

    def _unpack_redirect(self, link: str) -> str:
        """Unwrap DuckDuckGo uddg and Bing /ck/a redirects to the real URL."""
        link = html.unescape(link or "")
        m = re.search(r"uddg=([^&]+)", link)
        if m:
            return urllib.parse.unquote(m.group(1))
        if "/ck/a" in link:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
            u = qs.get("u")
            if u:
                encoded = u[0]
                if encoded.startswith("a1"):
                    encoded = encoded[2:]  # Bing prefixes the payload with a version tag
                encoded += "=" * (-len(encoded) % 4)
                try:
                    return base64.urlsafe_b64decode(encoded).decode("utf-8", "replace")
                except Exception:
                    pass
        if link.startswith("//"):
            return "https:" + link
        return link

    def _fetch(self, url: str, timeout: int = 9, max_bytes: int = 500_000) -> str:
        req = urllib.request.Request(url, headers=HTML_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes)
        return data.decode("utf-8", errors="replace")