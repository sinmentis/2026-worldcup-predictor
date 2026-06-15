from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import typer

from worldcup_predictor import db, engine, evaluate, ingest

app = typer.Typer(help="WorldCup Predictor CLI")


def _conn() -> sqlite3.Connection:
    path = os.environ.get("WC_DB_PATH")
    conn = db.connect(path)
    db.init_schema(conn)
    return conn


@app.command("init-db")
def init_db() -> None:
    """Create the SQLite schema."""
    _conn()
    typer.echo("Database initialized.")


@app.command()
def seed() -> None:
    """Seed 48 teams and 72 group fixtures."""
    conn = _conn()
    ingest.seed_teams_and_fixtures(conn)
    n = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    typer.echo(f"Seeded {n} teams.")


@app.command("load-history")
def load_history(
    file: str | None = typer.Option(None, help="Local CSV path; omit to fetch online"),
) -> None:
    """Load historical international results."""
    conn = _conn()
    if file:
        n = ingest.load_history_from_text(conn, Path(file).read_text(encoding="utf-8"))
    else:
        n = ingest.load_history(conn)
    typer.echo(f"Loaded {n} historical matches.")


@app.command("fetch-results")
def fetch_results() -> None:
    """Fetch finished WC results and update the DB."""
    conn = _conn()
    n = ingest.fetch_live_results(conn)
    typer.echo(f"Updated {n} results.")


@app.command()
def predict(match_id: int) -> None:
    """Predict a single fixture and persist it."""
    conn = _conn()
    out = engine.predict_fixture(conn, match_id)
    typer.echo(out)


@app.command()
def simulate(n: int = 50_000, seed: int | None = typer.Option(None)) -> None:
    """Run the Monte Carlo tournament simulation."""
    conn = _conn()
    result = engine.run_simulation(conn, n=n, seed=seed)
    top = sorted(result.items(), key=lambda kv: kv[1]["title"], reverse=True)[:5]
    for team, p in top:
        typer.echo(f"{team:20s} title={p['title']:.3f} advance={p['advance']:.3f}")


def evaluate_cmd() -> None:
    """Score finished predictions vs baseline."""
    conn = _conn()
    typer.echo(evaluate.score_finished_predictions(conn))


app.command("evaluate")(evaluate_cmd)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the web UI."""
    import uvicorn

    uvicorn.run("worldcup_predictor.web_server:app", host=host, port=port)


if __name__ == "__main__":
    app()
