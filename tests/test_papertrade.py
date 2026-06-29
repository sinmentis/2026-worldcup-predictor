from worldcup_predictor import db, papertrade


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def _add_match(conn, mid, kickoff, *, status="SCHEDULED", hs=None, as_=None, stage="group"):
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,kickoff,neutral,"
        "home_score,away_score,status) VALUES (?,?,?,?,?,?,1,?,?,?)",
        (mid, stage, "A", "Strong", "Weak", kickoff, hs, as_, status),
    )
    conn.commit()


def _bet(mid, **kw):
    b = {
        "match_id": mid,
        "home_team": "Strong",
        "away_team": "Weak",
        "group": "A",
        "kickoff": "2030-01-01T00:00:00Z",
        "market": "1x2",
        "outcome": "home",
        "line": None,
        "our_prob": 0.60,
        "market_prob": 0.50,
        "edge": 0.10,
        "best_price": 2.0,
        "bookmaker": "soft",
        "ev": 0.2,
        "kelly": 0.10,
    }
    b.update(kw)
    return b


def test_log_bets_inserts_and_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2030-01-01T00:00:00Z")
    assert papertrade.log_bets(conn, [_bet(1)]) == 1
    assert papertrade.log_bets(conn, [_bet(1, best_price=2.4)]) == 0  # same selection -> dedup
    row = conn.execute("SELECT * FROM paper_bets").fetchone()
    assert row["price_taken"] == 2.0  # keeps the FIRST price we saw
    assert row["outcome"] == "home" and row["kelly_frac"] == 0.10


