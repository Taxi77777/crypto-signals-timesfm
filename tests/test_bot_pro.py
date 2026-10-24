"""Tests hors ligne de bot_pro (aucun appel reseau) : python -m pytest -q"""
import datetime
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot_pro as B  # noqa: E402

DAY = datetime.date(2026, 10, 21)          # un mercredi


def synth_24h(drift_today=0.0, seed=3, p=100.0):
    """60 jours de bougies 5 min 24 h/24 (crypto / forex), tendance optionnelle le jour J."""
    rng = random.Random(seed)
    start = B.at(DAY - datetime.timedelta(days=60), B.hm(0), B.UTC)
    out = []
    for i in range(61 * 288):
        ts = start + i * 300
        if ts > B.at(DAY, B.hm(12), B.UTC):
            break
        mu = drift_today if ts >= B.at(DAY, B.hm(6), B.UTC) else 0.0
        o = p
        c = p * math.exp(rng.gauss(mu, 0.0015))
        out.append((ts, o, max(o, c) * 1.0005, min(o, c) * 0.9995, c, 1000.0))
        p = c
    return out


def synth_stock(seed=5, p=50.0):
    """60 seances US de bougies 5 min (9 h 30 - 16 h) avec volume, ouverture tres active le jour J."""
    rng = random.Random(seed)
    out = []
    d = DAY - datetime.timedelta(days=90)
    while d <= DAY:
        if B.stock_open(d):
            t0 = B.at(d, B.hm(9, 30), B.NY)
            for k in range(78):
                o = p
                c = p * math.exp(rng.gauss(0.0004 if d == DAY else 0.0, 0.003))
                v = 2e6 if k == 0 else 3e5
                if d == DAY and k == 0:
                    v, c = 6e6, o * 1.01                   # bougie d'ouverture haussiere, volume x3
                out.append((t0 + k * 300, o, max(o, c) * 1.001, min(o, c) * 0.999, c, v))
                p = c
        d += datetime.timedelta(days=1)
    return out


def html_ok(t):
    stack = []
    for m in re.finditer(r"<(/?)([a-z]+)>|<|&(?!amp;|lt;|gt;)", t):
        if not m.group(2) or m.group(2) not in ("b", "i", "code"):
            return False
        if m.group(1):
            if not stack or stack.pop() != m.group(2):
                return False
        else:
            stack.append(m.group(2))
    return not stack


def test_noise_boundary_signal_on_trend_day():
    bars = synth_24h(drift_today=0.0012)
    days = B.by_day(bars, B.UTC, B.hm(0))
    dk = sorted(days)
    sigs = B.mom_eval_day(days, DAY, "cr", None, days[dk[-2]][-1][4])
    assert sigs and sigs[0]["dir"] == 1
    s = sigs[0]
    assert s["price"] > s["upper"] and s["price"] > s["vwap"] and s["stop"] < s["price"]


def test_backtest_and_monte_carlo():
    days = B.by_day(synth_24h(), B.UTC, B.hm(0))
    rs = B.mom_backtest(days, "cr", DAY, 1)
    st = B.bt_stats(rs)
    assert st["n"] > 5 and all(r >= -1.5 for r in rs)
    mc = B.mc_trades([1, -1, 2, -1, 0.5, -1, 3, -1, -1, 1], sims=2000)
    assert 0 <= mc["p_win"] <= 1 and mc["p5"] <= mc["med"]


def test_stocks_in_play_selection():
    a = B.sip_analyse("TEST", synth_stock(), DAY)
    assert a and a["dir"] == 1 and a["rvol"] > 2.5
    assert a["stop"] < a["entry"] and abs(a["entry"] - a["stop"] - 0.10 * a["atr"]) < 1e-9
    rs = B.sip_backtest(a["days"], DAY, 0.10)
    assert all(r >= -1.5 for r in rs)


def test_market_calendars():
    sat = datetime.date(2026, 10, 24)
    assert B.mom_day_ok("cr", sat) and not B.mom_day_ok("fx", sat) and not B.mom_day_ok("stocks", sat)
    assert not B.stock_open(datetime.date(2026, 11, 26))          # Thanksgiving
    assert B.stock_close_t(datetime.date(2026, 11, 27)) == B.hm(13)


