"""MCP server for journal/paper research: arXiv + Semantic Scholar + OpenAlex.

Tools:
  search_papers(query, limit)  -- search papers, normalized results + dedup
  get_paper(doi)               -- details of a single paper via DOI (OpenAlex)
  download_pdf(url, path)      -- download a PDF to a local file
"""
import asyncio
import re
import sys

# mcp is pinned <2 (v1 API, verified at runtime). Pyright in other environments
# can resolve mcp 2.x which has no FastMCP -> false positive, suppressed precisely below.
import defusedxml.ElementTree as ET
import httpx
from mcp.server.fastmcp import FastMCP  # pyright: ignore[reportAttributeAccessIssue]

mcp = FastMCP("journal-research")

HEADERS = {"User-Agent": "journal-research-mcp/0.1 (mailto:journal-mcp@example.com)"}
ABSTRACT_MAX = 1500  # ponytail: truncate abstracts to keep tool output token-friendly


async def _get_retry(client, url, params=None, tries=3):
    """GET with backoff for 429/5xx/transient errors. Keyless S2 & arXiv often 429."""
    delay = 2.0
    for i in range(tries):
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if i == tries - 1 or (isinstance(e, httpx.HTTPStatusError)
                                  and e.response.status_code not in (429, 500, 502, 503, 504)):
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _clean(s, n=ABSTRACT_MAX):
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def _norm_doi(doi):
    if not doi:
        return ""
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip().lower())
    return doi if doi.startswith("10.") else ""


def _key(p):
    # cross-source dedup: DOI first, fallback to normalized title
    if p.get("doi"):
        return "doi:" + p["doi"]
    return "t:" + re.sub(r"[^a-z0-9]+", " ", (p.get("title") or "").lower()).strip()


def _oa_abstract(inv):
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return _clean(" ".join(pos[i] for i in sorted(pos)))


async def _arxiv(client, query, limit):
    r = await _get_retry(client, "https://export.arxiv.org/api/query",
                         {"search_query": f"all:{query}", "max_results": limit,
                          "sortBy": "relevance"})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        arxiv_id = e.findtext("a:id", "", ns)
        pdf = ""
        for link in e.findall("a:link", ns):
            if link.get("title") == "pdf":
                pdf = link.get("href", "")
        doi = _norm_doi(e.findtext("a:doi", "", ns) or "")
        year = e.findtext("a:published", "", ns)[:4]
        out.append({
            "title": _clean(e.findtext("a:title", "", ns)),
            "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
            "year": int(year) if year.isdigit() else None,
            "abstract": _clean(e.findtext("a:summary", "", ns)),
            "doi": doi,
            "url": arxiv_id,
            "pdf": pdf,
            "source": "arxiv",
            "citations": 0,  # ponytail: arXiv API has no citations; S2/OpenAlex fill this in
        })
    return out


async def _s2(client, query, limit):
    r = await _get_retry(
        client,
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {"query": query, "limit": limit,
         "fields": "title,abstract,year,authors,externalIds,citationCount,url,openAccessPdf"})
    out = []
    for p in r.json().get("data") or []:
        doi = _norm_doi((p.get("externalIds") or {}).get("DOI", ""))
        out.append({
            "title": _clean(p.get("title")),
            "authors": [a["name"] for a in p.get("authors") or []],
            "year": p.get("year"),
            "abstract": _clean(p.get("abstract")),
            "doi": doi,
            "url": p.get("url") or (f"https://doi.org/{doi}" if doi else ""),
            "pdf": (p.get("openAccessPdf") or {}).get("url") or "",
            "source": "semantic-scholar",
            "citations": p.get("citationCount") or 0,
        })
    return out


async def _openalex(client, query, limit):
    r = await _get_retry(client, "https://api.openalex.org/works",
                         {"search": query, "per-page": limit,
                          "mailto": "journal-mcp@example.com"})
    out = []
    for w in r.json().get("results", []):
        doi = _norm_doi(w.get("doi", ""))
        out.append({
            "title": _clean(w.get("title") or w.get("display_name")),
            "authors": [a["author"]["display_name"]
                        for a in w.get("authorships") or [] if a.get("author")],
            "year": w.get("publication_year"),
            "abstract": _oa_abstract(w.get("abstract_inverted_index")),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else w.get("id", ""),
            "pdf": (w.get("open_access") or {}).get("oa_url") or "",
            "source": "openalex",
            "citations": w.get("cited_by_count") or 0,
        })
    return out


async def _search_all(query, limit):
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        results = await asyncio.gather(
            _arxiv(client, query, limit),
            _s2(client, query, limit),
            _openalex(client, query, limit),
            return_exceptions=True,
        )
    seen, papers = set(), []
    # ponytail: one dead/erroring source must not kill the whole search - log to stderr, move on
    ok = [r for r in results if isinstance(r, list)]
    for f in [r for r in results if not isinstance(r, list)]:
        print(f"[journal-research] source failed: {str(f)[:150]}", file=sys.stderr)
    for src in ok:
        for p in src:
            k = _key(p)
            if not p["title"] or k in seen:
                continue
            seen.add(k)
            papers.append(p)
    papers.sort(key=lambda p: p["citations"], reverse=True)
    return papers


@mcp.tool()
async def search_papers(query: str, limit: int = 10) -> list[dict[str, object]]:
    """Search papers by topic across arXiv, Semantic Scholar, and OpenAlex.

    Returns a list of dicts: {title, authors, year, abstract, doi, url, pdf, source, citations}.
    Use English keywords for best results. Abstracts are included so you can judge
    topical relevance yourself.
    """
    return await _search_all(query, max(1, min(limit, 25)))


@mcp.tool()
async def get_paper(doi: str) -> dict[str, object]:
    """Get details of a single paper via DOI: full abstract, authors, venue, year, citations."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        r = await _get_retry(client, f"https://api.openalex.org/works/doi:{doi}",
                             {"mailto": "journal-mcp@example.com"})
        w = r.json()
    return {
        "title": _clean(w.get("title")),
        "authors": [a["author"]["display_name"]
                    for a in w.get("authorships") or [] if a.get("author")],
        "year": w.get("publication_year"),
        "abstract": _oa_abstract(w.get("abstract_inverted_index")),
        "doi": _norm_doi(w.get("doi", "")),
        "url": w.get("doi", ""),
        "pdf": (w.get("open_access") or {}).get("oa_url") or "",
        "source": w.get("primary_location", {}).get("source", {}) or {},
        "citations": w.get("cited_by_count") or 0,
    }


@mcp.tool()
async def download_pdf(url: str, path: str) -> str:
    """Download a paper PDF to a local path. url must be http(s), usually the pdf field."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=120) as client:
        r = await client.get(url)
        r.raise_for_status()
    try:
        with open(path, "wb") as f:
            f.write(r.content)
    except OSError as e:
        raise ValueError(f"cannot write {path}: {e}") from e
    return f"saved: {path} ({len(r.content)} bytes)"


if __name__ == "__main__":
    mcp.run()
