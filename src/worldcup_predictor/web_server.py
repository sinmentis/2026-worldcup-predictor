from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette import EventSourceResponse, ServerSentEvent

from worldcup_predictor import config, db, engine

STATIC = Path(__file__).parent / "static"


def _conn() -> sqlite3.Connection:
    return db.connect(os.environ.get("WC_DB_PATH"))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Ensure the schema exists once at startup instead of on every request.
    with closing(_conn()) as conn:
        db.init_schema(conn)
    yield


app = FastAPI(title="WorldCup Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/groups/{group}/standings")
def groups(group: str) -> list[dict[str, Any]]:
    if group.upper() not in config.GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group '{group}'")
    with closing(_conn()) as conn:
        return engine.get_group_standings(conn, group)


@app.get("/api/matches/upcoming")
def upcoming(limit: int = 10) -> list[dict[str, Any]]:
    limit = min(max(1, limit), 100)
    with closing(_conn()) as conn:
        return engine.get_upcoming_matches(conn, limit)


@app.get("/api/knockout/bracket")
def bracket() -> dict[str, list[dict[str, Any]]]:
    with closing(_conn()) as conn:
        return engine.get_knockout_bracket(conn)


@app.get("/api/forecast")
def forecast() -> list[dict[str, Any]]:
    with closing(_conn()) as conn:
        return engine.get_forecast(conn)


@app.get("/api/upcoming-predictions")
def upcoming_predictions(limit: int = 12) -> dict[str, Any]:
    limit = min(max(1, limit), 60)
    with closing(_conn()) as conn:
        return engine.get_upcoming_predictions(conn, limit)


@app.get("/api/accuracy")
def accuracy() -> dict[str, Any]:
    with closing(_conn()) as conn:
        return engine.get_accuracy(conn)


@app.get("/api/matches/{match_id}")
def match_detail(match_id: int) -> dict[str, Any]:
    with closing(_conn()) as conn:
        detail = engine.get_match_detail(conn, match_id)
    if detail["match"] is None:
        raise HTTPException(status_code=404, detail=f"No match with id {match_id}")
    return detail


@app.get("/api/events")
async def events(request: Request) -> EventSourceResponse:
    async def gen() -> AsyncIterator[ServerSentEvent]:
        conn = _conn()
        try:
            last = None
            while True:
                if await request.is_disconnected():
                    break
                cur = engine.get_last_update_ts(conn)
                if cur != last:
                    last = cur
                    yield ServerSentEvent(data=json.dumps({"ts": cur}), event="update")
                await asyncio.sleep(2)
        finally:
            conn.close()

    return EventSourceResponse(gen(), ping=30)


def main() -> None:
    import uvicorn

    host = os.environ.get("WC_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WC_WEB_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
