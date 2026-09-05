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
from jobpipe.models import SearchProfile
from jobpipe.score import score_job_fit_breakdown, score_job_fit_v1


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


def test_backend_security_full_persisted_path_is_title_first(initialized_workspace, runner) -> None:
    job_id = insert_job(_payload("backend-security-real-path"))
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?",
            ("Partner with the security engineer team while building APIs.", job_id),
        )

    assert runner.invoke(app, ["classify", "--job-id", str(job_id), "--force"]).exit_code == 0
    persisted = get_classification(job_id)
    assert persisted is not None
    assert persisted["primary_role_family"] == "backend_api"
    assert "security" in persisted["secondary_role_families_json"]
    queried = next(dict(row) for row in list_jobs() if row["id"] == job_id)
    assert queried["primary_role_family"] == "backend_api"
    _score, notes = score_job_fit_v1(queried)
    assert "role family: backend_api" in notes
    assert "backend_api" in [item["role_family"] for item in build_analytics_report().top_role_families_by_score]


def test_add_text_rejects_workspace_readme_metadata(initialized_workspace, runner) -> None:
    Path("README.md").write_text("# jobpipe\n\nA local-first CLI project.", encoding="utf-8")
    result = runner.invoke(app, ["add-text", "--file", "README.md"])
    assert result.exit_code != 0
    assert "workspace/package metadata" in str(result.exception)


def test_score_breakdown_matches_score_and_cli(initialized_workspace, runner) -> None:
    job_id = insert_job(_payload("score-breakdown", "Backend Engineer, AI Security"))
    row = next(dict(item) for item in list_jobs() if item["id"] == job_id)
    row["secondary_role_families"] = ["security"]
    breakdown = score_job_fit_breakdown(row)
    score, _notes = score_job_fit_v1(row)
    assert breakdown.displayed_score == score
    assert sum(item.amount for item in breakdown.contributions) == breakdown.pre_cap_score
    result = runner.invoke(app, ["score-breakdown", str(job_id), "--goal", "career_acceleration"])
    assert result.exit_code == 0, result.stdout
    assert "Primary: backend_api" in result.stdout
    assert "Pre-cap score:" in result.stdout


def _goal_profile(goal: str) -> SearchProfile:
    return SearchProfile(name="test", active_goal=goal)


def _contributions(breakdown, category: str) -> list[int]:
    return [item.amount for item in breakdown.contributions if item.category == category]


def test_secondary_role_goal_modifiers_are_truncated_and_capped() -> None:
    profile = _goal_profile("career_acceleration")
    backend_security = score_job_fit_breakdown(
        {"title": "Backend Engineer, AI Security", "description": "Build APIs.", "primary_role_family": "backend_api", "secondary_role_families": ["security", "help_desk"], "canonical_seniority": "unknown"},
        profile=profile,
    )
    # int(modifier / 4) truncates toward zero: 5 / 4 -> 1, -8 / 4 -> -2.
    assert _contributions(backend_security, "primary_role_goal") == [12]
    assert _contributions(backend_security, "secondary_role_goal") == [1, -2]

    capped = score_job_fit_breakdown(
        {"title": "Backend Engineer", "primary_role_family": "backend_api", "secondary_role_families": ["data_engineering", "python_automation", "software_engineering", "full_stack", "qa_automation"], "canonical_seniority": "unknown"},
        profile=profile,
    )
    assert sum(_contributions(capped, "secondary_role_goal")) > 6
    assert _contributions(capped, "secondary_role_goal_cap") == [6 - sum(_contributions(capped, "secondary_role_goal"))]
    assert sum(_contributions(capped, "secondary_role_goal") + _contributions(capped, "secondary_role_goal_cap")) == 6

    zero_primary = score_job_fit_breakdown(
        {"title": "Generalist", "primary_role_family": "other", "secondary_role_families": ["backend_api", "data_engineering", "python_automation"], "canonical_seniority": "unknown"},
        profile=profile,
    )
    assert sum(_contributions(zero_primary, "secondary_role_goal") + _contributions(zero_primary, "secondary_role_goal_cap")) == 3


def test_canonical_seniority_is_authoritative_and_breakdown_is_structured() -> None:
    profile = _goal_profile("career_acceleration")
    unknown = score_job_fit_breakdown(
        {"title": "Backend Engineering Internship", "primary_role_family": "backend_api", "secondary_role_families": ["security"], "canonical_seniority": "unknown"},
        profile=profile,
    )
    assert not _contributions(unknown, "seniority")

    entry = score_job_fit_breakdown({"title": "Backend Engineer", "primary_role_family": "backend_api", "canonical_seniority": "entry_level"}, profile=profile)
    staff = score_job_fit_breakdown({"title": "Backend Engineer", "primary_role_family": "backend_api", "canonical_seniority": "staff"}, profile=profile)
    assert _contributions(entry, "seniority") == [6]
    assert _contributions(staff, "seniority") == [-14]
    assert {item.category for item in entry.contributions} >= {"base", "primary_role_goal", "profile_practical_fit"}
    assert entry.pre_cap_score == sum(item.amount for item in entry.contributions)
    assert entry.displayed_score == score_job_fit_v1({"title": "Backend Engineer", "primary_role_family": "backend_api", "canonical_seniority": "entry_level"}, profile=profile)[0]


def test_goal_satisfaction_uses_classification_and_respects_goal_changes(initialized_workspace, runner) -> None:
    backend_id = insert_job(_payload("goal-backend", "Backend Engineer"))
    help_id = insert_job(_payload("goal-help", "Help Desk Analyst I"))
    rows = {int(row["id"]): dict(row) for row in list_jobs()}
    career_backend = score_job_fit_breakdown(rows[backend_id], profile=_goal_profile("career_acceleration"))
    career_help = score_job_fit_breakdown(rows[help_id], profile=_goal_profile("career_acceleration"))
    fast_backend = score_job_fit_breakdown(rows[backend_id], profile=_goal_profile("get_employed_fast"))
    fast_help = score_job_fit_breakdown(rows[help_id], profile=_goal_profile("get_employed_fast"))
    assert career_backend.goal_specific_total > 0
    assert career_help.goal_specific_total < 0
    assert fast_backend.goal_specific_total > 0
    assert fast_help.goal_specific_total > 0
    assert not _contributions(career_backend, "goal")

    assert runner.invoke(app, ["goal", "set", "career_acceleration"]).exit_code == 0
    assert build_analytics_report().summary["goal_satisfying_jobs"] == 1
    assert runner.invoke(app, ["goal", "set", "get_employed_fast"]).exit_code == 0
    assert build_analytics_report().summary["goal_satisfying_jobs"] == 2
