"""Concise rendering of source measurements; no fetching or persistence."""
from __future__ import annotations

from collections import Counter
from typing import Any
from rich.console import Console
from rich.table import Table

from .catalog import source_identity_label
from .ingest import SourceDiscoveryReport
from .models import JobSource
from .source_metrics import SourceMetrics, completed_runs, concentration, format_rate, pack_metrics, rate, run_stats


def print_run(console: Console, report: SourceMetrics, run: dict[str, Any], sources: list[JobSource], *, latest: bool = True) -> dict[str, dict[str, Any]]:
    console.print(f"Ingest run {run['id']} | {run['started_at']} → {run['completed_at']} | Profile: {run['profile']} | Goal: {run['goal'] or 'none'}")
    stats = run_stats(report, run["id"])
    table = Table(title="Run metrics (latest completed ingest)" if latest else "Run metrics", padding=(0, 0))
    for column in ("Source", "Fetched", "Passed", "Rejected", "Yield", "Inserted", "Updated"):
        table.add_column(column, no_wrap=True)
    labels = sorted(set(stats) | {source_identity_label(source) for source in sources} | (set(report.corpus) if latest else set()))
    for label in labels:
        stat = stats.get(label)
        if not stat or stat["fetch_status"] != "success":
            table.add_row(label, "--", "--", "--", "N/A", "--", "--")
            console.print(f"{label}: {stat['fetch_status'] if stat else 'not measured'}", markup=False)
        else:
            table.add_row(label, *(str(stat[key]) for key in ("fetched", "passed", "rejected")), format_rate(rate(stat["passed"], stat["fetched"])), str(stat["inserted"]), str(stat["updated"]))
        if stat and stat["error"]:
            console.print(f"{label}: {stat['error']}", markup=False)
    console.print(table)
    return stats


def print_history(console: Console, report: SourceMetrics) -> None:
    console.print("Completed ingest history (run metrics only)")
    for run in completed_runs(report):
        print_run(console, report, run, [], latest=False)
    omitted = len(report.runs) - len(completed_runs(report))
    if omitted:
        console.print(f"Excluded {omitted} running/aborted run(s) from measurements.")


def print_corpus(console: Console, report: SourceMetrics, sources: list[JobSource], stats: dict[str, dict[str, Any]]) -> None:
    table = Table(title="Current canonical corpus (current workflow buckets)", padding=(0, 0))
    for column in ("Source", "Total", "Active", "Saved", "Packaged", "Applied", "Save %", "Apply %"):
        table.add_column(column, no_wrap=True)
    labels = sorted(set(report.corpus) | set(stats) | {source_identity_label(source) for source in sources})
    for label in labels:
        counts = report.corpus.get(label, Counter())
        table.add_row(label, *(str(counts[key]) for key in ("total", "active", "saved", "packaged", "applied")), format_rate(rate(counts["saved"], counts["total"])), format_rate(rate(counts["applied"], counts["total"])))
    console.print(table)
    console.print("Save/apply rate denominator: all retained canonical jobs for that source, including inactive jobs. Counts are current buckets, not historical conversions.")
    if report.inferred_jobs:
        console.print(f"Legacy attribution: {report.inferred_jobs} job(s) uniquely matched by provider/company; no database backfill.")


def print_concentration(console: Console, report: SourceMetrics) -> None:
    summary = concentration(report)
    total = summary["active_jobs"]
    console.print(f"Active corpus: {total} | Employers represented: {summary['employers']} | Sources represented: {summary['sources']}")
    if report.unattributed_active:
        console.print(f"Active jobs with unknown source attribution: {report.unattributed_active}")
    for title, counts in (("employers", report.employers), ("sources", report.active_sources)):
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if ranked:
            name, count = ranked[0]
            console.print(f"Largest {title[:-1]}: {name} — {count} / {total} ({count / total:.1%})", markup=False)
        table = Table(title=f"Top {title} (active corpus)")
        for column in ("Name", "Active", "Share"):
            table.add_column(column)
        for name, count in ranked[:5]:
            table.add_row(name, str(count), format_rate(rate(count, total)))
        console.print(table)
    if summary["warning"]:
        console.print("Concentration warning: one employer exceeds 25% of active jobs. Diagnostic only; no jobs suppressed.")


def print_packs(console: Console, packs: dict[str, list[JobSource]], stats: dict[str, dict[str, Any]]) -> None:
    table = Table(title="Source packs (latest run; current catalog membership)")
    for column in ("Pack", "Sources", "Measured", "Fetched", "Passed", "Yield"):
        table.add_column(column)
    for row in pack_metrics(packs, stats):
        measured = row["measured"]
        table.add_row(row["pack"], str(row["sources"]), f"{measured}/{row['sources']}", str(row["fetched"]) if measured else "--", str(row["passed"]) if measured else "--", format_rate(row["yield_rate"]))
    console.print(table)
    console.print("Pack coverage counts successful source measurements; missing/failed members are unmeasured. Packs overlap. Yield uses summed passed / summed fetched under normal ingest rules, not pack-specific trial rules.")


def print_trial(console: Console, report: SourceDiscoveryReport, *, profile: str, goal: str | None) -> None:
    console.print(f"Source Trial: {report.label} | Provider: {report.source.provider} | Profile: {profile} | Goal: {goal or 'none'}")
    if report.status == "failed":
        console.print(f"Fetch failed: {report.error}", markup=False)
        console.print("Fetched: unavailable | Passed: unavailable | Rejected: unavailable | Yield: N/A")
    else:
        console.print(f"Fetched: {report.fetched_count} | Passed: {report.passed_count} | Rejected: {report.rejected_count} | Yield: {format_rate(rate(report.passed_count, report.fetched_count))}")
        for title, counts in (("Passed discovery roles", report.passed_role_family_counts), ("Passed discovery seniority", report.passed_seniority_counts), ("Top rejection reasons (overlapping)", report.reason_counts)):
            table = Table(title=title)
            table.add_column("Group")
            table.add_column("Count")
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            for name, count in ranked[:5] if counts is report.reason_counts else ranked:
                table.add_row(name, str(count))
            console.print(table)
        table = Table(title="Representative passing jobs")
        for column in ("Title", "Location", "URL"):
            table.add_column(column)
        for job in sorted(report.accepted_jobs, key=lambda job: (job.title, job.location, job.external_id))[:5]:
            table.add_row(job.title, job.location, job.apply_url or job.source_url or "")
        console.print(table)
    console.print("Canonical DB changes: 0 (trial performs no database or configuration writes)")