def test_log_bets_distinct_outcomes_both_logged(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2030-01-01T00:00:00Z")
    n = papertrade.log_bets(
        conn, [_bet(1, outcome="home"), _bet(1, outcome="away", best_price=5.0)]
    )
    assert n == 2


def test_log_skips_unpriceable(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2030-01-01T00:00:00Z")
    assert papertrade.log_bets(conn, [_bet(1, best_price=None)]) == 0
    assert papertrade.log_bets(conn, [_bet(1, best_price=1.0)]) == 0


def test_capture_closing_computes_clv(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z")  # already kicked off
    papertrade.log_bets(conn, [_bet(1, best_price=2.5, kickoff="2000-01-01T00:00:00Z")])
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',2.0,3.5,4.0,0)"  # closing home best = 2.0 (worse than our 2.5)
    )
    conn.commit()
    assert papertrade.capture_closing(conn) == 1
    r = conn.execute("SELECT * FROM paper_bets").fetchone()
    assert r["closing_price"] == 2.0
    assert abs(r["clv_price"] - (2.5 / 2.0 - 1.0)) < 1e-9  # got better odds -> positive
    # no-vig CLV: 2.5 * fair_home - 1 ; fair_home demargined from (2.0,3.5,4.0)
    assert r["clv"] is not None and r["clv"] > 0
    assert r["closed_at"] is not None


def test_capture_closing_skips_future_matches(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2030-01-01T00:00:00Z")  # in the future
    papertrade.log_bets(conn, [_bet(1)])
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',2.0,3.5,4.0,0)"
    )
    conn.commit()
    assert papertrade.capture_closing(conn) == 0  # not kicked off yet


def test_settle_1x2_win_and_loss(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=2, as_=0)
    papertrade.log_bets(conn, [_bet(1, outcome="home", best_price=2.0, kelly=0.10)])
    papertrade.log_bets(conn, [_bet(1, outcome="away", best_price=5.0, kelly=0.05)])
    assert papertrade.settle(conn) == 2
    rows = {r["outcome"]: r for r in conn.execute("SELECT * FROM paper_bets")}
    assert rows["home"]["result"] == "win"
    assert abs(rows["home"]["pnl_flat"] - 1.0) < 1e-9
    assert abs(rows["home"]["pnl_kelly"] - 0.10 * 100.0 * 1.0) < 1e-9  # 10u stake * (2-1)
    assert rows["away"]["result"] == "loss"
    assert rows["away"]["pnl_flat"] == -1.0
    assert abs(rows["away"]["pnl_kelly"] - (-0.05 * 100.0)) < 1e-9


def test_settle_totals_push_and_win(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=1, as_=1)  # total 2
    papertrade.log_bets(conn, [_bet(1, market="totals", outcome="over", line=2.0, best_price=1.9)])
    papertrade.settle(conn)
    r = conn.execute("SELECT * FROM paper_bets").fetchone()
    assert r["result"] == "push" and r["pnl_flat"] == 0.0 and r["pnl_kelly"] == 0.0

    conn2 = _conn(tmp_path / "b")
    _add_match(conn2, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=2, as_=1)  # total 3
    papertrade.log_bets(conn2, [_bet(1, market="totals", outcome="over", line=2.5, best_price=1.9)])
    papertrade.settle(conn2)
    assert conn2.execute("SELECT result FROM paper_bets").fetchone()["result"] == "win"


def test_settle_only_finished(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2030-01-01T00:00:00Z")  # scheduled
    papertrade.log_bets(conn, [_bet(1)])
    assert papertrade.settle(conn) == 0


def test_summary_aggregates_roi_and_open(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=2, as_=0)
    _add_match(conn, 2, "2030-01-01T00:00:00Z")  # open / future
    papertrade.log_bets(
        conn, [_bet(1, outcome="home", best_price=2.0, kelly=0.10, kickoff="2000-01-01T00:00:00Z")]
    )
    papertrade.log_bets(conn, [_bet(2, outcome="home", best_price=2.0, kelly=0.10)])
    papertrade.settle(conn)
    s = papertrade.summary(conn)
    a = s["aggregate"]
    assert a["n_total"] == 2 and a["n_open"] == 1 and a["n_settled"] == 1
    assert a["wins"] == 1 and a["losses"] == 0
    assert abs(a["roi_flat"] - 1.0) < 1e-9  # +1u profit on 1u staked
    assert len(s["open"]) == 1 and len(s["settled"]) == 1
    assert "1x2" in s["by_market"]


def test_result_dc():
    assert papertrade._result_dc(2, 1, "1x") == "win"
    assert papertrade._result_dc(0, 1, "1x") == "loss"
    assert papertrade._result_dc(0, 1, "x2") == "win"


def test_result_dnb_push_on_draw():
    assert papertrade._result_dnb(1, 1, "home") == "push"
    assert papertrade._result_dnb(2, 1, "home") == "win"


def test_settle_dispatches_double_chance_and_dnb(tmp_path):
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=1, as_=1)  # draw
    papertrade.log_bets(conn, [_bet(1, market="double_chance", outcome="x2", best_price=1.5)])
    papertrade.log_bets(conn, [_bet(1, market="dnb", outcome="home", best_price=1.8)])
    assert papertrade.settle(conn) == 2
    rows = {r["market"]: r for r in conn.execute("SELECT * FROM paper_bets")}
    assert rows["double_chance"]["result"] == "win"  # draw covered by x2
    assert rows["dnb"]["result"] == "push"  # draw refunds dnb


def test_settle_double_chance_with_past_kickoff_skips_closing(tmp_path):
    """Regression: a DC bet that already kicked off must not KeyError in capture_closing.

    DC/DNB are implied markets with no book line; their outcomes (1x/12/x2) aren't in _OUT_IDX,
    so routing them to _closing_1x2 raised KeyError and aborted the whole settle() batch. They
    should be skipped for closing capture (closing_price/clv left NULL) and still settle.
    """
    conn = _conn(tmp_path)
    _add_match(conn, 1, "2000-01-01T00:00:00Z", status="FINISHED", hs=1, as_=1)  # draw
    papertrade.log_bets(
        conn,
        [
            _bet(
                1,
                market="double_chance",
                outcome="x2",
                best_price=1.5,
                kickoff="2000-01-01T00:00:00Z",
            )
        ],
    )
    assert papertrade.settle(conn) == 1  # no KeyError aborting the batch
    r = conn.execute("SELECT * FROM paper_bets").fetchone()
    assert r["result"] == "win"  # draw covered by x2 -> DC result resolves
    assert r["closing_price"] is None and r["clv"] is None  # implied market: no closing line
    assert r["closed_at"] is not None  # marked closed so capture won't retry forever
