"""Shared requests.Session with automatic retry/backoff for every REST call
in the bot. Fixes the raw NewConnectionError/HTTPSConnectionPool/Max retries
exceeded crashes seen in the Dashboard log - those were plain requests.get()
calls with no retry, so any single DNS blip or dropped packet surfaced
straight to the user instead of being quietly retried.

Use get_session() everywhere instead of `import requests; requests.get(...)`.
Same requests.Session API (session.get(...), session.post(...)), so callers
barely change.
"""
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:
    # Older urllib3 bundled with some python-for-android builds exposes this
    # under a slightly different path - fall back rather than crash on import.
    from requests.packages.urllib3.util.retry import Retry

_session = None


def get_session():
    """Returns a shared, retry-hardened requests.Session. Safe to call from
    multiple threads (app.py's poll loop, paper_trader's monitor loop,
    telegram.py) - requests.Session is thread-safe for this usage pattern
    (no per-thread state is mutated after setup)."""
    global _session
    if _session is not None:
        return _session

    retry = Retry(
        total=3,                 # retry up to 3 times per request, not per poll cycle
        backoff_factor=0.5,      # 0.5s, 1s, 2s between retries - short enough to not stall the poll loop
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,   # let our own except blocks handle the final failure
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)

    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    _session = s
    return _session


def classify_error(exc):
    """Turn a requests exception (or an HTTP response) into one of a small
    set of clear categories, instead of letting a raw exception string like
    'Max retries exceeded... No address associated with hostname' get shown
    or logged as if it were a Binance 404/invalid-symbol/rate-limit issue.

    Returns one of: 'DNS', 'TIMEOUT', 'CONNECTION', 'HTTP_429', 'HTTP_4XX',
    'HTTP_5XX', 'UNKNOWN'. Pass either the caught exception, or a
    requests.Response (checked for status_code) if the request itself
    succeeded but returned an error status."""
    resp = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or (resp.status_code if resp is not None else None)
    if status_code is not None:
        if status_code == 429:
            return "HTTP_429"
        if 500 <= status_code < 600:
            return "HTTP_5XX"
        if 400 <= status_code < 500:
            return "HTTP_4XX"
    msg = str(exc)
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in msg.lower():
        return "TIMEOUT"
    if ("No address associated with hostname" in msg or "NewConnectionError" in msg
            or "Name or service not known" in msg or "nodename nor servname" in msg
            or "getaddrinfo failed" in msg):
        return "DNS"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "CONNECTION"
    return "UNKNOWN"


def error_label(exc):
    """Short human-readable label for a classified error, for status.py /
    UI display (e.g. Dashboard's last_error, Market Watch sync line)."""
    cat = classify_error(exc)
    return {
        "DNS": "No internet / DNS lookup failed",
        "TIMEOUT": "Request timed out",
        "CONNECTION": "Connection failed",
        "HTTP_429": "Binance rate limit (429)",
        "HTTP_4XX": f"Binance rejected the request ({exc})",
        "HTTP_5XX": "Binance server error",
        "UNKNOWN": str(exc),
    }.get(cat, str(exc))


def get_used_weight(response):
    """Extract Binance's request-weight-used-in-the-last-minute header, if
    present, as an int. Returns None if the header is missing (e.g. on a
    connection that never reached Binance)."""
    try:
        val = response.headers.get("x-mbx-used-weight-1m")
        return int(val) if val is not None else None
    except Exception:
        return None
