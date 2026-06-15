from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("WC_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DB_PATH = Path(os.environ.get("WC_DB_PATH", DATA_DIR / "worldcup.db"))
CACHE_DIR = DATA_DIR / "cache"

HISTORY_CSV_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
)
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
FOOTBALL_DATA_COMP = "WC"

# eloratings.net K-factors by competition importance
K_TABLE = {
    "world_cup": 60,
    "continental_final": 50,
    "qualifier": 40,
    "minor_tournament": 30,
    "friendly": 20,
}
HOME_ADVANTAGE_ELO = 100  # added to a non-neutral home team's rating
DEFAULT_ELO = 1500.0
ELO_SHRINK_GAMES = 30  # shrink sparse teams toward the mean over this many games
TIME_DECAY_XI = 0.001  # Dixon-Coles weight decay (~693-day half-life)

HOSTS = {"Mexico", "Canada", "United States"}

GROUPS: dict[str, list[str]] = {
    "A": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "B": ["Canada", "Switzerland", "Bosnia and Herzegovina", "Qatar"],
    "C": ["Brazil", "Morocco", "Scotland", "Haiti"],
    "D": ["United States", "Australia", "Paraguay", "Turkey"],
    "E": ["Germany", "Ecuador", "Ivory Coast", "Curacao"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Uruguay", "Saudi Arabia", "Cape Verde"],
    "I": ["France", "Senegal", "Norway", "Iraq"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Colombia", "DR Congo", "Uzbekistan"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

# Map alternate spellings from external data sources (martj42 history, football-data.org)
# to the canonical team names used in GROUPS above. Extend as new mismatches surface.
TEAM_ALIASES: dict[str, str] = {
    "Curaçao": "Curacao",
}


def canonical_team(name: str) -> str:
    """Return the canonical GROUPS name for a possibly differently-spelled team name."""
    return TEAM_ALIASES.get(name, name)
