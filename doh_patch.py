"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — DNS OVER HTTPS (DoH) PATCH        ║
║     doh_patch.py — Résolution DNS universelle 100% fiable        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import socket
import requests

_orig_getaddrinfo = socket.getaddrinfo

def resolve_doh(domain: str) -> list:
    """Résout un nom de domaine via Cloudflare DoH (1.1.1.1)."""
    try:
        url = f"https://1.1.1.1/dns-query?name={domain}&type=A"
        headers = {"accept": "application/dns-json"}
        r = requests.get(url, headers=headers, timeout=3).json()
        answers = r.get("Answer", [])
        return [a["data"] for a in answers if a.get("type") == 1]
    except Exception:
        return []

def custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        ips = resolve_doh(host)
        if ips:
            return _orig_getaddrinfo(ips[0], port, family, type, proto, flags)
        raise

def apply_doh_patch():
    """Applique le patch DNS globalement pour toutes les requêtes socket/requests."""
    socket.getaddrinfo = custom_getaddrinfo
