"""Test « maillage » : une journee complete simulee hors ligne, toutes les briques branchees ensemble
(briefing, pre-ouverture, actions du jour, declenchements, momentum actions / forex / crypto,
derniere demi-heure, suivi, resume), dans les deux formats de message. Aucun appel reseau."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot_pro as B  # noqa: E402
from test_bot_pro import DAY, synth_24h, synth_stock, html_ok  # noqa: E402

BANNERS = ("🪙 <b>CRYPTO</b>", "🥇 <b>MÉTAUX · Kraken perp</b>", "💱 <b>FOREX</b>", "🥇 <b>FOREX · MÉTAUX</b>", "📈 <b>ACTION US</b>",
           "📈 <b>ETF US</b>", "📈 <b>ACTIONS US</b>")


def _fixtures(url):
    if "faireconomy" in url:
        return [{"title": "CPI m/m", "country": "USD", "date": "2026-10-21T08:30:00-04:00", "impact": "High"}]
    if "cftc" in url:
        return [{"cftc_contract_market_code": "099741", "report_date_as_yyyy_mm_dd": "2026-10-13T00:00:00.000",
                 "open_interest_all": "1000", "noncomm_positions_long_all": "300", "noncomm_positions_short_all": "100"}]
    if "alternative.me" in url:
        return {"data": [{"value": "30", "value_classification": "Fear"}]}
    if "futures.kraken.com" in url:
        return {"tickers": [{"symbol": "PF_XBTUSD", "relativeFundingRate": 2e-5, "markPrice": 1e5, "openInterest": 100}]}
    return None


@pytest.mark.parametrize("simple", [1, 0])
def test_full_day_mesh(monkeypatch, simple):
    stock, fx = synth_stock(), synth_24h(drift_today=0.0012)
    daily = [(1790000000 + i * 86400, 100.0, 101.0, 99.0, 100.0 + 0.1 * i, 1e6) for i in range(70)]

    def chart(sym, rng, interval, prepost=False, meta=False):
        bars = daily if interval == "1d" else fx if ("=" in sym or "-USD" in sym) else stock
        return (bars, {"chartPreviousClose": bars[-80][4]}) if meta else bars
    sent = []
    monkeypatch.setattr(B, "chart", chart)
    monkeypatch.setattr(B, "_get_json", lambda url, ttl, headers=None: _fixtures(url))
    monkeypatch.setattr(B, "telegram", lambda m, html=True: sent.append(m))
    monkeypatch.setattr(B, "news", lambda s, n=2: ["Titre <b> & cie"])
    monkeypatch.setattr(B, "earnings_soon", lambda s, d: False)
    monkeypatch.setattr(B, "insider_buys", lambda s, days=30: {"n": 2, "usd": 150000.0})
    monkeypatch.setattr(B, "econ_today", lambda: [])
    monkeypatch.setattr(B.time, "sleep", lambda x: None)
    for k, v in (("SIMPLE", simple), ("MIN_SCORE", 0), ("NEED_BT", 0), ("CAPITAL", 2000.0), ("SIP", ["AAA", "BBB"]),
                 ("MOM_STOCKS", ["SPY", "NVDA"]), ("MOM_FX", ["EURUSD", "XAUUSD"]), ("MOM_CRYPTO", ["BTC", "ETH"]),
                 ("LH_STOCKS", ["SPY", "QQQ"]), ("LH_MIN", 0.0)):
        monkeypatch.setitem(B.CFG, k, v)
    st = B.load_state()
    ny = lambda h, m: B.at(DAY, B.hm(h, m), B.NY)  # noqa: E731
    utc = lambda h, m: B.at(DAY, B.hm(h, m), B.UTC)  # noqa: E731

    sent.append(B.briefing())
    B.premarket_gaps(st, ny(9, 20))
    B.sip_select(st, ny(9, 37))
    B.sip_triggers(st, ny(10, 0))
    for h in range(6, 13):
        for m in (0, 30):
            for kind in ("cr", "fx"):
                B.mom_scan(st, kind, utc(h, m) + 60)
    for h in range(10, 16):
        for m in (0, 30):
            B.mom_scan(st, "stocks", ny(h, m) + 60)
    t_dec, t_close = B.lh_times(DAY)
    for k in range(0, 9):
        B.LHT[0] = 0
        B.lh_scan(st, t_dec + 60 + 60 * k)
    B.track_update(st, t_close + 3600)
    st["done"].pop("recap", None)
    B.day_recap(st)
    B.save_state(st)

    assert len(sent) >= 5
    bad = [m for m in sent if not html_ok(m)]
    assert not bad, bad[:1]
    trade_words = ("ACHETER", "VENDRE", "terminé", "déclenché", "ACHAT", "VENTE", "RÉSULTAT", "DÉCLENCHÉ")
    for m in sent:
        if any(w in m for w in trade_words) and "BRIEFING" not in m and "RÉSUMÉ" not in m:
            assert m.startswith(BANNERS), m[:80]
    assert any("BRIEFING" in m for m in sent) and any("RÉSUMÉ" in m for m in sent)
    if simple:
        assert not any(" VWAP" in m or "PF " in m for m in sent)
    saved = open(os.path.join(B.CFG["STATE_DIR"], B.STATE_FILE)).read()
    assert "2000" not in saved and "CAPITAL" not in saved          # rien sur le capital dans l'etat public
