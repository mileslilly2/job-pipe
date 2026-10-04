from jobpipe.false_negative import provisional_label, sample_rejected_jobs, summarize_audit, write_audit_artifacts
from jobpipe.ingest import DiscoveryDiagnostics, FilterDecision, SourceDiscoveryReport
from jobpipe.models import DiscoveredJob, JobSource


def _job(source, title, external):
    return DiscoveredJob(provider="greenhouse", external_id=external, company=source.company, title=title, location="Remote", remote=True, apply_url=f"https://example.com/{external}", description="Python API automation")


def _report(source, jobs):
    report = SourceDiscoveryReport(source=source, status="success", fetched_count=len(jobs), rejected_count=len(jobs))
    for job, decision in jobs:
        report.rejected_jobs.append((job, decision))
        report.reason_counts.update(decision.reason_codes)
        report.rejected_role_family_counts[decision.role_family] += 1
    return report


def test_false_negative_sampling_is_deterministic_and_stratified(tmp_path):
    a = JobSource(provider="greenhouse", company="Alpha")
    b = JobSource(provider="greenhouse", company="Beta")
    irrelevant = FilterDecision(False, ("role_family_not_targeted",), "sales", "unknown")
    target = FilterDecision(False, ("seniority_excluded", "keyword_excluded"), "backend_api", "junior")
    diagnostics = DiscoveryDiagnostics([_report(a, [(_job(a, "Account Executive", "a1"), irrelevant), (_job(a, "Junior Backend Engineer", "a2"), target)]), _report(b, [(_job(b, "Account Executive", "b1"), irrelevant)])])
    first = sample_rejected_jobs(diagnostics, limit=3)
    second = sample_rejected_jobs(diagnostics, limit=3)
    assert first == second
    assert {record["source"] for record in first} == {"greenhouse:alpha", "greenhouse:beta"}
    assert first[1]["rejection_reasons"] == ["seniority_excluded", "keyword_excluded"] or first[2]["rejection_reasons"] == ["seniority_excluded", "keyword_excluded"]
    assert provisional_label(target, title="Junior Backend Engineer") == "plausible_false_negative"
    assert provisional_label(irrelevant) == "clearly_correct_rejection"


def test_false_negative_audit_summary_and_artifacts(tmp_path):
    records = [{"company": "Acme", "title": "Junior Backend Engineer", "source": "greenhouse:acme", "role_family": "backend_api", "seniority": "junior", "rejection_reasons": ["seniority_excluded"], "matched_positive_keywords": ["python"], "matched_negative_keywords": [], "missing_required_signals": [], "url": "https://example.com/a", "description_excerpt": "Build APIs", "provisional_label": "plausible_false_negative", "human_label": ""}, {"company": "Acme", "title": "Counsel", "source": "greenhouse:acme", "role_family": "legal_counsel", "seniority": "unknown", "rejection_reasons": ["role_family_not_targeted"], "matched_positive_keywords": [], "matched_negative_keywords": [], "missing_required_signals": [], "url": "", "description_excerpt": "", "provisional_label": "clearly_correct_rejection", "human_label": ""}]
    summary = summarize_audit(records)
    assert summary["sample_size"] == 2
    assert summary["plausible_false_negatives"] == 1
    assert summary["provisional_false_negative_rate"] == 0.5
    json_path, md_path = write_audit_artifacts(records, tmp_path)
    assert json_path.exists() and md_path.exists()
    assert "human_label" in json_path.read_text()
    assert "Junior Backend Engineer" in md_path.read_text()
