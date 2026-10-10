import threading
import time

from curl_cffi import requests

BLOCKED_STATUSES = {403, 429, 435, 503}


class BlockedError(Exception):
    """The site's bot protection refused us."""


class Browser:
    """A Chrome-impersonating session with retries, one per site, safe to share across threads."""

    def __init__(self, warmup_url: str | None = None, retries: int = 3):
        self.warmup_url = warmup_url
        self.retries = retries
        self._lock = threading.Lock()
        self._session: requests.Session | None = None

    def _fresh(self) -> requests.Session:
        session = requests.Session(impersonate="chrome")
        if self.warmup_url:
            try:
                session.get(self.warmup_url, timeout=20)
            except Exception:
                pass
        return session

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 25)
        with self._lock:
            last: requests.Response | None = None
            for attempt in range(self.retries):
                if self._session is None or attempt == self.retries - 1:
                    self._session = self._fresh()
                response = self._session.request(method, url, **kwargs)
                if response.status_code not in BLOCKED_STATUSES:
                    return response
                last = response
                time.sleep(1.5 * (attempt + 1))
            raise BlockedError(f"{url.split('?')[0]} returned {last.status_code if last else '?'}")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)