def test_message_is_valid_telegram_html(monkeypatch):
    bars = synth_24h(drift_today=0.0012)
    sent = []
    monkeypatch.setattr(B, "telegram", lambda m, html=True: sent.append(m))
    monkeypatch.setattr(B, "chart", lambda s, r, i: bars)
    monkeypatch.setattr(B, "regime_update", lambda force=False: {"label": "RISK-ON <test>", "spy": 1, "vix": 15, "ts": 0})
    monkeypatch.setattr(B, "news", lambda s, n=2: ["Titre avec <balise> & esperluette"])
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    monkeypatch.setitem(B.CFG, "MOM_CRYPTO", ["BTC"])
    monkeypatch.setitem(B.CFG, "MIN_SCORE", 0)
    monkeypatch.setitem(B.CFG, "NEED_BT", 0)
    monkeypatch.setitem(B.CFG, "STATE_DIR", "/tmp/botpro_test")
    st = {"alerts": {}, "done": {}, "sip": {}, "signals": [], "day": {}, "open": [], "track": []}
    for hh in range(6, 12):
        for mm in (0, 30):
            B.mom_scan(st, "cr", B.at(DAY, B.hm(hh, mm), B.UTC) + 60)
    assert sent, "aucun signal sur une journee en forte tendance"
    assert all(html_ok(m) for m in sent)
    assert "score" in sent[0] and "10 000 simulations" in sent[0]


def test_price_format():
    B.FMT[0] = 5
    assert B.fmt(151.234) == "151.234" and B.fmt(1.08123) == "1.08123"
    assert B.fmt(0.0000123) != B.fmt(0.0000124)
    B.FMT[0] = 2
    assert B.fmt(364.176) == "364.18"


def _stub(monkeypatch, bars, sent):
    monkeypatch.setattr(B, "telegram", lambda m, html=True: sent.append(m))
    monkeypatch.setattr(B, "chart", lambda s, r, i: bars)
    monkeypatch.setattr(B, "regime_update", lambda force=False: {"label": "neutre", "spy": 1, "vix": 15, "ts": 0})
    monkeypatch.setattr(B, "news", lambda s, n=2: [])
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    monkeypatch.setitem(B.CFG, "MIN_SCORE", 0)
    monkeypatch.setitem(B.CFG, "NEED_BT", 0)
    monkeypatch.setitem(B.CFG, "STATE_DIR", "/tmp/botpro_test")
    B.HIST.clear()


def test_correlation_guard_and_tracking(monkeypatch):
    bars = synth_24h(drift_today=0.0012)
    sent = []
    _stub(monkeypatch, bars, sent)
    monkeypatch.setitem(B.CFG, "MOM_CRYPTO", ["BTC", "ETH", "SOL", "XRP", "ADA"])
    monkeypatch.setitem(B.CFG, "MAX_SAME", 3)
    st = {"alerts": {}, "done": {}, "sip": {}, "signals": [], "day": {}, "open": [], "track": []}
    first = None
    for hh in range(6, 12):
        for mm in (0, 30):
            n = B.mom_scan(st, "cr", B.at(DAY, B.hm(hh, mm), B.UTC) + 60)
            assert n <= 3
            if n and first is None:
                first = n
    assert first == 3 and len(st["open"]) >= 3
    assert st["day"].get("mom_correles", 0) >= 2
    # suivi : on rejoue la fin de journee (tendance haussiere -> resultats positifs ou TP)
    sent.clear()
    end = B.at(DAY, B.hm(12), B.UTC) + 300
    for o in st["open"]:
        o["t_flat"] = B.at(DAY, B.hm(11, 55), B.UTC)
    B.track_update(st, end)
    assert not st["open"] and st["track"]
    assert all("RÉSULTAT" in m and html_ok(m) for m in sent)
    assert "Bilan" in B.scorecard(st)


def test_position_size(monkeypatch):
    monkeypatch.setitem(B.CFG, "CAPITAL", 1000.0)
    monkeypatch.setitem(B.CFG, "RISK_PCT", 1.0)
    line = B.size_line(100.0, 98.0, "stocks")
    assert "5 actions" in line or "5.000 actions" in line
    assert html_ok(line)
    monkeypatch.setitem(B.CFG, "CAPITAL", 0.0)
    assert "capital" in B.size_line(1.10, 1.098, "fx")
