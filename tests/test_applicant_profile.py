"""Synthetic applicant persistence, import, and direct local review tests."""
from __future__ import annotations

from contextlib import closing
import json

import pytest
import yaml
from pydantic import ValidationError

from jobpipe import applicant_profile as ap, db, services
from jobpipe.applicant_models import FactProposal, SECTIONS
from jobpipe.cli import app


@pytest.fixture()
def applicant_workspace(workspace):
    (workspace / "data/profiles").mkdir(parents=True)
    (workspace / "data/profiles/candidate.yaml").write_text(yaml.safe_dump({
        "profile": {"name": "Synthetic Applicant", "email": "synthetic@example.invalid", "summary": "Synthetic verified summary"},
        "skills": {"programming": ["SyntheticPython"]},
        "projects": [{"name": "Synthetic project", "bullets": ["Built a synthetic fixture"], "technologies": ["SyntheticPython"]}],
        "experience": [], "education": [], "constraints": {},
    }))
    (workspace / "data/search_profiles.yaml").write_text(yaml.safe_dump([{"name": "miles", "preferred_locations": ["United States"], "allowed_locations": ["Canada"], "preferred_work_styles": ["remote", "hybrid"]}]))
    db.init_database()
    return workspace


@pytest.fixture()
def canonical_applicant(applicant_workspace):
    ap.accept_import(ap.import_preview())
    return applicant_workspace


def attest(key, value):
    return ap.correct_fact(key, value, ap.state()["revision"])


def synthetic_job(**overrides):
    return db.insert_job({"provider": "manual", "title": "Junior Software Engineer", "company": "Synthetic Employer",
                          "location": "Remote - United States", "description": "Python API development.", "remote": True,
                          "apply_url": "https://example.invalid/synthetic", **overrides}, review_status="saved")


def test_preview_and_cutover_preserve_sources(applicant_workspace, runner):
    before = (applicant_workspace / "data/profiles/candidate.yaml").read_bytes()
    preview = ap.import_preview()
    assert not ap.state()["active"]
    assert preview["facts"] and all(f["verification_status"] == "imported_unverified" for f in preview["facts"])
    assert ap.state()["revision"] == 0
    assert runner.invoke(app, ["applicant", "import"]).exit_code == 0
    assert not ap.state()["active"]
    imported = runner.invoke(app, ["applicant", "import", "--accept"], input="y\n")
    assert imported.exit_code == 0, imported.output
    result = ap.get_applicant_profile(list(SECTIONS))
    assert result["revision"] == 1 and result["storage_mode"] == "canonical"
    assert (applicant_workspace / "data/profiles/candidate.yaml").read_bytes() == before
    assert not ap.accept_import(ap.import_preview())["changed"]
    assert ap.state()["revision"] == 1


def test_additive_idempotent_migration_and_legacy_database(applicant_workspace):
    job = synthetic_job()
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE application_package_provenance")
        conn.execute("DROP TABLE applicant_facts")
        conn.execute("DROP TABLE applicant_fact_revisions")
        conn.execute("DROP TABLE applicant_profiles")
    assert not ap.state()["active"]
    db.init_database()
    db.init_database()
    assert db.get_job(job)["status"] == "saved"
    assert ap.state()["revision"] == 0


@pytest.mark.parametrize("key,value", [("residence.country", "US"), ("education.enrolled", False),
    ("qualifications.professional_experience_years", 1.5), ("education.graduation_year", 2028),
    ("preferences.allowed_employment_types", ["full_time"]), ("residence.state", None)])
def test_typed_values_and_explicit_unknown(canonical_applicant, key, value):
    attest(key, value)
    facts = {f["key"]: f for f in ap.get_applicant_profile(list(SECTIONS))["facts"]}
    assert facts[key]["value"] == value
    assert type(facts[key]["value"]) is type(value)
    assert facts[key]["verification_status"] == ("unknown" if value is None else "verified")


