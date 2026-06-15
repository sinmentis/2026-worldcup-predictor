from itertools import combinations

from worldcup_predictor import config, db, ingest


def test_seed_creates_teams_and_group_fixtures(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)

    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 48
    # 12 groups * C(4,2)=6 = 72 group-stage fixtures
    n_group = conn.execute("SELECT COUNT(*) FROM matches WHERE stage='group'").fetchone()[0]
    assert n_group == 72
    # hosts flagged
    hosts = {r[0] for r in conn.execute("SELECT name FROM teams WHERE is_host=1").fetchall()}
    assert hosts == config.HOSTS
    # all group matches neutral in phase 1
    assert (
        conn.execute("SELECT COUNT(*) FROM matches WHERE stage='group' AND neutral=0").fetchone()[0]
        == 0
    )
    # exactly the round-robin pairings for group A
    a_pairs = {
        tuple(sorted((r["home_team"], r["away_team"])))
        for r in conn.execute(
            "SELECT home_team, away_team FROM matches WHERE group_id='A'"
        ).fetchall()
    }
    assert a_pairs == {tuple(sorted(p)) for p in combinations(config.GROUPS["A"], 2)}
