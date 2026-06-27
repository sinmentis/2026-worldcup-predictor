from __future__ import annotations

import sqlite3
from typing import Any

from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.predict import predict_match

# Round order and Chinese labels for the knockout tree.
_ROUND_ORDER: list[str] = ["R32", "R16", "QF", "SF", "FINAL"]
_LABELS: dict[str, str] = {
    "R32": "32强",
    "R16": "16强",
    "QF": "八强",
    "SF": "四强",
    "FINAL": "决赛",
}


def advance_prob(p_home: float, p_draw: float, p_away: float) -> tuple[float, float]:
    """Probability each side advances a knockout tie: 90' win + (draw → extra-time/shootout),
    where the draw is split by each side's regulation win share (a coin flip if even)."""
    denom = p_home + p_away
    share = p_home / denom if denom > 0 else 0.5
    adv_home = p_home + p_draw * share
    return adv_home, 1.0 - adv_home


def _load(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT id, ext_id, stage, home_team, away_team, kickoff, home_score, away_score, "
        " status, winner_team FROM matches WHERE stage IN ('R32','R16','QF','SF','3RD','FINAL')"
    ).fetchall()
    by_stage: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r)
    for _stage, ms in by_stage.items():
        ms.sort(key=lambda r: (r["kickoff"] or "", r["ext_id"] or 0))
    return by_stage


def _decide(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str | None,
    away: str | None,
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Build one match node: predict when both teams resolved; use the actual result when
    FINISHED."""
    node: dict[str, Any] = {
        "ext_id": row["ext_id"],
        "home": home,
        "away": away,
        "status": row["status"],
        "home_score": row["home_score"],
        "away_score": row["away_score"],
        "advance_home": None,
        "advance_away": None,
        "ml_home": None,
        "ml_away": None,
        "p_home": None,
        "p_draw": None,
        "p_away": None,
        "factors": [],
        "winner": None,
    }
    if row["status"] == "FINISHED":
        # Actual outcome: explicit penalty winner, else the higher score.
        if row["winner_team"]:
            node["winner"] = row["winner_team"]
        elif row["home_score"] is not None and row["away_score"] is not None:
            node["winner"] = home if row["home_score"] >= row["away_score"] else away
    if home is None or away is None:
        return node
    pred = predict_match(conn, model, home, away, neutral=True)
    ah, aa = advance_prob(pred.p_home, pred.p_draw, pred.p_away)
    node.update(
        advance_home=ah,
        advance_away=aa,
        ml_home=pred.ml_home,
        ml_away=pred.ml_away,
        p_home=pred.p_home,
        p_draw=pred.p_draw,
        p_away=pred.p_away,
        factors=[
            {"team": f.team, "description": f.description, "lambda_delta": f.lambda_delta}
            for f in pred.factors
        ],
    )
    if node["winner"] is None:  # not yet played → predicted winner drives downstream slots
        node["winner"] = home if ah >= aa else away
    return node


def has_knockout_fixtures(conn: sqlite3.Connection) -> bool:
    """True once the feed has populated any knockout fixture (R32→final, incl. the 3rd-place
    match). Lets callers skip fitting the goal model while the group stage is still in progress."""
    return any(_load(conn).values())


def empty_bracket() -> dict[str, Any]:
    """The bracket payload before any knockout fixture exists: the five rounds as empty shells,
    matching ``build_predicted_bracket``'s shape so consumers need no special-casing."""
    return {
        "rounds": [{"stage": s, "label": _LABELS[s], "matches": []} for s in _ROUND_ORDER],
        "third_place": None,
        "real_fixtures": 0,
        "total_fixtures": 0,
    }


def build_predicted_bracket(conn: sqlite3.Connection, model: GoalModel) -> dict[str, Any]:
    """Compose the knockout tree: feed teams where known, predicted winners projected forward."""
    by_stage = _load(conn)
    rounds_out: list[dict[str, Any]] = []
    prev_winners: list[str | None] = []  # winners of the previous round, in slot order
    sf_losers: list[str | None] = []
    real = 0
    total = sum(len(by_stage.get(s, [])) for s in _ROUND_ORDER) + len(by_stage.get("3RD", []))

    for stage in _ROUND_ORDER:
        matches = by_stage.get(stage, [])
        out_matches: list[dict[str, Any]] = []
        winners: list[str | None] = []
        for k, row in enumerate(matches):
            home, away = row["home_team"], row["away_team"]
            home_known, away_known = home is not None, away is not None
            if stage != "R32":  # fill TBD sides from the previous round's winners
                if home is None and 2 * k < len(prev_winners):
                    home = prev_winners[2 * k]
                if away is None and 2 * k + 1 < len(prev_winners):
                    away = prev_winners[2 * k + 1]
            if home_known and away_known:
                real += 1
            node = _decide(conn, model, home, away, row)
            node["slot"] = f"{stage}-{k + 1}"
            node["home_known"] = home_known
            node["away_known"] = away_known
            out_matches.append(node)
            winners.append(node["winner"])
            if stage == "SF":  # track losers for the third-place match
                loser = away if node["winner"] == home else (home if node["winner"] else None)
                sf_losers.append(loser)
        rounds_out.append({"stage": stage, "label": _LABELS[stage], "matches": out_matches})
        prev_winners = winners

    third = None
    third_rows = by_stage.get("3RD", [])
    if third_rows:
        row = third_rows[0]
        h = row["home_team"] or (sf_losers[0] if len(sf_losers) > 0 else None)
        a = row["away_team"] or (sf_losers[1] if len(sf_losers) > 1 else None)
        if row["home_team"] is not None and row["away_team"] is not None:
            real += 1
        third = _decide(conn, model, h, a, row)
        third["slot"] = "3RD"
        third["home_known"] = row["home_team"] is not None
        third["away_known"] = row["away_team"] is not None

    return {
        "rounds": rounds_out,
        "third_place": third,
        "real_fixtures": real,
        "total_fixtures": total,
    }
