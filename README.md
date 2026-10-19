# journal-research MCP

MCP server for journal/paper research — topical search via [arXiv](https://arxiv.org), [Semantic Scholar](https://semanticscholar.org), and [OpenAlex](https://openalex.org). All APIs are free, no keys required.

## Tools

| Tool | Purpose |
|---|---|
| `search_papers(query, limit)` | Search papers across 3 sources, normalized + DOI-deduped, sorted by citations |
| `get_paper(doi)` | Details of a single paper via DOI (OpenAlex) |
| `download_pdf(url, path)` | Download a PDF to a local file |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Register with an MCP client (pi example):

```bash
pi mcp add journal-research -- /path/to/journal-research/.venv/bin/python /path/to/journal-research/server.py
```

Topical relevance is judged by the LLM client (the server only fetches and normalizes data) — that's the design, not a limitation.

## Notes

- Semantic Scholar without an API key shares a rate-limited anonymous pool (frequent 429s) — get a [free key](https://www.semanticscholar.org/product/api) and set `S2_API_KEY` if needed.
- 429/5xx responses are retried with backoff; one failing source never aborts a search.
