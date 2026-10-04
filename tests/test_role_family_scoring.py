from jobpipe.catalog import load_search_profile
from jobpipe.models import DiscoveredJob, SearchProfile
from jobpipe.score import classify_role_family, score_job_fit_v1


def test_career_acceleration_ranks_data_engineering_over_help_desk() -> None:
    help_desk = DiscoveredJob(
        provider="lever",
        external_id="help-desk-1",
        company="Support Co",
        title="Help Desk Analyst",
        location="Remote",
        remote=True,
        apply_url="https://example.com/help-desk-1",
        description="Customer support tickets, basic troubleshooting, python sql automation",
        posted_at="2026-06-22T00:00:00+00:00",
    )
    junior_data_engineer = DiscoveredJob(
        provider="greenhouse",
        external_id="data-eng-1",
        company="Data Co",
        title="Junior Data Engineer",
        location="Onsite",
        remote=False,
        apply_url="https://example.com/data-eng-1",
        description="Python SQL ETL backend api",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    profile = SearchProfile(name="test", active_goal="career_acceleration")
    help_score, help_notes = score_job_fit_v1(help_desk, profile=profile)
    data_score, data_notes = score_job_fit_v1(junior_data_engineer, profile=profile)

    assert any("Help Desk works against career acceleration" in note for note in help_notes)
    assert any("Backend Api supports career acceleration" in note for note in data_notes)
    assert help_score > 0 and data_score > 0
    assert any("role family: help_desk" in note for note in help_notes)
    assert any("role family: data_engineer" in note for note in data_notes)


def test_career_acceleration_outweighs_remote_bonus_for_help_desk() -> None:
    remote_support = DiscoveredJob(
        provider="lever",
        external_id="help-desk-2",
        company="Support Co",
        title="Help Desk Analyst",
        location="Remote",
        remote=True,
        apply_url="https://example.com/help-desk-2",
        description="Python SQL ETL backend api automation",
        posted_at="2026-06-22T00:00:00+00:00",
    )
    onsite_target = DiscoveredJob(
        provider="greenhouse",
        external_id="data-eng-2",
        company="Data Co",
        title="Junior Data Engineer",
        location="Boston, MA",
        remote=False,
        apply_url="https://example.com/data-eng-2",
        description="Python SQL ETL",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    profile = SearchProfile(name="test", active_goal="career_acceleration")
    remote_support_score, remote_support_notes = score_job_fit_v1(remote_support, profile=profile)
    onsite_target_score, _ = score_job_fit_v1(onsite_target, profile=profile)

    assert any("Help Desk works against career acceleration" in note for note in remote_support_notes)
    assert remote_support_score > 0 and onsite_target_score > 0
    assert any("remote-friendly" in note for note in remote_support_notes)
    assert any("role family: help_desk" in note for note in remote_support_notes)


def test_non_target_professional_families_score_low() -> None:
    product_counsel = DiscoveredJob(
        provider="lever",
        external_id="legal-1",
        company="Legal Co",
        title="Product Counsel",
        location="Paris, France",
        remote=False,
        apply_url="https://example.com/legal-1",
        description="Counsel for product and platform teams with backend APIs and compliance",
        posted_at="2026-06-22T00:00:00+00:00",
    )
    data_products = DiscoveredJob(
        provider="lever",
        external_id="legal-2",
        company="Data Co",
        title="Data Products",
        location="London, UK",
        remote=False,
        apply_url="https://example.com/legal-2",
        description="Lead data products strategy with analytics, reporting, and stakeholder management",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    product_counsel_score, product_counsel_notes = score_job_fit_v1(product_counsel)
    data_products_score, data_products_notes = score_job_fit_v1(data_products)

    assert product_counsel_score < 40
    assert data_products_score < 45
    assert any("legal_counsel" in note for note in product_counsel_notes)
    assert any("data_products" in note for note in data_products_notes)


def test_paris_ai_research_engineer_is_demoted_not_perfect() -> None:
    paris_research = DiscoveredJob(
        provider="greenhouse",
        external_id="research-1",
        company="Research Co",
        title="Paris AI Research Engineer",
        location="Paris, France",
        remote=False,
        apply_url="https://example.com/research-1",
        description="Machine learning research, research scientist work, distributed systems, and model evaluation",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    score, notes = score_job_fit_v1(paris_research)

    assert score < 100
    assert score <= 60
    assert any("ml_research" in note or "senior_research" in note for note in notes)


def test_remote_us_backend_api_stays_high() -> None:
    remote_backend = DiscoveredJob(
        provider="greenhouse",
        external_id="backend-1",
        company="Backend Co",
        title="Backend/API Engineer",
        location="Remote - United States",
        remote=True,
        apply_url="https://example.com/backend-1",
        description="Python API microservices backend SQL ETL automation",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    score, notes = score_job_fit_v1(remote_backend)

    assert score >= 70
    assert any("backend_api" in note for note in notes)
    assert any("remote" in note for note in notes)


def test_canada_only_backend_api_is_demoted_without_canada_allowed() -> None:
    profile = load_search_profile()
    restricted_profile = profile.model_copy(update={"allowed_locations": []})
    canada_backend = DiscoveredJob(
        provider="greenhouse",
        external_id="backend-2",
        company="Backend Co",
        title="Backend/API Engineer",
        location="Toronto, Canada",
        remote=False,
        apply_url="https://example.com/backend-2",
        description="Python API microservices backend SQL automation",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    allowed_score, _ = score_job_fit_v1(canada_backend, profile=profile)
    restricted_score, restricted_notes = score_job_fit_v1(canada_backend, profile=restricted_profile)

    assert restricted_score < allowed_score
    assert any("Canada-only" in note or "location mismatch" in note for note in restricted_notes)


def test_technical_operations_needs_programming_signals() -> None:
    non_programming_ops = DiscoveredJob(
        provider="lever",
        external_id="ops-1",
        company="Ops Co",
        title="Technical Operations Specialist",
        location="Remote",
        remote=True,
        apply_url="https://example.com/ops-1",
        description="Coordinate vendor calls, reporting, and internal workflows",
        posted_at="2026-06-22T00:00:00+00:00",
    )
    programming_ops = DiscoveredJob(
        provider="lever",
        external_id="ops-2",
        company="Ops Co",
        title="Technical Operations Engineer",
        location="Remote",
        remote=True,
        apply_url="https://example.com/ops-2",
        description="Python automation, CLI tools, SQL pipelines, and testing",
        posted_at="2026-06-22T00:00:00+00:00",
    )

    non_programming_score, non_programming_notes = score_job_fit_v1(non_programming_ops)
    programming_score, programming_notes = score_job_fit_v1(programming_ops)

    family, matched, reason = classify_role_family(non_programming_ops)
    assert family == "unknown"
    assert matched == []
    assert reason == "technical operations signals ignored: not programming-heavy"
    assert programming_score > non_programming_score
    assert any("role family: technical_operations" in note for note in programming_notes)
