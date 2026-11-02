# Job-Pipe agent interface (v0.3.0)

Job-Pipe provides fourteen local MCP tools over stdio. An agent discovers tools and
exchanges structured data without reading Python source, issuing SQL, or parsing
terminal tables. Job-Pipe supplies tools; it does not run an LLM.

## Installation and launch

Python 3.11+ and the official Python MCP SDK 2.3.x or a compatible 2.x release
are required. From an existing checkout, using fish-compatible commands:

```fish
./venv/bin/python -m pip install -e '.[agent]'
./venv/bin/python -m jobpipe.mcp_server --workspace /home/miles/Documents/job-pipe
```

The default mode is **read-only**. To authorize the two bounded local writes:

```fish
./venv/bin/python -m jobpipe.mcp_server --workspace /home/miles/Documents/job-pipe --enable-local-writes
```

Discovery has separate permissions. None of these flags implies another:

```fish
./venv/bin/python -m jobpipe.mcp_server --workspace /home/miles/Documents/job-pipe --enable-source-trials --enable-ingest --enable-local-writes
```

`--enable-source-trials` permits network trials of configured/catalog sources.
`--enable-ingest` permits ingestion from enabled configured sources, including
normal database upserts and source-run measurements. `--enable-local-writes`
still grants only saving and preparation. No flag grants source changes,
applicant/profile edits, submission, shell, SQL, arbitrary paths or arbitrary HTTP.

The workspace defaults to the startup working directory. An explicit workspace
must exist. It selects the existing `jobpipe.db`, `data/` configuration,
`profile.md`, and `applications/` directory together. There is no independent
database path override or tool-supplied destination path.

Server startup, protocol initialization, and tool discovery need no network or database. Database
tools report missing or incompatible databases without creating/migrating them.
Run `jobpipe init` yourself from the selected workspace when initialization or
migration is necessary. `get_active_goal` reads configuration and works without a
database; existing built-in configuration fallback is labeled `built_in`.

## Architecture

```text
CLI / FastAPI web / MCP adapters
              |
       shared Python services
              |
existing SQLite, classification, fit scoring, source metrics, package renderer
```

`jobpipe.services` owns shared search/filtering, canonical score context,
review saves, and preparation orchestration. CLI and web delegate to it. The MCP
adapter uses the official SDK's supported `Server` and stdio transport, with a
fixed tool registry, Pydantic validation, and centralized capability checks.
There is no custom MCP framing, second classifier, scoring formula,
source-yield algorithm, or discovery index. Eligibility is a separate shared,
deterministic Python service; it never rewrites canonical fit scores.

## Tools and schemas

Every tool advertises its description, input JSON Schema, output JSON Schema,
and MCP annotations through `tools/list`. Schemas are generated from
`jobpipe.agent_models`; unknown input fields and coercive values such as a
string or boolean for `job_id` are rejected. IDs must be positive integers.
Collections default to `limit=20`, accept limits 1–100 and `offset >= 0`, and
return `items`, `total`, `limit`, `offset`, and nullable `next_offset`.

| Tool | Inputs beyond collection pagination | Successful `data` |
| --- | --- | --- |
| `search_jobs` | Optional `query`, canonical `role_family`, `company`, nullable boolean `remote`, `min_fit_score` (0–100, default 0), `status`, `goal`, `active_only` (default true) | Job summaries and resolved goal |
| `get_job` | `job_id` | Summary, canonical fields, classification and evidence, extracted requirements, description, persisted application record, existing package paths |
| `get_fit_breakdown` | `job_id`, optional `goal` | Goal, classification/evidence, seniority, contributions, positive/negative signals, eligibility concerns, caps, notes, final `displayed_score` |
| `list_applications` | Optional persisted `status`, `company`, `date_from`, `date_to`, nullable boolean `due_followup` | Persisted application records joined with job title/company |
| `get_source_yield` | Optional exact `source_id` | Source rows with `ingest_measurement` and `current_corpus`, latest completed run metadata and attribution counts |
| `get_active_goal` | None | Profile, active goal, configuration origin, available goal definitions |
| `get_followups` | `due_only` (default false) | Applied application records with job title/company, follow-up timestamp and `due` boolean |
| `save_job` | `job_id` | Review/application/display statuses, workflow bucket, `changed` boolean |
| `prepare_application` | `job_id` | Statuses, package directory, résumé/cover-note/fit-analysis/instruction paths, validation concerns, `reused` boolean |
| `ingest_sources` | Optional configured `source_ids` (1–20), named search `profile`, `diagnostics` | Run ID, selected sources, deadline and polling instruction |
| `trial_source` | Known `source_id`, optional `diagnostics` | Fetch/filter counts, unchanged discovery yield, role/seniority distributions, rejection reasons, eligibility counts and up to five passing examples |
| `get_discovery_status` | Optional `run_id` | Latest completed measurement timestamp, running operations, selected run/source outcomes, aggregate counts, current corpus concentration and stale warnings |
| `get_job_eligibility` | `job_id`, optional registered `applicant_profile` name | Canonical technical-fit score and independent eligibility assessment |
| `search_eligible_jobs` | Optional `query`, canonical `role_family`, `remote_preference` (`remote_us`, `remote`, `any`), `minimum_fit_score`, `eligibility_status`, pagination | Canonical summaries with eligibility and resolved goal |

