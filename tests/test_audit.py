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


def test_cot_bias_and_summary(monkeypatch):
    rows = []
    for date, eur, jpy in (("2026-10-20T00:00:00.000", (200, 50), (40, 140)), ("2026-10-13T00:00:00.000", (180, 60), (50, 120))):
        rows.append({"cftc_contract_market_code": "099741", "report_date_as_yyyy_mm_dd": date, "open_interest_all": "1000",
                     "noncomm_positions_long_all": str(eur[0]), "noncomm_positions_short_all": str(eur[1])})
        rows.append({"cftc_contract_market_code": "097741", "report_date_as_yyyy_mm_dd": date, "open_interest_all": "1000",
                     "noncomm_positions_long_all": str(jpy[0]), "noncomm_positions_short_all": str(jpy[1])})
    monkeypatch.setattr(B, "_get_json", lambda url, ttl, headers=None: rows if "cftc" in url else None)
    c = B.cot_data()
    assert round(c["EUR"]["net"]) == 15 and round(c["JPY"]["net"]) == -10 and round(c["EUR"]["chg"]) == 3
    assert B.cot_bias("EURUSD")[0] == 1 and B.cot_bias("USDJPY")[0] == 1 and B.cot_bias("EURJPY")[0] == 1
    assert "CONTRE" in B.cot_line("EURUSD", -1) and "dans ton sens" in B.cot_line("EURUSD", 1)
    assert "ils achètent euro" in B.cot_summary() and "ils vendent yen" in B.cot_summary()
    assert B.cot_bias("GBPUSD") == (0, None)


def test_openinsider_parser():
    html = ('<table class="tinytable"><tr><th>X</th></tr>'
            '<tr><td>M</td><td>2026-10-20</td><td>NVDA</td><td>CEO</td><td>P - Purchase</td><td>$120.00</td>'
            '<td>+1,000</td><td>+$120,000</td></tr>'
            '<tr><td>M</td><td>2026-10-21</td><td>NVDA</td><td>CFO</td><td>S - Sale</td><td>$121.00</td>'
            '<td>-500</td><td>-$60,500</td></tr></table>')
    assert B.parse_openinsider(html) == {"n": 1, "usd": 120000.0}
    assert B.parse_openinsider("<html>rien</html>") == {"n": 0, "usd": 0.0}


def test_kraken_perp_candles_and_universe(monkeypatch):
    class Resp:
        def __init__(self, j):
            self.j = j

        def json(self):
            return self.j

        def raise_for_status(self):
            pass
    t0 = 1792900800
    pages = [{"candles": [{"time": (t0 + k * 300) * 1000, "open": "100", "high": "101", "low": "99", "close": "100.5",
                           "volume": "3"} for k in range(3)], "more_candles": True},
             {"candles": [{"time": (t0 + k * 300) * 1000, "open": "100", "high": "101", "low": "99", "close": "100.5",
                           "volume": "3"} for k in range(3, 5)], "more_candles": False}]
    calls = []

    def get(url, params=None, timeout=0, **k):
        calls.append(params)
        return Resp(pages[len(calls) - 1])
    monkeypatch.setattr(B.SESSION, "get", get)
    bars = B.kraken_candles("PF_SOLUSD", t0, t0 + 3600)
    assert len(bars) == 5 and calls[1]["from"] == t0 + 3 * 300
    monkeypatch.setattr(B, "_get_json", lambda url, ttl, headers=None: {"tickers": [
        {"symbol": "PF_XBTUSD"}, {"symbol": "PF_SOLUSD"}, {"symbol": "PF_XDGUSD"}, {"symbol": "FI_XBTUSD_261225"}]})
    assert B.kraken_perps() == {"BTC": "PF_XBTUSD", "SOL": "PF_SOLUSD", "DOGE": "PF_XDGUSD"}
    monkeypatch.setitem(B.KR_SYM, "SOL", "PF_SOLUSD")
    assert "Kraken perp PF_SOLUSD" in B.label("SOL")


