from jobpipe.models import DiscoveredJob
from jobpipe.score import classify_role_family, score_job_fit_v1


def test_help_desk_analyst_does_not_outrank_junior_data_engineer() -> None:
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

    help_score, help_notes = score_job_fit_v1(help_desk)
    data_score, data_notes = score_job_fit_v1(junior_data_engineer)

    assert help_score < data_score
    assert any("role family: help_desk" in note for note in help_notes)
    assert any("role family: data_engineer" in note for note in data_notes)


def test_remote_bonus_cannot_overcome_bad_role_family() -> None:
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

    remote_support_score, remote_support_notes = score_job_fit_v1(remote_support)
    onsite_target_score, _ = score_job_fit_v1(onsite_target)

    assert remote_support_score < onsite_target_score
    assert any("remote-friendly" in note for note in remote_support_notes)
    assert any("role family: help_desk" in note for note in remote_support_notes)


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
