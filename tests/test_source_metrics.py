from collections import Counter
from pathlib import Path
import sqlite3

import pytest
import yaml

from jobpipe.application_package import application_package_paths
from jobpipe.catalog import load_search_profile, source_identity_label
from jobpipe.cli import app
from jobpipe.db import SCHEMA, connect, get_job, init_database, insert_feedback, insert_job, set_review_status, set_status
from jobpipe.ingest import SourceDiscoveryReport, collect_discovery_diagnostics, filter_discovered_jobs
from jobpipe.models import DiscoveredJob, JobSource
from jobpipe.source_metrics import build_source_metrics, completed_runs, finish_ingest_run, format_rate, pack_metrics, rate, record_source_run, run_stats, start_ingest_run


def source(token="acme", company="Acme", provider="greenhouse"):
    return JobSource(provider=provider, company=company, company_slug="display-slug", board_token=token if provider == "greenhouse" else None, account=token if provider == "lever" else None)


def posting(identifier="1", provider="greenhouse", company="Acme", title="Junior Data Engineer"):
    return DiscoveredJob(provider=provider, external_id=identifier, company=company, title=title, description="Python SQL ETL automation backend API", location="Remote", apply_url=f"https://example.com/{identifier}")


def configure(*sources):
    Path("data").mkdir(exist_ok=True)
    Path("data/job_sources.yaml").write_text(yaml.safe_dump([s.model_dump() for s in sources]))


def database_snapshot():
    with sqlite3.connect("file:jobpipe.db?mode=ro", uri=True) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in tables}


def file_snapshot():
    return {str(p): p.read_bytes() for p in Path(".").rglob("*") if p.is_file()}


def test_source_identity_uses_existing_provider_token_and_normalizes_case():
    assert source_identity_label(source(" AcMe ")) == "greenhouse:acme"
    assert source_identity_label(source("mistral", "Mistral AI", "lever")) == "lever:mistral"
    assert source_identity_label(JobSource(provider="Greenhouse", company_slug=" AcMe ")) == "greenhouse:acme"


def test_rates():
    assert rate(8, 40) == 0.2
    assert format_rate(rate(8, 40)) == "20.00%"
    assert format_rate(rate(0, 600)) == "0.00%"
    assert rate(0, 0) is None
    assert format_rate(rate(0, 0)) == "N/A"


def test_ingest_persists_independent_source_runs_and_preserves_workflow(initialized_workspace, runner, monkeypatch):
    import jobpipe.providers as providers
    acme, beta = source(), source("beta", "Beta", "lever")
    failed = source("unsupported", "Broken", "unsupported")
    configure(acme, beta, failed)
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda _: [posting(), posting("2", title="Senior Data Engineer")])
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "lever", lambda _: [])
    first = runner.invoke(app, ["ingest", "--diagnostics"])
    assert first.exit_code == 0, first.stdout
    assert "greenhouse:acme" in first.stdout
    job_id = get_job_id()
    set_review_status(job_id, "saved")
    set_status(job_id, "applied")
    insert_feedback(job_id, "saved")
    with connect() as conn:
        feedback_before = conn.execute("SELECT * FROM feedback").fetchall()
        application_before = dict(conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone())
    assert runner.invoke(app, ["ingest"]).exit_code == 0
    report = build_source_metrics([acme, beta, failed])
    runs = completed_runs(report)
    assert len(runs) == 2 and runs[0]["id"] > runs[1]["id"]
    latest, earlier = (run_stats(report, r["id"]) for r in runs)
    assert latest["greenhouse:acme"]["fetched"] == 2
    assert latest["greenhouse:acme"]["passed"] == 1
    assert latest["greenhouse:acme"]["rejected"] == 1
    assert latest["greenhouse:acme"]["inserted"] == 0
    assert latest["greenhouse:acme"]["updated"] == 1
    assert earlier["greenhouse:acme"]["inserted"] == 1
    assert latest["lever:beta"]["fetched"] == 0
    assert latest["unsupported:display-slug"]["fetch_status"] == "failed"
    assert "Unsupported provider" in latest["unsupported:display-slug"]["error"]
    assert get_job(job_id)["status"] == "saved"
    assert get_job(job_id)["source_id"] == "greenhouse:acme"
    with connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM feedback")] == [tuple(r) for r in feedback_before]
        assert dict(conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()) == application_before
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert runs[0]["profile"] == "miles"
    assert '"preferred"' in runs[0]["profile_json"]
    history = runner.invoke(app, ["sources", "yield", "--history"])
    assert history.exit_code == 0, history.stdout
    assert "Completed ingest history" in history.stdout and "N/A" in history.stdout


