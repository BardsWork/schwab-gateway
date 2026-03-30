"""Tests for GET /llm-docs."""


class TestLlmDocs:
    def test_returns_200(self, test_app):
        resp = test_app.get("/llm-docs")
        assert resp.status_code == 200

    def test_content_type_is_text_plain(self, test_app):
        resp = test_app.get("/llm-docs")
        assert "text/plain" in resp.headers["content-type"]

    def test_contains_key_endpoints(self, test_app):
        resp = test_app.get("/llm-docs")
        for endpoint in ("/bars/", "/options/", "/quotes", "/stream"):
            assert endpoint in resp.text, f"Expected {endpoint!r} in /llm-docs output"
