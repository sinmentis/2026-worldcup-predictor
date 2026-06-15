# Web: Upcoming Predictions + Accuracy + Fancy UI — Design Spec

**Date:** 2026-06-16
**Status:** Approved (autopilot; user asked to implement directly) -> building

## 1. Goal

Three user-facing web improvements:
1. **Accuracy view** — show the difference between our original predictions and actual results.
2. **Upcoming matches view** — list confirmed scheduled matches with our prediction for each, so
   the user knows how many matches are coming and what we expect.
3. **Fancier UI/UX** — modern, polished, Chinese-language single-page UI.

## 2. Data facts (verified)

- `predictions` table persists every prediction with `created_at` (84 rows). The EARLIEST row per
  match is treated as our "original" pre-result prediction.
- `evaluate.py` already computes model vs baseline RPS/Brier; reuse it for a per-match breakdown.
- football-data.org `/competitions/WC/matches` returns 104 matches: 12 FINISHED + 92 TIMED, each
  with `utcDate` and `matchday`. `matches.kickoff` is currently NULL; populate it by team-pair.

## 3. Backend

- `ingest.apply_fixtures_payload(conn, payload)` / `fetch_fixtures(conn, token)`: set
  `matches.kickoff = utcDate` for every match matched order-independently by team pair; also apply
  finished scores. CLI: `worldcup fetch-fixtures`.
- `evaluate.per_match_breakdown(conn)`: list of finished matches joined with their EARLIEST
  prediction; per row: teams, predicted probs + scoreline, actual score, outcome, top-pick-correct,
  exact-scoreline-hit, model RPS, baseline RPS.
- `engine.get_upcoming_predictions(conn, limit)`: scheduled matches ordered by
  `(kickoff IS NULL, kickoff, id)`, each with a live `predict_match` (probs, most-likely scoreline,
  expected goals) + active intel factors + kickoff + group; plus `remaining` total count.
- `engine.get_accuracy(conn)`: `{aggregate: evaluate.score_finished_predictions, matches:
  evaluate.per_match_breakdown, extra hit-rate/exact-rate}`.
- Endpoints: `GET /api/upcoming-predictions?limit=N`, `GET /api/accuracy`.

## 4. Frontend (static/)

Full rewrite: dark broadcast aesthetic, accent gradient, flag emojis (48-team map), animated
probability bars, color-coded W/D/L, matchday grouping + kickoff display, accuracy scoreboard
(model RPS vs baseline, hit-rate, exact-rate), responsive cards. Chinese chrome.
Tabs: 即将开赛 (default) · 预测总览 · 战绩对比 · 小组积分 · 淘汰赛. SSE live-refresh retained.

## 5. Testing

Unit: `per_match_breakdown`, `get_upcoming_predictions`, `get_accuracy`, `apply_fixtures_payload`
(kickoff set). Web: new endpoints return 200 + expected keys. Smoke: load page, curl endpoints.

## 6. Out of scope

Per-match detail redesign beyond data already exposed; auth; historical prediction charts.
