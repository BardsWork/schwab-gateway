"""LLM-friendly API documentation: GET /llm-docs."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["Docs"])

_DOCS_PATH = Path(__file__).parent.parent / "llm_docs.md"


@router.get("/llm-docs", response_class=PlainTextResponse)
def llm_docs() -> str:
    """Return the full API reference as plain text (Markdown).

    Designed for LLM agent consumption — call this endpoint to understand
    every endpoint, parameter, response shape, streaming protocol, and error
    code without exploring the source repository.
    """
    return _DOCS_PATH.read_text(encoding="utf-8")
