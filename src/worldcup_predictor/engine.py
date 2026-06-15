from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from worldcup_predictor import backtest as _backtest
from worldcup_predictor import calibrate as _calibrate
from worldcup_predictor import config, db
from worldcup_predictor import evaluate as _eval
from worldcup_predictor import intel as _intel
from worldcup_predictor import news as _news
from worldcup_predictor import odds as _odds
from worldcup_predictor import player_status as _ps
from worldcup_predictor import team_signal as _ts
from worldcup_predictor import tune as _tune
from worldcup_predictor import valuebet as _valuebet
from worldcup_predictor.goal_model import GoalModel, history_frame
from worldcup_predictor.models import IntelEvent
from worldcup_predictor.predict import predict_match
from worldcup_predictor.simulate import simulate_tournament, standings_from_results


def get_group_standings(conn: sqlite3.Connection, group: str) -> list[dict[str, Any]]:
    group = group.upper()
    teams = config.GROUPS[group]
    results = [
        (r["home_team"], r["away_team"], r["home_score"], r["away_score"])
        for r in conn.execute(
            "SELECT home_team, away_team, home_score, away_score FROM matches "
            "WHERE group_id=? AND status='FINISHED'",
            (group,),
        ).fetchall()
    ]
    return [asdict(row) for row in standings_from_results(teams, results)]


