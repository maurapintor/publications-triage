import pytest
import responses

from triage.http import BlockedByPolicy, HttpClient


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def client(tmp_path, clock, **kw):
    return HttpClient("t@example.org", {"default": 2.0}, tmp_path, official_hosts=("api.example",),
                      sleep=clock.sleep, clock=clock.now, **kw)


@responses.activate
def test_cache_hit(tmp_path):
    responses.add(responses.GET, "https://api.example/x", body="hello")
    c = client(tmp_path, Clock())
    assert not c.get("https://api.example/x").from_cache
    assert c.get("https://api.example/x").from_cache
    assert len(responses.calls) == 1
    # a fresh client (new process) also hits the on-disk cache
    assert client(tmp_path, Clock()).get("https://api.example/x").text == "hello"
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_spacing(tmp_path):
    responses.add(responses.GET, "https://api.example/a", body="a")
    responses.add(responses.GET, "https://api.example/b", body="b")
    clk = Clock()
    c = client(tmp_path, clk)
    c.get("https://api.example/a")
    c.get("https://api.example/b")
    assert clk.sleeps == [2.0]


@responses.activate
def test_retry_after_429(tmp_path):
    responses.add(responses.GET, "https://api.example/x", status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, "https://api.example/x", body="ok")
    clk = Clock()
    assert client(tmp_path, clk).get("https://api.example/x").text == "ok"
    assert 7.0 in clk.sleeps


@responses.activate
def test_gives_up_after_retries(tmp_path):
    responses.add(responses.GET, "https://api.example/x", status=503)
    r = client(tmp_path, Clock(), max_retries=2).get("https://api.example/x")
    assert r.status_code == 503 and len(responses.calls) == 3


@responses.activate
def test_robots_disallow(tmp_path):
    responses.add(responses.GET, "https://other.example/robots.txt", body="User-agent: *\nDisallow: /private\n")
    responses.add(responses.GET, "https://other.example/private/f.pdf", body="x")
    responses.add(responses.GET, "https://other.example/public/f.pdf", body="y")
    c = client(tmp_path, Clock())
    with pytest.raises(BlockedByPolicy):
        c.get("https://other.example/private/f.pdf")
    assert c.get("https://other.example/public/f.pdf").text == "y"


@responses.activate
def test_official_host_skips_robots(tmp_path):
    responses.add(responses.GET, "https://api.example/x", body="ok")
    client(tmp_path, Clock()).get("https://api.example/x")
    assert [c.request.url for c in responses.calls] == ["https://api.example/x"]


@responses.activate
def test_robots_crawl_delay_respected(tmp_path):
    waits = []
    clock = [0.0]
    responses.add(responses.GET, "https://slow.example/robots.txt", body="User-agent: *\nCrawl-delay: 7\n")
    responses.add(responses.GET, "https://slow.example/a", body="x")
    responses.add(responses.GET, "https://slow.example/b", body="y")
    h = HttpClient("t@e.org", {"default": 1.0}, tmp_path, sleep=lambda s: waits.append(s), clock=lambda: clock[0])
    h.get("https://slow.example/a", use_cache=False)
    h.get("https://slow.example/b", use_cache=False)
    assert waits and max(waits) == pytest.approx(7.0)
