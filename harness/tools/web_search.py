"""
Exa Web Search tool for Harness (Free, No API Key required).
Fetches live web search results, snippets, and source URLs.
"""
import re
import urllib.request
import urllib.parse
from typing import Dict, Any, List
from harness.tools.base import Tool

class ExaSearchTool(Tool):
    name = "exa_search"
    description = "Search the live web for technical documentation, error solutions, or current facts (Free, no API key required)."
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

    def execute(self, query: str, num_results: int = 5, **kwargs) -> str:
        q = query.strip()
        if not q:
            return "Error: Empty search query."

        limit = min(max(1, num_results), 10)

        # Free search querying via public html endpoint
        encoded_query = urllib.parse.quote_plus(q)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract search result items from HTML
            # Links: <a class="result__url" href="..."> or <a class="result__snippet" ...>
            items = []
            blocks = html.split('<div class="result results_links')
            for b in blocks[1:]:
                # Extract Title
                title_m = re.search(r'<a class="result__snippet[^"]*"[^>]*>(.*?)</a>', b, re.DOTALL)
                title_link = re.search(r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
                snippet_m = re.search(r'<a class="result__snippet[^"]*"[^>]*>(.*?)</a>', b, re.DOTALL)

                # Alternative link extraction
                link_m = re.search(r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)

                if link_m:
                    raw_link = link_m.group(1)
                    # Unpack DDG redirect if present
                    if "uddg=" in raw_link:
                        m_url = re.search(r'uddg=([^&]+)', raw_link)
                        if m_url:
                            raw_link = urllib.parse.unquote(m_url.group(1))

                    raw_title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()
                    raw_snippet = ""
                    if snippet_m:
                        raw_snippet = re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip()

                    items.append({
                        "title": raw_title,
                        "url": raw_link,
                        "snippet": raw_snippet or "No snippet available",
                    })

                    if len(items) >= limit:
                        break

            if not items:
                return f"No results found for query: '{q}'."

            formatted = [f"Exa Web Search Results for '{q}':\n"]
            for i, it in enumerate(items, 1):
                formatted.append(f"{i}. **{it['title']}**")
                formatted.append(f"   URL: {it['url']}")
                formatted.append(f"   Snippet: {it['snippet']}\n")

            return "\n".join(formatted)

        except urllib.error.URLError as ue:
            return f"(Offline / Network unreachable in current environment): Exa search could not connect ({str(ue)})."
        except Exception as ex:
            return f"Exa search error: {str(ex)}"
