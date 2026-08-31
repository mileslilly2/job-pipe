from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpipe import web as web_module
from jobpipe.analytics import build_analytics_report
from jobpipe.classification import Classification, classify_job
from jobpipe.cli import app
from jobpipe.db import (
    connect,
    fetch_all,
    get_analysis,
    get_classification,
    get_job_by_provider_external_id,
    insert_job,
    list_jobs,
    upsert_classification,
)
from jobpipe.models import DiscoveredJob
from jobpipe.score import score_job_fit_v1


def _payload(external_id: str, title: str = "Backend Engineer, AI Security") -> dict[str, object]:
    return {
        "provider": "greenhouse", "external_id": external_id, "company": "Acme",
        "title": title, "location": "Remote", "remote": True,
        "apply_url": f"https://example.com/{external_id}",
        "description": "Build API services and security tooling.",
    }


def _facts(job_id: int) -> tuple[object, ...]:
    row = get_classification(job_id)
    assert row is not None
    return tuple(row[key] for key in (
        "primary_role_family", "secondary_role_families_json", "seniority",
        "role_evidence_json", "seniority_evidence_json", "provenance",
    ))


def test_ats_ingest_persists_canonical_classification(initialized_workspace, runner, monkeypatch: pytest.MonkeyPatch) -> None:
    Path("data/job_sources.yaml").write_text(
        "- provider: greenhouse\n  company: Acme\n  board_token: acme\n", encoding="utf-8",
    )
    import jobpipe.providers as providers
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda source: [
        DiscoveredJob(
            provider="greenhouse", external_id="ats-backend", company=source.company,
            title="Backend Engineer, AI Security", location="Remote", remote=True,
            apply_url="https://example.com/ats", source_url="https://example.com/ats",
            description="Build secure API services.", posted_at="2026-08-01T00:00:00+00:00",
        )
    ])

    assert runner.invoke(app, ["ingest"]).exit_code == 0
    job = get_job_by_provider_external_id("greenhouse", "ats-backend")
    assert job is not None
    row = get_classification(int(job["id"]))
    assert row is not None
    assert row["primary_role_family"] == "backend_api"
    assert "security" in row["secondary_role_families_json"]
    assert row["seniority"] == "unknown"
    assert row["provenance"] == "deterministic"


def test_manual_classification_is_preserved_until_force(initialized_workspace, runner) -> None:
    payload = _payload("manual-preserved", "Backend Engineer")
    job_id = insert_job(payload)
    upsert_classification(
        job_id, Classification("help_desk", (), "entry_level", ("manual",), ("manual",)),
        provenance="manual",
    )

    assert insert_job(payload) == job_id
    assert _facts(job_id)[:3] == ("help_desk", "[]", "entry_level")
    assert runner.invoke(app, ["classify", "--job-id", str(job_id)]).exit_code == 0
    assert get_classification(job_id)["provenance"] == "manual"

    assert runner.invoke(app, ["classify", "--job-id", str(job_id), "--force"]).exit_code == 0
    assert _facts(job_id)[0] == "backend_api"
    assert _facts(job_id)[-1] == "deterministic"


def test_analyze_backfills_missing_classification_before_scoring(initialized_workspace, runner) -> None:
    job_id = insert_job(_payload("analyze-backfill", "Help Desk Analyst I"))
    with connect() as conn:
        conn.execute("DELETE FROM job_classifications WHERE job_id = ?", (job_id,))

    assert runner.invoke(app, ["analyze", str(job_id)]).exit_code == 0
    row, analysis = get_classification(job_id), get_analysis(job_id)
    assert row is not None and analysis is not None
    assert (row["primary_role_family"], row["seniority"]) == ("help_desk", "entry_level")
    assert (analysis["role_type"], analysis["seniority_level"]) == ("help_desk", "entry_level")


def test_persisted_primary_role_controls_scoring_over_secondary_specialization(initialized_workspace) -> None:
    job_id = insert_job(_payload("primary-over-secondary"))
    row = next(dict(item) for item in list_jobs() if item["id"] == job_id)

    score, notes = score_job_fit_v1(
        row, learned_weights={"role_families": {"backend_api": 9, "security": 40}},
    )

    assert score > 0
    assert "role family: backend_api" in notes
    assert "+9 learned adjustment: role family 'backend_api'" in notes
    assert not any("role family 'security'" in note for note in notes)


def test_analytics_aggregates_legacy_helpdesk_as_canonical_id(initialized_workspace) -> None:
    insert_job(_payload("canonical-help-desk", "Help Desk Analyst I"))
    legacy_id = insert_job(_payload("legacy-helpdesk", "Operations Specialist"))
    upsert_classification(
        legacy_id, Classification("help_desk", (), "unknown", ("legacy",), ("legacy",)),
        provenance="manual",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE job_classifications SET primary_role_family = 'helpdesk' WHERE job_id = ?",
            (legacy_id,),
        )

    families = build_analytics_report().top_role_families_by_score
    names = [item["role_family"] for item in families]
    assert names.count("help_desk") == 1
    assert "helpdesk" not in names
    assert next(item for item in families if item["role_family"] == "help_desk")["count"] == 2


def test_repeated_classification_integration_is_deterministic_and_idempotent(initialized_workspace, runner) -> None:
    payload = _payload("repeat-classification")
    job_id = insert_job(payload)
    first = _facts(job_id)
    for command in (
        ["classify", "--job-id", str(job_id)],
        ["analyze", str(job_id)],
        ["classify", "--job-id", str(job_id)],
        ["analyze", str(job_id)],
    ):
        assert runner.invoke(app, command).exit_code == 0
    assert insert_job(payload) == job_id
    assert _facts(job_id) == first
    assert len(fetch_all("SELECT job_id FROM job_classifications WHERE job_id = ?", (job_id,))) == 1


def test_web_job_detail_renders_classification_and_missing_fallback(initialized_workspace) -> None:
    job_id = insert_job(_payload("web-classification"))
    client = TestClient(web_module.create_app())
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    for text in (
        "Classification", "Primary role family", "backend_api", "Secondary role families",
        "security", "Seniority", "Role evidence", "Seniority evidence", "Provenance",
    ):
        assert text in response.text

    with connect() as conn:
        conn.execute("DELETE FROM job_classifications WHERE job_id = ?", (job_id,))
    fallback = client.get(f"/jobs/{job_id}")
    assert fallback.status_code == 200
    assert "Classification not available." in fallback.text


def test_backend_security_classification_is_deterministic() -> None:
    first = classify_job({"title": "Backend Engineer, AI Security", "description": "Secure APIs"})
    second = classify_job({"title": "Backend Engineer, AI Security", "description": "Secure APIs"})
    assert first == second
    assert first.primary_role_family == "backend_api"
    assert "security" in first.secondary_role_families
