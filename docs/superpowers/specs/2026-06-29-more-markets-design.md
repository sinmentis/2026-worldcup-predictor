# More bet markets: Double Chance + Draw No Bet (derived) — design

## Problem
Value betting covers only 1X2, totals, spreads. The user wants more popular markets. The
Odds API bulk endpoint supports ONLY h2h/totals/spreads; btts/double_chance/draw_no_bet are
per-event "additional markets" (1 credit x market x region x event) — not budget-safe for
68 matches with 174 credits left. New market ODDS are not affordable.

## Insight
Double Chance and Draw No Bet are exact functions of the 1X2 prices already stored in
`odds`. Both our model probability AND the consensus derive from home/draw/away — no new
fetch, no new table, no credits. Two popular 玩法 for free.

## Markets (derived from existing 1X2)
- **Double Chance** (双重机会): model 1X=p_h+p_d, 12=p_h+p_a, X2=p_d+p_a. Consensus: same
  pairwise sums of the de-margined h2h median; price = 1/consensus_dc; EV vs that.
- **Draw No Bet** (DNB): model home=p_h/(p_h+p_a), away=p_a/(p_h+p_a); draw voids (push).
  Consensus normalised the same; price = 1/consensus.

## Approach
`value_bets_dc` / `value_bets_dnb` in valuebet.py reuse `consensus_probs` (existing
de-margined 1X2 median), combine to DC/DNB, compare to grid, edge >= floor, EV/Kelly vs
the consensus-implied price. No odds table, no fetch change. Paper-settle: DC on 90'
result; DNB win/void(draw). UI tags 双重机会 / DNB. Calibration deferred (like spreads).

## Out of scope
BTTS / correct-score / HT-FT (need paid per-event odds); calibration. Prices derive from
1X2, so these are variety/presentation, not new alpha — flag honestly.

## Testing
DC pairwise sums complementary; DNB normalises + draw voids; value fn flags when combined
prob beats consensus; paper-settle win/void. ruff/mypy --strict/pytest.
