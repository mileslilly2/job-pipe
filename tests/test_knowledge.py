from __future__ import annotations

from jobpipe.catalog import load_search_profile
from jobpipe.knowledge import build_knowledge_report, load_knowledge
from jobpipe.models import DiscoveredJob
from jobpipe.score import score_job_fit_v1


def _job(*, title: str, description: str, external_id: str) -> DiscoveredJob:
    return DiscoveredJob(
        provider="greenhouse",
        external_id=external_id,
        company="Acme",
        title=title,
        location="Remote",
        remote=True,
        apply_url="https://example.com/" + external_id,
        description=description,
        posted_at="2026-06-22T00:00:00+00:00",
    )


def test_internal_tooling_maps_to_automation_and_backend_api() -> None:
    job = _job(
        title="Internal Tools Engineer",
        description="Build internal tooling for APIs, workflow automation, and support workflows.",
        external_id="knowledge-1",
    )

    report = build_knowledge_report(job)
    concepts = {match.concept for match in report.matches}

    assert "automation" in concepts
    assert "backend_api" in concepts

    score, notes = score_job_fit_v1(job)
    assert any("automation" in note for note in notes)
    assert any("backend_api" in note for note in notes)
    assert any("knowledge score adjustment" in note for note in notes)
    assert score > 0


def test_help_desk_creates_a_conflict_penalty() -> None:
    job = _job(
        title="Help Desk Analyst",
        description="Help desk and customer support with ticket triage.",
        external_id="knowledge-2",
    )

    score, notes = score_job_fit_v1(job)

    assert any("role family: help_desk" in note for note in notes)
    assert score > 0


def test_data_pipelines_maps_to_data_engineering() -> None:
    job = _job(
        title="Data Platform Engineer",
        description="Build data pipelines, ETL jobs, and warehouse syncs.",
        external_id="knowledge-3",
    )

    report = build_knowledge_report(job, catalog=load_knowledge())

    assert any(match.concept == "data_engineering" for match in report.alias_matches)
    assert any(match.phrase == "data pipelines" for match in report.alias_matches)

    score, notes = score_job_fit_v1(job)
    assert any("data_engineering" in note for note in notes)


def test_related_matches_are_weaker_than_exact_matches() -> None:
    job = _job(
        title="Automation Engineer",
        description="automation",
        external_id="knowledge-4",
    )

    report = build_knowledge_report(job, catalog=load_knowledge())
    exact = next(match for match in report.exact_matches if match.concept == "automation")
    related = next(match for match in report.related_matches if match.concept == "backend_api")

    assert related.score < exact.score


def test_fit_breakdown_shows_location_family_and_practical_fit_labels() -> None:
    profile = load_search_profile()
    legal_job = _job(
        title="Product Counsel",
        description="Counsel for product and platform teams with backend APIs and compliance",
        external_id="knowledge-6",
    )
    legal_job.location = "Paris, France"
    legal_job.remote = False

    _, legal_notes = score_job_fit_v1(legal_job, profile=profile)
    assert any("location mismatch" in note for note in legal_notes)
    assert any("role preference: avoid legal_counsel" in note for note in legal_notes)

    infra_job = _job(
        title="Infrastructure Engineer",
        description="Distributed systems, platform infrastructure, and large scale service reliability",
        external_id="knowledge-7",
    )
    _, infra_notes = score_job_fit_v1(infra_job, profile=profile)
    assert any("practical-fit concern" in note for note in infra_notes)


def test_fit_breakdown_shows_explanations() -> None:
    job = _job(
        title="Help Desk Analyst",
        description="Help desk with internal tooling and API triage.",
        external_id="knowledge-5",
    )

    _, notes = score_job_fit_v1(job)

    assert any("exact concept match" in note or "alias match" in note for note in notes)
    assert not any("conflict penalty" in note for note in notes)
    assert any("knowledge score adjustment" in note for note in notes)



def test_goal_changes_role_ranking_and_is_deterministic() -> None:
    from jobpipe.models import SearchProfile

    backend = _job(
        title="Backend API Engineer",
        description="Build backend APIs and microservices.",
        external_id="goal-backend",
    )
    help_desk = _job(
        title="Help Desk Analyst",
        description="Help desk ticket triage and customer support.",
        external_id="goal-helpdesk",
    )
    qa = _job(
        title="QA Automation Engineer",
        description="Build QA automation with Playwright and pytest.",
        external_id="goal-qa",
    )

    acceleration = SearchProfile(name="test", active_goal="career_acceleration")
    fast = SearchProfile(name="test", active_goal="get_employed_fast")

    backend_acceleration, _ = score_job_fit_v1(backend, profile=acceleration)
    help_acceleration, _ = score_job_fit_v1(help_desk, profile=acceleration)
    backend_fast, _ = score_job_fit_v1(backend, profile=fast)
    help_fast, help_fast_notes = score_job_fit_v1(help_desk, profile=fast)
    qa_fast, _ = score_job_fit_v1(qa, profile=fast)
    qa_acceleration, _ = score_job_fit_v1(qa, profile=acceleration)

    assert backend_acceleration > help_acceleration
    assert help_fast > backend_fast
    assert qa_fast != qa_acceleration
    assert score_job_fit_v1(help_desk, profile=fast) == (help_fast, help_fast_notes)
    assert any("goal: Get Employed Fast" in note for note in help_fast_notes)
    assert any("Help Desk supports rapid employment" in note for note in help_fast_notes)


def test_acceptable_role_is_neutral_and_not_a_conflict() -> None:
    from jobpipe.models import SearchProfile

    job = _job(
        title="Help Desk Analyst",
        description="Help desk ticket triage.",
        external_id="goal-acceptable",
    )
    profile = SearchProfile(name="test", active_goal="get_employed_fast", acceptable=["help_desk"])

    report = build_knowledge_report(job, profile=profile)
    _, notes = score_job_fit_v1(job, profile=profile)

    assert report.goal_modifier == 10
    assert not report.conflict_penalties
    assert any("acceptable help_desk (+0)" in note for note in notes)
