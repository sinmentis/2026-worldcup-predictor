import pytest
from mcp.server.fastmcp.exceptions import ToolError

from worldcup_predictor import mcp_server


def test_mcp_player_status_accepts_and_validates_affects(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "t.db"))
    # valid defense tag is forwarded and stored active (official => gate passes)
    out = mcp_server.upsert_player_status(
        team="Germany",
        player="Schlotterbeck",
        tier="key",
        status="out",
        confidence=0.9,
        source_url="https://fed",
        official=True,
        affects="defense",
    )
    assert out["team"] == "Germany"
    conn = mcp_server._conn()
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"
    # invalid value rejected
    with pytest.raises(ToolError):
        mcp_server.upsert_player_status(
            team="Germany",
            player="X",
            tier="key",
            status="out",
            confidence=0.9,
            source_url="https://fed",
            affects="midfield",
        )


def test_mcp_team_signal_accepts_affects(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "t.db"))
    out = mcp_server.upsert_team_signal(
        team="Italy",
        category="tactical",
        direction="strengthen",
        magnitude_tier="major",
        confidence=0.9,
        source_url="https://fed",
        official=True,
        affects="defense",
    )
    assert out["team"] == "Italy"
    with pytest.raises(ToolError):
        mcp_server.upsert_team_signal(
            team="Italy",
            category="tactical",
            direction="strengthen",
            magnitude_tier="major",
            confidence=0.9,
            source_url="https://fed",
            affects="x",
        )
