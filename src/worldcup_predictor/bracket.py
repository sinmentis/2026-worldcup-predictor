from __future__ import annotations

import sqlite3
from typing import Any

from worldcup_predictor import bracket_topology as _bt
from worldcup_predictor import config
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.predict import predict_match
from worldcup_predictor.simulate import standings_from_results

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


def _group_winners_runners(
    conn: sqlite3.Connection,
) -> tuple[dict[str, str], dict[str, str]]:
    """Winner and runner-up per group, only for groups whose six matches are all FINISHED."""
    winners: dict[str, str] = {}
    runners: dict[str, str] = {}
    for gid, teams in config.GROUPS.items():
        rows = conn.execute(
            "SELECT home_team, away_team, home_score, away_score FROM matches "
            "WHERE stage='group' AND group_id=? AND status='FINISHED'",
            (gid,),
        ).fetchall()
        if len(rows) < 6:
            continue  # group not complete → standings not final
        results = [
            (r["home_team"], r["away_team"], int(r["home_score"]), int(r["away_score"]))
            for r in rows
        ]
        table = standings_from_results(list(teams), results)
        winners[gid] = table[0].team
        runners[gid] = table[1].team
    return winners, runners


def _resolve_slot(token: str, winners: dict[str, str], runners: dict[str, str]) -> str | None:
    """Resolve a template token ('W_E' / 'RU_C') to a team, or None for the '3' wildcard or an
    unfinished group."""
    if token == "3":
        return None
    side, gid = token.split("_")
    return winners.get(gid) if side == "W" else runners.get(gid)


def _r32_signatures(winners: dict[str, str], runners: dict[str, str]) -> dict[int, tuple[str, Any]]:
    """Per R32 fixture (73-88), a signature to match a feed row: an ('anchor', team) for the
    W_x/RU_x side of a 'W_x vs 3rd' fixture, or a ('pair', frozenset) of both resolved teams."""
    sigs: dict[int, tuple[str, Any]] = {}
    for i, (ta, tb) in enumerate(_bt.R32_TEMPLATE):
        fixture = _bt.R32_FIXTURES[i]
        a = _resolve_slot(ta, winners, runners)
        b = _resolve_slot(tb, winners, runners)
        if ta == "3" or tb == "3":
            sigs[fixture] = ("anchor", b if ta == "3" else a)
        else:
            sigs[fixture] = ("pair", frozenset({a, b}))
    return sigs


