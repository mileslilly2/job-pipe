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
It measures discovery-filter acceptance, independently of application eligibility
and any human judgment that a job is useful. A successful
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
the enabled source list. There is no composite source score. v0.3.0 narrowly
corrects built-in keyword exclusions that contradicted explicitly targeted
support/help-desk roles; explicit source/profile exclusions remain authoritative.
Canonical scoring is unchanged.

## v0.3.0 enabled-source refresh and candidate cohort — 2026-10-29

The investigation used real stdio MCP calls in a disposable workspace containing
copies of the active profile/configuration. Production database, enabled sources,
learned weights and existing application materials were hash-checked unchanged.
Normal automated tests mock provider transport and require no live APIs.

### Enabled-source refresh

The refresh checked 10 enabled sources: 9 fetched successfully and Postman's
configured Greenhouse board failed with HTTP 404. It fetched **2,594**, accepted
**10**, inserted **10**, updated **0**, and rejected **2,584**. Discovery yield was
**10 / 2,594 = 0.386%**. The successful empty Mistral response contributes zero
fetched jobs; it does not establish healthy current coverage.

The resulting canonical corpus contained **0 eligible**, **7 needs review** and
**3 ineligible** jobs. There were three employers: Stripe 5/10, Coinbase 3/10 and
Datadog 2/10. Stripe's 50% share triggered the existing concentration warning.
No live packages were prepared. There are no representative confirmed-eligible
opportunities to report; three technically high-scoring review jobs are not
substitutes for confirmed eligibility.

### Fourteen non-persistent trials

Counts below are separate from the enabled-source refresh. Eligibility columns
partition discovery passes. Failed/time-limited measurements are unavailable,
never zeros. Each trial used its configured/catalog definition and the same
production adapters and filters.

| Source | Fetch outcome | Fetched | Passed | Discovery yield | Eligible | Review | Ineligible |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `greenhouse:posthog` | HTTP 404 | — | — | — | — | — | — |
| `greenhouse:vercel` | Success | 92 | 0 | 0% | 0 | 0 | 0 |
| `greenhouse:postman` | HTTP 404 | — | — | — | — | — | — |
| `lever:mistral` | Success, empty | 0 | 0 | N/A | 0 | 0 | 0 |
| `greenhouse:linear` | HTTP 404 | — | — | — | — | — | — |
| `greenhouse:notion` | HTTP 404 | — | — | — | — | — | — |
| `greenhouse:openai` | HTTP 404 | — | — | — | — | — | — |
| `greenhouse:huggingface` | HTTP 404 | — | — | — | — | — | — |
| `greenhouse:customerio` | Success | 25 | 0 | 0% | 0 | 0 | 0 |
| `greenhouse:grafanalabs` | Success | 131 | 0 | 0% | 0 | 0 | 0 |
| `greenhouse:canonical` | Success | 314 | 6 | 1.91% | 0 | 4 | 2 |
| `greenhouse:testlio` | Success | 16 | 1 | 6.25% | 0 | 1 | 0 |
| `lever:weloglobal` | 30-second deadline | — | — | — | — | — | — |
| `lever:rws` | Success | 156 | 12 | 7.69% | 0 | 2 | 10 |

The seven successfully measured trials fetched **734**, passed **19** and rejected
**715**: yield **19/734 = 2.59%**. Their passes were **0 eligible, 7 review and 12
ineligible**, across three employers. One review record is a future-role pool.
RWS supplied 12/19 passes (63.2%), so volume remains concentrated. These are single
measurements; they do not establish durable source quality or hiring probability.

Of the six new candidate boards, five were successfully measured, fetching 642
and passing 19 (2.96%); Welo Global timed out. Retaining all 19 as useful or
US-eligible would be incorrect.

Verified public feed endpoints for the measured new boards:

