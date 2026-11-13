# Job Pipe

Job Pipe is a local-first CLI and dashboard for repetitive job application prep. It keeps job data, fit scoring, tailored notes, and application status in a local workspace so you can review roles quickly without handing the workflow to a hosted service.

## What Problem It Solves

Job search workflows get noisy fast: postings arrive from multiple sources, the same role needs to be scored repeatedly, and application materials have to stay consistent with your real background. Job Pipe centralizes that work locally. It ingests jobs, scores fit deterministically, records decisions, generates tailored bundles, and tracks follow-up state so you can spend less time redoing the same preparation.

## Current Features

- Ingest jobs from Greenhouse and Lever public postings.
- Maintain editable job-source, search-profile, and source-pack YAML files.
- Score fit with a deterministic knowledge graph plus learned feedback weights.
- Review jobs in the CLI or the local FastAPI dashboard.
- Save, skip, package, and apply jobs with tracked follow-up dates.
- Generate local application bundles under `applications/<company>-<title>-job-<id>/`.
- Tailor resume bullets, cover notes, and short answers from the candidate profile.
- Export tracker data to CSV and inspect analytics dashboards.
- Copy generated text or open local folders and URLs when you want browser assistance.

## Requirements

- Python 3.11+
- Fish shell for the setup examples below

## Setup

```fish
python3 -m venv venv
source venv/bin/activate.fish
pip install -e ".[dev]"
jobpipe init
```

`jobpipe init` seeds the workspace with starter sources, candidate data, knowledge data, and the SQLite database. If you skip `init`, `jobpipe ingest` can still fall back to the packaged starter job sources when `data/job_sources.yaml` is missing or empty.

## Fish-Compatible Examples

```fish
jobpipe sources list
jobpipe sources add --provider greenhouse --company GitLab --company-slug gitlab --board-token gitlab
jobpipe sources disable gitlab
jobpipe sources enable gitlab
jobpipe sources test
```

```fish
jobpipe ingest
jobpipe review
jobpipe batch-review
jobpipe shortlist --min-fit 70 --max-risk 40
jobpipe due
```

```fish
jobpipe show 1
jobpipe save 1
jobpipe package 1
jobpipe apply-flow 1
jobpipe skip 2
jobpipe status 1 applied --reason interesting_company
```

```fish
jobpipe web --no-open
jobpipe analytics
jobpipe feedback-report
jobpipe export
```

## Main CLI Workflows

### 1. Set up the workspace

- Run `jobpipe init` once in a fresh checkout.
- Review `data/job_sources.yaml`, `data/search_profiles.yaml`, and `data/knowledge.yaml` if you want to tune the defaults.
- Edit `data/profiles/candidate.yaml` and `profile.md` to match your current background.

### 2. Ingest and score jobs

- `jobpipe ingest` fetches jobs from the configured sources.
- Each job receives a deterministic fit score, role-family classification, and risk/quality metadata.
- `jobpipe list` shows the current queue and `jobpipe show <job-id>` shows the breakdown.

### 3. Review and learn

- Use `jobpipe review` for a single-job decision loop.
- Use `jobpipe batch-review` to step through the ranked queue.
- When prompted, choose `saved`, `skipped`, `packaged`, or `applied` and optionally add a reason code and note.
- `jobpipe feedback-report` shows how the deterministic learner is changing role-family and keyword weights.

### 4. Package and apply

- `jobpipe save <job-id>` marks a job as worth keeping.
- `jobpipe package <job-id>` writes `job.md`, `fit.md`, `resume-tailored.md`, `cover-note.md`, and `apply.md` into the job-specific bundle directory.
- `jobpipe apply-flow <job-id>` opens the apply URL, shows the generated files, and asks for manual confirmation before marking the job applied.

### 5. Track outcomes

- `jobpipe status <job-id> <status>` updates the application tracker.
- `jobpipe due` lists follow-ups.
- `jobpipe analytics` and `/analytics` summarize local tracker data and surface the top jobs to apply today.

## Knowledge Representation and Scoring

Job Pipe uses a deterministic knowledge graph rather than an LLM for core ranking.

- `data/knowledge.yaml` defines canonical concepts, aliases, related concepts, role-family links, the available goals, and goal-dependent modifiers.
- `data/search_profiles.yaml` selects one active goal and role preference tiers: `preferred`, `acceptable`, and `avoid`. Acceptable roles are neutral rather than conflicts.
- `data/learned_weights.yaml` stores feedback-driven adjustments derived from saved, skipped, packaged, and applied decisions.
- `score_job_fit_v1` combines base signals, goal modifiers, knowledge matches, preference tiers, practical constraints, and learned feedback weights.
- Use `python3 -m jobpipe.cli goals`, `python3 -m jobpipe.cli goal get`, and `python3 -m jobpipe.cli goal set <goal-id>` to inspect or change the current objective.
- The score is explainable: CLI details and dashboard views show the notes that produced the final fit score.
- `score_job_fit_v1` is the canonical user-facing fit score. SQLite `fit_score` is a cache for convenience, not the source of truth.

## Analytics and Dashboard

The local FastAPI dashboard runs on `127.0.0.1:8765`.

- `/` shows the overview summary.
- `/jobs` provides filters for provider, company, score, packaging, follow-up due status, and text search.
- `/kanban` groups jobs by pipeline stage.
- `/views` links to saved filter presets.
- `/followups` lists applied jobs that need attention.
- `/analytics` summarizes applications today, applications this week, the current goal, jobs with positive goal impact, skip reasons, and role-family rates.

The dashboard is read-only except for the same workflow actions exposed in the CLI: save, skip, package, open apply, open package, copy text, and move status.

## Example Usage

```fish
jobpipe init
jobpipe ingest
jobpipe review
jobpipe show 1
jobpipe save 1
jobpipe package 1
jobpipe apply-flow 1
jobpipe analytics
```

## Testing

Run the full test suite from the repository root:

```fish
./venv/bin/python -m pytest -q
```

Useful focused checks:

```fish
./venv/bin/python -m pytest tests/test_web.py -q
./venv/bin/python -m pytest tests/test_package_generation.py tests/test_deterministic_learning.py -q
```

## Roadmap / Next Improvements

- Add more provider adapters so ingest covers more of the job sources you actually use.
- Expand analytics with better follow-up planning and decision-history views.
- Harden status and feedback validation across the CLI and dashboard.
- Grow the knowledge graph and role packs for additional job families.
- Keep adding regression tests around ingest, packaging, and dashboard filters as the workspace evolves.

## Agent interface (v0.3.0)

Job-Pipe exposes fourteen typed MCP tools for controlled source refresh and trials,
canonical search and scoring, evidence-based eligibility, tracking, saving, and
application preparation. Read-only is the default. Network trials require
`--enable-source-trials`, ingestion requires `--enable-ingest`, and saving/package
preparation requires `--enable-local-writes`. MCP preparation requires confirmed
eligibility; no application is submitted externally.

See [the agent interface guide](docs/AGENT_INTERFACE.md) for installation,
Codex/Antigravity setup, permissions, schemas, and protocol validation.