def get_job_id():
    with connect() as conn:
        return conn.execute("SELECT id FROM jobs ORDER BY id LIMIT 1").fetchone()[0]


def test_latest_run_skips_aborted_and_running_runs(initialized_workspace, runner):
    profile = load_search_profile()
    acme = source()
    old = start_ingest_run(profile)
    record_source_run(old, SourceDiscoveryReport(acme, "success", fetched_count=40, passed_count=8, rejected_count=32))
    finish_ingest_run(old)
    latest = start_ingest_run(profile)
    record_source_run(latest, SourceDiscoveryReport(acme, "success"))
    finish_ingest_run(latest)
    aborted = start_ingest_run(profile)
    finish_ingest_run(aborted, "interrupted")
    start_ingest_run(profile)
    report = build_source_metrics([acme])
    assert [r["id"] for r in completed_runs(report)] == [latest, old]
    configure(acme)
    result = runner.invoke(app, ["sources", "yield"])
    assert result.exit_code == 0, result.stdout
    assert f"Ingest run {latest}" in result.stdout
    assert "N/A" in result.stdout and "20.00%" not in result.stdout
    history = runner.invoke(app, ["sources", "yield", "--history"])
    assert "20.00%" in history.stdout
    assert "Excluded 2 running/aborted run(s)" in history.stdout


def test_ingest_abort_is_recorded(initialized_workspace, runner, monkeypatch):
    import jobpipe.cli as cli
    import jobpipe.providers as providers
    configure(source())
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda _: [posting()])
    def fail(*args, **kwargs):
        raise RuntimeError("database insertion failed")
    monkeypatch.setattr(cli, "insert_job", fail)
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code != 0
    report = build_source_metrics([source()])
    assert report.runs[0]["status"] == "aborted"
    assert "database insertion failed" in report.runs[0]["error"]
    assert report.stats[0]["passed"] == 1 and report.stats[0]["inserted"] == 0


def test_corpus_attribution_and_concentration(initialized_workspace):
    acme, other_board = source(), source("acme-other")
    beta = source("beta", "Beta", "lever")
    for i, (src, review, application) in enumerate([(acme, "new", "saved"), (acme, "saved", "saved"), (acme, "saved", "applied"), (acme, "skipped", "saved"), (other_board, "saved", "saved"), (beta, "saved", "rejected")]):
        job_id = insert_job({**posting(str(i), src.provider, src.company).model_dump(), "source_id": source_identity_label(src)}, review_status=review)
        set_status(job_id, application)
        if i == 4:
            paths = application_package_paths(dict(get_job(job_id)))
            paths.directory.mkdir(parents=True)
            paths.job_md.write_text("Current package")
    report = build_source_metrics([acme, other_board, beta])
    assert report.corpus["greenhouse:acme"] == Counter(total=4, active=3, saved=1, applied=1)
    assert report.corpus["greenhouse:acme-other"] == Counter(total=1, active=1, packaged=1)
    assert report.corpus["lever:beta"] == Counter(total=1)
    assert report.active_count == 4
    assert report.employers == Counter(Acme=4)
    assert report.active_sources == Counter({"greenhouse:acme": 3, "greenhouse:acme-other": 1})
    assert rate(report.corpus["greenhouse:acme"]["saved"], report.corpus["greenhouse:acme"]["total"]) == 0.25


@pytest.mark.parametrize("review,application,has_package,expected", [
    ("new", "saved", False, "new"), ("saved", "saved", False, "saved"),
    ("saved", "saved", True, "packaged"), ("saved", "applied", True, "applied"),
    ("skipped", "saved", True, "skipped"), ("saved", "rejected", True, "rejected"),
    ("saved", "analyzed", True, "assessment"), ("saved", "interview", True, "interview"),
    ("saved", "offer", True, "offer"), ("saved", "archived", False, "archived"),
])
def test_source_workflow_matches_dashboard(review, application, has_package, expected):
    from jobpipe.workflow import job_bucket
    from jobpipe.web import _job_bucket
    job = dict(review_status=review, status=application, has_package=has_package)
    assert job_bucket(job) == _job_bucket(job) == expected


def test_legacy_attribution_is_unique_or_explicitly_unknown(initialized_workspace):
    acme = source()
    job_id = insert_job(posting().model_dump(), review_status="new")
    report = build_source_metrics([acme])
    assert report.corpus["greenhouse:acme"]["active"] == 1
    assert report.inferred_jobs == 1
    assert get_job(job_id)["source_id"] is None
    ambiguous = build_source_metrics([acme, source("different")])
    assert ambiguous.unattributed_active == 1
    assert not ambiguous.active_sources
    assert ambiguous.employers == Counter(Acme=1)
    # Recorded metadata still resolves a source removed from current config.
    run_id = start_ingest_run(load_search_profile())
    record_source_run(run_id, SourceDiscoveryReport(acme, "success"))
    finish_ingest_run(run_id)
    assert build_source_metrics([]).corpus["greenhouse:acme"]["active"] == 1


