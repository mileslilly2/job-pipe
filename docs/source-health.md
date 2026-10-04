# Source health note

Verified 2026-09-05. The configured Greenhouse board tokens `linear`, `notion`, `openai`, and `huggingface` returned HTTP 404 from the public boards API. Their official careers pages still exist for Linear, Notion, and OpenAI, which indicates an ATS migration or changed board identifier rather than a company closure. Hugging Face has no verified public Greenhouse careers board in the checked sources. These entries are disabled (not deleted) in the local and packaged source lists pending dedicated ATS adapters or confirmed replacement board identifiers.

The audit intentionally does not scrape LinkedIn or Indeed. Re-enable a source only after its public ATS endpoint is verified.
