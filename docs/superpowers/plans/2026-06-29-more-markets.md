# Double Chance + Draw No Bet markets — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Add Double Chance (1X/12/X2) and Draw No Bet value markets, derived from the existing 1X2 odds + grid — no new fetch, no credits.

**Architecture:** New `value_bets_dc`/`value_bets_dnb` in valuebet.py reuse `consensus_probs` and `predict_match`; settle fns in papertrade.py; wired into `engine.get_value_bets`, CLI display, UI tags. Bookmakers rarely list these, so the "price" is the consensus-implied fair price (1/prob) — variety, not new alpha.

**Tech Stack:** Python 3.12, uv, sqlite3, pytest, ruff, mypy --strict. No new deps.

## Global Constraints
- DC: 1X=p_h+p_d, 12=p_h+p_a, X2=p_d+p_a; markets "double_chance"; outcomes "1x"/"12"/"x2".
- DNB: home=p_h/(p_h+p_a), away=p_a/(p_h+p_a); market "dnb"; draw voids (push). best_price=1/market_prob, bookmaker="implied".
- Bet dict keys identical to value_bets: match_id, home_team, away_team, group, kickoff, market, outcome, line(None), our_prob, market_prob, edge, best_price, ev, kelly.
- ruff/mypy --strict/pytest all pass; English; Conventional Commit + Copilot trailer.

---

### Task 1: DC + DNB value functions
**Files:** Modify `src/worldcup_predictor/valuebet.py`; Test `tests/test_valuebet.py`
**Interfaces:** Produces `value_bets_dc(conn,model,min_edge=None,kelly_fraction=None)` and `value_bets_dnb(...)` -> list[dict] (same keys as value_bets).

- [ ] Step 1: Write failing tests
```python
def test_value_bets_dc_flags_safe_double_chance(tmp_path):
    conn=_conn(tmp_path); m=_model()
    conn.execute("INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at) VALUES (1,'b',1.30,6.0,9.0,1.0),(1,'c',1.32,6.2,9.2,1.0)"); conn.commit()
    bets=valuebet.value_bets_dc(conn,m,min_edge=0.01)
    assert any(b["market"]=="double_chance" and b["outcome"]=="1x" for b in bets)
def test_value_bets_dnb_normalises(tmp_path):
    conn=_conn(tmp_path); m=_model()
    conn.execute("INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at) VALUES (1,'b',1.30,6.0,9.0,1.0),(1,'c',1.32,6.2,9.2,1.0)"); conn.commit()
    bets=valuebet.value_bets_dnb(conn,m,min_edge=0.0)
    assert all(b["market"]=="dnb" for b in bets) and all(b["best_price"]>1 for b in bets)
```
- [ ] Step 2: `uv run pytest tests/test_valuebet.py -k "dc or dnb" -q` → FAIL (no attr)
- [ ] Step 3: Implement (append to valuebet.py, mirror value_bets_totals row loop):
```python
def _derived_bets(conn, model, min_edge, kelly_fraction, build):
    edge_floor = config.VALUE_MIN_EDGE if min_edge is None else min_edge
    kfrac = config.KELLY_FRACTION if kelly_fraction is None else kelly_fraction
    rows = conn.execute("SELECT DISTINCT m.id, m.home_team, m.away_team, m.group_id, m.kickoff, m.neutral FROM matches m JOIN odds o ON o.match_id=m.id WHERE m.status='SCHEDULED' AND (m.kickoff IS NULL OR m.kickoff > ?) ORDER BY (m.kickoff IS NULL), m.kickoff, m.id", (_now_z(),)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        cons = consensus_probs(conn, r["id"])
        if cons is None: continue
        pred = predict_match(conn, model, r["home_team"], r["away_team"], match_id=None, neutral=bool(r["neutral"]))
        for market, outcome, op, mp in build(pred, cons):
            edge = op - mp
            if edge < edge_floor: continue
            price = 1.0 / mp if mp > 0 else 0.0
            out.append({"match_id": r["id"], "home_team": r["home_team"], "away_team": r["away_team"], "group": r["group_id"], "kickoff": r["kickoff"], "market": market, "outcome": outcome, "line": None, "our_prob": op, "market_prob": mp, "edge": edge, "best_price": price if price > 1 else None, "bookmaker": "implied", "ev": op * price - 1.0 if price > 1 else None, "kelly": max(0.0, (op*price-1.0)/(price-1.0))*kfrac if price > 1 else 0.0})
    out.sort(key=lambda b: b["edge"], reverse=True); return out

def value_bets_dc(conn, model, min_edge=None, kelly_fraction=None):
    def b(p, c): return [("double_chance","1x",p.p_home+p.p_draw,c[0]+c[1]),("double_chance","12",p.p_home+p.p_away,c[0]+c[2]),("double_chance","x2",p.p_draw+p.p_away,c[1]+c[2])]
    return _derived_bets(conn, model, min_edge, kelly_fraction, b)

def value_bets_dnb(conn, model, min_edge=None, kelly_fraction=None):
    def b(p, c):
        d=p.p_home+p.p_away; dc=c[0]+c[2]
        return [] if d<=0 or dc<=0 else [("dnb","home",p.p_home/d,c[0]/dc),("dnb","away",p.p_away/d,c[2]/dc)]
    return _derived_bets(conn, model, min_edge, kelly_fraction, b)
```
Add types/signatures matching value_bets_totals (`-> list[dict[str, Any]]`, conn typed).
- [ ] Step 4: pytest pass. Step 5: ruff/mypy clean. Step 6: commit `feat(valuebet): double chance + draw-no-bet value bets`.