def test_first_source_attribution_survives_updates(initialized_workspace):
    payload = posting().model_dump()
    job_id = insert_job({**payload, "source_id": "greenhouse:original"})
    insert_job({**payload, "source_id": "greenhouse:other"})
    assert get_job(job_id)["source_id"] == "greenhouse:original"


def test_pack_metrics_use_real_unique_members_and_successful_coverage():
    acme, beta = source(), source("beta", "Beta", "lever")
    stats = {"greenhouse:acme": dict(fetch_status="success", fetched=40, passed=8), "lever:beta": dict(fetch_status="failed", fetched=0, passed=0)}
    rows = pack_metrics({"real": [acme, acme, beta], "overlap": [acme], "empty": []}, stats)
    real = next(row for row in rows if row["pack"] == "real")
    assert real == dict(pack="real", sources=2, measured=1, fetched=40, passed=8, yield_rate=0.2)
    assert next(row for row in rows if row["pack"] == "empty")["yield_rate"] is None


def test_migration_preserves_existing_database_rows_and_adds_schema(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_schema = SCHEMA.split("CREATE TABLE IF NOT EXISTS ingest_runs")[0].replace("    source_id TEXT,\n", "")
    with sqlite3.connect(path) as conn:
        conn.executescript(legacy_schema)
        conn.execute("INSERT INTO jobs (provider,external_id,title,company,status,created_at,updated_at,discovered_at,apply_url,fit_score) VALUES ('greenhouse','legacy','Job','Acme','saved','then','then','then','https://example.com',42)")
        conn.execute("INSERT INTO applications (job_id,status,created_at,updated_at) VALUES (1,'applied','then','then')")
        conn.execute("INSERT INTO feedback (job_id,action,created_at) VALUES (1,'saved','then')")
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in ("jobs", "applications", "feedback")}
    before_bytes = path.read_bytes()
    legacy_report = build_source_metrics([source()], path=path)
    assert not legacy_report.runs
    assert legacy_report.corpus["greenhouse:acme"]["applied"] == 1
    assert path.read_bytes() == before_bytes
    init_database(path)
    init_database(path)
    with sqlite3.connect(path) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
        old_columns = [name for name in columns if name != "source_id"]
        assert conn.execute(f"SELECT {','.join(old_columns)} FROM jobs").fetchall() == before["jobs"]
        assert conn.execute("SELECT source_id FROM jobs").fetchone() == (None,)
        for table in ("applications", "feedback"):
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == before[table]
        assert conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM source_run_stats").fetchone() == (0,)


def test_reporting_is_read_only_without_database(workspace, runner):
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "yield"])
    assert result.exit_code == 0, result.stdout
    assert "No completed ingest measurements" in result.stdout
    assert "Active corpus: 0" in result.stdout
    assert file_snapshot() == before


def test_trial_real_adapter_production_filter_and_zero_writes(initialized_workspace, runner, monkeypatch):
    import jobpipe.providers.greenhouse as greenhouse
    acme = source()
    configure(acme)
    monkeypatch.setattr(greenhouse, "_fetch_json", lambda _: {"jobs": [
        dict(id=1, title="Junior Data Engineer", content="<p>Python SQL ETL automation backend API</p>", absolute_url="https://example.com/1"),
        dict(id=2, title="Senior Data Engineer", content="Python SQL ETL", absolute_url="https://example.com/2"),
        dict(id=3, title="Sales Director", content="Sales", absolute_url="https://example.com/3"),
    ]})
    jobs = greenhouse.fetch_greenhouse_jobs(acme)
    expected = filter_discovered_jobs(jobs, acme, load_search_profile())
    assert [job.external_id for job in expected] == ["1"]
    report = collect_discovery_diagnostics([acme], load_search_profile()).source_reports[0]
    assert report.accepted_jobs == expected
    assert sum(report.passed_role_family_counts.values()) == sum(report.passed_seniority_counts.values()) == 1
    # Include existing feedback/status to verify all tables, not only job count.
    job_id = insert_job(posting("existing").model_dump(), review_status="saved")
    insert_feedback(job_id, "saved")
    before_db, before_files = database_snapshot(), file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "greenhouse:acme"])
    repeat = runner.invoke(app, ["sources", "trial", "Acme"])
    assert result.exit_code == repeat.exit_code == 0, result.stdout
    assert result.stdout == repeat.stdout
    assert "Fetched: 3 | Passed: 1 | Rejected: 2 | Yield: 33.33%" in result.stdout
    assert "Junior Data Engineer" in result.stdout
    assert "Canonical DB changes: 0" in result.stdout
    assert database_snapshot() == before_db
    assert file_snapshot() == before_files
    # Normal ingest uses identical production decisions and accepts only that job.
    assert runner.invoke(app, ["ingest"]).exit_code == 0
    with connect() as conn:
        assert {r[0] for r in conn.execute("SELECT external_id FROM jobs")} == {"existing", "1"}


