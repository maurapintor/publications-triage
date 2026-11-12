from pathlib import Path

import pytest

from triage.config import ConfigError, load_config

VALID = """
input_path: p.json
contact_email: a@b.org
iris: {oai_url: "https://iris.example/oai"}
projects:
  - name: AURORA
  - {name: CoDesign, aliases: [Co-Design]}
  - Data4AI4Data
"""


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_valid_loads(tmp_path):
    c = load_config(write(tmp_path, VALID))
    assert [p.name for p in c.projects] == ["AURORA", "CoDesign", "Data4AI4Data"]
    assert c.projects[1].terms == ["CoDesign", "Co-Design"]
    assert c.sources == ["iris", "arxiv"] and c.cache_dir == "cache"


def test_example_config_loads(tmp_path):
    """The example only needs the user's ORCID iD (and contact email) filled in."""
    text = Path("config/triage.example.yaml").read_text().replace(
        "orcid: null ", "orcid: 0000-0002-1825-0097 ", 1)
    c = load_config(write(tmp_path, text))
    from triage.config import effective_rate_limits
    lim = effective_rate_limits(c)
    assert c.rate_limits == {} and lim["export.arxiv.org"] == 3.0 and lim["arxiv.org"] == 15.0
    assert c.paper_list.source == "orcid" and c.sources == ["iris", "arxiv"] and c.contact_email is None
    with pytest.raises(ConfigError, match="needs user.orcid"):
        load_config("config/triage.example.yaml")


@pytest.mark.parametrize("missing", ["projects"])
def test_missing_required(tmp_path, missing):
    text = "\n".join(l for l in VALID.splitlines() if not l.startswith(missing))
    if missing == "projects":
        text = VALID.split("projects:")[0]
    with pytest.raises(ConfigError, match=missing):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize("projects", ["projects: []", "projects: [{aliases: [x]}]",
                                      "projects: [{name: A, aliases: x}]", "projects: [A, a]"])
def test_malformed_projects(tmp_path, projects):
    text = VALID.split("projects:")[0] + projects
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_unknown_source_and_missing_iris(tmp_path):
    with pytest.raises(ConfigError, match="unknown sources"):
        load_config(write(tmp_path, VALID + "sources: [google_scholar]\n"))
    default_iris = VALID.replace('iris: {oai_url: "https://iris.example/oai"}', "")
    c = load_config(write(tmp_path, default_iris))
    assert c.sources == ["iris", "arxiv"] and c.iris.url == "https://iris.unica.it"  # default IRIS
    off = VALID.replace('iris: {oai_url: "https://iris.example/oai"}', "iris: {url: null}")
    assert load_config(write(tmp_path, off)).sources == ["arxiv"]  # IRIS switched off
    with pytest.raises(ConfigError, match="iris.url"):
        load_config(write(tmp_path, off + "sources: [iris, arxiv]\n"))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_dashboard_defaults(tmp_path):
    d = load_config(write(tmp_path, VALID)).dashboard
    assert d.columns == ["title", "year", "iris", "arxiv", "rank", "acks"]
    assert (d.host, d.port, d.default_filters) == ("127.0.0.1", 8000, {})


def test_dashboard_custom_and_unknown_column(tmp_path):
    c = load_config(write(tmp_path, VALID + "dashboard: {port: 9000, columns: [title, ack_AURORA, doi], "
                                            "default_filters: {iris: missing}}\n"))
    assert c.dashboard.port == 9000 and c.dashboard.columns == ["title", "ack_AURORA", "doi"]
    assert c.dashboard.default_filters == {"iris": "missing"}
    with pytest.raises(ConfigError, match=r"unknown dashboard.columns \['bogus'\].*valid"):
        load_config(write(tmp_path, VALID + "dashboard: {columns: [title, bogus]}\n"))


def test_save_roundtrip_and_backup(tmp_path):
    from triage.config import config_to_dict, save_config

    path = write(tmp_path, VALID)
    data = config_to_dict(load_config(path))
    data["projects"].append({"name": "NewProj", "aliases": ["NP"]})
    data["dashboard"]["columns"] = ["title", "ack_NewProj"]
    saved = save_config(data, path)
    assert (tmp_path / "c.yaml.bak").read_text() == VALID
    again = load_config(path)
    assert config_to_dict(again) == config_to_dict(saved) == data
    assert again.projects[-1].terms == ["NewProj", "NP"]


def test_invalid_save_leaves_file(tmp_path):
    from triage.config import config_to_dict, save_config

    path = write(tmp_path, VALID)
    data = config_to_dict(load_config(path))
    data["sources"] = ["nope"]
    with pytest.raises(ConfigError, match="unknown sources"):
        save_config(data, path)
    assert path.read_text() == VALID
    assert sorted(f.name for f in tmp_path.iterdir()) == ["c.yaml"]


