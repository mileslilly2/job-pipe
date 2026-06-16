# jobpipe

`jobpipe` is a local-first CLI for repetitive job application prep. It stores job postings, analyzes fit/risk/quality, drafts truthful tailored materials, and tracks applications without auto-submitting anything.

## Requirements

- Python 3.11+

## Setup

```fish
python -m venv venv
source venv/bin/activate.fish
pip install -e ".[dev]"
jobpipe init
```

## Example flow

```fish
jobpipe add-text --file tests/fixtures/helpdesk_clean.txt
jobpipe analyze 1
jobpipe packet 1
jobpipe tailor 1
jobpipe shortlist --min-fit 60 --max-risk 50
jobpipe status 1 ready_to_apply
jobpipe export
```

## Commands

- `jobpipe init`
- `jobpipe add-text --file PATH`
- `jobpipe add-url URL`
- `jobpipe list`
- `jobpipe analyze JOB_ID`
- `jobpipe shortlist --min-fit INT --max-risk INT --limit INT`
- `jobpipe tailor JOB_ID`
- `jobpipe packet JOB_ID`
- `jobpipe status JOB_ID STATUS`
- `jobpipe open JOB_ID`
- `jobpipe export`

## Notes

- `add-url` uses lightweight extraction. If a page does not parse cleanly, `jobpipe` saves the URL and tells you to paste the job description with `add-text`.
- Tailoring is constrained by the candidate profile and never invents paid experience, tools, certifications, or years of experience.
