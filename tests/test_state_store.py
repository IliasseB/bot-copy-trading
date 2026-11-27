from utils.state_store import StateStore


def test_aller_retour(tmp_path):
    st = StateStore(tmp_path / "s.json")
    st.save({"last_signal_msg_id": 10853, "reply_root": {"10854": 10853}})
    assert StateStore(tmp_path / "s.json").load() == {"last_signal_msg_id": 10853, "reply_root": {"10854": 10853}}


def test_fichier_absent_ou_corrompu(tmp_path):
    assert StateStore(tmp_path / "absent.json").load() == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{pas du json", encoding="utf-8")
    assert StateStore(bad).load() == {}