def test_economic_calendar_pause(monkeypatch):
    ev = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-28T08:30:00-04:00", "impact": "High",
           "forecast": "0.3%", "previous": "0.4%"},
          {"title": "German Ifo", "country": "EUR", "date": "2026-10-28T05:00:00-04:00", "impact": "Medium"},
          {"title": "bad", "country": "USD", "date": "pas une date", "impact": "High"}]
    monkeypatch.setattr(B, "_get_json", lambda url, ttl, headers=None: ev if "faireconomy" in url else None)
    t_cpi = int(B.datetime.datetime.fromisoformat("2026-10-28T08:30:00-04:00").timestamp())
    assert B.news_pause("fx", "EURUSD", t_cpi - 600) and B.news_pause("cr", "BTC", t_cpi + 900)
    assert B.news_pause("fx", "EURJPY", t_cpi) is None          # pas de dollar dans la paire
    assert B.news_pause("fx", "EURUSD", t_cpi - 3 * 3600) is None
    assert "13:30" in B.next_event_line("stocks", "SPY", t_cpi - 3600)   # 8 h 30 New York = 13 h 30 Paris
    monkeypatch.setattr(B.time, "time", lambda: t_cpi)
    cal = B.econ_calendar_today()
    assert len(cal) == 2 and cal[1].startswith("13:30 🔴 🇺🇸 CPI m/m")


def test_tp_reached_message(monkeypatch):
    t = lambda h, m: B.at(DAY, B.hm(h, m), B.NY)  # noqa: E731
    bars = [(t(10, 0) + k * 300, 100 + k * 0.5, 100.6 + k * 0.5, 99.9 + k * 0.5, 100.5 + k * 0.5, 1.0) for k in range(12)]
    sent = []
    monkeypatch.setattr(B, "telegram", lambda m, html=True: sent.append(m))
    monkeypatch.setattr(B, "hist", lambda kind, sym: bars)
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    st = B.load_state()
    st["open"] = [{"strat": "stocks in play", "kind": "stocks", "sym": "AAA", "dir": 1, "entry": 100.5, "stop": 99.0,
                   "tp": 103.0, "t_flat": t(15, 55), "day": DAY.isoformat(), "sent": t(10, 0)}]
    B.track_update(st, t(10, 35))
    assert any("prix de gain" in m for m in sent) and st["open"][0]["tp_told"]
    n = len(sent)
    B.track_update(st, t(10, 35))
    assert len(sent) == n                         # une seule fois


def test_crypto_wide_stop_is_consistent(monkeypatch):
    from test_bot_pro import synth_24h
    days = B.by_day(synth_24h(drift_today=0.0012), B.UTC, B.hm(0))
    dk = sorted(days)
    monkeypatch.setitem(B.CFG, "CR_STOP_MULT", 2.0)
    b = B.mom_eval_day(days, DAY, "cr", None, days[dk[-2]][-1][4])[0]
    rule = max(b["upper"], b["vwap"])                     # stop de l'etude pour un achat
    assert abs((b["price"] - b["stop"]) - 2 * (b["price"] - rule)) < 1e-9 and b["pad"] > 0
    j = b["k"] + 6
    s1 = B.trail_stop(days, DAY, "cr", b, j, b["stop"], 1)
    s0 = B.trail_stop(days, DAY, "cr", dict(b, pad=0.0), j, rule, 1)
    assert s1 >= b["stop"] and (abs((s0 - s1) - b["pad"]) < 1e-9 or s1 == b["stop"])


def test_forex_wide_stop(monkeypatch):
    from test_bot_pro import synth_24h
    days = B.by_day(synth_24h(drift_today=0.0012), B.UTC, B.hm(0))
    dk = sorted(days)
    monkeypatch.setitem(B.CFG, "FX_STOP_MULT", 2.0)
    sig = B.mom_eval_day(days, DAY, "fx", None, days[dk[-2]][-1][4])
    assert sig and all(abs((s["price"] - s["stop"]) * s["dir"] - 2 * abs(s["price"] - (
        max(s["upper"], s["vwap"]) if s["dir"] > 0 else min(s["lower"], s["vwap"])))) < 1e-9 for s in sig)
