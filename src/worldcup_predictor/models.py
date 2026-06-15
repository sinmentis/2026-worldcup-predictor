from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HistMatch:
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool


@dataclass(frozen=True)
class Fixture:
    id: int
    stage: str
    group_id: str | None
    home_team: str | None
    away_team: str | None
    kickoff: str | None
    neutral: bool
    home_score: int | None
    away_score: int | None
    status: str


@dataclass
class IntelFactor:
    team: str
    description: str
    lambda_delta: float


@dataclass
class MatchPrediction:
    home_team: str
    away_team: str
    p_home: float
    p_draw: float
    p_away: float
    exp_home_goals: float
    exp_away_goals: float
    ml_home: int
    ml_away: int
    factors: list[IntelFactor] = field(default_factory=list)

    @property
    def most_likely_scoreline(self) -> str:
        return f"{self.ml_home}-{self.ml_away}"


@dataclass
class GroupRow:
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    gd: int
    pts: int


@dataclass
class IntelEvent:
    team: str
    event_type: str
    direction: str
    magnitude: float
    source_url: str
    credibility: float
    player: str | None = None
    valid_from: str | None = None
    notes: str | None = None
