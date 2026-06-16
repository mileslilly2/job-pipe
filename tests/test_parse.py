from pathlib import Path

from jobpipe.parse import parse_job_text


def test_parse_extracts_core_fields() -> None:
    text = Path("tests/fixtures/helpdesk_clean.txt").read_text(encoding="utf-8")
    parsed = parse_job_text(text)
    assert parsed.title == "Help Desk Analyst I"
    assert parsed.company == "Northstar Health Systems"
    assert parsed.location == "Remote - United States"
    assert parsed.remote_type == "remote"
    assert "$24" in parsed.salary_text
