from tests.conftest import make_pdf
from triage.acknowledgements import analyze_pdf, analyze_text

BODY = "Introduction. We study robust learning in depth. " * 5


def test_case_insensitive_ack(projects):
    t = BODY + "\nAcknowledgments\nThis work was supported by the SEC4AI4SEC project.\nReferences\n[1] x"
    assert analyze_text(t, projects).matches == {"Sec4AI4Sec": "ack"}


def test_elsa_not_in_elsevier(projects):
    t = BODY + "\nAcknowledgements\nPublished by Elsevier. Thanks to reviewers.\n"
    assert analyze_text(t, projects).matches == {}


def test_first_name_elsa_is_body(projects):
    t = "Elsa Smith, John Doe\n" + BODY + "\nAcknowledgements\nWe thank the reviewers.\nReferences\n"
    assert analyze_text(t, projects).matches == {"ELSA": "body"}


def test_hyphenated_across_lines(projects):
    t = BODY + "\nFunding\nSupported by Sec4AI4-\nSec and by the ELSA\nproject.\nReferences\n"
    r = analyze_text(t, projects)
    assert r.matches == {"Sec4AI4Sec": "ack", "ELSA": "ack"}
    assert sorted(r.acknowledged) == ["ELSA", "Sec4AI4Sec"]


def test_related_work_is_body_and_alias(projects):
    t = "Related work: CoEvolution was studied earlier. " + BODY + "\nAcknowledgements\nThanks.\nReferences\n"
    assert analyze_text(t, projects).matches == {"CoEvolution": "body"}
    t2 = BODY + "\nAcknowledgements\nPart of the Co-Evolution project.\n"
    assert analyze_text(t2, projects).matches == {"CoEvolution": "ack"}


def test_footnote_sentence(projects):
    t = BODY + " This work has been partially supported by ELSA, the European Lighthouse. " + BODY
    assert analyze_text(t, projects).matches == {"ELSA": "ack"}


def test_mention_after_section_end_is_body(projects):
    t = BODY + "\nAcknowledgements\nThanks.\nReferences\n[1] ELSA report\n"
    assert analyze_text(t, projects).matches == {"ELSA": "body"}


def test_unreadable_text(projects):
    assert analyze_text("  \n ", projects).status == "unreadable"


def test_real_pdfs(tmp_path, projects):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf([["Robust learning paper body text that is long enough to count as text.", "Acknowledgements",
                             "This work was funded by ELSA and CoEvolution.", "References"]]))
    assert analyze_pdf(f, projects).matches == {"ELSA": "ack", "CoEvolution": "ack"}
    g = tmp_path / "b.pdf"
    g.write_bytes(make_pdf([[]]))
    assert analyze_pdf(g, projects).status == "unreadable"
    h = tmp_path / "c.pdf"
    h.write_bytes(b"not a pdf")
    assert analyze_pdf(h, projects).status == "unreadable"


FILLER = ["Robust learning paper body text that is long enough to count as readable text."] * 3


def test_ack_page_heading_on_page_3(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER, FILLER, FILLER + ["Acknowledgements", "Funded by ELSA and Sec4AI4Sec."],
                            ["References", "[1] something"]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"ELSA": "ack", "Sec4AI4Sec": "ack"}
    assert r.ack_page == 3 and r.pages == {"ELSA": 3, "Sec4AI4Sec": 3}
    assert r.regions[0].kind == "heading" and r.regions[0].page == 3 and "ELSA" in r.regions[0].snippet


def test_footnote_on_page_1(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER + ["This work was partially supported by ELSA."], FILLER]))
    r = analyze_pdf(f, projects)
    assert r.ack_page == 1 and any(x.kind == "footnote" and x.page == 1 for x in r.regions)


def test_section_running_into_next_page_reports_start(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER, FILLER + ["Acknowledgements", "We thank our colleagues."],
                            ["and the CoEvolution project for support.", "References"]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"CoEvolution": "ack"} and r.ack_page == 2


def test_no_ack_page_without_regions(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER + ["ELSA is mentioned here only."]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"ELSA": "body"} and r.ack_page is None


def test_funding_sentence_inside_section_not_duplicated(projects):
    t = BODY + "\nAcknowledgements\nThis work was supported by ELSA.\nReferences\n"
    r = analyze_text(t, projects)
    assert [g.kind for g in r.regions] == ["heading"] and r.matches == {"ELSA": "ack"}
