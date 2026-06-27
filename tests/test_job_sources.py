from pathlib import Path

from jobpipe.cli import app
from jobpipe.db import get_job_by_provider_external_id, init_database
from jobpipe.ingest import load_job_sources, resolve_job_sources
from jobpipe.models import DiscoveredJob


def _set_packaged_sources(monkeypatch, tmp_path: Path, content: str) -> Path:
    pkg_dir = tmp_path / "pkg"
    (pkg_dir / "data").mkdir(parents=True)
    (pkg_dir / "data" / "job_sources.yaml").write_text(content, encoding="utf-8")
    monkeypatch.setattr("jobpipe.ingest.package_dir", lambda: pkg_dir)
    return pkg_dir


def test_init_seeds_default_job_sources(initialized_workspace) -> None:
    sources = load_job_sources(Path("data/job_sources.yaml"))
    assert len(sources) >= 4
    assert {source.provider for source in sources} >= {"greenhouse", "lever"}
    assert all(source.company_slug for source in sources)
    assert all(source.target_role_families for source in sources)


def test_resolve_job_sources_prefers_workspace_file(workspace, monkeypatch, tmp_path) -> None:
    _set_packaged_sources(
        monkeypatch,
        tmp_path,
        """- provider: greenhouse
  company: Built In
  board_token: built-in
""",
    )
    Path("data").mkdir()
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Workspace Source
  board_token: workspace-source
""",
        encoding="utf-8",
    )

    loaded = resolve_job_sources(Path("data/job_sources.yaml"))

    assert loaded.origin == "workspace"
    assert [source.company for source in loaded.sources] == ["Workspace Source"]


def test_resolve_job_sources_uses_built_ins_when_workspace_missing(workspace, monkeypatch, tmp_path) -> None:
    _set_packaged_sources(
        monkeypatch,
        tmp_path,
        """- provider: lever
  company: Built In
  account: built-in
""",
    )

    loaded = resolve_job_sources(Path("data/job_sources.yaml"))

    assert loaded.origin == "built_in"
    assert [source.company for source in loaded.sources] == ["Built In"]


def test_resolve_job_sources_uses_built_ins_when_workspace_is_blank(workspace, monkeypatch, tmp_path) -> None:
    _set_packaged_sources(
        monkeypatch,
        tmp_path,
        """- provider: lever
  company: Built In
  account: built-in
""",
    )
    Path("data").mkdir()
    Path("data/job_sources.yaml").write_text("[]\n", encoding="utf-8")

    loaded = resolve_job_sources(Path("data/job_sources.yaml"))

    assert loaded.origin == "built_in"
    assert [source.company for source in loaded.sources] == ["Built In"]


def test_resolve_job_sources_does_not_fallback_when_workspace_disables_all_sources(workspace, monkeypatch, tmp_path) -> None:
    _set_packaged_sources(
        monkeypatch,
        tmp_path,
        """- provider: greenhouse
  company: Built In
  board_token: built-in
""",
    )
    Path("data").mkdir()
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Disabled
  board_token: disabled
  enabled: false
""",
        encoding="utf-8",
    )

    loaded = resolve_job_sources(Path("data/job_sources.yaml"))

    assert loaded.origin == "workspace"
    assert loaded.sources == []


def test_ingest_uses_built_in_sources_when_workspace_file_is_missing(workspace, runner, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("jobpipe.cli._ensure_workspace", lambda: init_database())
    _set_packaged_sources(
        monkeypatch,
        tmp_path,
        """- provider: greenhouse
  company: Built In Tech
  board_token: built-in-tech
  target_role_families:
    - data_engineer
""",
    )

    import jobpipe.providers as providers

    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "greenhouse",
        lambda source: [
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-built-in",
                company=source.company,
                title="Junior Data Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-built-in",
                description="Python SQL ETL backend API",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-built-in",
            )
        ],
    )

    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0, result.stdout
    assert "Using built-in starter job sources" in result.stdout
    assert get_job_by_provider_external_id("greenhouse", "gh-built-in") is not None


