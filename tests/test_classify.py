from pathlib import Path

from jobpipe.classify import classify_job, detect_seniority
from jobpipe.models import RolePack
from jobpipe.parse import parse_job_text
from jobpipe.utils import load_yaml


def _packs() -> dict[str, RolePack]:
    base = Path("jobpipe/role_packs")
    return {path.stem: RolePack.model_validate(load_yaml(path)) for path in base.glob("*.yaml")}


def test_helpdesk_classifies_even_when_senior() -> None:
    job = parse_job_text(Path("tests/fixtures/helpdesk_senior.txt").read_text(encoding="utf-8"))
    role_type, matched, missing = classify_job(job, _packs())
    assert role_type == "helpdesk"
    assert matched
    assert missing == [] or isinstance(missing, list)

    seniority, flags = detect_seniority(job)
    assert seniority in {"senior", "manager"}
    assert flags


def test_clean_helpdesk_entry_level() -> None:
    job = parse_job_text(Path("tests/fixtures/helpdesk_clean.txt").read_text(encoding="utf-8"))
    role_type, _, _ = classify_job(job, _packs())
    seniority, _ = detect_seniority(job)
    assert role_type == "helpdesk"
    assert seniority == "entry_level"
