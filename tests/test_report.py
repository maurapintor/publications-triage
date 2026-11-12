import csv
from datetime import date

from triage.acknowledgements import AckResult
from triage.papers import Paper
from triage.pipeline import PaperRecord
from triage.report import write_csv
from triage.sources.base import SourceResult

GOLDEN = """paper_id,title,year,doi,venue,iris_status,iris_url,arxiv_status,arxiv_url,iris_vs_arxiv,pdf_source,ack_status,ack_projects_found,ack_ELSA,ack_Sec4AI4Sec,ack_CoEvolution,notes
10.1/a,"Title, with comma",2024,10.1/a,Venue,present,https://hdl/1,missing,,,iris,ok,ELSA,Y,N,body,
t-1,Ünïcode,2023,,,error,,uncertain,https://arxiv.org/abs/1,,,no_pdf,,,,,iris: error (boom) | arxiv: uncertain match
"""


def test_golden_csv(tmp_path, projects):
    r1 = PaperRecord(Paper("10.1/a", "Title, with comma", year="2024", doi="10.1/a", venue="Venue"),
                     {"iris": SourceResult("iris", "present", url="https://hdl/1"), "arxiv": SourceResult("arxiv", "missing")},
                     pdf_source="iris", ack=AckResult("ok", {"ELSA": "ack", "CoEvolution": "body"}))
    r2 = PaperRecord(Paper("t-1", "Ünïcode", year="2023"),
                     {"iris": SourceResult("iris", "error"), "arxiv": SourceResult("arxiv", "uncertain", url="https://arxiv.org/abs/1")},
                     notes=["iris: error (boom)", "arxiv: uncertain match"])
    path = write_csv([r1, r2], ["iris", "arxiv"], projects, tmp_path, today=date(2026, 1, 2))
    assert path.name == "triage_2026-01-02.csv"
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    assert raw.decode("utf-8-sig").replace("\r\n", "\n") == GOLDEN
    assert len(list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))) == 2


def test_json_roundtrip(tmp_path, projects):
    from triage.acknowledgements import AckRegion
    from triage.report import load_json, record_to_dict, write_json

    ack = AckResult("ok", {"ELSA": "ack", "CoEvolution": "body"}, [AckRegion(3, "heading", "Funded by ELSA")], {"ELSA": 3})
    r1 = PaperRecord(Paper("10.1/a", "Title </script> Ü", authors=["A, B"], year="2024", doi="10.1/a"),
                     {"arxiv": SourceResult("arxiv", "present", url="https://arxiv.org/abs/1", score=0.98)},
                     pdf_path=tmp_path / "a.pdf", pdf_source="arxiv", ack=ack, ack_page_pdf=tmp_path / "a_p3.pdf")
    r2 = PaperRecord(Paper("t-1", "No pdf"), {}, notes=["x"])
    path = write_json([r1, r2], ["iris", "arxiv"], projects, tmp_path, today=date(2026, 1, 2), generated_at="T")
    assert path.name == "triage_2026-01-02.json"
    data = load_json(path)
    assert data == load_json(tmp_path / "latest.json")
    assert data["records"] == [record_to_dict(r1, ["iris", "arxiv"]), record_to_dict(r2, ["iris", "arxiv"])]
    rec = data["records"][0]
    assert rec["ack"]["page"] == 3 and rec["ack"]["acknowledged"] == ["ELSA"]
    assert rec["sources"]["iris"]["status"] == "not_checked" and rec["sources"]["arxiv"]["score"] == 0.98
    assert data["records"][1]["ack"] is None and data["projects"] == ["ELSA", "Sec4AI4Sec", "CoEvolution"]