@pytest.mark.parametrize("key,value", [("contact.password", "secret"), ("education.enrolled", "yes"),
    ("qualifications.professional_experience_years", True), ("qualifications.professional_experience_years", -1),
    ("qualifications.professional_experience_years", float("inf")), ("employment.one.start_date", "2026-02-30"),
    ("employment.one.employment_type", "project"), ("residence.country", "usa"), ("skills.one.experience_basis", ["expert"]),
    ("languages.one.proficiency", "somewhat")])
def test_reject_unsupported_types_and_values(key, value):
    with pytest.raises((ValueError, ValidationError)):
        FactProposal(key=key, value=value)


def test_proposals_cannot_verify_or_replace_and_rejection_keeps_history(canonical_applicant):
    attest("residence.state", "NY")
    before = ap.state()["revision"]
    proposal = ap.propose_applicant_facts([{"key": "residence.state", "value": "CA", "explanation": "Agent extracted a possible update", "source_reference": "synthetic.md"}], before)
    profile = ap.get_applicant_profile(["residence"])
    assert profile["facts"][0]["value"] == "NY"
    assert profile["proposals"][0]["verification_status"] != "verified"
    assert profile["conflicting_keys"] == ["residence.state"]
    assert "residence.state" not in ap.verified_values(profile)
    assert not ap.propose_applicant_facts([{"key": "residence.state", "value": "CA"}], proposal["revision"])["changed"]
    ap.review_facts([], proposal["proposal_ids"], proposal["revision"])
    assert ap.verified_values()["residence.state"]["value"] == "NY"
    assert db.fetch_one("SELECT review_state FROM applicant_fact_revisions WHERE id=?", (proposal["proposal_ids"][0],))[0] == "rejected"
    ap.propose_applicant_facts([{"key": "residence.state", "value": "CA"}], ap.state()["revision"])
    attest("residence.state", "NY")
    assert not ap.get_applicant_profile(["residence"])["conflicting_keys"]


def test_confirmation_and_correction_provenance(canonical_applicant):
    result = ap.propose_applicant_facts([{"key": "residence.state", "value": "NY", "source_reference": "synthetic.md", "explanation": "A proposed value"}], ap.state()["revision"])
    ap.review_facts(result["proposal_ids"], [], result["revision"])
    fact = ap.verified_values()["residence.state"]
    assert fact["source_type"] == "applicant_attestation" and fact["confirmed_at"]
    assert fact["source_reference"] == "synthetic.md"
    attest("residence.state", "CA")
    assert len(db.fetch_all("SELECT * FROM applicant_fact_revisions WHERE fact_key='residence.state'")) == 3
    before = ap.state()["revision"]
    assert not attest("residence.state", "CA")["changed"]
    assert before == ap.state()["revision"]


def test_conflicting_batch_rollback_and_stale_update(canonical_applicant):
    result = ap.propose_applicant_facts([{"key": "residence.state", "value": "NY"}], ap.state()["revision"])
    before = db.fetch_all("SELECT * FROM applicant_fact_revisions")
    with pytest.raises(services.ServiceError):
        ap.review_facts([*result["proposal_ids"], 999999], [], result["revision"])
    assert [tuple(r) for r in before] == [tuple(r) for r in db.fetch_all("SELECT * FROM applicant_fact_revisions")]
    assert ap.state()["revision"] == result["revision"]
    with pytest.raises(services.ServiceError) as error:
        ap.correct_fact("residence.state", "TX", result["revision"] - 1)
    assert error.value.code == "profile_conflict"


def test_import_retains_existing_attestation_and_reimport_cannot_replace(applicant_workspace):
    path = applicant_workspace / "data/profiles/candidate.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["eligibility"] = {"residence_country": {"value": "US", "verified": True, "evidence": "Synthetic previous applicant confirmation"}}
    path.write_text(yaml.safe_dump(raw))
    ap.accept_import(ap.import_preview())
    assert ap.verified_values()["residence.country"]["source_type"] == "legacy_attestation"
    raw["eligibility"]["residence_country"]["value"] = "CA"
    path.write_text(yaml.safe_dump(raw))
    ap.accept_import(ap.import_preview())
    profile = ap.get_applicant_profile(["residence"])
    assert profile["facts"][0]["value"] == "US"
    assert profile["conflicting_keys"] == ["residence.country"]


