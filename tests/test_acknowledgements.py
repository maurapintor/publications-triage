from tests.conftest import make_pdf
from triage.acknowledgements import analyze_pdf, analyze_text

BODY = "Introduction. We study robust learning in depth. " * 5


def test_case_insensitive_ack(projects):
    t = BODY + "\nAcknowledgments\nThis work was supported by the DATA4AI4DATA project.\nReferences\n[1] x"
    assert analyze_text(t, projects).matches == {"Data4AI4Data": "ack"}


def test_acronym_not_inside_longer_word(projects):
    t = BODY + "\nAcknowledgements\nPublished by Auroral Press. Thanks to reviewers.\n"
    assert analyze_text(t, projects).matches == {}


def test_same_first_name_is_body(projects):
    t = "Aurora Smith, John Doe\n" + BODY + "\nAcknowledgements\nWe thank the reviewers.\nReferences\n"
    assert analyze_text(t, projects).matches == {"AURORA": "body"}


def test_hyphenated_across_lines(projects):
    t = BODY + "\nFunding\nSupported by Data4AI4-\nData and by the AURORA\nproject.\nReferences\n"
    r = analyze_text(t, projects)
    assert r.matches == {"Data4AI4Data": "ack", "AURORA": "ack"}
    assert sorted(r.acknowledged) == ["AURORA", "Data4AI4Data"]


def test_related_work_is_body_and_alias(projects):
    t = "Related work: CoDesign was studied earlier. " + BODY + "\nAcknowledgements\nThanks.\nReferences\n"
    assert analyze_text(t, projects).matches == {"CoDesign": "body"}
    t2 = BODY + "\nAcknowledgements\nPart of the Co-Design project.\n"
    assert analyze_text(t2, projects).matches == {"CoDesign": "ack"}


def test_footnote_sentence(projects):
    t = BODY + " This work has been partially supported by AURORA, a research network. " + BODY
    assert analyze_text(t, projects).matches == {"AURORA": "ack"}


def test_mention_after_section_end_is_body(projects):
    t = BODY + "\nAcknowledgements\nThanks.\nReferences\n[1] AURORA report\n"
    assert analyze_text(t, projects).matches == {"AURORA": "body"}


def test_unreadable_text(projects):
    assert analyze_text("  \n ", projects).status == "unreadable"


def test_real_pdfs(tmp_path, projects):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf([["Robust learning paper body text that is long enough to count as text.", "Acknowledgements",
                             "This work was funded by AURORA and CoDesign.", "References"]]))
    assert analyze_pdf(f, projects).matches == {"AURORA": "ack", "CoDesign": "ack"}
    g = tmp_path / "b.pdf"
    g.write_bytes(make_pdf([[]]))
    assert analyze_pdf(g, projects).status == "unreadable"
    h = tmp_path / "c.pdf"
    h.write_bytes(b"not a pdf")
    assert analyze_pdf(h, projects).status == "unreadable"


FILLER = ["Robust learning paper body text that is long enough to count as readable text."] * 3


def test_ack_page_heading_on_page_3(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER, FILLER, FILLER + ["Acknowledgements", "Funded by AURORA and Data4AI4Data."],
                            ["References", "[1] something"]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"AURORA": "ack", "Data4AI4Data": "ack"}
    assert r.ack_page == 3 and r.pages == {"AURORA": 3, "Data4AI4Data": 3}
    assert r.regions[0].kind == "heading" and r.regions[0].page == 3 and "AURORA" in r.regions[0].snippet


def test_footnote_on_page_1(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER + ["This work was partially supported by AURORA."], FILLER]))
    r = analyze_pdf(f, projects)
    assert r.ack_page == 1 and any(x.kind == "footnote" and x.page == 1 for x in r.regions)


def test_section_running_into_next_page_reports_start(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER, FILLER + ["Acknowledgements", "We thank our colleagues."],
                            ["and the CoDesign project for support.", "References"]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"CoDesign": "ack"} and r.ack_page == 2


def test_no_ack_page_without_regions(tmp_path, projects):
    f = tmp_path / "p.pdf"
    f.write_bytes(make_pdf([FILLER + ["AURORA is mentioned here only."]]))
    r = analyze_pdf(f, projects)
    assert r.matches == {"AURORA": "body"} and r.ack_page is None


def test_funding_sentence_inside_section_not_duplicated(projects):
    t = BODY + "\nAcknowledgements\nThis work was supported by AURORA.\nReferences\n"
    r = analyze_text(t, projects)
    assert [g.kind for g in r.regions] == ["heading"] and r.matches == {"AURORA": "ack"}
