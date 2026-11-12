import pytest
import responses

from triage.cli import main
from triage.config import load_config
from triage.http import BlockedByPolicy, HttpClient
from triage.rankings_download import download, effective_rankings, save_list

CFG = """
input_path: p.json
contact_email: t@e.org
cache_dir: {d}/cache
sources: [arxiv]
projects: [ELSA]
rankings: {{core_source: ICORE2023, sjr_year: 2024}}
"""
CORE_CSV = "".join(f'"{i}","Conference {i}","C{i}","ICORE2023","A","Yes","4603","No","4"\n' for i in range(60))
SJR_CSV = "Rank;Sourceid;Title;Issn;SJR Best Quartile;Categories\n" + "".join(
    f'{i};{i};Journal {i};"0000000{i % 10}";Q1;"Software (Q1)"\n' for i in range(60))
ROBOTS_OK = "User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: *\nDisallow: /core-tools/media/\n"


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG.format(d=tmp_path))
    return load_config(tmp_path / "c.yaml"), tmp_path


def http(tmp_path):
    return HttpClient("t@e.org", {"default": 0}, tmp_path / "http", official_hosts=(), sleep=lambda s: None)


@responses.activate
def test_core_download_and_sjr_import_used_by_default(cfg, tmp_path):
    config, tmp = cfg
    responses.add(responses.GET, "https://portal.core.edu.au/robots.txt", body=ROBOTS_OK)
    responses.add(responses.GET, "https://portal.core.edu.au/conf-ranks/", body=CORE_CSV, match=[
        responses.matchers.query_param_matcher({"search": "", "by": "all", "source": "ICORE2023", "sort": "arank",
                                                "page": "1", "do": "Export"})])
    h = http(tmp)
    assert effective_rankings(config).core_csv is None
    r1 = download(config, h, "core")
    assert r1["rows"] == 60 and r1["path"].endswith("cache/rankings/core.csv")
    ua = responses.calls[-1].request.headers["User-Agent"]
    assert ua.startswith("publications-triage") and "Claude" not in ua
    r2 = save_list(config, "sjr", SJR_CSV.encode())
    assert r2["rows"] == 60 and r2["path"].endswith("cache/rankings/sjr.csv")
    eff = effective_rankings(config)
    assert eff.core_csv == r1["path"] and eff.sjr_csv == r2["path"]


@responses.activate
def test_sjr_never_fetched(cfg):
    config, tmp = cfg
    with pytest.raises(PermissionError, match="Disallow /\\*out=xls"):
        download(config, http(tmp), "sjr")
    assert len(responses.calls) == 0


@responses.activate
def test_robots_disallow_is_respected(cfg):
    config, tmp = cfg
    responses.add(responses.GET, "https://portal.core.edu.au/robots.txt", body="User-agent: *\nDisallow: /\n")
    with pytest.raises(BlockedByPolicy):
        download(config, http(tmp), "core")
    assert len(responses.calls) == 1  # only robots.txt was fetched


def test_real_scimago_rules_block_csv(cfg):
    """The tail of the real SCImago robots.txt (2026-11-12): CSV downloads disallowed for every agent."""
    import urllib.robotparser
    rules = ("User-agent: *\nDisallow: /*area=*\nDisallow: /*out=xls\nAllow: /journalrank.php?area=\n"
             "Disallow: /journalrank.php?area=*&\n")
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(rules.splitlines())
    assert not rp.can_fetch("publications-triage/0.1", "https://www.scimagojr.com/journalrank.php?area=1700&type=j&out=xls")


@responses.activate
def test_html_page_rejected_and_file_kept(cfg):
    config, tmp = cfg
    target = tmp / "cache" / "rankings" / "core.csv"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    responses.add(responses.GET, "https://portal.core.edu.au/robots.txt", body=ROBOTS_OK)
    responses.add(responses.GET, "https://portal.core.edu.au/conf-ranks/",
                  body="<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>")
    with pytest.raises(RuntimeError, match="web page"):
        download(config, http(tmp), "core")
    assert target.read_text() == "old"


@responses.activate
def test_cli_and_api(cfg, capsys):
    import io
    config, tmp = cfg
    responses.add(responses.GET, "https://portal.core.edu.au/robots.txt", body=ROBOTS_OK)
    responses.add(responses.GET, "https://portal.core.edu.au/conf-ranks/", body=CORE_CSV)
    assert main(["rankings", "--config", str(tmp / "c.yaml"), "--core"], http=http(tmp)) == 0
    assert "core: 60 rows" in capsys.readouterr().out
    (tmp / "sjr_dl.csv").write_text(SJR_CSV)
    assert main(["rankings", "--config", str(tmp / "c.yaml"), "--sjr-file", str(tmp / "sjr_dl.csv")]) == 0
    assert "sjr: imported 60 rows" in capsys.readouterr().out
    (tmp / "bad.csv").write_text("<html>nope</html>")
    assert main(["rankings", "--config", str(tmp / "c.yaml"), "--sjr-file", str(tmp / "bad.csv")]) == 1

    from triage.web import create_app
    app = create_app(tmp / "c.yaml", http_factory=lambda c: http(tmp))
    client = app.test_client()
    r = client.post("/api/rankings/download", json={"which": "core"})
    assert r.status_code == 200 and r.get_json()["rows"] == 60
    assert client.post("/api/rankings/download", json={"which": "x"}).status_code == 400
    r = client.post("/api/rankings/download", json={"which": "sjr"})
    assert r.status_code == 403 and "upload" in r.get_json()["error"]
    r = client.post("/api/rankings/upload", data={"which": "sjr", "file": (io.BytesIO(SJR_CSV.encode()), "s.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["rows"] == 60
    r = client.post("/api/rankings/upload", data={"which": "sjr", "file": (io.BytesIO(b"<html></html>"), "x.html")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "right file" in r.get_json()["error"]
    papers = client.get("/api/papers").get_json()
    assert papers["rankings"]["conferences"] == 60 and papers["rankings"]["journals"] == 60
    assert papers["ranking_pages"]["sjr"].startswith("https://www.scimagojr.com/journalrank.php?area=1700")