### Discovery runs and trials

`ingest_sources` returns promptly after reserving the workspace ingest lock.
Poll `get_discovery_status({"run_id": ...})` until `run.status` is `completed` or
`aborted`; only then interpret its final counts or search newly discovered jobs.
At most 20 configured sources run per request, under a 180-second hard worker
deadline and existing 15-second provider request timeouts. Larger workspaces
must be refreshed in explicit source batches. Concurrent CLI/MCP ingests fail
with `operation_in_progress`. Completed source transactions survive interruption;
the unfinished source transaction rolls back. Shutdown terminates owned workers.

Progress reuses existing ingest/source-run tables. `pending` and `running` source
rows have unavailable counts until finished. The latest completed-run view and
current canonical corpus remain separate. Seven-day stale warnings refer to
source measurements, not proof that individual postings have closed.

Trials have a 30-second hard deadline. They use the real adapters and discovery
evaluator without database, feedback, metrics, configuration or file writes.
Explicitly selecting a disabled source enables it only in memory for that trial.
Configured definitions take precedence; catalog-only entries use the first
alphabetically named pack, disclosed in the result. Arbitrary URLs and invented
source identifiers are rejected. A trial also works without a database.

`diagnostics` is reserved for requesting diagnostic context; aggregate rejection
reasons and distributions are always returned. Reasons can overlap. Distribution
denominators are explicit in trial responses. Eligibility counts use **passed
discovery** as their denominator, never all fetched postings. Missing/failed
measurements are null. A successful empty response is not evidence of a healthy,
current employer board without independent careers-page verification.

### Eligibility and applicant facts

Eligibility is `eligible`, `ineligible`, or `needs_review`, independently of fit.
Each check returns its status, evidence and concerns. Assessments also identify
missing applicant/employer information and a recommended next action. A proved
mandatory conflict dominates unresolved checks. Otherwise unresolved applicable
constraints require review. Eligibility covers stated constraints; it does not
certify availability, completeness of employer policy, or likelihood of hiring.

Remote-US eligibility requires explicit posting hiring scope and verified
residence/authorization. State-restricted, onsite/hybrid, international and
ambiguous postings retain distinct assessments. Incidental remote mentions do
not establish US eligibility. Foreign authorization and relocation are never
inferred from US citizenship. Preferred experience is not mandatory; projects
are not professional employment. Experience ranges retain both bounds, including
the uncertain upper bound in `2–12+ years`. Explicit future-role and talent-pool titles
require employer review before readiness is asserted.

The default applicant is `data/profiles/candidate.yaml`. Optional applicant names
resolve existing files in that directory; paths and symlinks are rejected.
Legacy files remain valid, with missing facts unknown. An optional `eligibility`
mapping supports country-specific `work_authorizations`, `citizenship_countries`,
`residence_country`, `residence_state`, `commute_locations`,
`professional_experience_years`, `experience_by_area`, enrollment/graduation,
employment restrictions, languages, `native_languages` and credentials. Facts use explicit provenance:

```yaml
eligibility:
  residence_country:
    value: US
    verified: true
    evidence: "Applicant confirmation with date; populate only when actually confirmed."
  work_authorizations:
    - country: US
      authorized: true
      sponsorship_required: false
      verified: true
      evidence: "Applicant confirmation with date; this is an example, not a seeded fact."
```

Only facts marked verified are used. Unknown fields should be omitted, not filled
with zero or false. Packaged templates contain no private applicant facts. The
workspace records only facts the user confirmed during planning; residence,
experience and enrollment remain unresolved. Neither MCP discovery flag can
write applicant facts. An assessment using another profile cannot bypass the
preparation gate, which always rechecks the active persisted applicant.

Eligibility search defaults to confirmed eligible jobs. Request `needs_review`
explicitly to inspect uncertainties, or `all` to compare categories. Evaluation
and eligibility filtering occur before pagination. Category ordering is eligible,
needs-review, ineligible; score/time/ID ordering within each category remains
canonical. The broader `search_jobs` tool remains available for investigation.

