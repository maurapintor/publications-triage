"""Polite HTTP client: per-host rate limit, retries, on-disk cache, robots.txt."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

DEFAULT_OFFICIAL_HOSTS = ("export.arxiv.org", "api.openalex.org", "api.unpaywall.org", "pub.orcid.org",
                         "api.crossref.org")


class BlockedByPolicy(Exception):
    """Raised when robots.txt disallows a fetch. Never worked around."""


@dataclass
class Response:
    status_code: int
    content: bytes
    content_type: str = ""
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


class HttpClient:
    def __init__(
        self,
        contact_email: str | None,
        rate_limits: dict[str, float] | None = None,
        cache_dir: str | Path | None = None,
        official_hosts=DEFAULT_OFFICIAL_HOSTS,
        max_retries: int = 3,
        refresh: bool = False,
        sleep=time.sleep,
        clock=time.monotonic,
        session: requests.Session | None = None,
    ):
        self.user_agent = "publications-triage/0.1" + (f" (mailto:{contact_email})" if contact_email else "")
        self.rate_limits = dict(rate_limits or {})
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.official_hosts = set(official_hosts)
        self.max_retries = max_retries
        self.refresh = refresh
        self._sleep = sleep
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = self.user_agent
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._crawl_delay: dict[str, float] = {}  # robots.txt Crawl-delay per host (respected on top of rate_limits)

    # -- cache -----------------------------------------------------------
    def _cache_paths(self, url: str):
        h = hashlib.sha256(url.encode()).hexdigest()
        base = self.cache_dir / "http"
        return base / f"{h}.body", base / f"{h}.json"

    def _cache_read(self, url: str) -> Response | None:
        if not self.cache_dir or self.refresh:
            return None
        body, meta = self._cache_paths(url)
        if body.exists() and meta.exists():
            m = json.loads(meta.read_text())
            return Response(200, body.read_bytes(), m.get("content_type", ""), from_cache=True)
        return None

    def _cache_write(self, url: str, resp: Response) -> None:
        if not self.cache_dir:
            return
        body, meta = self._cache_paths(url)
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(resp.content)
        meta.write_text(json.dumps({"url": url, "content_type": resp.content_type}))

    # -- rate limit ------------------------------------------------------
    def _delay_for(self, host: str) -> float:
        configured = self.rate_limits.get(host, self.rate_limits.get("default", 1.0))
        return max(configured, self._crawl_delay.get(host) or 0.0)

    def _throttle(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self._delay_for(host) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    # -- robots ----------------------------------------------------------
    def _check_robots(self, url: str) -> None:
        p = urlparse(url)
        host = p.netloc
        if host in self.official_hosts:
            return
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._request(f"{p.scheme}://{host}/robots.txt")
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
                else:
                    self._robots[host] = None  # no usable robots.txt -> allowed
            except requests.RequestException:
                self._robots[host] = None
        rp = self._robots[host]
        if rp is not None and host not in self._crawl_delay:
            try:
                self._crawl_delay[host] = float(rp.crawl_delay(self.user_agent) or 0)
            except (TypeError, ValueError):
                self._crawl_delay[host] = 0.0
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            raise BlockedByPolicy(f"robots.txt of {host} disallows {url}")

    # -- request ---------------------------------------------------------
    def _request(self, url: str, headers: dict | None = None) -> Response:
        host = urlparse(url).netloc
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            r = self._session.get(url, timeout=60, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.max_retries:
                    return Response(r.status_code, r.content, r.headers.get("Content-Type", ""))
                retry_after = r.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2.0 ** (attempt + 1)
                self._sleep(wait)
                continue
            return Response(r.status_code, r.content, r.headers.get("Content-Type", ""))
        raise AssertionError("unreachable")

    def get(self, url: str, params: dict | None = None, use_cache: bool = True,
            headers: dict | None = None) -> Response:
        if params:
            url = requests.Request("GET", url, params=params).prepare().url
        if use_cache:
            cached = self._cache_read(url)
            if cached:
                return cached
        self._check_robots(url)
        resp = self._request(url, headers)
        if resp.status_code == 200 and use_cache:
            self._cache_write(url, resp)
        return resp
