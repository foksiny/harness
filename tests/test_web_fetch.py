"""Hermetic web_fetch + search-extraction tests (E5/E6)."""
import unittest
from unittest.mock import patch
from harness.tools.web_search import WebFetchTool, extract_readable


class TestWebFetch(unittest.TestCase):
    def test_extract_readable_strips_scripts(self):
        html = "<html><head><style>body{}</style></head><body><script>evil()</script><h1>Title</h1><p>Hello world</p></body></html>"
        out = extract_readable(html)
        self.assertIn("Hello world", out)
        self.assertNotIn("evil", out)
        self.assertNotIn("<script", out)

    def test_fetch_rejects_non_http(self):
        t = WebFetchTool()
        self.assertIn("Error", t.execute("file:///etc/passwd"))
        self.assertIn("Error", t.execute("ftp://example.com"))

    def test_fetch_returns_readable_text(self):
        html = "<html><body><h1>Docs</h1><p>API reference text here.</p></body></html>"
        class _Resp:
            def __init__(self, body):
                self._body = body
            def read(self, *a):
                return self._body.encode()
            def geturl(self):
                return "https://example.com/docs"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        t = WebFetchTool()
        with patch("urllib.request.urlopen", return_value=_Resp(html)):
            out = t.execute("https://example.com/docs")
            self.assertIn("API reference", out)
            self.assertIn("https://example.com/docs", out)


if __name__ == "__main__":
    unittest.main()
