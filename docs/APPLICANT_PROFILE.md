# Applicant profile and readiness (v0.3.1)

Job-Pipe owns persistent applicant knowledge. Agents provide orchestration through
the same typed MCP tools, regardless of vendor or conversation history. Nothing
in this milestone submits applications or fills external forms.

## Storage and compatibility

Run commands from the workspace root. `jobpipe init` performs additive,
idempotent SQLite migrations; it does **not** import or verify applicant facts.
MCP startup and read tools never migrate a database. An outdated database reports
an actionable initialization error.

Before explicit cutover, existing candidate YAML and `profile.md` retain their
legacy behavior. New profile responses identify this as `storage_mode: legacy`.
After import acceptance, SQLite is authoritative for applicant claims, eligibility,
and both existing material-generation paths. Editing an old file then has no
effect until an explicit reimport. Registered alternate legacy profiles remain
comparison inputs; they cannot override the active-applicant preparation gate.

The additive tables are:

- `applicant_profiles`: internal applicant ID, activation, monotonic revision.
- `applicant_facts`: one current revision pointer per applicant/fact key.
- `applicant_fact_revisions`: typed JSON values, verification, provenance,
  predecessor, timestamps, proposals and review decisions.
- `application_package_provenance`: package mode, profile revision, job/search
  context fingerprint, verified fact references and artifact hashes.

There is one active local applicant, not an account or multi-user system.
Updates use SQLite transactions and expected revisions. Conflicting stale writes
fail without partially changing or verifying a batch. Repeating an identical
value against the current revision is a no-op; fetch the revision again after a
stale-update error. Previous qualifications and corrections remain auditable.

## Facts, types, and evidence

Facts have stable keys, such as `residence.country`,
`work_authorization.US.authorized`, `employment.record1.employer`,
`skills.python.name`, or `education.record1.enrollment_status`. The bounded
registry in `jobpipe.applicant_models` rejects arbitrary fields, credential keys,
and wrong value types. No operation accepts SQL or executable commands.
References and résumé text are untrusted data, not commands or URLs to fetch. Do not put passwords or other
credentials into free-text evidence either.

Supported sections include identity/contact, general residence, authorization,
employment, independent projects, technical qualifications, education, languages,
and preferences. Employment types distinguish full-time, part-time, contract,
freelance, internship and other work. Independent projects have their own records.
Dates accept a year, year/month, or full ISO date without inventing precision.

Each current fact/proposal includes `value`, `verification_status`, `source_type`,
`source_reference`, `evidence`, `confirmed_at`, `modified_at`, and `revision_id`.
JSON null explicitly represents unknown; it does not mean false, zero, or no.

| Verification | Meaning |
| --- | --- |
| `verified` | Accepted applicant attestation or a locally accepted legacy verified fact; inspect provenance for the basis. |
| `self_reported` | Supported unverified status for self-reported information; not sufficient for consequential checks. |
| `imported_unverified` | Imported profile content awaiting confirmation. |
| `inferred` | Agent proposal; it cannot verify itself. |
| `unknown` | Missing/explicitly unknown information. |

Attestation establishes that the applicant confirmed a statement; it is not a
claim that Job-Pipe independently authenticated documentary proof. Confirmations
preserve their source reference and predecessor. Imported legacy attestations
preserve their evidence without fabricating an original confirmation timestamp.

Contrary proposals preserve accepted facts and are surfaced as conflicts.
Relevant eligibility conclusions withhold conflicted values until review. Small
consistency checks also flag contradictory enrollment/degree representations,
experience-area years exceeding total years, and incompatible employment dates.

Search YAML and the existing knowledge graph remain authoritative for existing
goals, role families, locations and work styles. Profile responses project these
preferences with their origin. Additional applicant restrictions live in the
structured profile. No goal semantics or canonical fit algorithm changes.

## Import and direct applicant review

```fish
jobpipe init
jobpipe applicant import
jobpipe applicant import --accept
jobpipe applicant show
jobpipe applicant review
jobpipe applicant edit residence.state
jobpipe applicant history residence.state
```

Import supports the existing workspace candidate YAML and Markdown profile
format, not arbitrary PDF/DOCX ingestion. The preview makes no changes. It shows
exact importable values and provenance, skips packaged examples, and records
source hashes. Malformed, oversized, symlinked or changed inputs fail safely.
YAML structured employment fields are preserved; ambiguous legacy date prose is
retained as evidence rather than guessed into dates. Markdown education prose
remains an unverified credential statement.