def test_trial_disabled_source_no_database_no_config_changes(workspace, runner, monkeypatch):
    import jobpipe.providers as providers
    acme = source().model_copy(update={"enabled": False})
    configure(acme)
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda _: [])
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "acme"])
    assert result.exit_code == 0, result.stdout
    assert "Fetched: 0 | Passed: 0 | Rejected: 0 | Yield: N/A" in result.stdout
    assert file_snapshot() == before and not Path("jobpipe.db").exists()


def test_trial_real_lever_adapter_without_database(workspace, runner, monkeypatch):
    import jobpipe.providers.lever as lever
    configure(source("beta", "Beta", "lever"))
    monkeypatch.setattr(lever, "_fetch_with_fallback", lambda _: [dict(
        id="lever-1", text="Junior Data Engineer", categories={"location": "Remote"},
        content={"description": "Python SQL ETL automation backend API"},
        urls={"apply": "https://example.com/lever-1"},
    )])
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "lever:beta"])
    assert result.exit_code == 0, result.stdout
    assert "Fetched: 1 | Passed: 1 | Rejected: 0 | Yield: 100.00%" in result.stdout
    assert "Junior Data Engineer" in result.stdout
    assert file_snapshot() == before


@pytest.mark.parametrize("empty_file", [False, True])
def test_trial_packaged_fallback_does_not_seed_config(workspace, runner, monkeypatch, empty_file):
    import jobpipe.providers as providers
    if empty_file:
        configure()
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda _: [])
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "stripe"])
    assert result.exit_code == 0, result.stdout
    assert "greenhouse:stripe" in result.stdout
    assert file_snapshot() == before


def test_trial_provider_failure_clean_and_read_only(workspace, runner, monkeypatch):
    import jobpipe.providers as providers
    configure(source())
    def fail(_):
        raise RuntimeError("endpoint unavailable")
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", fail)
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "acme"])
    assert result.exit_code == 1
    assert "endpoint unavailable" in result.stdout
    assert "Yield: N/A" in result.stdout and "Canonical DB changes: 0" in result.stdout
    assert file_snapshot() == before


def test_trial_pack_catalog_and_ambiguous_selector(workspace, runner, monkeypatch):
    import jobpipe.providers as providers
    configure(source("primary", "Primary"))
    Path("data/source_packs.yaml").write_text(yaml.safe_dump({"catalog": [source("prospective", "Prospective").model_dump()]}))
    monkeypatch.setitem(providers.PROVIDER_FETCHERS, "greenhouse", lambda _: [])
    before = file_snapshot()
    result = runner.invoke(app, ["sources", "trial", "prospective"])
    assert result.exit_code == 0, result.stdout
    assert "Catalog definition: catalog" in result.stdout
    assert "greenhouse:prospective" in result.stdout
    assert file_snapshot() == before
    configure(source("one", "Same"), source("two", "Same"))
    assert runner.invoke(app, ["sources", "trial", "Same"]).exit_code != 0


def test_concentration_output_includes_warning(initialized_workspace, runner):
    configure(source())
    insert_job({**posting().model_dump(), "source_id": "greenhouse:acme"})
    result = runner.invoke(app, ["sources", "yield"])
    assert result.exit_code == 0, result.stdout
    assert "Employers represented: 1" in result.stdout
    assert "Sources represented: 1" in result.stdout
    assert "1 / 1 (100.0%)" in result.stdout
    assert "exceeds 25%" in result.stdout


def test_missing_run_metrics_do_not_hide_existing_corpus(initialized_workspace, runner):
    acme, beta = source(), source("beta", "Beta", "lever")
    configure(acme, beta)
    insert_job({**posting().model_dump(), "source_id": "greenhouse:acme"})
    run_id = start_ingest_run(load_search_profile())
    record_source_run(run_id, SourceDiscoveryReport(beta, "success"))
    finish_ingest_run(run_id)
    result = runner.invoke(app, ["sources", "yield"])
    assert result.exit_code == 0, result.stdout
    assert "not measured" in result.stdout
    assert build_source_metrics([acme, beta]).corpus["greenhouse:acme"]["active"] == 1
