import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "sched-results.sh"


def _run_scheduler(
    tmp_path,
    ticks,
    fail_first_refresh=False,
    fail_fetch_after_first=False,
    fail_paper=False,
):
    repo = tmp_path / "repo"
    deploy = repo / "deploy"
    bin_dir = repo / ".venv" / "bin"
    data = repo / "data"
    deploy.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    data.mkdir()

    script = SCRIPT.read_text(encoding="utf-8")
    tick_values = " ".join(str(i) for i in range(ticks))
    script = script.replace(
        'while [ "$(date +%s)" -lt "$end_epoch" ]; do',
        f"for _tick in {tick_values}; do",
    )
    script = script.replace('  sleep "$interval"', "  :")
    script = script.replace("/tmp/wc-sched-results.log", str(tmp_path / "scheduler.log"))
    script = script.replace("/tmp/wc-sched-results.pid", str(tmp_path / "scheduler.pid"))
    script_path = deploy / "sched-results.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)

    calls = tmp_path / "calls.log"
    attempts = tmp_path / "refresh-attempts"
    fetch_attempts = tmp_path / "fetch-attempts"
    fake_worldcup = bin_dir / "worldcup"
    fake_worldcup.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$WC_FAKE_CALLS"\n'
        'if [ "${1:-}" = "fetch-fixtures" ]; then\n'
        '  n=$(cat "$WC_FAKE_FETCH_ATTEMPTS" 2>/dev/null || echo 0)\n'
        "  n=$((n + 1))\n"
        '  echo "$n" > "$WC_FAKE_FETCH_ATTEMPTS"\n'
        '  if [ "${WC_FAIL_FETCH_AFTER_FIRST:-0}" = "1" ] && [ "$n" -gt 1 ]; then\n'
        "    exit 8\n"
        "  fi\n"
        "fi\n"
        'if [ "${1:-}" = "paper-settle" ] && [ "${WC_FAIL_PAPER:-0}" = "1" ]; then\n'
        "  exit 9\n"
        "fi\n"
        'if [ "${1:-}" = "refresh-results-model" ]; then\n'
        '  n=$(cat "$WC_FAKE_ATTEMPTS" 2>/dev/null || echo 0)\n'
        "  n=$((n + 1))\n"
        '  echo "$n" > "$WC_FAKE_ATTEMPTS"\n'
        '  if [ "${WC_FAIL_FIRST_REFRESH:-0}" = "1" ] && [ "$n" -eq 1 ]; then\n'
        "    exit 7\n"
        "  fi\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_worldcup.chmod(0o755)
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/usr/bin/env bash\necho 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "WC_FAKE_CALLS": str(calls),
            "WC_FAKE_ATTEMPTS": str(attempts),
            "WC_FAKE_FETCH_ATTEMPTS": str(fetch_attempts),
            "WC_FAIL_FIRST_REFRESH": "1" if fail_first_refresh else "0",
            "WC_FAIL_FETCH_AFTER_FIRST": "1" if fail_fetch_after_first else "0",
            "WC_FAIL_PAPER": "1" if fail_paper else "0",
            "WC_SCHED_RESULTS_LOCK": str(tmp_path / "scheduler.lock"),
        }
    )
    result = subprocess.run(
        ["bash", str(script_path), "0", "4102444800"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    call_lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, call_lines


def test_scheduler_refreshes_on_same_count_corrections(tmp_path):
    result, calls = _run_scheduler(tmp_path, ticks=1)

    assert result.returncode == 0
    assert calls == [
        "fetch-fixtures",
        "paper-settle",
        "refresh-results-model --n 20000",
    ]


def test_scheduler_retries_failed_refresh_on_next_tick(tmp_path):
    result, calls = _run_scheduler(
        tmp_path,
        ticks=2,
        fail_first_refresh=True,
        fail_fetch_after_first=True,
    )

    assert result.returncode == 0
    assert calls.count("refresh-results-model --n 20000") == 2


def test_scheduler_refreshes_even_when_paper_settlement_fails(tmp_path):
    result, calls = _run_scheduler(tmp_path, ticks=1, fail_paper=True)

    assert result.returncode == 0
    assert "refresh-results-model --n 20000" in calls


def test_scheduler_uses_single_instance_lock_and_no_count_gate():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "flock -n" in script
    assert "finished_count" not in script