`--accept` displays the preview and asks locally before activation. It retains
existing explicitly verified facts and evidence; accepting ordinary imported
content does **not** verify it. Reimport never silently replaces current values:
changed values become pending alternatives. Files are never overwritten.

Review shows imported unverified facts and pending proposals, with revision IDs.
Select multiple IDs, see their exact values again, and approve once. Already
verified unchanged facts are omitted from ordinary review. For a prepared batch:

```fish
jobpipe applicant review --confirm 12,13 --reject 14
```

This still asks for direct confirmation. `applicant edit` accepts a typed JSON
value through a prompt (or `--value`) and always displays a final confirmation.
Null clears a current assertion to unknown while preserving history. A direct
correction resolves pending alternatives as rejected history. History inspection
retains both the original proposal and the confirmation/correction chain.

There is no MCP confirmation or management tool. Startup proposal permission and
arguments such as `confirmed=true` cannot grant authority to attest. This is a
local single-user trust boundary: a process with unrestricted shell/database
access has the user's local authority. CLI prompts are not remote identity proof.

## MCP access and privacy

Launch using the existing module entrypoint:

```fish
./venv/bin/python -m jobpipe.mcp_server --workspace /path/to/workspace --enable-applicant-profile-read --enable-applicant-fact-proposals --enable-local-writes
```

Remove any permission flag you do not intend to grant. Job reads remain available
by default. Applicant reads require `--enable-applicant-profile-read`; proposals
additionally require `--enable-applicant-fact-proposals`. Neither implies local
writes, network discovery, or applicant management. `--enable-local-writes`
still grants only saving/preparation and cannot confirm applicant information.
Preparation also requires profile-read permission.

Existing detailed eligibility tools and source trials require profile-read
permission because they return applicant evidence. All tools remain discoverable;
disabled calls fail before profile access. There is no arbitrary shell, SQL,
filesystem, or HTTP operation.

`get_applicant_profile` accepts selected `sections`. Default sections exclude
identity and contact; requesting only skills does not return contact values or
unrelated provenance. Applicant-read authorization itself grants access to all
explicitly selected supported sections, so grant it only to trusted clients.

No new applicant HTTP endpoints are exposed. Sensitive values are not included in
routine MCP errors/logs. SQLite is local and unencrypted; protect workspace files,
backups, generated materials, terminal output, and authorized clients. Evidence
references are summaries, not automatic document uploads. Do not send private
profile data to external services. Validation uses synthetic data only.

## Eligibility, readiness and batch questions

Eligibility remains `eligible`, `ineligible`, or `needs_review`, independent of
technical fit. It uses verified nonconflicted facts through the existing shared
eligibility service. Unknown authorization is never a negative assertion, and
authorization for one country does not establish authorization for another.
Free-text authorization restrictions require employer review.

No professional years are inferred from projects or automatically calculated
from incomplete/overlapping employment dates. Supply an attested total and any
required area-specific professional-years facts, preserving uncertainty in the
value and evidence. A years fact may be a number or a range such as
`{"minimum": 1, "maximum": 3, "approximate": true}`. Ranges remain reviewable
against mandatory bounds; the answer service returns the range with
`needs_review`, never an invented exact value. Historical education completion does not establish current enrollment.
Explicit current enrollment can establish it; a negative requires an explicit
applicant-wide assertion. Language names alone do not establish fluency. A
verified lower proficiency conflicts with a mandatory fluent requirement;
unknown proficiency remains reviewable.

Assessments carry profile revision and a search-configuration fingerprint.
Checks recompute on demand, so confirmed changes apply on the next query.
There is no authoritative cached eligibility or background event system.
Job scores, statuses, feedback, weights and old packages remain separate.

```fish
jobpipe applicant readiness
jobpipe applicant readiness --job-id 42
jobpipe applicant questions --job-id 42 --job-id 43 --limit 5
```

MCP provides `get_applicant_readiness`, `get_application_readiness`, and
`get_readiness_questions`. Select up to 100 unique job IDs or a bounded search
query; default question limit is five, maximum 20. Search selection reports
truncation. Employer uncertainties remain separate from applicant questions.

