from typer.testing import CliRunner

from worldcup_predictor.cli import app

runner = CliRunner()


def test_init_db_and_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    assert runner.invoke(app, ["init-db"]).exit_code == 0
    res = runner.invoke(app, ["seed"])
    assert res.exit_code == 0
    assert "48" in res.stdout


def test_simulate_small(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["seed"])
    runner.invoke(app, ["load-history", "--file", "tests/fixtures/mini_history.csv"])
    res = runner.invoke(app, ["simulate", "--n", "50", "--seed", "1"])
    assert res.exit_code == 0


def test_fetch_news_command_wired(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    # Point all feeds at an unreachable host so the command runs offline and returns 0.
    from worldcup_predictor import config

    monkeypatch.setattr(config, "RSS_FEEDS", {"x": "http://127.0.0.1:0/none.xml"})
    runner.invoke(app, ["init-db"])
    res = runner.invoke(app, ["fetch-news"])
    assert res.exit_code == 0
    assert "0" in res.stdout
