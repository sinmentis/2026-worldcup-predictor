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
