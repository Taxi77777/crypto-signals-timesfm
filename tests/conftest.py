"""Isolation des tests : aucun fichier d'etat ecrit dans le repo, caches remis a zero, pas de reseau."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot_pro as B  # noqa: E402


@pytest.fixture(autouse=True)
def _isolation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(B.CFG, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(B, "_get_json", lambda url, ttl, headers=None: None)
    monkeypatch.setattr(B, "usd_per", lambda ccy: {"USD": 1.0, "EUR": 1.10, "JPY": 1 / 150.0}.get(ccy.upper()))
    B.HIST.clear()
    B._OFF.clear()
    B.OBB["cache"].clear()
    B.FMT[0] = 2
    B.REGIME.update(label="inconnu", spy=0, vix=None, ts=0)
    B.STOP[0] = False
    yield
    B.HIST.clear()
    B._OFF.clear()
