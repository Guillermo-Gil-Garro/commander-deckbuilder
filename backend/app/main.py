"""FastAPI app entrypoint.

Run from ``backend/`` as ``uvicorn app.main:app``. Serves the API under
``/api`` and, when a frontend build exists at ``frontend/dist/``, the SPA
at ``/`` with an index.html fallback for client-side routes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CARDS_FILE = REPO_ROOT / "data" / "processed" / "cards.jsonl"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

app = FastAPI(title="Commander Deckbuilder")


def count_cards(path: Path) -> int:
    """Number of cards in the processed pool (0 if the file is missing)."""
    if not path.is_file():
        logger.warning("Card pool not found at %s", path)
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


@app.get("/api/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "cards_loaded": count_cards(CARDS_FILE)}


class SPAStaticFiles(StaticFiles):
    """Static files with index.html fallback for client-side routes.

    Unknown ``/api`` paths keep their 404 instead of returning the SPA shell.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # StaticFiles normalizes the path with os.sep, so use posix form.
            posix_path = path.replace("\\", "/")
            if exc.status_code != 404 or posix_path.startswith("api/"):
                raise
        return await super().get_response("index.html", scope)


if FRONTEND_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning("Frontend build not found at %s; serving API only", FRONTEND_DIST)