def test_user_and_iris_mode(tmp_path):
    from triage.config import iris_mode
    c = load_config(write(tmp_path, VALID))
    assert c.user.orcid is None and c.iris.mode == "auto" and iris_mode(c) == "harvest"
    c = load_config(write(tmp_path, VALID + "user: {name: Ada Lovelace, orcid: 'https://orcid.org/0000-0002-1825-0097'}\n"))
    assert c.user.orcid == "0000-0002-1825-0097" and c.user.name == "Ada Lovelace" and iris_mode(c) == "orcid"
    with pytest.raises(ConfigError, match="not an ORCID"):
        load_config(write(tmp_path, VALID + "user: {orcid: 1234}\n"))
    with pytest.raises(ConfigError, match="needs user.orcid"):
        load_config(write(tmp_path, VALID.replace('iris: {oai_url: "https://iris.example/oai"}',
                                                  'iris: {oai_url: "https://iris.example/oai", mode: orcid}')))
    with pytest.raises(ConfigError, match="iris.mode"):
        load_config(write(tmp_path, VALID.replace('iris: {oai_url: "https://iris.example/oai"}',
                                                  'iris: {oai_url: "https://iris.example/oai", mode: nope}')))


def test_save_keeps_permissions(tmp_path):
    import os
    import stat

    from triage.config import config_to_dict, save_config
    path = write(tmp_path, VALID)
    os.chmod(path, 0o640)
    save_config(config_to_dict(load_config(path)), path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_paper_list_config(tmp_path):
    c = load_config(write(tmp_path, VALID))  # legacy input_path -> source file
    assert (c.paper_list.source, c.paper_list.file) == ("file", "p.json")
    no_input = "\n".join(l for l in VALID.splitlines() if not l.startswith("input_path")) + "\n"
    with pytest.raises(ConfigError, match="needs user.orcid"):
        load_config(write(tmp_path, no_input))
    c = load_config(write(tmp_path, no_input + "user: {orcid: 0000-0002-1825-0097}\n"
                                    "paper_list: {arxiv_author_id: lovelace_a_1, exclude_titles: [AISec]}\n"))
    assert (c.paper_list.source, c.paper_list.arxiv_author_id, c.paper_list.exclude_titles) == ("orcid", "lovelace_a_1", ["AISec"])
    with pytest.raises(ConfigError, match="needs paper_list.file"):
        load_config(write(tmp_path, no_input + "paper_list: {source: file}\n"))


def test_new_user_minimal_config_and_defaults(tmp_path):
    from triage.config import config_to_dict, effective_rate_limits, save_config
    text = ("user: {name: Ada Lovelace, orcid: 0000-0002-1825-0097, email: ada@uni.example}\n"
            "iris: {url: https://iris.uni.example}\nprojects: [AURORA]\n")
    c = load_config(write(tmp_path, text))
    assert c.contact_email == "ada@uni.example" and c.sources == ["iris", "arxiv"]
    assert c.iris.endpoint == "https://iris.uni.example/oai/request" and c.paper_list.source == "orcid"
    lim = effective_rate_limits(c)
    assert lim["iris.uni.example"] == 5.0 and lim["export.arxiv.org"] == 3.0 and lim["default"] == 1.0
    path = tmp_path / "c.yaml"
    full = config_to_dict(c)
    full["rate_limits"] = {"export.arxiv.org": 4.0}
    save_config(full, path)
    import yaml
    saved = yaml.safe_load(path.read_text())
    assert set(saved) == {"user", "iris", "projects", "rate_limits"}  # only what differs from the defaults
    assert saved["user"]["email"] == "ada@uni.example" and effective_rate_limits(load_config(path))["export.arxiv.org"] == 4.0


def test_legacy_contact_email_still_works(tmp_path):
    c = load_config(write(tmp_path, VALID))
    assert c.contact_email == "a@b.org" and c.iris.endpoint == "https://iris.example/oai"


def test_old_oai_url_becomes_iris_address(tmp_path):
    c = load_config(write(tmp_path, VALID.replace('iris: {oai_url: "https://iris.example/oai"}',
                                                  'iris: {oai_url: "https://iris.example/oai/request"}')))
    assert c.iris.url == "https://iris.example" and c.iris.oai_url is None
    assert c.iris.endpoint == "https://iris.example/oai/request"


def test_minimal_save_drops_default_delays(tmp_path):
    from triage.config import config_to_dict
    c = load_config(write(tmp_path, VALID + "rate_limits: {export.arxiv.org: 3.0, api.crossref.org: 1.5}\n"))
    assert config_to_dict(c, minimal=True)["rate_limits"] == {"api.crossref.org": 1.5}


def test_email_optional_unless_unpaywall(tmp_path):
    no_email = VALID.replace("contact_email: a@b.org\n", "")
    assert load_config(write(tmp_path, no_email)).contact_email is None
    with pytest.raises(ConfigError, match="Unpaywall"):
        load_config(write(tmp_path, no_email + "sources: [arxiv, unpaywall]\n"))
    with pytest.raises(ConfigError, match="email address"):
        load_config(write(tmp_path, no_email + "user: {email: nope}\n"))


def test_iris_off_survives_minimal_save(tmp_path):
    from triage.config import config_to_dict, save_config
    path = write(tmp_path, VALID.replace('iris: {oai_url: "https://iris.example/oai"}', "iris: {url: null}"))
    save_config(config_to_dict(load_config(path)), path)
    c = load_config(path)
    assert c.iris.url is None and c.sources == ["arxiv"]
