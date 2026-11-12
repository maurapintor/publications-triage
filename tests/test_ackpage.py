from pypdf import PdfReader

from tests.conftest import make_pdf
from triage.ackpage import extract_ack_page

FILLER = ["Body text that is long enough to be readable."]


def _pdf(tmp_path):
    f = tmp_path / "paper.pdf"
    f.write_bytes(make_pdf([FILLER, FILLER + ["Acknowledgements", "Funded by ELSA."], ["References"]]))
    return f


def test_single_page_written(tmp_path):
    out = extract_ack_page(_pdf(tmp_path), 2, tmp_path / "acks", "10.1/x")
    assert out.name == "10.1_x_p2.pdf"
    reader = PdfReader(str(out))
    assert len(reader.pages) == 1 and "Acknowledgements" in reader.pages[0].extract_text()


def test_existing_file_reused(tmp_path):
    pdf = _pdf(tmp_path)
    out = extract_ack_page(pdf, 2, tmp_path / "acks", "p")
    mtime = out.stat().st_mtime_ns
    assert extract_ack_page(pdf, 2, tmp_path / "acks", "p") == out
    assert out.stat().st_mtime_ns == mtime


def test_no_page_or_bad_page(tmp_path):
    pdf = _pdf(tmp_path)
    assert extract_ack_page(pdf, None, tmp_path / "acks", "p") is None
    assert extract_ack_page(pdf, 9, tmp_path / "acks", "p") is None
    assert not (tmp_path / "acks").exists() or not any((tmp_path / "acks").iterdir())