def test_import_changed_preview_and_unsafe_file_rejected(applicant_workspace):
    preview = ap.import_preview()
    path = applicant_workspace / "data/profiles/candidate.yaml"
    path.write_text(path.read_text() + "\n# Changed after preview\n")
    with pytest.raises(services.ServiceError):
        ap.accept_import(preview)
    assert ap.state()["revision"] == 0
    target = applicant_workspace / "outside.yaml"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError):
        ap.import_preview()


def test_markdown_import_is_unverified_and_templates_are_not_applicant_facts(applicant_workspace):
    (applicant_workspace / "profile.md").write_text("# Profile\n## Summary\nSynthetic researcher.\n## Skills\n- SyntheticSQL\n## Projects\n### Synthetic compiler\n- Built a synthetic parser.\n## Education\n- Synthetic Institute coursework\n")
    preview = ap.import_preview()
    assert any(f["value"] == "SyntheticSQL" for f in preview["facts"])
    assert all(f["verification_status"] == "imported_unverified" for f in preview["facts"])


def test_cli_review_and_correction_are_local_and_batchable(canonical_applicant, runner):
    proposal = ap.propose_applicant_facts([{"key": "residence.state", "value": "NY"}, {"key": "residence.country", "value": "US"}], ap.state()["revision"])
    ids = ",".join(map(str, proposal["proposal_ids"]))
    result = runner.invoke(app, ["applicant", "review", "--confirm", ids, "--reject", ""], input="y\n")
    assert result.exit_code == 0, result.output
    assert ap.verified_values()["residence.state"]["value"] == "NY"
    assert ap.verified_values()["residence.country"]["value"] == "US"
    result = runner.invoke(app, ["applicant", "edit", "residence.state", "--value", '"CA"'], input="n\n")
    assert result.exit_code == 0 and ap.verified_values()["residence.state"]["value"] == "NY"
    result = runner.invoke(app, ["applicant", "edit", "residence.state", "--value", '"CA"'], input="y\n")
    assert result.exit_code == 0 and ap.verified_values()["residence.state"]["value"] == "CA"


def test_section_privacy(canonical_applicant):
    result = ap.get_applicant_profile(["skills"])
    assert all(f["key"].startswith("skills.") for f in result["facts"])
    assert "synthetic@example.invalid" not in json.dumps(result)
    assert not result["preferences"]


def test_profile_transaction_rolls_back_a_mid_batch_storage_failure(canonical_applicant, monkeypatch):
    before = ap.state()["revision"]
    original = ap._append
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Synthetic interrupted update")
        return original(*args, **kwargs)
    monkeypatch.setattr(ap, "_append", fail_second)
    with pytest.raises(RuntimeError):
        ap.propose_applicant_facts([{"key": "residence.country", "value": "US"}, {"key": "residence.state", "value": "NY"}], before)
    assert ap.state()["revision"] == before
    assert not ap.get_applicant_profile(["residence"])["proposals"]


def test_import_distinguishes_roles_at_one_employer(applicant_workspace):
    path = applicant_workspace / "data/profiles/candidate.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["experience"] = [{"organization": "Synthetic Employer", "title": title, "dates": dates} for title, dates in [("Junior Engineer", "2020-2022"), ("Engineer", "2022-2024")]]
    path.write_text(yaml.safe_dump(raw))
    ap.accept_import(ap.import_preview())
    facts = ap.get_applicant_profile(["employment"])["facts"]
    assert len([f for f in facts if f["key"].endswith(".title")]) == 2
    assert not ap.get_applicant_profile(["employment"])["conflicting_keys"]
