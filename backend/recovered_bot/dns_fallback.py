"""
DNS fallback for "No address associated with hostname" errors.

WHY THIS EXISTS: repeated on-device testing showed getaddrinfo failing for
fapi.binance.com/stream.binance.com even on a stable WiFi connection with
good signal - not the low-signal/mobile-data DNS flakiness seen in earlier
sessions. That pattern (fails consistently, same domain, regardless of
network type) matches ISP/router-level DNS filtering of crypto-exchange
domains, which several Indian ISPs are known to do at the DNS layer - the
domain simply never resolves via that ISP's DNS server, no matter how good
the connection otherwise is.

WHAT THIS DOES: if the OS resolver fails for one of the watched hostnames,
falls back to resolving it via Cloudflare's DNS-over-HTTPS JSON API
(https://1.1.1.1/dns-query) - a plain HTTPS GET to the IP 1.1.1.1 directly,
which bypasses the local/ISP DNS server entirely (no hostname lookup needed
to reach 1.1.1.1, it's a literal IP). Uses the `requests` library already in
this project - no new pip dependency, buildozer.spec untouched.

Successful DoH results are cached in-memory with a TTL and monkeypatched
into socket.getaddrinfo, so every library in the process (requests,
websocket-client) that tries to connect to one of these hosts transparently
gets the DoH-resolved IP if the normal resolver keeps failing.

If Cloudflare's DoH endpoint is ALSO unreachable, this fails silently and
behavior is unchanged from before (original getaddrinfo error propagates) -
this is a best-effort fallback, never a hard dependency.
"""

import socket
import time
import threading

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

DOH_URL = "https://1.1.1.1/dns-query"
CACHE_TTL_SECONDS = 60  # was 600 - if the resolved IP happens to land on a
                          # lagging/behind edge node, 10 minutes pinned to it
                          # is a long time to keep getting stale responses
                          # from; re-resolving every 60s recovers much faster

# Only intercept lookups for hosts this app actually talks to - never
# silently reroute arbitrary DNS for the whole process.
_WATCHED_HOSTS = {
    "fapi.binance.com",
    "fstream.binance.com",   # actual Futures WebSocket host (market_ws.py's
                              # WS_URL) - missing here meant the WS connection
                              # was NEVER covered by this fallback, only REST
                              # calls were, which is why the WS error kept
                              # happening even after this file was fixed.
    "stream.binance.com",
    "stream.binancefuture.com",
}

_lock = threading.Lock()
_cache = {}  # host -> (ip, expires_at)
_patched = False
_original_getaddrinfo = None


def _doh_resolve(host: str):
    """Resolve `host` via Cloudflare DoH, connecting to the literal IP
    1.1.1.1 so no local/ISP DNS lookup is needed for this step itself."""
    if not _REQUESTS_AVAILABLE:
        return None
    try:
        resp = requests.get(
            DOH_URL,
            params={"name": host, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        for answer in data.get("Answer", []):
            if answer.get("type") == 1:  # A record
                return answer.get("data")
    except Exception:
        return None
    return None


def _get_cached_ip(host: str):
    now = time.time()
    with _lock:
        entry = _cache.get(host)
        if entry and entry[1] > now:
            return entry[0]
    ip = _doh_resolve(host)
    if ip:
        with _lock:
            _cache[host] = (ip, now + CACHE_TTL_SECONDS)
    return ip


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if host in _WATCHED_HOSTS:
            ip = _get_cached_ip(host)
            if ip:
                return _original_getaddrinfo(ip, port, family, type, proto, flags)
        raise


def ensure_dns_fallback():
    """Call once at app startup, before any network code runs. Safe to call
    more than once - only patches on the first call."""
    global _patched, _original_getaddrinfo
    if _patched:
        return
    _original_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = _patched_getaddrinfo
    _patched = True
    
