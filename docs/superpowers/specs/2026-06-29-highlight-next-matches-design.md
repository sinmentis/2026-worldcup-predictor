# Highlight next knockout matches — design

## Problem
The knockout tree shows real ties (已赛/真实) and projected ones (推测), but nothing
draws the eye to the matches about to be played next. The user wants the upcoming ties
highlighted.

## Definition of "next"
A node qualifies as **next** when: both teams are known (`home_known && away_known`),
`status !== "FINISHED"`, and its kickoff is in the future, AND its kickoff date equals the
**earliest future kickoff date** among all such nodes. This highlights the whole next
match-day's ties together (e.g. two R32 ties tomorrow), not a single match. Finished and
projected (推测) nodes never highlight.

## Approach (frontend only)
Pure client-side, in `app.js`/`styles.css` — the `/api/knockout/bracket` payload already
carries `status`, `kickoff`, `home_known`, `away_known`. No backend or data change.

- In `loadBracket`, after the data loads, compute the earliest future kickoff date across
  all real scheduled nodes (`nextMatchDay`). Pass it to `bracketNode`.
- `bracketNode` adds class `next` when the node's kickoff date equals `nextMatchDay` (and it
  is real + not finished), plus a small `下一场` tag.
- CSS `#bracket .match.next`: accent border + soft glow, consistent with the existing
  `:hover` accent; tag styled like the other badges. Compact (no width growth) so the tree
  still fits one screen.

## Out of scope
No countdown/live state in the tree (the 即将开赛 tab already does that); no backend.

## Testing
Visual: serve worktree on a spare port against the prod DB copy; screenshot the 淘汰赛 tab;
confirm the next-day real ties glow with the tag, finished/projected do not, and no
horizontal scroll at ~1280px.
