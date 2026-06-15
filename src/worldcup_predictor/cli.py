from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import typer

from worldcup_predictor import db, engine, evaluate, ingest, news, ratings

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


@app.command()
def rate() -> None:
    """Compute Elo ratings from loaded history and store them on teams."""
    conn = _conn()
    table = ratings.compute_elo_ratings(conn)
    top = sorted(table.items(), key=lambda kv: kv[1], reverse=True)[:5]
    for team, elo in top:
        typer.echo(f"{team:20s} elo={elo:.0f}")


@app.command("fetch-results")
def fetch_results() -> None:
    """Fetch finished WC results and update the DB."""
    conn = _conn()
    n = ingest.fetch_live_results(conn)
    typer.echo(f"Updated {n} results.")


@app.command("fetch-fixtures")
def fetch_fixtures() -> None:
    """Fetch all WC fixtures and populate kickoff times (and any results)."""
    conn = _conn()
    n = ingest.fetch_fixtures(conn)
    typer.echo(f"Set kickoff on {n} fixtures.")


@app.command("fetch-news")
def fetch_news() -> None:
    """Fetch configured RSS feeds and store new articles (cron-friendly)."""
    conn = _conn()
    n = news.fetch_news(conn)
    typer.echo(f"Stored {n} new articles.")


@app.command("fetch-odds")
def fetch_odds() -> None:
    """Fetch bookmaker odds from The Odds API (needs ODDS_API_KEY in .env)."""
    conn = _conn()
    try:
        n = engine.fetch_odds(conn)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1) from e
    typer.echo(f"Stored {n} odds rows.")


@app.command("value-bets")
def value_bets(
    min_edge: float = typer.Option(0.05), kelly_fraction: float = typer.Option(0.25)
) -> None:
    """List positive-EV bets (our model vs the best bookmaker price)."""
    conn = _conn()
    bets = engine.get_value_bets(conn, min_edge=min_edge, kelly_fraction=kelly_fraction)
    if not bets:
        typer.echo("No value bets (fetch odds first, or our model doesn't beat the market).")
        return
    for b in bets:
        ev = f"{b['ev']:+.0%}" if b["ev"] is not None else "n/a"
        price = f"{b['best_price']:.2f}" if b["best_price"] else "n/a"
        typer.echo(
            f"{b['home_team']} v {b['away_team']}  {b['outcome']:<5} "
            f"our={b['our_prob']:.0%} mkt={b['market_prob']:.0%} edge={b['edge']:+.0%}  "
            f"best {price} ({b['bookmaker']}) EV={ev} kelly={b['kelly']:.1%}"
        )


@app.command("intel-pending")
def intel_pending() -> None:
    """List intel items awaiting approval (player statuses and team signals)."""
    conn = _conn()
    for r in engine.list_pending_intel(conn):
        if r["kind"] == "team":
            typer.echo(
                f"[{r['ref']}] {r['team']} - {r['category']}: {r['direction']} "
                f"({r['magnitude_tier']}) cred={r['credibility']:.2f} sources={r['sources']}"
            )
        else:
            typer.echo(
                f"[{r['ref']}] {r['team']} - {r['player']} {r['status']} ({r['tier']}) "
                f"cred={r['credibility']:.2f} sources={r['sources']}"
            )


@app.command("intel-approve")
def intel_approve(ref: str) -> None:
    """Approve a pending intel item by its ref ('ps:<id>'/'ts:<id>', or a bare id)."""
    engine.approve_intel(_conn(), ref)
    typer.echo(f"Approved {ref}.")


@app.command("intel-reject")
def intel_reject(ref: str) -> None:
    """Reject (delete) an intel item by its ref ('ps:<id>'/'ts:<id>', or a bare id)."""
    engine.reject_intel(_conn(), ref)
    typer.echo(f"Rejected {ref}.")


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


