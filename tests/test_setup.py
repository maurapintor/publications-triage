import responses

from triage.config import load_config, setup_needed
from triage.http import HttpClient
from triage.web import create_app

ORCID = "0000-0002-1825-0097"


def app_for(tmp_path):
    app = create_app(tmp_path / "config" / "triage.yaml", run_fn=lambda *a: {})
    app.config["SETUP_HTTP"] = HttpClient(None, {"default": 0}, tmp_path / "http", sleep=lambda s: None)
    return app, app.test_client()


def test_setup_needed_reasons(tmp_path):
    p = tmp_path / "c.yaml"
    assert setup_needed(p) == "no configuration yet"
    p.write_text("user: {orcid: null}\nprojects: [X]\n")
    assert "ORCID" in setup_needed(p)
    p.write_text(f"user: {{orcid: {ORCID}}}\n")
    assert "projects" in setup_needed(p)
    p.write_text(f"user: {{orcid: {ORCID}}}\nprojects: [X]\n")
    assert setup_needed(p) is None
    p.write_text("user: [broken\n")
    assert setup_needed(p) is None  # broken YAML is reported, never overwritten


@responses.activate
def test_first_start_flow(tmp_path):
    app, c = app_for(tmp_path)
    st = c.get("/api/setup").get_json()
    assert st["needed"] and st["default_iris_url"] == "https://iris.unica.it"
    r = c.get("/api/papers")
    assert r.status_code == 409 and r.get_json()["setup_needed"]

    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/person",
                  json={"name": {"given-names": {"value": "Ada"}, "family-name": {"value": "Lovelace"}}})
    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/works", json={"group": [{}, {}, {}]})
    r = c.post("/api/setup/orcid", json={"orcid": f"https://orcid.org/{ORCID}"})
    assert r.get_json() == {"orcid": ORCID, "name": "Ada Lovelace", "works": 3}
    assert c.post("/api/setup/orcid", json={"orcid": "123"}).status_code == 400

    assert c.post("/api/setup", json={"orcid": ORCID, "projects": []}).status_code == 400
    r = c.post("/api/setup", json={"name": "Ada Lovelace", "orcid": ORCID, "email": "", "iris_url": "",
                                   "projects": [{"name": "MYPROJECT", "aliases": ["My-Project"]}]})
    assert r.status_code == 200
    cfg = load_config(tmp_path / "config" / "triage.yaml")
    assert cfg.user.orcid == ORCID and cfg.iris.url is None and cfg.sources == ["arxiv"]
    assert cfg.projects[0].terms == ["MYPROJECT", "My-Project"] and cfg.contact_email is None
    assert not c.get("/api/setup").get_json()["needed"] and c.get("/api/papers").status_code == 200


@responses.activate
def test_unknown_orcid(tmp_path):
    _, c = app_for(tmp_path)
    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/person", status=404)
    assert c.post("/api/setup/orcid", json={"orcid": ORCID}).status_code == 404


def test_setup_keeps_other_settings(tmp_path):
    p = tmp_path / "config" / "triage.yaml"
    p.parent.mkdir()
    p.write_text("user: {orcid: null}\nprojects: [OLD]\nrate_limits: {export.arxiv.org: 4.0}\n")
    _, c = app_for(tmp_path)
    c.post("/api/setup", json={"orcid": ORCID, "iris_url": "https://iris.unica.it", "projects": [{"name": "NEW"}]})
    cfg = load_config(p)
    assert [x.name for x in cfg.projects] == ["NEW"] and cfg.rate_limits == {"export.arxiv.org": 4.0}
    assert cfg.iris.url == "https://iris.unica.it" and cfg.sources == ["iris", "arxiv"]


def test_serve_starts_without_config(tmp_path, capsys):
    from triage.cli import main
    started = []
    assert main(["serve", "--config", str(tmp_path / "missing.yaml")],
                app_runner=lambda app, h, p: started.append((h, p))) == 0
    assert started == [("127.0.0.1", 8000)] and "First start" in capsys.readouterr().out