- [Customer.io Greenhouse feed](https://boards-api.greenhouse.io/v1/boards/customerio/jobs?content=true)
- [Grafana Labs Greenhouse feed](https://boards-api.greenhouse.io/v1/boards/grafanalabs/jobs?content=true)
- [Canonical Greenhouse feed](https://boards-api.greenhouse.io/v1/boards/canonical/jobs?content=true)
- [Testlio Greenhouse feed](https://boards-api.greenhouse.io/v1/boards/testlio/jobs?content=true)
- [RWS TrainAI Lever feed](https://api.lever.co/v0/postings/rws?mode=json&include=content)

Welo Global's identity is published by its [official recruitment FAQ](https://welocalizetalent.zendesk.com/hc/en-us/articles/26896102574103-Initial-Recruitment-F-A-Q).
Its [Lever board](https://jobs.lever.co/weloglobal/) is verified, but this run did
not obtain a completed production-adapter measurement. It is not in the proposal.

### Posting evidence and readiness

The following are **needs-review examples**, not confirmed suitable applications:

- [Canonical Cloud Support Engineer](https://job-boards.greenhouse.io/canonical/jobs/2456563)
  explicitly permits global remote work. Residence and applicable authorization
  still need verification. Its requirements include strong English, academic
  history and technical support capability; its form requests academic details.
- [Canonical Junior Linux Kernel Engineer](https://job-boards.greenhouse.io/canonical/jobs/5370815)
  describes junior/early-career work, C/Rust skills, prospective/recent graduates
  and international travel. Those qualifications and travel availability are not
  established by the applicant's current placeholder profile.
- [Canonical Software Support Engineer](https://job-boards.greenhouse.io/canonical/jobs/6635593)
  describes global remote work, substantial Linux/open-source troubleshooting and
  international travel. Technical fit and practical constraints need review.
- [RWS Chinese Simplified speech evaluation, USA](https://jobs.lever.co/rws/b6ae34f4-57e5-4bb8-8f29-4c69cf25ecbd)
  explicitly describes US home-based contract work and native-level Chinese
  proficiency. Citizenship does not establish residence or language proficiency.
- [Testlio QA Tester (Future Roles)](https://job-boards.greenhouse.io/testlio/jobs/6953297)
  explicitly solicits interest for future work. It is excluded from the proposed
  pack and cannot establish readiness for a current vacancy.

Posting pages were inspected read-only. Accessibility and a successful feed fetch
do not guarantee that a role remains open. No forms were filled or submitted.
Neither Canonical's hiring burden nor a language-specific RWS role has been
certified suitable for the active `get_employed_fast` goal.

### Twenty-employer careers investigation and adapter dependencies

The bounded research set included the eight initial/catalog diagnostics above
and twelve additional employers. Official evidence identified these dependencies:

| Employer | Current evidence / decision |
| --- | --- |
| PostHog | [Careers](https://posthog.com/careers) and [hiring handbook](https://posthog.com/handbook/people/hiring-process) identify current roles and Ashby use. Old Greenhouse token is 404; a replacement public-feed identity is not assumed. |
| Vercel | [Careers](https://vercel.com/careers) uses Greenhouse; catalog trial succeeded with no passes. |
| Postman | Old token is 404. Its [careers page](https://www.postman.com/company/careers/open-positions/) references Workday; [published Workday posting](https://postman.wd108.myworkdayjobs.com/careers/job/Bangalore-India/Software-Engineer--Fullstack---Collections_JR1000043) confirms the provider. No replacement Greenhouse token was invented. |
| Mistral | [Official careers](https://mistral.ai/careers/) links [Ashby](https://jobs.ashbyhq.com/mistral.ai). Empty legacy Lever response is not evidence of no vacancies. |
| Linear | [Official careers](https://linear.app/careers) links Ashby `Linear`; old disabled Greenhouse token remains untouched. |
| Notion | [Official careers](https://www.notion.com/careers) links Ashby `notion`; old disabled token remains untouched. |
| OpenAI | [Official careers](https://openai.com/careers/search/) links Ashby; direct research-script fetching received 403, while the web reader exposed the official links. No protection was bypassed. |
| Hugging Face | [Official JOIN-US page](https://huggingface.co/JOIN-US) links [Workable](https://apply.workable.com/huggingface/); no adapter added. |
| Automattic | [Official jobs](https://automattic.com/work-with-us/jobs/) was accessible, but a verified public feed identifier was not established; no guessed-token trial. |
| Help Scout | [Official careers](https://www.helpscout.com/company/careers/) links an Ashby future-openings form; adapter dependency, no guaranteed vacancy. |
| Customer.io | [Official careers](https://customer.io/careers/) links Greenhouse `customerio`; successful trial, no passes. |
| Grafana Labs | [Official careers](https://grafana.com/careers/) links [Greenhouse `grafanalabs`](https://job-boards.greenhouse.io/grafanalabs/); successful trial, no passes. |
| Elastic | [Official careers](https://www.elastic.co/careers) links [custom jobs portal](https://jobs.elastic.co/); supported public-feed identity not established. |
| Canonical | [Official role list](https://canonical.com/careers/all) links Greenhouse `canonical`; proposed with explicit requirements/hiring-burden caveats. |
| ModSquad | [Official careers](https://join.modsquad.com/careers/) links Workday; no new adapter added. |
| SupportNinja | [Official careers](https://www.supportninja.com/careers) links region-specific ADP recruitment pages; no new adapter added. |
| Welocalize / Welo Global | Official recruitment FAQ identifies `weloglobal`; bounded trial timed out. |
| RWS TrainAI | [Official RWS careers](https://www.rws.com/about/careers/) links [Lever `rws`](https://jobs.lever.co/rws); proposed for review of language-specific contract opportunities. |
| TELUS Digital | [Official careers](https://www.telusdigital.com/careers) links separate CX, digital and AI-community portals; no supported adapter established. Direct research fetch received 403; no bypass attempted. |
| Testlio | [Official careers](https://www.testlio.com/careers) embeds Greenhouse `testlio`; sole pass was a future-role pool, so not proposed. |

Ashby has the clearest demonstrated future coverage value across several migrated
boards. Implementing it is outside this milestone; none of these dependency notes
grants source changes, credentials, browser automation or submission.

### Opt-in source proposal

`agent_discovery_v030` is delivered in the standalone
[proposed source pack](source-pack-v0.3.0-proposed.yaml), with Canonical and RWS
TrainAI both disabled. Workspace and packaged source catalogs remain unchanged.
To import later, first merge that named mapping into `data/source_packs.yaml`,
preserving its existing entries. Then use the existing idempotent source manager:

```fish
./venv/bin/jobpipe sources trial greenhouse:canonical
./venv/bin/jobpipe sources trial lever:rws
./venv/bin/jobpipe sources import-pack agent_discovery_v030
```

The Canonical/RWS proposal widens measurable coverage, not confirmed eligibility.
Import creates disabled records. Repeat trials and resolve applicant/posting
questions before explicitly enabling a source. It omits failed/unverified boards,
zero-pass boards and the Testlio future-role pool. A separately named disabled
research pack retains additional verified feed definitions for later investigation.
Existing low-yield and migrated entries were not disabled or rewritten. Removing
or disabling an imported record remains an explicit source-management action.

Raw dated careers-page evidence, live MCP responses and stdio acceptance output
are stored locally under `diagnostics/v0.3.0-source-research/` (Git-ignored).
Trial and refresh cohorts must not be added as though they were one ingest run.

<!-- jobpipe-v030-live-evaluation -->
## Supplementary audit cohort — separate measurements

A separate bounded careers investigation produced the following audit cohort.
These measurements are additional to the enabled-source refresh and fourteen
trials above; do not combine overlapping employers or denominators. Twenty-one configured or
cataloged source identifiers were trialed through real stdio MCP in a disposable
workspace. The production database, source configuration, learned weights and
existing application materials retained identical before/after hashes.

Across 15 successful trials, **2,929 fetched / 11 passed = 0.38%
discovery yield**. Six source failures are excluded from that denominator and
remain unknown. Mistral's successful empty Lever response has undefined yield;
its current official careers page points to Ashby. Eligibility among the eleven
passes: **0 eligible, 8 needs review, 3 ineligible**. Revalidation through a fresh
MCP server confirmed these counts and canonical score consistency for all eleven.

| Employer | Identifier | Fetch status | Fetched | Passed discovery | Eligible | Review | Ineligible |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| GitLab | `greenhouse:gitlab` | success | 232 | 0 | 0 | 0 | 0 |
| Stripe | `greenhouse:stripe` | success | 722 | 5 | 0 | 4 | 1 |
| Coinbase | `greenhouse:coinbase` | success | 228 | 3 | 0 | 3 | 0 |
| Postman | `greenhouse:postman` | failed | — | — | — | — | — |
| Mistral AI | `lever:mistral` | success | 0 | 0 | 0 | 0 | 0 |
| Wellfound | `lever:angellist` | success | 24 | 0 | 0 | 0 | 0 |
| Datadog | `greenhouse:datadog` | success | 396 | 2 | 0 | 0 | 2 |
| Figma | `greenhouse:figma` | success | 141 | 0 | 0 | 0 | 0 |
| Linear | `greenhouse:linear` | failed | — | — | — | — | — |
| Notion | `greenhouse:notion` | failed | — | — | — | — | — |
| OpenAI | `greenhouse:openai` | failed | — | — | — | — | — |
| Anthropic | `greenhouse:anthropic` | success | 680 | 0 | 0 | 0 | 0 |
| Scale AI | `greenhouse:scaleai` | success | 171 | 0 | 0 | 0 | 0 |
| Hugging Face | `greenhouse:huggingface` | failed | — | — | — | — | — |
| Customer.io | `greenhouse:customerio` | success | 25 | 0 | 0 | 0 | 0 |
| Grafana Labs | `greenhouse:grafanalabs` | success | 131 | 0 | 0 | 0 | 0 |
| LaunchDarkly | `greenhouse:launchdarkly` | success | 51 | 0 | 0 | 0 | 0 |
| Testlio | `greenhouse:testlio` | success | 16 | 1 | 0 | 1 | 0 |
| Invisible Technologies | `greenhouse:invisibletech` | success | 20 | 0 | 0 | 0 | 0 |
| PostHog | `greenhouse:posthog` | failed | — | — | — | — | — |
| Vercel | `greenhouse:vercel` | success | 92 | 0 | 0 | 0 | 0 |

The subsequent selected-source refresh ingested Stripe, Coinbase, Datadog and
Testlio into disposable state: **1,362 fetched, 11 passed, 11 inserted, 0 updated**,
yield **0.81%**. This is a selected four-source denominator, not the all-source
trial yield above. Four employers are represented; Stripe contributes 5/11
(45.5%). Trials for other enabled sources do not become persistent ingest
measurements, so discovery-status warnings for those sources remain appropriate.

There are **no representative confirmed eligible opportunities**. High-score
review examples include Coinbase Data Engineer Intern (99; hybrid and unresolved
student eligibility) and Stripe Backend Engineer, Developer & End-user Experience
Platform (74; residence, work region, and experience-range questions). Testlio's
one QA Tester pass is explicitly **Future Roles**, not a confirmed vacancy.
The Madrid and Paris Datadog internships and São Paulo Stripe posting are
ineligible under current evidence/preferences. Scores were not adjusted to
produce the eligibility result.

### Provider investigations and bounded candidate research

PostHog's configured Greenhouse endpoint returns 404; its [official handbook](https://posthog.com/handbook/people/hiring-process)
identifies Ashby. That handbook also requires a paid full-day SuperDay; a low-burden
hiring process must not be assumed. Vercel's Greenhouse endpoint is verified and
fetched 92 postings, with no discovery passes. Postman's configured API failure
reproduced as HTTP 404. The additional investigation above identified a published Workday posting.
Workday is unsupported; no replacement Greenhouse identifier is invented.

[Mistral](https://mistral.ai/careers/), [Linear](https://linear.app/careers),
[Notion](https://www.notion.com/careers), and [OpenAI](https://openai.com/careers/search/)
link to Ashby. [Hugging Face](https://huggingface.co/join-us) links to Workable.
Those migrations require adapters; the legacy configured identifiers were not
silently changed or re-enabled. The bounded additional candidates and evidence:

| Employer careers page | ATS evidence observed |
| --- | --- |
| [Automattic](https://automattic.com/work-with-us/) | No verified supported ATS link in inspected page |
| [Customer.io](https://customer.io/careers) | https://job-boards.greenhouse.io/customerio |
| [Grafana Labs](https://grafana.com/careers/) | https://job-boards.greenhouse.io/grafanalabs |
| [Help Scout](https://www.helpscout.com/company/careers/) | https://jobs.ashbyhq.com/helpscout |
| [Hugging Face](https://huggingface.co/join-us) | https://apply.workable.com/huggingface |
| [Invisible Technologies](https://www.invisible.co/careers) | No verified supported ATS link in inspected page |
| [LaunchDarkly](https://launchdarkly.com/careers/) | https://boards.greenhouse.io/launchdarkly |
| [Linear](https://linear.app/careers) | https://jobs.ashbyhq.com/Linear |
| [Mistral AI](https://mistral.ai/careers/) | https://jobs.ashbyhq.com/mistral.ai |
| [Notion](https://www.notion.com/careers) | https://jobs.ashbyhq.com/notion |
| [OpenAI](https://openai.com/careers/) | Ashby via official search page |
| [PostHog](https://posthog.com/careers) | Ashby via official handbook |
| [Postman](https://www.postman.com/company/careers/open-positions/) | No verified supported ATS link in inspected page |
| [QA Wolf](https://www.qawolf.com/careers) | No verified supported ATS link in inspected page |
| [Recharge](https://getrecharge.com/careers/) | https://jobs.ashbyhq.com/recharge |
| [Sentry](https://sentry.io/careers/) | https://jobs.ashbyhq.com/sentry |
| [Testlio](https://testlio.com/careers/) | https://boards.greenhouse.io/embed/job_board?for=testlio |
| [Vercel](https://vercel.com/careers) | https://job-boards.greenhouse.io/vercel |
| [Zapier](https://zapier.com/jobs) | https://jobs.ashbyhq.com/zapier |
| [dbt Labs](https://www.getdbt.com/careers) | No verified supported ATS link in inspected page |

An Ashby adapter is the most concentrated coverage dependency: several requested
sources and the Sentry/Recharge/Zapier/Help Scout candidates now use it. Potential
value is access to developer, support, and operations listings; there are no
production-filter or applicant-eligibility measurements for those unsupported
feeds. Workable is a separate dependency. Neither adapter is implemented here.
No Indeed/LinkedIn scraping, authentication bypass, CAPTCHA bypass or employer
correspondence occurred.

### Separate disabled research catalog

This artifact is distinct from the two-source `agent_discovery_v030` proposal
above. It supplies verified board definitions for further trials, not activation.

[The proposed research pack](source-pack-v0.3.0-proposed.yaml) contains five verified
Greenhouse endpoints: Customer.io, Grafana Labs, LaunchDarkly, Testlio, and Invisible
Technologies. Their measured total is 243 fetched and one discovery pass, which
is the Testlio future-role pool. This is **a disabled research catalog, not an
activation recommendation**. No production source was added, enabled or disabled.

For a later administrator-controlled import, merge the pack's named mapping into
`data/source_packs.yaml`, preserving existing entries. Then run:

```fish
./venv/bin/python -m jobpipe.cli sources packs
./venv/bin/python -m jobpipe.cli sources import-pack agent_discovery_v030_research
```

Import preserves `enabled: false`. An agent with trial permission can then call
`trial_source` with a listed identifier without enabling ingestion. Enable an
individual source through existing source management only after a repeat trial
and review establish useful current postings. Do not enable Testlio based only
on a future-role pool. Source changes remain unavailable through MCP.

Machine-readable measurements and posting URLs are in
[source-discovery-v0.3.0.json](source-discovery-v0.3.0.json). Full local-only MCP
evidence is under `diagnostics/v0.3.0-acceptance/`. Applicant residence and work
history remain unverified, and résumé identity/education fields still contain
placeholders. Those are blockers to real application readiness, not zero years
of experience or inferred absence of qualifications.