### Task 2: Paper settle DC + DNB
**Files:** Modify `src/worldcup_predictor/papertrade.py`; Test `tests/test_papertrade.py`
**Interfaces:** `_result_dc(hs,as_,outcome)`, `_result_dnb(hs,as_,outcome)`; settle dispatch on market "double_chance"/"dnb".
- [ ] Step 1: failing tests: DC 1x wins if hs>=as; DNB home win if hs>as, push if hs==as.
```python
def test_result_dc(): assert _result_dc(2,1,"1x")=="win"; assert _result_dc(0,1,"1x")=="loss"; assert _result_dc(0,1,"x2")=="win"
def test_result_dnb_push_on_draw(): assert _result_dnb(1,1,"home")=="push"; assert _result_dnb(2,1,"home")=="win"
```
- [ ] Step 2 FAIL. Step 3 implement:
```python
def _result_dc(hs,as_,outcome): w="home" if hs>as_ else "away" if as_>hs else "draw"; pairs={"1x":("home","draw"),"12":("home","away"),"x2":("draw","away")}; return "win" if w in pairs[outcome] else "loss"
def _result_dnb(hs,as_,outcome):
    if hs==as_: return "push"
    return "win" if (outcome=="home")==(hs>as_) else "loss"
```
add `elif r["market"]=="double_chance": res=_result_dc(hs,as_,r["outcome"])` / `elif r["market"]=="dnb": res=_result_dnb(...)` to settle dispatch.
- [ ] Step 4 pass. Step 5 lint. Step 6 commit `feat(papertrade): settle double chance + dnb`.

### Task 3: Wire engine/CLI/UI
**Files:** Modify `engine.py` get_value_bets; `cli.py` value_bets display; `static/app.js` market tags; Test `tests/test_engine_read.py`
- [ ] Step1 test: get_value_bets includes double_chance + dnb markets when odds present.
- [ ] Step3: `bets += _valuebet.value_bets_dc(...); bets += _valuebet.value_bets_dnb(...)` after spreads (mirror lines 335-338). CLI: add elif market formatting (双重机会 outcome, DNB Team). app.js valuebet tag: 双重机会/不败. 
- [ ] Step4 suite pass; ruff/mypy; visual value-bets tab shows new tags. Step6 commit `feat: surface double chance + dnb in recs + UI`.

## Deployment: restart service; value-bets shows new 玩法 (uncalibrated, like spreads — flag).
