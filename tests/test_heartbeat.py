import json
import time

from utils.heartbeat import read_heartbeat, write_heartbeat


def test_bot_vivant(tmp_path):
    p = tmp_path / "hb.json"
    write_heartbeat({"tracked_trades": 2}, p)
    hb = read_heartbeat(p)
    assert hb["alive"] and hb["tracked_trades"] == 2


def test_bot_arrete(tmp_path):
    p = tmp_path / "hb.json"
    p.write_text(json.dumps({"ts": time.time() - 600}), encoding="utf-8")
    assert read_heartbeat(p)["alive"] is False
    assert read_heartbeat(tmp_path / "absent.json") is None
