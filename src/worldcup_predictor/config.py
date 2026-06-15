from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load a project-root .env (if present) without overriding real environment variables.
# This lets the CLI / web / MCP / cron pick up FOOTBALL_DATA_TOKEN and WC_* settings.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

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

# Off-pitch intel tuning (Phase 2a). ADJUST_CLAMP bounds the net per-team lambda multiplier delta.
LAMBDA_MIN = 0.05
ADJUST_CLAMP = (-0.6, 0.6)

# Free RSS news feeds for off-pitch intelligence (no API key needed).
RSS_FEEDS = {
    "BBC Sport": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "Sky Sports": "https://www.skysports.com/rss/12040",
    "Guardian Football": "https://www.theguardian.com/football/rss",
    "ESPN Soccer": "https://www.espn.com/espn/rss/soccer/news",
}

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
    "Czechia": "Czech Republic",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
}


def canonical_team(name: str) -> str:
    """Return the canonical GROUPS name for a possibly differently-spelled team name."""
    return TEAM_ALIASES.get(name, name)