def fixture_of_r32_row(
    home: str | None, away: str | None, sigs: dict[int, tuple[str, Any]]
) -> int | None:
    """The R32 fixture number a feed row belongs to, or None if not yet identifiable."""
    teams = {t for t in (home, away) if t is not None}
    if not teams:
        return None
    for fixture, (kind, sig) in sigs.items():
        if kind == "anchor" and sig is not None and sig in teams:
            return fixture
        if kind == "pair" and None not in sig and sig <= teams:
            return fixture
    return None


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
    *,
    ext_id: int | None = None,
    kickoff: str | None = None,
    status: str = "SCHEDULED",
    home_score: int | None = None,
    away_score: int | None = None,
    winner_team: str | None = None,
) -> dict[str, Any]:
    """Build one match node: predict when both teams are resolved; use the actual result when
    FINISHED. Result fields are passed explicitly so projected slots (no feed row) need none."""
    node: dict[str, Any] = {
        "ext_id": ext_id,
        "kickoff": kickoff,
        "home": home,
        "away": away,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
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
    if status == "FINISHED":
        if winner_team:
            node["winner"] = winner_team
        elif home_score is not None and away_score is not None:
            node["winner"] = home if home_score >= away_score else away
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


def _decide_row(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str | None,
    away: str | None,
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Convenience: build a node from a feed row's result fields."""
    return _decide(
        conn,
        model,
        home,
        away,
        ext_id=row["ext_id"],
        kickoff=row["kickoff"],
        status=row["status"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        winner_team=row["winner_team"],
    )


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


def _overlay_index(
    by_stage: dict[str, list[sqlite3.Row]],
) -> dict[str, dict[frozenset[str], sqlite3.Row]]:
    """Index each downstream stage's feed rows by their (known) two-team set, to overlay real
    teams/results/kickoff onto the projected slot once the feed has filled it."""
    out: dict[str, dict[frozenset[str], sqlite3.Row]] = {}
    for stage in ("R16", "QF", "SF", "FINAL"):
        idx: dict[frozenset[str], sqlite3.Row] = {}
        for row in by_stage.get(stage, []):
            if row["home_team"] is not None and row["away_team"] is not None:
                idx[frozenset({row["home_team"], row["away_team"]})] = row
        out[stage] = idx
    return out


def _match_overlay(
    idx: dict[frozenset[str], sqlite3.Row], home: str | None, away: str | None
) -> sqlite3.Row | None:
    if home is None or away is None:
        return None
    return idx.get(frozenset({home, away}))


def build_predicted_bracket(conn: sqlite3.Connection, model: GoalModel) -> dict[str, Any]:
    """Compose the knockout tree: feed teams where known, predicted winners projected forward via
    the official feeder topology, every slot ordered by fixture number."""
    by_stage = _load(conn)
    total = sum(len(by_stage.get(s, [])) for s in _ROUND_ORDER) + len(by_stage.get("3RD", []))
    winners_g, runners_g = _group_winners_runners(conn)
    sigs = _r32_signatures(winners_g, runners_g)

    row_by_fixture: dict[int, sqlite3.Row] = {}
    for feed_row in by_stage.get("R32", []):
        fx = fixture_of_r32_row(feed_row["home_team"], feed_row["away_team"], sigs)
        if fx is not None:
            row_by_fixture[fx] = feed_row

    node_by_fixture: dict[int, dict[str, Any]] = {}
    winner_by_fixture: dict[int, str | None] = {}
    real = 0

    # R32: one node per fixture 73-88 (mapped feed row if present, else a TBD shell).
    for idx, fx in enumerate(_bt.R32_FIXTURES):
        row = row_by_fixture.get(fx)
        if row is not None:
            home, away = row["home_team"], row["away_team"]
            node = _decide_row(conn, model, home, away, row)
            node["home_known"] = home is not None
            node["away_known"] = away is not None
            if home is not None and away is not None:
                real += 1
        else:
            node = _decide(conn, model, None, None)
            node["home_known"] = node["away_known"] = False
        node["slot"] = f"R32-{idx + 1}"
        node_by_fixture[fx] = node
        winner_by_fixture[fx] = node["winner"]

    # R16 → Final via FEEDERS, overlaying the feed's real row when its teams match the projection.
    overlay = _overlay_index(by_stage)
    for stage, fixtures in (
        ("R16", _bt.R16_FIXTURES),
        ("QF", _bt.QF_FIXTURES),
        ("SF", _bt.SF_FIXTURES),
        ("FINAL", (_bt.FINAL_FIXTURE,)),
    ):
        for idx, fx in enumerate(fixtures):
            fa, fb = _bt.FEEDERS[fx]
            home = winner_by_fixture.get(fa)
            away = winner_by_fixture.get(fb)
            row = _match_overlay(overlay.get(stage, {}), home, away)
            home_known = away_known = False
            if row is not None and row["home_team"] is not None and row["away_team"] is not None:
                home, away, home_known, away_known = row["home_team"], row["away_team"], True, True
                real += 1
            node = (
                _decide_row(conn, model, home, away, row)
                if row is not None
                else _decide(conn, model, home, away)
            )
            node["home_known"] = home_known
            node["away_known"] = away_known
            node["slot"] = f"{stage}-{idx + 1}"
            node_by_fixture[fx] = node
            winner_by_fixture[fx] = node["winner"]

    rounds_out = [
        {
            "stage": stage,
            "label": _LABELS[stage],
            "matches": [node_by_fixture[fx] for fx in fixtures],
        }
        for stage, fixtures in (
            ("R32", _bt.R32_FIXTURES),
            ("R16", _bt.R16_FIXTURES),
            ("QF", _bt.QF_FIXTURES),
            ("SF", _bt.SF_FIXTURES),
            ("FINAL", (_bt.FINAL_FIXTURE,)),
        )
    ]

    # Third place = the two SF losers; overlay the feed's 3RD row if present.
    sf_losers: list[str | None] = []
    for fx in _bt.SF_FIXTURES:
        node = node_by_fixture[fx]
        w = node["winner"]
        sf_losers.append(node["away"] if w == node["home"] else (node["home"] if w else None))
    third_rows = by_stage.get("3RD", [])
    third: dict[str, Any] | None = None
    if third_rows or any(sf_losers):
        h = sf_losers[0] if sf_losers else None
        a = sf_losers[1] if len(sf_losers) > 1 else None
        home_known = away_known = False
        if third_rows:
            row = third_rows[0]
            if row["home_team"] is not None and row["away_team"] is not None:
                h, a, home_known, away_known = row["home_team"], row["away_team"], True, True
                real += 1
            third = _decide_row(conn, model, h, a, row)
        else:
            third = _decide(conn, model, h, a)
        third["slot"] = "3RD"
        third["home_known"] = home_known
        third["away_known"] = away_known

    return {
        "rounds": rounds_out,
        "third_place": third,
        "real_fixtures": real,
        "total_fixtures": total,
    }
