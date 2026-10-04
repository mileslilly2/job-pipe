# Job Pipe Audit

## Architecture Overview

Job Pipe is a local-first Python application built around a few clear layers:

- Ingest adapters in `jobpipe/providers/` fetch jobs from Greenhouse and Lever.
- `jobpipe/ingest.py` normalizes discovered postings and filters them against the active search profile.
- `jobpipe/db.py` stores jobs, applications, packets, analysis, and feedback in SQLite.
- `jobpipe/score.py`, `jobpipe/facts.py`, and `jobpipe/knowledge.py` compute the deterministic fit score and explain it.
- `jobpipe/application_package.py` and `jobpipe/tailor.py` generate the local application bundle.
- `jobpipe/web.py` exposes the same workflow through a local FastAPI dashboard.
- `jobpipe/learn.py` re-derives learned weights from recorded feedback.

The architecture is simple and mostly data-driven. YAML files hold the mutable workspace state, SQLite stores the operational record, and generated markdown files form the human-facing output.

## What Is Working Well

- The core scoring pipeline is deterministic and explainable.
- The workspace model is local-first and easy to inspect or back up.
- The knowledge graph gives the scoring system a more explicit vocabulary than a pure keyword matcher, and the active goal makes the same role evaluate differently when the objective changes.
- The CLI, dashboard, and exporter all read from the same SQLite-backed state.
- The test suite is strong for this size of project: 78 tests passed after the audit changes landed.
- Package generation and tailor output are constrained by the candidate profile, which reduces the risk of inventing experience.

## Remediated Findings

These were confirmed during the audit and fixed in the same pass:

- Package bundle paths now include the job id, so same-company/title jobs no longer overwrite each other.
- `jobpipe feedback` now rejects unsupported actions instead of recording arbitrary values.
- The web `/jobs` filter now preserves explicit `archived` and `ignored` queries instead of hiding them behind the default active-only filter.

## Remaining Risks and Likely Future Bugs

- The workspace is bound to the current working directory. That is deliberate, but it is fragile when commands are run from the wrong place.
- Application status, review status, and analysis state are related but separate concepts. The code is consistent today, but future changes need to update all three layers together.
- Public Greenhouse and Lever endpoints remain an external dependency. Upstream format changes or rate limiting can break ingest.
- The test suite still emits a FastAPI/Starlette `TestClient` deprecation warning through `httpx`.
- Generated bundles are filesystem artifacts with no migration layer; changing their naming or layout requires compatibility care.

## Missing Tests

Coverage is good, but a few areas still deserve attention:

- Network failure paths for provider fetchers are only lightly exercised.
- More explicit regression coverage for dashboard status transitions and `open-*` helpers would reduce the chance of UI drift.
- There is still room for more tests around malformed YAML in the mutable workspace files.
- Performance-related behavior on a much larger job corpus is not covered by tests.

## Data Model and Knowledge-Representation Weaknesses

- `data/knowledge.yaml` is flexible, but it is also easy to overfit to one candidate profile.
- Learned weights are stored separately from the concept graph, so the effective score is distributed across multiple files.
- Role-family classification still depends on hand-maintained phrase lists and can be brittle for unusual titles.
- The current model favors deterministic explainability over statistical robustness: goals, preference tiers, knowledge matches, and learned weights are all local data or deterministic rules and should be documented whenever weights change.

## CLI UX Issues

- The command surface is broad, which is useful, but the mental model is split between review, application, tracker, and dashboard commands.
- Some commands have overlapping behavior (`save`, `package`, `apply-flow`, `status`), so help text and docs need to stay current.
- The package bundle directory is now job-id-suffixed, which is safer but slightly less human-guessable than the old layout.
- Feedback commands accept a compact reason/note format, but there is no structured reason-code schema yet.

## Error Handling Issues

- Provider fetch failures are caught in ingest, but the user still gets a coarse error message rather than provider-specific recovery advice.
- `jobpipe feedback` previously accepted arbitrary actions; this is now fixed, but it shows why input validation should stay close to the command boundary.
- Several helpers still rely on filesystem presence and assume the workspace has been initialized correctly.

## Performance Concerns

- Several read-heavy commands recompute scores for every job on each run and reload learned weights/profile data repeatedly.
- This is acceptable at the current scale, but the repeated scans will get slower as the job table grows.
- The dashboard decorates jobs on each request, so large workspaces may benefit from caching or pagination later.

## Packaging and Deployment Problems

- The project is packaged as an editable/local Python app, not as a standalone binary.
- Runtime behavior assumes the current working directory is the workspace root.
- Generated artifacts live beside the workspace rather than in a managed output store.
- The distribution currently relies on bundled YAML/template package data being kept in sync with the root workspace files.

## Security and Privacy Concerns

- Job data, candidate profile data, notes, and learned preferences are stored unencrypted on disk.
- The browser helpers can open URLs and copy generated content to the clipboard, so users should treat the workspace as sensitive.
- Ingest relies on public job-board endpoints and will transmit the requested URLs to those providers.
- The app does not attempt to automate submission or bypass anti-bot mechanisms, which is the right choice for safety.

## Highest-Value Next Features

1. Add more provider adapters or import paths so the ingest layer can cover more jobs with the same workflow.
2. Expand the analytics and follow-up workflow so applied jobs are easier to manage over time.
3. Add stronger schema validation and structured reason codes for feedback and status changes.
4. Add pagination or cached views for the dashboard and CLI list commands if the workspace grows substantially.
5. Continue expanding the knowledge graph and role packs for more job families and more nuanced exclusions.

## Recommended Implementation Order

1. Strengthen ingest and source-management coverage for more providers and more failure paths.
2. Improve analytics and follow-up planning so the tracker becomes more action-oriented.
3. Harden the mutable data files with schema validation and migration helpers.
4. Add lightweight performance caching only if the workspace size starts to make the current scans painful.
5. Expand role packs and knowledge graph entries after the core workflow stays stable.

## Audit Notes

- The audit was performed against the repository state that already included the knowledge-graph scoring work.
- The three safest, highest-leverage issues identified during the audit were implemented immediately and are now covered by regression tests.
