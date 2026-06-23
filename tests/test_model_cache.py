"""Goal-model cache concurrency: a cold cache under concurrent callers fits ONCE."""

import threading
import time

import pandas as pd

from worldcup_predictor import db, engine


def test_get_model_fit_lock_serializes_concurrent_cold_fits(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    engine._reset_model_cache()

    calls = {"n": 0}

    class _FakeModel:
        def fit(self, frame: pd.DataFrame, xi: float | None = None) -> "_FakeModel":
            calls["n"] += 1
            time.sleep(0.3)  # simulate a slow fit so threads overlap on the cold path
            return self

    monkeypatch.setattr(engine, "GoalModel", _FakeModel)
    monkeypatch.setattr(engine, "history_frame", lambda c: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(engine._tune, "current_xi", lambda c: 0.001)

    results: list[object] = []

    def worker() -> None:
        # Each request gets its own connection in production; SQLite forbids sharing a
        # connection across threads. The shared state is the model cache + fit-lock.
        c = db.connect(tmp_path / "t.db")
        try:
            results.append(engine.get_model(c))
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Despite 8 concurrent cold callers, only ONE fit runs (no thundering herd),
    # and every caller receives the same cached model instance.
    assert calls["n"] == 1
    assert len(results) == 8
    assert all(r is results[0] for r in results)

    engine._reset_model_cache()