@app.command("backtest")
def backtest_cmd(
    since: str | None = typer.Option(None, help="Test-window start date (YYYY-MM-DD)"),
    refit_days: int = typer.Option(30, help="Refit the model every N days"),
    test_years: int = typer.Option(2, help="Backtest over the last N years of history"),
    fit_calibration: bool = typer.Option(
        False, "--fit-calibration", help="Fit and store the calibrator from the backtest"
    ),
) -> None:
    """Walk-forward backtest: out-of-sample skill, reliability, and optional calibration fit."""
    conn = _conn()
    rep = engine.run_backtest(
        conn,
        since=since,
        refit_days=refit_days,
        test_years=test_years,
        fit_calibration=fit_calibration,
    )
    if not rep.get("n"):
        typer.echo("No out-of-sample predictions (need more history).")
        return
    typer.echo(f"Out-of-sample matches: {rep['n']}")
    typer.echo(f"Model RPS    {rep['model_rps']:.4f}   Baseline RPS {rep['baseline_rps']:.4f}")
    typer.echo(f"Model Brier  {rep['model_brier']:.4f}   log-loss {rep['model_log_loss']:.4f}")
    typer.echo(f"ECE (calibration error): {rep['ece']:.4f}")
    typer.echo("Reliability (predicted confidence -> actual accuracy):")
    for b in rep["reliability"]:
        typer.echo(
            f"  [{b['lo']:.1f}-{b['hi']:.1f}] n={b['n']:<4} conf={b['confidence']:.3f} "
            f"acc={b['accuracy']:.3f}"
        )
    if "calibration" in rep:
        c = rep["calibration"]
        typer.echo(
            f"\nFitted calibration: draw_mult={c['draw_mult']} temperature={c['temperature']}"
        )
        typer.echo(
            f"  RPS {c['rps_before']:.4f} -> {c['rps_after']:.4f}   "
            f"ECE {c['ece_before']:.4f} -> {c['ece_after']:.4f}"
        )
        typer.echo("Stored. predict / accuracy will now use it.")


@app.command("tune")
def tune_cmd(
    apply: bool = typer.Option(False, "--apply", help="Adopt the best value (default: dry-run)"),
    refit_days: int = typer.Option(45, help="Refit the model every N days during the backtest"),
    test_years: int = typer.Option(2, help="Backtest over the last N years of history"),
) -> None:
    """Auto-tune the model's recency decay by walk-forward out-of-sample RPS (Phase 2b)."""
    conn = _conn()
    rep = engine.run_tuning(conn, apply=apply, refit_days=refit_days, test_years=test_years)
    if rep.get("best") is None:
        typer.echo("No out-of-sample predictions (need more history).")
        return

    def fmt(v: float | None) -> str:
        return f"{v:.4f}" if v is not None else "n/a"

    typer.echo(f"Current decay xi={rep['current_xi']:.4f}  OOS RPS={fmt(rep['current_rps'])}")
    typer.echo("Sweep (decay xi -> out-of-sample RPS):")
    best = rep["best"]
    for r in rep["results"]:
        mark = " <- best" if best and abs(r["xi"] - best["xi"]) < 1e-12 else ""
        typer.echo(f"  xi={r['xi']:.4f}  n={r['n']:<5} rps={fmt(r['rps'])}{mark}")
    if best:
        typer.echo(f"\nBest xi={best['xi']:.4f}  rps={best['rps']:.4f}")
    if rep["would_adopt"]:
        tail = " APPLIED + model refit." if rep["applied"] else " Re-run with --apply to adopt."
        typer.echo("Beats current beyond the guardrail." + tail)
    else:
        typer.echo("No guard-railed improvement; keeping the current xi.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the web UI (bind 127.0.0.1 by default; use --host 0.0.0.0 for LAN access)."""
    import uvicorn

    uvicorn.run("worldcup_predictor.web_server:app", host=host, port=port)


if __name__ == "__main__":
    app()