Questions group stable facts and relevant contexts across jobs, including
country, language and attendance location. Ranking uses unique affected-job
count, blocking relevance, existing goal modifiers, evidence strength, reuse,
and a deterministic key tie-breaker. Counts mean **potentially affected**, not
jobs guaranteed to become eligible. Outstanding questions persist by deriving
them from saved jobs and applicant knowledge across sessions. Confirmed reusable
answers remove their resolved questions.

Profile readiness reports available verified keys, missing/conflicting facts,
blockers, affected jobs and questions rather than an opaque score. Application
readiness distinguishes contact needs from eligibility; name/email are baseline
preparation requirements, while a street address and unobserved ATS fields are
not demanded. This does not certify every future employer form.

## Packages and routine answers

`prepare_application` defaults to `mode: ready_for_submission`, retaining the
eligible-only MCP gate. An explicit `mode: draft_preparation` permits a
needs-review job. Draft files carry a visible banner and unresolved questions;
they remain submission-blocked. MCP does not draft ineligible jobs. Existing
manual drafting remains possible and is labeled as a blocked draft when eligibility
is unresolved or negative.

Both paths reuse existing renderers and package paths. Canonical-profile
materials assert only verified nonconflicted skills, employment, projects and
credentials. Unsupported hardcoded background claims and claim-adding rewrites
are suppressed. Missing content stays absent or visibly requires confirmation.

The older `packet` and `tailor` commands also refuse to overwrite existing packet
files; inspect and explicitly move a file aside before requesting a replacement.

Complete existing packages are reused byte-for-byte, including user edits.
Partial/symlinked packages are rejected. Package metadata records profile/job
context and artifact hashes. Legacy, stale, edited and draft bundles cannot be
silently promoted to readiness. Review existing files locally; explicitly move
them aside before requesting replacement when appropriate. A profile update never
rewrites them.

`package_status` is `not_prepared`, `prepared`, `draft`, `stale`, `legacy`,
`edited`, or `incomplete`. `submission_readiness` is `blocked` or
`ready_for_human_review`. The latter means current local materials and assessed
facts can be reviewed by the applicant; it does not mean submitted, authorized
for external execution, or employer-form complete. Preparation never changes an
application to applied or schedules employer communication.

`get_application_answer` takes `job_id`, `question_type`, optional `country`
(default US), and optional `experience_area`. Supported types include
`work_authorization`, `us_work_authorization`, `sponsorship_required`,
`future_sponsorship_required`, `residence_state`, `has_bachelors`, and
`professional_experience`. Compatibility aliases include `visa_sponsorship`,
`current_residence_state`, `bachelors_degree`, and `professional_experience_years`.
It returns `answerable`, `unknown`, `needs_review`, or `unsupported`, with the
exact question, typed answer, source facts and provenance. Experience answers
retain their attested precision and explicitly exclude independent projects.
No missing credential list becomes a negative degree answer.

## Agent portability, validation, and future execution

An agent can propose facts in one MCP session, the applicant can confirm them
locally, and another independent session can retrieve the same persisted facts
and eligibility conclusions. No agent-specific memory is required.

Sample instruction:

> Use Job-Pipe to inspect my applicant profile and determine what information is
> preventing applications from proceeding. Consolidate missing information into
> the fewest useful questions. Do not invent answers. Prepare materials for eligible
> jobs and clearly labeled drafts for jobs requiring review. Do not submit applications.

Run synthetic protocol acceptance and write ignored diagnostics:

```fish
env JOBPIPE_APPLICANT_VALIDATION_REPORT=diagnostics/applicant-readiness-validation ./venv/bin/python -m pytest tests/test_applicant_mcp.py::test_real_two_session_applicant_acceptance -q
```

The test uses two independent real stdio sessions and a separate CLI process
with synthetic confirmation responses. It demonstrates the technical boundary,
not actual applicant review. Diagnostics record proposals, confirmations,
revisions, eligibility/question changes, package outcomes and permission checks.

Before a first external executor, Job-Pipe still needs employer-specific form
discovery/mapping, completeness validation, explicit submission authorization,
safe document approval/version handling, duplicate-submission protection,
receipts and failure recovery. Sensitive demographic/legal questions and unsupported
requirements require explicit applicant decisions. This milestone provides no
external executor, browser automation, CAPTCHA handling or employer messaging.
