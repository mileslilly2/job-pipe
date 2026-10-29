# Source health note

Verified 2026-09-05. The configured Greenhouse board tokens `linear`, `notion`, `openai`, and `huggingface` returned HTTP 404 from the public boards API. Their official careers pages still exist for Linear, Notion, and OpenAI, which indicates an ATS migration or changed board identifier rather than a company closure. Hugging Face has no verified public Greenhouse careers board in the checked sources. These entries are disabled (not deleted) in the local and packaged source lists pending dedicated ATS adapters or confirmed replacement board identifiers.

The audit intentionally does not scrape LinkedIn or Indeed. Re-enable a source only after its public ATS endpoint is verified.

## Measuring discovery yield

Run these commands from the workspace root, with the project environment active:

```fish
source venv/bin/activate.fish
python3 -m jobpipe.cli ingest --diagnostics
python3 -m jobpipe.cli sources yield
python3 -m jobpipe.cli sources yield --history
python3 -m jobpipe.cli sources trial greenhouse:stripe
python3 -m jobpipe.cli sources trial gitlab
python3 -m jobpipe.cli sources trial datadog
python3 -m jobpipe.cli sources trial coinbase
python3 -m jobpipe.cli sources trial lever:mistral
```

Discovery yield is **passed / fetched** under the existing source/profile filters.
It measures eligibility, not a human judgment that a job is useful. A successful
empty fetch has yield `N/A`; a successful nonempty fetch with no passes has yield
`0.00%`. Failed or absent fetch measurements show `--` and `N/A`, with an explicit
status. Failures must not be interpreted as evidence of low yield.

Normal ingest records one SQLite `ingest_runs` row and per-source
`source_run_stats` aggregates automatically, whether or not `--diagnostics` is
used. Runs retain timestamps, profile/goal and configuration snapshots, fetch
status/error, and fetched/passed/rejected/inserted/updated counts. Rejected job
descriptions are not stored for these metrics. Completed ingests may include
provider failures; interrupted/aborted runs are excluded from latest-run and
history measurements. An updated count means an existing accepted job was
upserted, not necessarily that its content changed.

`sources yield` separates the **latest completed ingest** from the **current
canonical corpus**. A source can have no fetched jobs in that run and still have
active jobs discovered earlier. `--history` shows each completed run separately;
current corpus counts are displayed once, never summed across runs. Historical
measurements before this schema was introduced are unavailable.

The corpus table uses the dashboard's current workflow buckets: Saved means
currently saved and not already in another dashboard bucket; Packaged means the
current packaged bucket, determined by existing bundle artifacts; Applied means
currently applied. Advancing to interview/offer does not remain in Applied.
These are current states, not cumulative milestone totals. The automatic
application-status `saved` row created by ingest does not make a new job saved.
Active excludes rejected, skipped, archived, and ignored; applied, interview,
and offer jobs remain active. Active is a workflow definition, not a guarantee
that an upstream posting is still open.

**Save % = current saved / all retained canonical jobs for that source. Apply % =
current applied / all retained canonical jobs for that source.** The denominator
includes inactive jobs. Rates with no retained jobs are `N/A`. These are corpus
state ratios, not conversion rates for the latest ingest cohort.

Source identity is the existing normalized `provider:board-token` or
`provider:account` label, with company-slug fallback when the provider token is
omitted. For example, the current Mistral catalog identifier is `lever:mistral`.
Accepted jobs retain their first exact `jobs.source_id`; manual jobs are not
silently assigned an ATS source. Legacy jobs without this field are attributed
in reports only when their normalized provider/company uniquely matches a
configured source, pack member, or recorded source. Ambiguous/unmatched jobs
appear as explicit unattributed buckets, are included in employer totals, and
are excluded from the count of known sources represented.

Database initialization adds these tables, the nullable attribution column, and
indexes idempotently. No historical runs are fabricated and no legacy source
attribution is backfilled by reporting. The existing job deduplication key,
statuses, feedback, scoring, classification, and filtering rules are preserved.
`sources yield` opens existing databases read-only and does not initialize or
migrate them. Use normal `ingest` or `init` to migrate an older workspace.

## Source trials

`sources trial <source-name>` fetches and normalizes through the real provider
adapter and uses the same discovery evaluator as normal ingest. It reports
fetched/passed/rejected/yield, passing discovery role and seniority distributions,
top rejection reasons, and up to five passing examples. Distributions use the
discovery filter's classifications, which are distinct from canonical persisted
classifications. Rejection reasons may overlap for a single rejected job.
Examples and count ties are sorted deterministically, rather than ranked by a
new source score. Provider failures return a nonzero exit status with unavailable
measurements.

Trials perform **zero database or configuration writes**, including on failure.
They create no jobs, classifications, feedback, run statistics, files, or learned
weights, and work without a database. An explicitly selected disabled source is
enabled only in memory for that trial. Company/name/token aliases remain valid;
ambiguous names require the full source identifier.

Workspace source definitions take precedence. Missing/empty source lists use
the existing packaged catalog fallback. Sources present only in source packs
can also be tested without importing them. When packs contain different rule
definitions for the same source, the first definition in alphabetical pack order
is used and its pack name is printed. Trial configuration remains unchanged.
Trial output is not persisted automatically; capture output explicitly when
recording an experiment. No arbitrary URL crawling, LinkedIn/Indeed scraping, or
external LLM is involved.

## Concentration and packs

Yield reporting and `sources audit` show active employer/source counts, largest
employer/source shares, and the top five of each. An employer exceeding 25% of
active jobs produces a diagnostic warning. Concentration does not suppress jobs
or affect filtering. Employer names are grouped by stripped, case-insensitive
name; distinct corporate aliases are not merged heuristically.

The pack table uses real current catalog memberships. It reports unique members,
successfully measured coverage, and summed passed / summed fetched from the
latest run. Missing/disabled/failed members are unmeasured, so partial coverage
is explicit. Packs overlap and their totals must not be added together. These
are membership aggregates of normal ingest measurements, not full pack trials:
pack-specific source filters can differ from the workspace's ingest definitions.

Low-yield sources are not automatically disabled. Source volume, provider health,
changing posting inventories, and the candidate's existing discovery rules all
affect yield. The next expansion step is a bounded set of catalog source trials,
followed by human review of passing jobs and repeat measurements before changing
the enabled source list. This milestone does not introduce a composite source
score or change discovery policy.
