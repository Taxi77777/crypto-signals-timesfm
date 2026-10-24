"""Tests de non-regression des points releves par l'audit (hors ligne)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot_pro as B  # noqa: E402
from test_bot_pro import DAY, synth_24h, _stub, html_ok  # noqa: E402


def _t(h, m, d=DAY):
    return B.at(d, B.hm(h, m), B.NY)


def test_sip_stop_before_fill_is_not_a_stop(monkeypatch):
    bars = [(_t(9, 30), 100.5, 101.0, 100.4, 100.9, 1e6), (_t(9, 35), 100.9, 100.95, 100.4, 100.6, 1e5),
            (_t(9, 40), 100.6, 101.2, 100.6, 101.1, 1e5), (_t(9, 45), 101.1, 101.3, 101.0, 101.2, 1e5)]
    sent = []
    monkeypatch.setattr(B, "telegram", lambda m, html=True: sent.append(m))
    monkeypatch.setattr(B, "chart", lambda s, r, i, prepost=False, meta=False: bars)
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    st = B.load_state()
    st["sip"] = {"date": DAY.isoformat(), "sent": _t(9, 37),
                 "picks": [{"sym": "TEST", "dir": 1, "entry": 101.0, "stop": 100.5, "hit": False, "score": 70}]}
    B.sip_triggers(st, _t(9, 50))
    assert sent and "SL a déjà été touché" not in sent[0] and len(st["open"]) == 1
    assert html_ok(sent[0])
    # le stop est touche APRES l'entree -> -1 R (moins les frais) publie au bilan
    bars.append((_t(9, 50), 101.2, 101.2, 100.3, 100.4, 1e5))
    B.HIST.clear()
    B.track_update(st, _t(10, 0))
    assert not st["open"] and st["track"][0]["r"] < -1.0 and st["track"][0]["why"] == "SL"


def test_trailing_stop_never_fills_above_market():
    d = DAY
    t0 = B.at(d, B.hm(0), B.UTC)
    bs = [(t0 + k * 300, 100.0, 100.2, 99.9, 100.0 + 0.01 * k, 1.0) for k in range(10)]
    bs += [(t0 + 10 * 300, 100.1, 100.15, 100.0, 100.05, 1.0), (t0 + 11 * 300, 100.05, 100.1, 98.0, 98.5, 1.0)]
    s = {"dir": 1, "k": 2, "price": 100.02, "stop": 99.5, "upper": 101.5, "lower": 98.5, "hi0": 100.0,
         "lo0": 100.0, "kind": "cr", "t_flat": t0 + 86400}
    r = B.mom_exit({d: bs}, d, s)
    # borne 101.5 deja au-dessus du prix au controle de 00:30 -> sortie a la cloture, jamais au-dessus
    assert r <= (max(b[2] for b in bs[3:]) - 100.02) / 0.52


def test_track_bad_record_does_not_repeat(monkeypatch):
    bars = synth_24h(drift_today=0.0012)
    sent = []
    _stub(monkeypatch, bars, sent)
    good = {"strat": "dernière 1/2 h", "kind": "cr", "sym": "BTC", "dir": 1, "entry": bars[-50][4],
            "stop": bars[-50][4] * 0.9, "t_flat": bars[-2][0], "day": DAY.isoformat(), "sent": bars[-50][0]}
    bad = {"strat": "momentum", "kind": "cr", "sym": "ETH", "dir": 1, "entry": 1.0, "stop": 0.9, "t_flat": 0}
    gone = dict(good, day=(DAY - datetime.timedelta(days=70)).isoformat(), t_flat=bars[0][0] - 86400)
    st = B.load_state()
    st["open"] = [good, bad, gone]
    for _ in range(3):
        B.track_update(st, bars[-1][0] + 600)
    assert len(st["track"]) == 1 and len(sent) == 1 and st["open"] == []


def test_load_state_repairs_types(tmp_path):
    (tmp_path / B.STATE_FILE).write_text('{"open": null, "alerts": [1, 2], "track": [{"r": "x"}, {"r": 1.0}]}')
    st = B.load_state()
    assert st["open"] == [] and st["alerts"] == {} and len(st["track"]) == 1
    B.save_state(st)


def test_offsets_cache_is_content_keyed():
    d = DAY
    t0 = B.at(d, B.hm(0), B.UTC)
    a = {d: [(t0, 100.0, 100, 100, 100, 1)]}
    m1 = B.day_offsets(a, d, "cr")
    del a
    b = {d: [(t0, 30000.0, 30000, 30000, 30000, 1)]}
    assert B.day_offsets(b, d, "cr")[0][1] == 30000.0 and m1[0][1] == 100.0


def test_public_log_hides_position_size(monkeypatch, caplog):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with caplog.at_level("INFO", logger="botpro"):
        B.telegram("ligne\n📦 Taille : <b>12 actions</b> pour risquer 10 €")
    assert "12 actions" not in caplog.text and "masquée" in caplog.text


def test_envf_rejects_bad_values(monkeypatch):
    monkeypatch.setenv("XX_TEST", "inf")
    assert B.envf("XX_TEST", 3) == 3
    monkeypatch.setenv("XX_TEST", "")
    assert B.envf("XX_TEST", 2.5) == 2.5
    monkeypatch.setenv("XX_TEST", "0,5")
    assert B.envf("XX_TEST", 1.0) == 0.5


def test_half_day_momentum_exit_before_close():
    d = datetime.date(2026, 11, 27)                      # cloture a 13 h
    t0 = B.at(d, B.hm(9, 30), B.NY)
    days = {}
    for i in range(30, 0, -1):
        x = d - datetime.timedelta(days=i)
        if B.stock_open(x):
            days[x] = [(B.at(x, B.hm(9, 30), B.NY) + k * 300, 100, 100.3, 99.7, 100 + (0.2 if k % 2 else -0.2), 1)
                       for k in range(78)]
    days[d] = [(t0 + k * 300, 100.0, 100.5 + k, 100.0, 100.4 + k, 1) for k in range(42)]
    sig = B.mom_eval_day(days, d, "stocks", None, 100.0)
    assert sig and all(s["t_flat"] == B.at(d, B.hm(12, 55), B.NY) for s in sig)
    assert all(s["ts"] <= B.at(d, B.hm(12, 30), B.NY) for s in sig)


def test_lh_waits_for_decision_bar(monkeypatch):
    d = DAY
    t_dec, _ = B.lh_times(d)
    sent = []
    bars = synth_24h()
    _stub(monkeypatch, bars, sent)
    # aucune bougie du jour jusqu'a 15 h 30 -> pas de decision, on reessaie (done non pose)
    st = B.load_state()
    monkeypatch.setattr(B, "hist", lambda kind, sym: [b for b in bars if b[0] + 300 <= t_dec - 900])
    B.lh_scan(st, t_dec + 60)
    assert "lh" not in st["done"]


def test_fomc_after_announcement():
    t = B.at(datetime.date(2026, 10, 28), B.hm(15), B.NY)
    assert "annonce faite" in B.fomc_note(t)


def test_briefing_uses_two_decimals(monkeypatch):
    daily = [(1790000000 + i * 86400, 18.0, 18.5, 17.5, 18.2, 0.0) for i in range(70)]
    monkeypatch.setattr(B, "chart", lambda s, r, i, prepost=False, meta=False: daily)
    monkeypatch.setattr(B, "econ_today", lambda: [])
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    B.FMT[0] = 5
    m = B.briefing()
    assert "18.20000" not in m and "18.20" in m and html_ok(m)