def test_ingest_reports_no_config_when_workspace_and_built_ins_are_unavailable(workspace, runner, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("jobpipe.cli._ensure_workspace", lambda: init_database())
    _set_packaged_sources(monkeypatch, tmp_path, "[]\n")

    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 0, result.stdout
    assert "No enabled job sources configured." in result.stdout


def test_ingest_uses_configured_company_slugs_and_filters_low_fit_roles(initialized_workspace, runner, monkeypatch) -> None:
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Acme Tech
  company_slug: acme-tech
  target_role_families:
    - data_engineer
- provider: lever
  company: Beta Systems
  company_slug: beta-systems
  target_role_families:
    - qa_automation
""",
        encoding="utf-8",
    )

    sources = load_job_sources(Path("data/job_sources.yaml"))
    assert [source.company_slug for source in sources] == ["acme-tech", "beta-systems"]
    assert [source.board_token for source in sources] == [None, None]
    assert [source.account for source in sources] == [None, None]
    assert [source.target_role_families for source in sources] == [["data_engineer"], ["qa_automation"]]

    import jobpipe.providers as providers

    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "greenhouse",
        lambda source: [
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-good",
                company=source.company,
                title="Junior Data Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-good",
                description="Python SQL ETL backend API",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-good",
            ),
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-bad",
                company=source.company,
                title="Help Desk Analyst",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-bad",
                description="Customer support tickets and troubleshooting",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-bad",
            ),
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-generic",
                company=source.company,
                title="Operations Specialist",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-generic",
                description="Own API integrations and Python backend workflows for internal stakeholders",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-generic",
            ),
        ],
    )
    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "lever",
        lambda source: [
            DiscoveredJob(
                provider="lever",
                external_id="lv-good",
                company=source.company,
                title="QA Automation Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/lv-good",
                description="pytest playwright python api",
                posted_at="2026-06-21T00:00:00+00:00",
                source_url="https://example.com/lv-good",
            ),
            DiscoveredJob(
                provider="lever",
                external_id="lv-bad",
                company=source.company,
                title="Customer Success Manager",
                location="Remote",
                remote=True,
                apply_url="https://example.com/lv-bad",
                description="Client onboarding and account management",
                posted_at="2026-06-21T00:00:00+00:00",
                source_url="https://example.com/lv-bad",
            ),
        ],
    )

    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0, result.stdout
    assert "Using configured job sources from data/job_sources.yaml." in result.stdout
    assert "Filtered out 3 job(s)." in result.stdout

    assert get_job_by_provider_external_id("greenhouse", "gh-good") is not None
    assert get_job_by_provider_external_id("lever", "lv-good") is not None
    assert get_job_by_provider_external_id("greenhouse", "gh-bad") is None
    assert get_job_by_provider_external_id("greenhouse", "gh-generic") is None
    assert get_job_by_provider_external_id("lever", "lv-bad") is None


def test_sources_list_shows_active_state(initialized_workspace, runner) -> None:
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: GitLab
  company_slug: gitlab
  board_token: gitlab
  enabled: true
- provider: lever
  company: Mistral AI
  company_slug: mistral
  account: mistral
  enabled: false
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["sources", "list"])

    assert result.exit_code == 0, result.stdout
    assert "Job Sources" in result.stdout
    assert "GitLab" in result.stdout
    assert "Mistral AI" in result.stdout
    assert "yes" in result.stdout
    assert "no" in result.stdout


def test_sources_add_enable_disable_persist_yaml(initialized_workspace, runner) -> None:
    add_result = runner.invoke(
        app,
        [
            "sources",
            "add",
            "--provider",
            "greenhouse",
            "--company",
            "Acme",
            "--company-slug",
            "acme",
            "--board-token",
            "acme",
        ],
    )
    assert add_result.exit_code == 0, add_result.stdout
    content = Path("data/job_sources.yaml").read_text(encoding="utf-8")
    assert "company: Acme" in content
    assert "company_slug: acme" in content
    assert "enabled: true" in content

    disable_result = runner.invoke(app, ["sources", "disable", "acme"])
    assert disable_result.exit_code == 0, disable_result.stdout
    content = Path("data/job_sources.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in content

    enable_result = runner.invoke(app, ["sources", "enable", "acme"])
    assert enable_result.exit_code == 0, enable_result.stdout
    content = Path("data/job_sources.yaml").read_text(encoding="utf-8")
    assert content.count("enabled: true") >= 1


def test_sources_test_uses_active_sources_and_allows_targeting_disabled_sources(initialized_workspace, runner, monkeypatch) -> None:
    Path("data/job_sources.yaml").write_text(
        """- provider: greenhouse
  company: Acme
  company_slug: acme
  board_token: acme
  enabled: true
- provider: lever
  company: Beta
  company_slug: beta
  account: beta
  enabled: false
""",
        encoding="utf-8",
    )

    calls: list[tuple[str, str]] = []
    import jobpipe.providers as providers

    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "greenhouse",
        lambda source: calls.append(("greenhouse", source.company_slug)) or [
            DiscoveredJob(
                provider="greenhouse",
                external_id="gh-1",
                company=source.company,
                title="Data Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/gh-1",
                description="python sql etl",
                posted_at="2026-06-22T00:00:00+00:00",
                source_url="https://example.com/gh-1",
            )
        ],
    )
    monkeypatch.setitem(
        providers.PROVIDER_FETCHERS,
        "lever",
        lambda source: calls.append(("lever", source.company_slug)) or [
            DiscoveredJob(
                provider="lever",
                external_id="lv-1",
                company=source.company,
                title="Automation Engineer",
                location="Remote",
                remote=True,
                apply_url="https://example.com/lv-1",
                description="python api",
                posted_at="2026-06-21T00:00:00+00:00",
                source_url="https://example.com/lv-1",
            )
        ],
    )

    active_result = runner.invoke(app, ["sources", "test"])
    assert active_result.exit_code == 0, active_result.stdout
    assert calls == [("greenhouse", "acme")]
    assert "Testing greenhouse:acme (active)" in active_result.stdout
    assert "fetched 1 job(s)" in active_result.stdout

    calls.clear()
    disabled_result = runner.invoke(app, ["sources", "test", "beta"])
    assert disabled_result.exit_code == 0, disabled_result.stdout
    assert calls == [("lever", "beta")]
    assert "Testing lever:beta (disabled)" in disabled_result.stdout
