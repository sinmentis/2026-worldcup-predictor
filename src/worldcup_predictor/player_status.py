from worldcup_predictor.config import ADJUST_CLAMP, LAMBDA_MIN  # noqa: F401

TIERS = {"key", "regular", "fringe"}
STATUSES = {"out", "doubtful", "suspended", "available"}

MAGNITUDE_TABLE: dict[tuple[str, str], float] = {
    ("key", "out"): 0.72,
    ("key", "suspended"): 0.72,
    ("key", "doubtful"): 0.88,
    ("regular", "out"): 0.85,
    ("regular", "suspended"): 0.85,
    ("regular", "doubtful"): 0.93,
    ("fringe", "out"): 0.96,
    ("fringe", "suspended"): 0.96,
    ("fringe", "doubtful"): 0.98,
}

ACTIVE_CRED_THRESHOLD = 0.70
ACTIVE_CONF_THRESHOLD = 0.60
DEFAULT_EXPIRY_DAYS = 14


def status_mult(tier: str, status: str) -> float:
    return MAGNITUDE_TABLE.get((tier, status), 1.0)


def derive_credibility(n_sources: int, official: bool) -> float:
    if official:
        return 0.95
    if n_sources >= 2:
        return 0.80
    return 0.50
