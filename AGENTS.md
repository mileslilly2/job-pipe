# AGENTS.md

## Project Purpose
Job Pipe is a local-first CLI and web dashboard for repetitive job application prep. It ingests job postings, scores fit deterministically, tracks review/apply status, and generates local application bundles without relying on a hosted service.

## Repository Layout
- `jobpipe/` core package, CLI, scoring, ingest, tracker, package generation, and dashboard code.
- `jobpipe/providers/` provider adapters for Greenhouse and Lever.
- `data/` workspace state: job sources, search profiles, knowledge graph, learned weights, and candidate profile.
- `role_packs/` role-specific prompt and tailoring packs.
- `templates/` markdown/Jinja templates for generated packets and notes.
- `tests/` pytest suite and fixtures.
- `applications/` generated application bundles.
- `output/` CSV exports and other derived outputs.
- `docs/` written audit and supporting documentation.

## Development Commands
Fish-friendly setup from the repository root:

```fish
python3 -m venv venv
source venv/bin/activate.fish
pip install -e ".[dev]"
jobpipe init
```

Useful day-to-day commands:
- `jobpipe ingest`
- `jobpipe review`
- `jobpipe batch-review`
- `jobpipe package <job-id>`
- `jobpipe apply-flow <job-id>`
- `jobpipe web --no-open`
- `jobpipe analytics`
- `jobpipe feedback-report`

## Test Commands
- `./venv/bin/python -m pytest -q`
- `./venv/bin/python -m pytest tests/test_web.py -q`
- `./venv/bin/python -m pytest tests/test_package_generation.py tests/test_deterministic_learning.py -q`

## Coding Conventions
- Target Python 3.11+ and keep type annotations on public helpers and data structures.
- Prefer deterministic, explainable behavior over implicit heuristics.
- Keep CLI output user-facing and concise; favor rich tables and explicit status messages.
- Preserve local-first behavior. New features should work without an external account or hosted backend.
- Keep file formats stable unless a migration or compatibility note is added with tests.
- Use fish-compatible command examples in documentation.

## Data and Knowledge-Representation Conventions
- `data/job_sources.yaml` is a YAML list of sources. An empty list or missing file can trigger built-in starter source fallback for ingest.
- `data/search_profiles.yaml` is a YAML list of named profiles. The default profile name is `miles`.
- `data/source_packs.yaml` is a YAML mapping of pack name to source list.
- `data/knowledge.yaml` is the deterministic knowledge graph used by fit scoring. It contains `match_weights`, `concepts`, and `profiles`.
- `data/learned_weights.yaml` is generated from feedback and should be treated as derived state.
- `data/profiles/candidate.yaml` is the editable candidate profile used for tailoring and packages.
- `profile.md` is the base resume/profile content used when generating application bundles.
- Generated bundles live under `applications/<company>-<title>-job-<id>/` so same-company/title jobs do not overwrite each other.
- Keep alias/canonical names normalized in YAML. Existing loaders lower-case and strip values.

## CLI Usage Expectations
- Run commands from the repository root so `data/`, `output/`, and `jobpipe.db` resolve correctly.
- `jobpipe init` seeds workspace data and the SQLite database.
- `jobpipe ingest` may use packaged starter sources when `data/job_sources.yaml` is missing or empty.
- `jobpipe package` requires a saved job and writes the application bundle locally.
- `jobpipe apply-flow` is manual by design and should not auto-submit forms or bypass CAPTCHAs.
- `jobpipe feedback` accepts only `saved`, `skipped`, `packaged`, or `applied`.
- `jobpipe web` listens on `127.0.0.1:8765` unless overridden.

## Rules for Future Coding Agents
- Inspect the current worktree first and preserve user changes you did not make.
- Add or update tests for every behavior change.
- Prefer low-risk, incremental changes over broad rewrites.
- Do not change schema, scoring, or packaging behavior without updating docs and regression tests.
- Avoid destructive git commands and do not assume a clean tree.
- If a change touches ingest, scoring, or tracker state, verify the CLI and web views still agree on status semantics.

## Known Risks and Fragile Areas
- The workspace is cwd-bound, so commands depend on where they are launched.
- The app combines review status, application status, and analysis state; these layers are easy to drift apart if a change updates only one of them.
- Provider fetchers depend on public Greenhouse and Lever endpoints and can fail due to upstream changes.
- The knowledge graph and learned weights are mutable user data; malformed edits can change ranking behavior quickly.
- Clipboard and browser helpers depend on local OS tooling that may be absent.
- The test suite currently emits a FastAPI/Starlette `TestClient` deprecation warning through `httpx`.

## Safe Change Checklist
- Prefer a targeted edit with a regression test over a cross-cutting rewrite.
- Re-run the smallest relevant test file first, then the full suite.
- When changing package artifacts, confirm the output paths are unique and stable.
- When changing data files, keep the existing YAML shape and update docs together with code.
- When changing CLI prompts or workflow commands, verify the fish examples and user-facing help text still make sense.