def get_upcoming_matches(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, stage, group_id, home_team, away_team, kickoff, status "
        "FROM matches WHERE status='SCHEDULED' ORDER BY COALESCE(kickoff,''), id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_knockout_bracket(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rounds: dict[str, list[dict[str, Any]]] = {}
    for stage in ("R32", "R16", "QF", "SF", "3RD", "FINAL"):
        rows = conn.execute(
            "SELECT id, home_team, away_team, home_score, away_score, status "
            "FROM matches WHERE stage=? ORDER BY id",
            (stage,),
        ).fetchall()
        rounds[stage] = [dict(r) for r in rows]
    return rounds


def get_match_detail(conn: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    pred = conn.execute(
        "SELECT * FROM predictions WHERE match_id=? ORDER BY created_at DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return {
        "match": dict(match) if match else None,
        "prediction": dict(pred) if pred else None,
    }


def get_last_update_ts(conn: sqlite3.Connection) -> str | None:
    return db.get_last_update_ts(conn)


def get_forecast(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the stored simulation leaderboard (title/advancement odds), best first."""
    rows = conn.execute(
        "SELECT team, title_prob, final_prob, sf_prob, qf_prob, r16_prob, advance_prob, n_iter "
        "FROM sim_results ORDER BY title_prob DESC, advance_prob DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_upcoming_predictions(conn: sqlite3.Connection, limit: int = 12) -> dict[str, Any]:
    """Scheduled matches ordered by real kickoff, each with our live prediction + factors."""
    limit = min(max(1, limit), 60)
    model = get_model(conn)
    rows = conn.execute(
        "SELECT id, group_id, home_team, away_team, kickoff, neutral FROM matches "
        "WHERE status='SCHEDULED' ORDER BY (kickoff IS NULL), kickoff, id LIMIT ?",
        (limit,),
    ).fetchall()
    remaining = conn.execute("SELECT COUNT(*) FROM matches WHERE status='SCHEDULED'").fetchone()[0]
    matches: list[dict[str, Any]] = []
    for r in rows:
        pred = predict_match(
            conn,
            model,
            r["home_team"],
            r["away_team"],
            match_id=r["id"],
            neutral=bool(r["neutral"]),
        )
        matches.append(
            {
                "match_id": r["id"],
                "group": r["group_id"],
                "home_team": pred.home_team,
                "away_team": pred.away_team,
                "kickoff": r["kickoff"],
                "p_home": pred.p_home,
                "p_draw": pred.p_draw,
                "p_away": pred.p_away,
                "ml_home": pred.ml_home,
                "ml_away": pred.ml_away,
                "exp_home_goals": pred.exp_home_goals,
                "exp_away_goals": pred.exp_away_goals,
                "factors": [
                    {"team": f.team, "description": f.description, "lambda_delta": f.lambda_delta}
                    for f in pred.factors
                ],
            }
        )
    return {"remaining": remaining, "matches": matches}


def get_accuracy(conn: sqlite3.Connection) -> dict[str, Any]:
    """Original-prediction-vs-actual breakdown for finished matches, plus a scoreboard."""
    rows = _eval.per_match_breakdown(conn)
    n = len(rows)
    aggregate: dict[str, Any] = {"n": n}
    if n:
        aggregate["model_rps"] = sum(float(r["model_rps"]) for r in rows) / n
        aggregate["baseline_rps"] = sum(float(r["baseline_rps"]) for r in rows) / n
        aggregate["pick_hit_rate"] = sum(1 for r in rows if r["pick_correct"]) / n
        aggregate["exact_rate"] = sum(1 for r in rows if r["exact_scoreline"]) / n
        aggregate["beats_baseline"] = aggregate["model_rps"] < aggregate["baseline_rps"]
    return {"aggregate": aggregate, "matches": rows}


def run_backtest(
    conn: sqlite3.Connection,
    since: str | None = None,
    refit_days: int = 30,
    test_years: int = 2,
    fit_calibration: bool = False,
) -> dict[str, Any]:
    """Walk-forward backtest: out-of-sample skill + calibration, optionally fit+store calibrator."""
    oos = _backtest.walk_forward_predictions(
        conn, since=since, refit_days=refit_days, test_years=test_years
    )
    report: dict[str, Any] = {"n": len(oos)}
    if not oos:
        return report
    base = _backtest.metrics(oos)
    rel = _backtest.reliability(oos)
    report.update(base)
    report["ece"] = rel["ece"]
    report["reliability"] = rel["bins"]
    if fit_calibration:
        params = _calibrate.fit(oos)
        knobs = {"draw_mult": params["draw_mult"], "temperature": params["temperature"]}
        after = _backtest.metrics(oos, params=knobs)
        cal_oos = [
            {**r, "p_home": cp[0], "p_draw": cp[1], "p_away": cp[2]}
            for r in oos
            for cp in [_calibrate.apply(r["p_home"], r["p_draw"], r["p_away"], knobs)]
        ]
        rel_after = _backtest.reliability(cal_oos)
        meta = {
            "n_test": len(oos),
            "rps_before": base["model_rps"],
            "rps_after": after["calibrated_rps"],
            "ece_before": rel["ece"],
            "ece_after": rel_after["ece"],
        }
        _calibrate.store(conn, knobs, meta=meta)
        db.touch_update(conn)
        report["calibration"] = {**knobs, **meta}
    return report


def fetch_news(conn: sqlite3.Connection) -> int:
    n = _news.fetch_news(conn)
    db.touch_update(conn)
    return n


def fetch_odds(conn: sqlite3.Connection) -> int:
    return _odds.fetch_odds(conn)


def get_value_bets(
    conn: sqlite3.Connection,
    min_edge: float | None = None,
    kelly_fraction: float | None = None,
) -> list[dict[str, Any]]:
    model = get_model(conn)
    return _valuebet.value_bets(conn, model, min_edge=min_edge, kelly_fraction=kelly_fraction)


def get_unprocessed_news(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, source, url, title, summary, published_at FROM news_articles "
        "WHERE processed=0 ORDER BY id LIMIT ?",
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_news_processed(conn: sqlite3.Connection, ids: list[int]) -> int:
    for i in ids:
        conn.execute("UPDATE news_articles SET processed=1 WHERE id=?", (i,))
    conn.commit()
    db.touch_update(conn)
    return len(ids)


def upsert_player_status(conn: sqlite3.Connection, **kwargs: Any) -> dict[str, object]:
    out = _ps.upsert_status(conn, **kwargs)
    db.touch_update(conn)
    return out


def upsert_team_signal(conn: sqlite3.Connection, **kwargs: Any) -> dict[str, object]:
    out = _ts.upsert_signal(conn, **kwargs)
    db.touch_update(conn)
    return out


def _resolve_ref(ref: int | str) -> tuple[str, int]:
    """Resolve a pending-item reference to (kind, id).

    Accepts a prefixed ref ("ps:3" player / "ts:5" team) or a bare integer, which is
    treated as a player_status id for backward compatibility.
    """
    s = str(ref).strip()
    if ":" in s:
        prefix, _, num = s.partition(":")
        if prefix.lower() in {"ts", "team", "team_signal"}:
            return "team", int(num)
        return "player", int(num)
    return "player", int(s)


def list_pending_intel(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all pending items awaiting human review, across both intel stores.

    Each item keeps its raw ``id`` and gains a ``kind`` ("player"|"team") plus a
    ``ref`` ("ps:<id>"/"ts:<id>") for unambiguous approve/reject.
    """
    items: list[dict[str, Any]] = []
    for r in _ps.list_pending(conn):
        d = dict(r)
        d["kind"] = "player"
        d["ref"] = f"ps:{r['id']}"
        items.append(d)
    for r in _ts.list_pending(conn):
        d = dict(r)
        d["kind"] = "team"
        d["ref"] = f"ts:{r['id']}"
        items.append(d)
    return items


def approve_intel(conn: sqlite3.Connection, ref: int | str) -> None:
    kind, num = _resolve_ref(ref)
    if kind == "team":
        _ts.approve(conn, num)
    else:
        _ps.approve(conn, num)
    db.touch_update(conn)


def reject_intel(conn: sqlite3.Connection, ref: int | str) -> None:
    kind, num = _resolve_ref(ref)
    if kind == "team":
        _ts.reject(conn, num)
    else:
        _ps.reject(conn, num)
    db.touch_update(conn)


_MODEL: GoalModel | None = None
_MODEL_DB: str | None = None
_MODEL_XI: float | None = None


def _db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row["file"]) if row else ""


def _reset_model_cache() -> None:
    global _MODEL, _MODEL_DB, _MODEL_XI
    _MODEL = None
    _MODEL_DB = None
    _MODEL_XI = None


def get_model(conn: sqlite3.Connection, refit: bool = False) -> GoalModel:
    """Return a fitted goal model, cached per database file and tuned decay.

    The cache is keyed on the DB path and the tuned ``time_decay_xi`` so reusing the process
    against a different database, or after auto-tuning, refits instead of reusing a stale model.
    """
    global _MODEL, _MODEL_DB, _MODEL_XI
    path = _db_path(conn)
    xi = _tune.current_xi(conn)
    if _MODEL is None or refit or path != _MODEL_DB or xi != _MODEL_XI:
        frame = history_frame(conn)
        if frame.empty:
            raise ValueError(
                "No historical data loaded. Run 'worldcup load-history' before predicting."
            )
        _MODEL = GoalModel().fit(frame, xi=xi)
        _MODEL_DB = path
        _MODEL_XI = xi
    return _MODEL


def run_tuning(
    conn: sqlite3.Connection,
    apply: bool = False,
    refit_days: int = 45,
    test_years: int = 2,
    grid: list[float] | None = None,
) -> dict[str, Any]:
    """Auto-tune the decay via walk-forward backtest. Adopt only on a guard-railed improvement."""
    rep = _tune.tune_decay(conn, grid=grid, refit_days=refit_days, test_years=test_years)
    best = rep["best"]
    cur_rps = rep["current_rps"]
    would_adopt = False
    if best is not None and abs(best["xi"] - rep["current_xi"]) > 1e-12:
        if cur_rps is None or best["rps"] < cur_rps - _tune.IMPROVE_EPS:
            would_adopt = True
    rep["would_adopt"] = would_adopt
    rep["applied"] = False
    if apply and would_adopt and best is not None:
        _tune.store_model_params(
            conn,
            {"time_decay_xi": best["xi"]},
            meta={
                "rps": best["rps"],
                "prev_xi": rep["current_xi"],
                "prev_rps": cur_rps,
                "n_test": best["n"],
            },
        )
        _reset_model_cache()
        db.touch_update(conn)
        rep["applied"] = True
    return rep


def record_result(
    conn: sqlite3.Connection, match_id: int, home_score: int, away_score: int
) -> None:
    cur = conn.execute(
        "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' WHERE id=?",
        (home_score, away_score, match_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"No match with id {match_id}")
    conn.commit()
    db.touch_update(conn)


_VALID_DIRECTIONS = {"weaken", "strengthen"}


def record_intel_event(conn: sqlite3.Connection, **kwargs: Any) -> None:
    direction = str(kwargs.get("direction", "")).strip().lower()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError("direction must be 'weaken' or 'strengthen'")
    credibility = float(kwargs.get("credibility", 0.0))
    if not 0.0 <= credibility <= 1.0:
        raise ValueError("credibility must be in [0, 1]")
    _intel.record_intel(conn, IntelEvent(**kwargs))
    db.touch_update(conn)


def predict_fixture(conn: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    m = conn.execute(
        "SELECT home_team, away_team, neutral FROM matches WHERE id=?", (match_id,)
    ).fetchone()
    if m is None:
        raise ValueError(f"No match with id {match_id}")
    model = get_model(conn)
    pred = predict_match(
        conn, model, m["home_team"], m["away_team"], match_id=match_id, neutral=bool(m["neutral"])
    )
    db.touch_update(conn)
    return {
        "home_team": pred.home_team,
        "away_team": pred.away_team,
        "p_home": pred.p_home,
        "p_draw": pred.p_draw,
        "p_away": pred.p_away,
        "exp_home_goals": pred.exp_home_goals,
        "exp_away_goals": pred.exp_away_goals,
        "most_likely": pred.most_likely_scoreline,
        "factors": [
            {"team": f.team, "description": f.description, "delta": f.lambda_delta}
            for f in pred.factors
        ],
    }


def run_simulation(
    conn: sqlite3.Connection, n: int = 50_000, seed: int | None = None
) -> dict[str, dict[str, float]]:
    model = get_model(conn)
    result = simulate_tournament(conn, model, n=n, seed=seed)
    db.touch_update(conn)
    return result