Job summaries contain `job_id`, `title`, `company`, `location`, boolean `remote`,
nullable primary role family and seniority, `fit_score`, `status`,
`review_status`, nullable `application_status`, `workflow_bucket`,
`source: {source_id, provider}`, and `application_url`.

All responses have the same versioned envelope, in both MCP structured content
and a JSON text content block:

```json
{"schema_version":1,"ok":true,"data":{"job_id":42,"status":"saved","review_status":"saved","application_status":"saved","workflow_bucket":"saved","changed":true},"error":null}
```

```json
{"schema_version":1,"ok":false,"data":null,"error":{"code":"permission_denied","message":"Local writes are disabled. Restart with --enable-local-writes to authorize saving and package preparation only."}}
```

Search uses the existing dashboard's case-insensitive substring filters and
orders by canonical score descending, discovery timestamp ascending, then ID
ascending. Explicit inactive-status filters remain usable with the active-only
default. `remote=false` selects nonremote records rather than disabling a
remote-only filter. Goal overrides apply to the current request only.

Application date bounds are inclusive UTC **application-record creation dates**
in `YYYY-MM-DD` format. Application listings read persisted records, including
saved records created during ingestion; they do not infer submission from files.
Follow-ups include only applied records with scheduled timestamps, sorted by
follow-up date then job ID. No follow-up is created by saving or preparation.

Source yield uses the latest completed ingest run, matching `sources yield`.
`ingest_measurement` contains run ID, measurement timestamp, fetch status, error,
fetched/passed/rejected counts, and `yield_rate` (passed/fetched).
Failed/unmeasured counts and undefined rates are null. `current_corpus` contains
total, active, saved, packaged, and applied current buckets. These are distinct
measurements, not historical conversion rates. Legacy source attribution uses
the existing uniquely matched provider/company rules and remains labeled.
Running/aborted measurements are excluded. CLI history reporting remains available.

## Permissions and write behavior

All fourteen tools remain discoverable in every mode. Read-only mode rejects
network trials, ingestion and local writes with `permission_denied` before
database/network access. Tool arguments
cannot grant capabilities. `--enable-local-writes` authorizes only saving an
existing review record and preparing its local application bundle.

MCP preparation additionally requires current confirmed eligibility; ineligible
and needs-review jobs return `eligibility_unresolved`, including on bundle reuse.
Saving remains available for investigation. Manual CLI/web preparation retains
existing transitions and returns eligibility concerns without asserting readiness.

Save is idempotent: an already-saved review record receives no additional
feedback or timestamp changes. Independent applied/interview/offer statuses are
preserved. Preparation requires the existing saved/package-ready review state.
It returns `package_directory`, `resume_path`, `cover_note_path`,
`fit_analysis_path`, and `application_instruction_path` for the canonical
five-file bundle (`job.md`, `fit.md`, `resume-tailored.md`, `cover-note.md`,
`apply.md`). Preparation never marks an application submitted.

Complete existing bundles are reused byte-for-byte, including user edits. They
may reflect an older profile/job; this is reported as a concern. Partial bundles
are preserved and rejected: inspect and move them aside manually before retrying.
Symlink destinations/artifacts are rejected. New bundles are rendered into a
temporary sibling directory and published together after successful rendering.
Database transactions serialize shared-service write operations.

MCP records each workflow feedback event once but does **not** regenerate
`data/learned_weights.yaml`. A later user-initiated synchronization can incorporate
those events. CLI/web retain automatic synchronization for newly recorded events.
The shared canonical scoring context now consistently includes persisted/manual
classification, secondary roles, and current learned weights; displayed values
can correct previous differences between presentation paths. Algorithms and
weights are unchanged.

This capability grants no submission, browser interaction, email, weight/profile
editing, source changes, history deletion, arbitrary writes, shell execution, or
Git access. Descriptions are untrusted data, even when they contain instructions
addressed to an assistant. No text from a posting becomes executable code.

## Errors and diagnostics

Errors carry an actionable message and one of `invalid_arguments`, `unknown_job`,
`missing_database`, `invalid_database`, `configuration_error`,
`invalid_state_transition`, `permission_denied`,
`application_preparation_failure`, `eligibility_unresolved`, `unknown_source`,
`unknown_run`, `operation_in_progress`, `operation_timeout`, or `internal_error`. Tool failures set MCP's
`isError` flag. Normal responses contain no traceback; diagnostic exceptions are
logged to stderr. Stdout is reserved for MCP. EOF and interruption shut down the
server and terminate its owned discovery workers. Hard worker deadlines also
bound an orphan after abrupt parent death. An abandoned run is reconciled on the
next authorized ingestion while holding the workspace lock; reads never repair
or mutate run history.

