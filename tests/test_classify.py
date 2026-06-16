from pathlib import Path

from jobpipe.classify import classify_job
from jobpipe.models import RolePack
from jobpipe.parse import parse_job_text
from jobpipe.utils import load_yaml


def _packs() -> dict[str, RolePack]:
    base = Path("jobpipe/role_packs")
    return {path.stem: RolePack.model_validate(load_yaml(path)) for path in base.glob("*.yaml")}


def test_classify_helpdesk() -> None:
    job = parse_job_text(Path("tests/fixtures/helpdesk_clean.txt").read_text(encoding="utf-8"))
    role_type, seniority, matched, missing = classify_job(job, _packs())
    assert role_type == "helpdesk"
    assert seniority == "entry_level"
    assert matched
    assert "troubleshooting" not in missing


def test_classify_scam_customer_support() -> None:
    job = parse_job_text(Path("tests/fixtures/chat_support_scam.txt").read_text(encoding="utf-8"))
    role_type, _seniority, _matched, _missing = classify_job(job, _packs())
    assert role_type == "customer_support"
