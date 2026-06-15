from worldcup_predictor import db, ingest
from worldcup_predictor.goal_model import history_frame

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,Brazil,Germany,2,1,Friendly,X,Y,False
2019-01-01,Brazil,Germany,1,1,Friendly,X,Y,False
"""


def test_history_frame_respects_since(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.load_history_from_text(conn, CSV)
    frame = history_frame(conn, since="2023-01-01")
    assert list(frame.columns) == [
        "date",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
        "neutral",
    ]
    assert len(frame) == 1
    assert frame.iloc[0]["home_goals"] == 2