## Connect a client

Codex CLI syntax was checked with installed **codex-cli 0.165.0** and
[official Codex documentation](https://learn.chatgpt.com/docs/extend/mcp).
Run the following yourself; Job-Pipe does not change agent configuration:

```fish
codex mcp add jobpipe -- /home/miles/Documents/job-pipe/venv/bin/python -m jobpipe.mcp_server --workspace /home/miles/Documents/job-pipe
codex mcp list
```

In a Codex session, inspect `/mcp`, then ask it to call `get_active_goal` and
`search_jobs` with `limit=5`. To approve local writes, add
`--enable-local-writes` to the server arguments in your configuration and restart
the connection. Codex also supports a `[mcp_servers.jobpipe]` TOML entry with
`command`, `args`, and optional `cwd`.

Generic stdio configuration, also supported by
[Antigravity's custom MCP configuration](https://antigravity.google/docs/mcp):

```json
{
  "mcpServers": {
    "jobpipe": {
      "command": "/home/miles/Documents/job-pipe/venv/bin/python",
      "args": ["-m", "jobpipe.mcp_server", "--workspace", "/home/miles/Documents/job-pipe"]
    }
  }
}
```

In Antigravity IDE, use **MCP Servers → Manage MCP Servers → View raw config**.
Its documented workspace location is `.agents/mcp_config.json`; its documented
global location is `~/.gemini/config/mcp_config.json`. Use the location exposed by
your installed client. Refresh the MCP server list after editing. Other clients
can use the same executable and argument array in their stdio-server settings.

## Connection and acceptance tests

Install development dependencies and run isolated tests without live ATS access:

```fish
./venv/bin/python -m pip install -e '.[dev]'
./venv/bin/python -m pytest tests/test_mcp.py -q
env JOBPIPE_VALIDATION_REPORT=/home/miles/Documents/job-pipe/diagnostics/agent-interface-validation ./venv/bin/python -m pytest tests/test_mcp.py -k stdio_acceptance -q
./venv/bin/python -m pytest -q
git diff --check
```

The acceptance tests launch the server from another directory against temporary
workspaces. SDK clients discover tools, retrieve goals, search/inspect five jobs,
compare explanations, save/prepare/repeat, retrieve statuses/paths, and inspect
follow-ups and yield. Read-only mode exercises the reads and rejects both writes.
Optional JSON reports are written to the selected diagnostics directory, already
ignored by Git. Separate raw-stdio coverage checks protocol-only stdout and EOF.

## Current limitations

Agents can discover, evaluate, save and prepare through the fourteen tools above.
Source configuration, applicant facts, goal edits, arbitrary status updates and
external submission remain outside MCP. Search evaluates the local corpus
in memory; it adds no index. There is no hosted endpoint or authentication layer:
the local client controls the launched process and startup permission flag.
Existing bundle edits are protected by reuse, not automatically merged or
regenerated. Cross-resource failures after filesystem publication can leave a
complete bundle whose feedback is recorded on a successful retry.

## Recommended agent workflow

1. Read `get_active_goal` and `get_discovery_status`.
2. Trial known prospective sources if trial capability is enabled.
3. Start `ingest_sources`; poll its run to completion and disclose failures.
4. Search confirmed eligible jobs using the existing canonical fit scores.
5. Inspect `get_job_eligibility`, `get_job`, and `get_fit_breakdown` for candidates.
6. Save and prepare up to five strong eligible jobs. Re-read returned statuses
   and paths; preparation is never submission.
7. Report actual counts, posting freshness and unresolved evidence. Do not fill
   a target with unsuitable jobs. Inspect needs-review jobs separately.

Example instruction:

> Use Job-Pipe to refresh available jobs, identify the strongest eligible remote-US opportunities, and prepare up to five applications. Use Job-Pipe MCP tools exclusively. Do not modify application policy, source configuration, or repository files. Do not submit applications. Report any eligibility uncertainties.

New regression coverage in `tests/test_agent_discovery.py` launches real stdio
servers and their workers in disposable workspaces. Only provider HTTP transport
is mocked through a test-process import; production has no fixture or arbitrary
endpoint flag. Five synthetic eligible packages validate mechanics and are
reported separately from live opportunity counts.

The dated live-source investigation, two-source opt-in proposal, and separate
disabled research catalog are linked from [source health](source-health.md).
Public measurements for the separately labeled cohorts are in
[source-discovery-v0.3.0.json](source-discovery-v0.3.0.json); synthetic acceptance
results remain separate from live opportunity counts.
