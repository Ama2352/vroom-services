# Runbooks

Operational runbooks consumed by the `runbook-retriever` service at runtime.

Each `.md` file is split into paragraphs and indexed for keyword search. When an alert fires, the retriever returns the most relevant sections to the n8n LLM diagnosis step.

## Adding a runbook

Create a new `<alert-name>.md` file. Structure it as short paragraphs — the retriever scores by paragraph, not by file.

## Current runbooks

| File | Covers |
|------|--------|
| *(none yet — add when deploying Plan 10)* | |
