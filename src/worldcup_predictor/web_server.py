from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette import EventSourceResponse, ServerSentEvent

from worldcup_predictor import db, engine

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="WorldCup Predictor")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _conn() -> sqlite3.Connection:
    conn = db.connect()
    db.init_schema(conn)
    return conn


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/groups/{group}/standings")
def groups(group: str) -> list[dict[str, Any]]:
    return engine.get_group_standings(_conn(), group)


@app.get("/api/matches/upcoming")
def upcoming(limit: int = 10) -> list[dict[str, Any]]:
    return engine.get_upcoming_matches(_conn(), limit)


@app.get("/api/knockout/bracket")
def bracket() -> dict[str, list[dict[str, Any]]]:
    return engine.get_knockout_bracket(_conn())


@app.get("/api/matches/{match_id}")
def match_detail(match_id: int) -> dict[str, Any]:
    return engine.get_match_detail(_conn(), match_id)


@app.get("/api/events")
async def events(request: Request) -> EventSourceResponse:
    async def gen() -> AsyncIterator[ServerSentEvent]:
        last = None
        while True:
            if await request.is_disconnected():
                break
            cur = engine.get_last_update_ts(_conn())
            if cur != last:
                last = cur
                yield ServerSentEvent(data=json.dumps({"ts": cur}), event="update")
            await asyncio.sleep(2)

    return EventSourceResponse(gen(), ping=30)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
