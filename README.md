# jobpipe

`jobpipe` is a local-first CLI for repetitive job application prep. It stores job postings, ranks them with a deterministic fit score, and lets you review, save, package, or skip jobs without a web UI.

## Requirements

- Python 3.11+

## Setup

```fish
python3 -m venv venv
source venv/bin/activate.fish
pip install -e ".[dev]"
jobpipe init
```

## Supported Providers

`jobpipe init` seeds `data/job_sources.yaml` with starter Greenhouse and Lever sources. `jobpipe ingest` also falls back to the packaged starter list when `data/job_sources.yaml` is missing or set to `[]`, so you can ingest immediately and then customize the workspace file later.

- Greenhouse public job board API
- Lever postings API

Minimal Greenhouse source:

```yaml
- provider: greenhouse
  company: GitLab
  company_slug: gitlab
  board_token: gitlab
  enabled: true
```

Minimal Lever source:

```yaml
- provider: lever
  company: Mistral AI
  company_slug: mistral
  account: mistral
  enabled: true
```

Role-targeted source config:

```yaml
- provider: greenhouse
  company: Stripe
  company_slug: stripe
  board_token: stripe
  target_role_families:
    - data_engineer
    - backend_api
    - python_automation

- provider: lever
  company: Wellfound
  company_slug: angellist
  account: angellist
  target_role_families:
    - junior_software_engineer
    - software_engineer
```

Optional per-source keyword overrides:

```yaml
include_keywords:
  - data engineer
  - backend api
  - qa automation
exclude_keywords:
  - help desk
  - customer support
  - manager
```

Field notes:

- `company_slug` is jobpipe's stable company identifier.
- `board_token` is the Greenhouse public board slug when it differs from `company_slug`.
- `account` is the Lever postings account slug when it differs from `company_slug`.
- `target_role_families` filters the ingest stream before scoring so generic support or sales jobs do not reach the review queue.

Source management:

```fish
jobpipe sources list
jobpipe sources add --provider greenhouse --company GitLab --company-slug gitlab --board-token gitlab
jobpipe sources disable gitlab
jobpipe sources enable gitlab
jobpipe sources test
```

## Example Flow

```fish
jobpipe init
jobpipe ingest
jobpipe review
jobpipe batch-review
jobpipe show 1
jobpipe save 1
jobpipe package 1
jobpipe apply-flow 1
jobpipe skip 2
jobpipe analyze 3
jobpipe packet 3
jobpipe tailor 3
jobpipe shortlist --min-fit 60 --max-risk 50
jobpipe status 3 applied --reason interesting_company
jobpipe due
jobpipe feedback-report
jobpipe export
```

## Review Workflow

1. Run `jobpipe init` to seed the workspace with starter sources, or skip it and let `jobpipe ingest` use the packaged defaults when `data/job_sources.yaml` is missing or `[]`.
2. Use `jobpipe sources list` to inspect the current source set, then add, enable, or disable entries as needed.
3. Edit `data/job_sources.yaml` directly when you want to tune `target_role_families` or keyword overrides.
4. Run `jobpipe ingest` to fetch jobs and score them locally.
4. Run `jobpipe review` for quick single-job triage, or `jobpipe batch-review` to process the ranked queue in one session.
5. Use the interactive actions in `batch-review`:
   - `s` save only
   - `p` save + package
   - `a` save + package + apply-flow
   - `k` skip
   - `d` details
   - `o` open URL
   - `q` quit
6. After choosing `s`, `p`, `a`, or `k`, you will be prompted for an optional **reason code** (e.g. `too_senior`, `strong_python`) and an optional **note**. These help the deterministic learner adjust your fit scores.
7. Use `jobpipe show <job-id>` for a full local detail view, including a breakdown of learned adjustments.
8. Use `jobpipe package <job-id>` to generate a local application bundle under `applications/`.
9. Use `jobpipe apply-flow <job-id>` to open the apply URL, review the generated files, and mark the job applied manually.
10. Use `jobpipe feedback-report` to see how the tool is adjusting your scores.

## Commands

- `jobpipe init`
- `jobpipe ingest`
- `jobpipe review`
- `jobpipe batch-review`
- `jobpipe show JOB_ID`
- `jobpipe save JOB_ID`
- `jobpipe package JOB_ID`
- `jobpipe apply-flow JOB_ID`
- `jobpipe skip JOB_ID`
- `jobpipe add-text --file PATH`
- `jobpipe add-url URL`
- `jobpipe list`
- `jobpipe analyze JOB_ID`
- `jobpipe shortlist --min-fit INT --max-risk INT --limit INT`
- `jobpipe tailor JOB_ID`
- `jobpipe packet JOB_ID`
- `jobpipe status JOB_ID STATUS`
- `jobpipe edit JOB_ID [--title ...] [--company ...] [--location ...]`
- `jobpipe due [--limit INT]`
- `jobpipe open JOB_ID`
- `jobpipe export`
- `jobpipe feedback JOB_ID ACTION [--reason ...] [--note ...]`
- `jobpipe feedback-report`
- `jobpipe sync-weights`
- `jobpipe sources list`
- `jobpipe sources add --provider PROVIDER --company COMPANY [--company-slug SLUG] [--board-token TOKEN] [--account ACCOUNT]`
- `jobpipe sources enable SOURCE_ID`
- `jobpipe sources disable SOURCE_ID`
- `jobpipe sources test [SOURCE_ID]`

## Notes

- Ingest uses only local configuration plus the public Greenhouse/Lever APIs.
- Fit scoring v1 is deterministic and explainable; no LLMs are used for core ranking.
- `score_job_fit_v1` is the canonical user-facing fit score. Review, batch-review, list, details, and analyze all render from that helper; SQLite `fit_score` is a cache only.
- The scorer is role-family-first, so remote/location bonuses cannot outrank a bad-fit family.
- **Deterministic Learning**: Job-Pipe learns from your review decisions (save, skip, package, apply). If you repeatedly save jobs with a certain role family or keyword, its weight increases. If you skip them, its weight decreases. Adjustments are stored in `data/learned_weights.yaml` and are capped at +/- 20 to ensure hard filters are never overridden.
- Tailoring is constrained by the candidate profile and never invents paid experience, tools, certifications, or years of experience.
- `profile.md` is the editable local base resume/profile used for package generation.
