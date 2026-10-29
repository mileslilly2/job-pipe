# Job-Pipe agent interface (v0.2.0)

Job-Pipe provides nine local MCP tools over stdio. An agent discovers tools and
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

The workspace defaults to the startup working directory. An explicit workspace
must exist. It selects the existing `jobpipe.db`, `data/` configuration,
`profile.md`, and `applications/` directory together. There is no independent
database path override or tool-supplied destination path.

Startup, initialization, and discovery need no network or database. Database
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
There is no custom MCP framing, second classifier, scoring formula, eligibility
policy, source-yield algorithm, or discovery index.

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

All nine tools remain discoverable in both modes. Read-only mode rejects both
write operations with `permission_denied` before database access. Tool arguments
cannot grant capabilities. `--enable-local-writes` authorizes only saving an
existing review record and preparing its local application bundle.

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
`application_preparation_failure`, or `internal_error`. Tool failures set MCP's
`isError` flag. Normal responses contain no traceback; diagnostic exceptions are
logged to stderr. Stdout is reserved for MCP. EOF and interruption shut down the
server without starting background services.

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

Only the nine tools above are exposed. Agents operate on existing canonical jobs;
ingest, source trials, goal edits, arbitrary status updates, and external
submission remain human-interface operations. Search evaluates the local corpus
in memory; it adds no index. There is no hosted endpoint or authentication layer:
the local client controls the launched process and startup permission flag.
Existing bundle edits are protected by reuse, not automatically merged or
regenerated. Cross-resource failures after filesystem publication can leave a
complete bundle whose feedback is recorded on a successful retry.
