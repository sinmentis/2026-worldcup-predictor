import pytest

from worldcup_predictor import mcp_server


def _structured_result(result):
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict):
            # list returns are wrapped as {"result": [...]}; dict returns are the dict itself
            return structured.get("result", structured)
        return structured
    return getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)


@pytest.mark.asyncio
async def test_list_tools_registered():
    tools = await mcp_server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "get_group_standings",
        "get_upcoming_matches",
        "record_match_result",
        "predict_match",
        "record_intel",
        "run_simulation",
    } <= names


@pytest.mark.asyncio
async def test_intel_tools_registered():
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "get_unprocessed_news",
        "upsert_player_status",
        "upsert_team_signal",
        "mark_news_processed",
        "list_pending_intel",
        "approve_intel",
        "reject_intel",
    } <= names


@pytest.mark.asyncio
async def test_upsert_team_signal_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "mcp.db"))
    mcp_server._reset_conn()

    result = await mcp_server.mcp.call_tool(
        "upsert_team_signal",
        {
            "team": "Brazil",
            "category": "tactical",
            "direction": "strengthen",
            "magnitude_tier": "moderate",
            "confidence": 0.9,
            "source_url": "https://fed",
            "official": True,
        },
    )
    out = _structured_result(result)
    assert out["status"] == "active"
    assert out["team"] == "Brazil"


@pytest.mark.asyncio
async def test_upsert_team_signal_rejects_bad_category(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "mcp.db"))
    mcp_server._reset_conn()

    with pytest.raises(Exception):  # noqa: B017 - ToolError surfaced by FastMCP
        await mcp_server.mcp.call_tool(
            "upsert_team_signal",
            {
                "team": "Brazil",
                "category": "vibes",
                "direction": "strengthen",
                "magnitude_tier": "moderate",
                "confidence": 0.9,
                "source_url": "https://fed",
            },
        )


@pytest.mark.asyncio
async def test_get_group_standings_returns_valid_group(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "mcp.db"))
    mcp_server._reset_conn()

    result = await mcp_server.mcp.call_tool("get_group_standings", {"group": "A"})

    standings = _structured_result(result)
    assert len(standings) == 4
    assert {row["team"] for row in standings} == {
        "Mexico",
        "South Africa",
        "South Korea",
        "Czech Republic",
    }
