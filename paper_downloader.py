#!/usr/bin/env python3
"""
Download papers by title from arXiv, IEEE Xplore, Google Scholar portal search,
and major publisher sites.

Priority order is fixed:
1) arXiv
2) IEEE
3) Google Scholar + portal filters (Elsevier/ACS/CNS)
4) ORCID works lookup
5) Major publisher fallback (ACM/Springer/Elsevier/Wiley/Nature via DOI landing page)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By

    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False
    webdriver = None  # type: ignore[assignment]
    WebDriverException = Exception  # type: ignore[assignment]
    ChromeOptions = None  # type: ignore[assignment]
    By = None  # type: ignore[assignment]

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
IEEE_BASE_URL = "https://ieeexplore.ieee.org"
IEEE_SEARCH_URL_TEMPLATE = (
    "https://ieeexplore.ieee.org/search/searchresult.jsp?newsearch=true&queryText={query}"
)
CROSSREF_API_URL = "https://api.crossref.org/works"
ORCID_EXPANDED_SEARCH_URL = "https://pub.orcid.org/v3.0/expanded-search/"
ORCID_WORKS_URL_TEMPLATE = "https://pub.orcid.org/v3.0/{orcid_id}/works"
SCHOLAR_BASE_URL = "https://scholar.google.com"
SCHOLAR_SEARCH_URL_TEMPLATE = "https://scholar.google.com/scholar?q={query}"

MAJOR_DOMAIN_LABELS = (
    ("dl.acm.org", "acm"),
    ("link.springer.com", "springer"),
    ("sciencedirect.com", "elsevier"),
    ("pubs.acs.org", "acs"),
    ("science.org", "science"),
    ("sciencemag.org", "science"),
    ("cell.com", "cell"),
    ("onlinelibrary.wiley.com", "wiley"),
    ("nature.com", "nature"),
    ("tandfonline.com", "taylor-francis"),
    ("journals.sagepub.com", "sage"),
    ("ieeexplore.ieee.org", "ieee"),
)

PORTAL_FILTERS = (
    ("elsevier", "sciencedirect.com"),
    ("acs", "pubs.acs.org"),
    ("cns-nature", "nature.com"),
    ("cns-science", "science.org"),
    ("cns-cell", "cell.com"),
)

CITATION_CHAIN_INSERT_BREAK_RE = re.compile(
    r"((?:19|20)\d{2}[a-z]?(?:[\).,;:])?)\s+"
    r"(?=(?:[A-Z][A-Za-z'`\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'`\-]+)?"
    r"(?:\s+et al\.)?,?\s*(?:19|20)\d{2}[a-z]?))"
)
PERIOD_AUTHOR_BREAK_RE = re.compile(
    r"\.\s+(?=(?:[A-Z][A-Za-z'`\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'`\-]+)?"
    r"(?:\s+et al\.)?,?\s*(?:19|20)\d{2}[a-z]?))"
)
NUMBERED_ITEM_BREAK_RE = re.compile(r"\s+(?=(?:\d+\s*[\).、]))")
REFERENCE_PREFIX_RE = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+\s*[\).、]|[-*•▪●])\s*")
LIST_SPLIT_RE = re.compile(r"(?:\n+|[;；|]+)")
ORCID_ID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", re.IGNORECASE)


@dataclass(frozen=True)
class SearchResult:
    source: str
    query: str
    title: str
    pdf_url: str
    score: float


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def parse_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = _env(name, str(default))
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def parse_float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = _env(name, str(default))
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_env = Path(__file__).resolve().parent / ".env"
    parser = argparse.ArgumentParser(
        description=(
            "Download papers from arXiv/IEEE/Scholar/ORCID/major sites using paper names."
        )
    )
    parser.add_argument(
        "--env-file",
        default=str(default_env),
        help="Path to .env file (default: paper-downloader/.env)",
    )
    parser.add_argument(
        "--papers",
        nargs="*",
        default=[],
        help='Paper titles, e.g. --papers "Deep Learning" "Vision Transformer"',
    )
    parser.add_argument(
        "--papers-file",
        help="Text file containing one paper title per line.",
    )
    parser.add_argument(
        "--download-dir",
        help="Download directory. Overrides DOWNLOAD_DIR in .env.",
    )
    parser.add_argument(
        "--source",
        choices=["arxiv", "ieee", "scholar", "orcid", "major", "both"],
        help="Source mode (default from PAPER_SOURCE/both).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel download workers (default: 1).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        help="How many arXiv candidates to evaluate per query.",
    )
    parser.add_argument(
        "--major-max-results",
        type=int,
        help="How many Crossref candidates to evaluate per query.",
    )
    parser.add_argument(
        "--orcid-max-profiles",
        type=int,
        help="How many ORCID profiles to evaluate per query.",
    )
    parser.add_argument(
        "--orcid-max-works",
        type=int,
        help="Max works to scan per ORCID profile.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        help="Minimum title match score (0-1). Lower matches are ignored.",
    )
    parser.add_argument(
        "--ieee-headless",
        action="store_true",
        help="Run IEEE browser headless.",
    )
    parser.add_argument(
        "--ieee-manual-login",
        action="store_true",
        help="Pause for manual IEEE login/CAPTCHA before searching.",
    )
    parser.add_argument(
        "--ieee-wait",
        type=int,
        help="Seconds to wait for IEEE pages to render.",
    )
    parser.add_argument(
        "--scholar-headless",
        action="store_true",
        help="Run Google Scholar browser headless.",
    )
    parser.add_argument(
        "--scholar-manual-login",
        action="store_true",
        help="Pause for manual Scholar/portal login or CAPTCHA before searching.",
    )
    parser.add_argument(
        "--scholar-wait",
        type=int,
        help="Seconds to wait for Scholar pages to render.",
    )
    return parser.parse_args(argv)


def extract_paper_queries(raw_text: str) -> list[str]:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text).strip()
    if not text:
        return []

    rough_chunks = LIST_SPLIT_RE.split(text)
    candidates: list[str] = []

    for chunk in rough_chunks:
        normalized_chunk = normalize_whitespace(chunk)
        if not normalized_chunk:
            continue
        normalized_chunk = REFERENCE_PREFIX_RE.sub("", normalized_chunk).strip(" ,;")
        if not normalized_chunk:
            continue

        # Handles concatenated citation strings such as:
        # "Liu et al., 2023 Wang et al., 2022 ..."
        split_ready_chunk = CITATION_CHAIN_INSERT_BREAK_RE.sub(
            r"\1\n", normalized_chunk
        )
        split_ready_chunk = PERIOD_AUTHOR_BREAK_RE.sub(".\n", split_ready_chunk)
        split_ready_chunk = NUMBERED_ITEM_BREAK_RE.sub("\n", split_ready_chunk)
        split_chunks = split_ready_chunk.splitlines()
        for split_chunk in split_chunks:
            title = normalize_whitespace(split_chunk)
            title = REFERENCE_PREFIX_RE.sub("", title).strip(" ,;")
            if len(title) >= 3:
                candidates.append(title)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_whitespace(candidate).lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def read_papers(papers_inline: Sequence[str], papers_file: str | None) -> list[str]:
    papers: list[str] = []
    for inline_item in papers_inline:
        if not inline_item.strip():
            continue
        papers.extend(extract_paper_queries(inline_item))

    if papers_file:
        file_path = Path(papers_file)
        if not file_path.exists():
            raise FileNotFoundError(f"Papers file not found: {file_path}")
        file_text = file_path.read_text(encoding="utf-8")
        papers.extend(extract_paper_queries(file_text))

    unique: list[str] = []
    seen = set()
    for paper in papers:
        if paper not in seen:
            unique.append(paper)
            seen.add(paper)
    return unique


def build_download_dir(args: argparse.Namespace) -> Path:
    download_dir = (
        Path(args.download_dir).expanduser()
        if args.download_dir
        else Path(_env("DOWNLOAD_DIR", str(Path.cwd() / "downloads"))).expanduser()
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", text.lower()).strip()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def match_score(query: str, title: str) -> float:
    nq = normalize_text(query)
    nt = normalize_text(title)
    if not nq or not nt:
        return 0.0

    ratio = SequenceMatcher(None, nq, nt).ratio()
    q_tokens = set(nq.split())
    t_tokens = set(nt.split())
    overlap = (len(q_tokens & t_tokens) / len(q_tokens)) if q_tokens else 0.0
    contains_bonus = 0.12 if nq in nt else 0.0
    return (0.65 * ratio) + (0.35 * overlap) + contains_bonus


def sanitize_title_for_filename(real_title: str) -> str:
    replaced = real_title.replace(":", " ")
    safe = re.sub(r"[\\/*?\"<>|]+", "_", replaced)
    safe = re.sub(r"\s+", " ", safe).strip().strip(".")
    return safe or "paper"


def unique_output_path(download_dir: Path, real_title: str) -> Path:
    base = sanitize_title_for_filename(real_title)
    path = download_dir / f"{base}.pdf"
    index = 2
    while path.exists():
        path = download_dir / f"{base}_{index}.pdf"
        index += 1
    return path


def is_pdf_response(response: requests.Response) -> bool:
    ctype = response.headers.get("Content-Type", "").lower()
    if "pdf" in ctype:
        return True
    return response.url.lower().endswith(".pdf")


def derive_arxiv_pdf_url(entry: ET.Element) -> str | None:
    for link in entry.findall("atom:link", ATOM_NS):
        href = (link.attrib.get("href") or "").strip()
        link_title = (link.attrib.get("title") or "").strip().lower()
        link_type = (link.attrib.get("type") or "").strip().lower()
        if not href:
            continue
        if link_title == "pdf" or "application/pdf" in link_type or "/pdf/" in href:
            return href if href.lower().endswith(".pdf") else f"{href}.pdf"

    entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS).strip()
    if "/abs/" in entry_id:
        return entry_id.replace("/abs/", "/pdf/") + ".pdf"
    return None


def parse_arxiv_results(xml_text: str, query: str) -> list[SearchResult]:
    root = ET.fromstring(xml_text)
    results: list[SearchResult] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NS)
        title = normalize_whitespace(title)
        if not title:
            continue

        pdf_url = derive_arxiv_pdf_url(entry)
        if not pdf_url:
            continue

        score = match_score(query, title)
        results.append(
            SearchResult(
                source="arxiv",
                query=query,
                title=title,
                pdf_url=pdf_url,
                score=score,
            )
        )
    return results


def search_arxiv(
    session: requests.Session,
    query: str,
    max_results: int,
    timeout: int,
    min_score: float,
) -> SearchResult | None:
    safe_query = query.replace('"', " ").strip()
    params = {
        "search_query": f'all:"{safe_query}"',
        "start": 0,
        "max_results": max_results,
    }
    response = session.get(ARXIV_API_URL, params=params, timeout=timeout)
    response.raise_for_status()
    candidates = parse_arxiv_results(response.text, query)
    if not candidates:
        return None

    candidates.sort(key=lambda item: item.score, reverse=True)
    if candidates[0].score < min_score:
        return None
    return candidates[0]


def build_ieee_driver(headless: bool, user_agent: str) -> Any:
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium is not available. Install requirements.txt first.")

    options = ChromeOptions()
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1200")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
    if headless:
        options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(90)
    return driver


def manual_checkpoint(message: str) -> None:
    print(message)
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("Press Enter to continue...")
            return
    except Exception:
        pass

    wait_seconds = parse_int_env("MANUAL_LOGIN_WAIT_SECONDS", default=120, minimum=5)
    print(
        "[INFO] Non-interactive mode detected. "
        f"Waiting {wait_seconds}s for manual login to complete."
    )
    time.sleep(wait_seconds)


def maybe_manual_ieee_login(driver: Any, enabled: bool) -> None:
    if not enabled:
        return
    driver.get(IEEE_BASE_URL)
    manual_checkpoint(
        "Complete IEEE login/CAPTCHA in the browser window. "
        "No next request will be sent until this checkpoint finishes."
    )


def first_matching_href(driver: Any, selectors: Sequence[str]) -> str | None:
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            href = (element.get_attribute("href") or "").strip()
            if not href or href.startswith("javascript:"):
                continue
            if "/document/" in href:
                return urljoin(IEEE_BASE_URL, href)
    return None


def extract_ieee_document_url(driver: Any) -> str | None:
    selectors = [
        "a.result-item-title-link",
        "h2 a.result-item-title-link",
        "a[href*='/document/']",
    ]
    href = first_matching_href(driver, selectors)
    if href:
        return href

    html = driver.page_source
    match = re.search(r"href=['\"](/document/\d+[^'\"]*)['\"]", html)
    if match:
        return urljoin(IEEE_BASE_URL, match.group(1))
    return None


def extract_ieee_title(driver: Any) -> str:
    selectors = [
        "meta[name='citation_title']",
        "h1.document-title",
        "h1",
    ]
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            if selector.startswith("meta"):
                text = (element.get_attribute("content") or "").strip()
            else:
                text = (element.text or "").strip()
            text = normalize_whitespace(text)
            if text:
                return text
    return ""


def extract_ieee_pdf_url(driver: Any) -> str | None:
    selectors = [
        "a.stats-document-lh-action-downloadpdf",
        "a[href*='/stamp/stamp.jsp']",
        "a[href*='stampPDF/getPDF.jsp']",
        "a[href*='.pdf']",
    ]
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            href = (element.get_attribute("href") or "").strip()
            if not href or href.startswith("javascript:"):
                continue
            lowered = href.lower()
            if ".pdf" in lowered or "/stamp/" in lowered or "getpdf" in lowered:
                return urljoin(IEEE_BASE_URL, href)

    article_match = re.search(r"/document/(\d+)", driver.current_url)
    if article_match:
        return f"{IEEE_BASE_URL}/stamp/stamp.jsp?tp=&arnumber={article_match.group(1)}"
    return None


def search_ieee(
    driver: Any, query: str, wait_seconds: int, min_score: float
) -> SearchResult | None:
    search_url = IEEE_SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    driver.get(search_url)
    time.sleep(max(2, wait_seconds))

    page_title = (driver.title or "").lower()
    if "request rejected" in page_title:
        print("[WARN] IEEE returned Request Rejected. Try --ieee-manual-login.")
        return None

    doc_url = extract_ieee_document_url(driver)
    if not doc_url:
        return None

    driver.get(doc_url)
    time.sleep(max(2, wait_seconds))

    title = extract_ieee_title(driver)
    if not title:
        title = query
    pdf_url = extract_ieee_pdf_url(driver)
    if not pdf_url:
        return None

    score = match_score(query, title)
    if score < min_score:
        return None

    return SearchResult(
        source="ieee",
        query=query,
        title=title,
        pdf_url=pdf_url,
        score=score,
    )


def parse_crossref_candidates(payload: dict[str, Any], query: str) -> list[dict[str, Any]]:
    message = payload.get("message", {})
    items = message.get("items", [])
    candidates: list[dict[str, Any]] = []
    for item in items:
        titles = item.get("title") or []
        if isinstance(titles, list) and titles:
            title = normalize_whitespace(str(titles[0]))
        else:
            title = normalize_whitespace(str(titles))
        doi = normalize_whitespace(str(item.get("DOI", "")))
        if not title or not doi:
            continue

        url = normalize_whitespace(str(item.get("URL", "")))
        if not url:
            url = f"https://doi.org/{doi}"

        candidates.append(
            {
                "title": title,
                "doi": doi,
                "url": url,
                "score": match_score(query, title),
            }
        )

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    return candidates


def identify_major_source(*urls: str) -> str:
    joined = " ".join(urls).lower()
    for domain, label in MAJOR_DOMAIN_LABELS:
        if domain in joined:
            return label
    return "major"


def extract_major_pdf_url(html: str, base_url: str, doi: str) -> str | None:
    candidates: list[tuple[int, str]] = []

    meta_patterns = [
        r"<meta[^>]+name=['\"]citation_pdf_url['\"][^>]+content=['\"]([^'\"]+)['\"]",
        r"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+name=['\"]citation_pdf_url['\"]",
    ]
    for pattern in meta_patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            href = normalize_whitespace(match).replace("&amp;", "&")
            if href:
                candidates.append((300, urljoin(base_url, href)))

    for match in re.findall(
        r"<link[^>]+type=['\"]application/pdf['\"][^>]+href=['\"]([^'\"]+)['\"]",
        html,
        flags=re.IGNORECASE,
    ):
        href = normalize_whitespace(match).replace("&amp;", "&")
        if href:
            candidates.append((260, urljoin(base_url, href)))

    hrefs = re.findall(r"href=['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE)
    for raw_href in hrefs:
        href = normalize_whitespace(raw_href).replace("&amp;", "&")
        if not href or href.startswith("javascript:"):
            continue
        lower = href.lower()
        score = 0
        if ".pdf" in lower:
            score += 200
        if "/doi/pdf/" in lower or "/doi/epdf/" in lower:
            score += 190
        if "/content/pdf/" in lower or "articlepdf" in lower:
            score += 180
        if "pdf" in lower:
            score += 80
        if score > 0:
            candidates.append((score, urljoin(base_url, href)))

    if doi and "dl.acm.org" in base_url.lower():
        candidates.append((170, f"https://dl.acm.org/doi/pdf/{doi}"))

    if not candidates:
        return None

    deduped: dict[str, int] = {}
    for score, link in candidates:
        if link not in deduped or score > deduped[link]:
            deduped[link] = score

    ranked = sorted(deduped.items(), key=lambda item: item[1], reverse=True)
    return ranked[0][0]


def resolve_pdf_from_landing(
    session: requests.Session,
    landing_url: str,
    doi: str,
    timeout: int,
) -> tuple[str, str] | None:
    try:
        landing_resp = session.get(landing_url, timeout=timeout, allow_redirects=True)
        landing_resp.raise_for_status()
    except requests.RequestException:
        return None

    if is_pdf_response(landing_resp):
        source_label = identify_major_source(landing_url, landing_resp.url)
        return landing_resp.url, source_label

    content_type = landing_resp.headers.get("Content-Type", "").lower()
    if "html" not in content_type and "<html" not in landing_resp.text[:400].lower():
        return None

    pdf_url = extract_major_pdf_url(landing_resp.text, landing_resp.url, doi)
    if not pdf_url:
        return None
    source_label = identify_major_source(landing_resp.url, pdf_url)
    return pdf_url, source_label


def search_major_sites(
    session: requests.Session,
    query: str,
    max_results: int,
    timeout: int,
    min_score: float,
) -> SearchResult | None:
    params = {
        "query.title": query,
        "rows": max_results,
    }
    response = session.get(CROSSREF_API_URL, params=params, timeout=timeout)
    response.raise_for_status()
    candidates = parse_crossref_candidates(response.json(), query)
    if not candidates:
        return None

    for candidate in candidates:
        landing_url = str(candidate["url"])
        doi = str(candidate["doi"])
        title = str(candidate["title"])
        score = float(candidate["score"])
        if score < min_score:
            continue
        resolved = resolve_pdf_from_landing(session, landing_url, doi=doi, timeout=timeout)
        if not resolved:
            continue
        pdf_url, source_label = resolved
        return SearchResult(
            source=source_label,
            query=query,
            title=title,
            pdf_url=pdf_url,
            score=score,
        )
    return None


def parse_orcid_profile_ids(payload: dict[str, Any]) -> list[str]:
    raw_results = payload.get("expanded-result") or []
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw_results:
        orcid_id = normalize_whitespace(str(item.get("orcid-id", "")))
        if not orcid_id:
            continue
        if not ORCID_ID_RE.search(orcid_id):
            continue
        if orcid_id not in seen:
            seen.add(orcid_id)
            ids.append(orcid_id)
    return ids


def extract_doi_from_external_ids(external_ids_payload: Any) -> str | None:
    if not isinstance(external_ids_payload, dict):
        return None
    external_ids = external_ids_payload.get("external-id") or []
    if not isinstance(external_ids, list):
        return None

    for external_id in external_ids:
        if not isinstance(external_id, dict):
            continue
        external_type = normalize_whitespace(
            str(external_id.get("external-id-type", ""))
        ).lower()
        if external_type != "doi":
            continue
        raw_value = normalize_whitespace(str(external_id.get("external-id-value", "")))
        if not raw_value:
            normalized_payload = external_id.get("external-id-normalized") or {}
            raw_value = normalize_whitespace(str(normalized_payload.get("value", "")))
        if raw_value:
            return raw_value
    return None


def search_orcid(
    session: requests.Session,
    query: str,
    timeout: int,
    min_score: float,
    max_profiles: int,
    max_works_per_profile: int,
) -> SearchResult | None:
    safe_query = query.replace('"', " ").strip()
    if not safe_query:
        return None

    params = {
        "q": f'work-titles:"{safe_query}"',
        "start": 0,
        "rows": max_profiles,
    }
    response = session.get(ORCID_EXPANDED_SEARCH_URL, params=params, timeout=timeout)
    response.raise_for_status()
    profile_ids = parse_orcid_profile_ids(response.json())
    if not profile_ids:
        return None

    for orcid_id in profile_ids[:max_profiles]:
        works_url = ORCID_WORKS_URL_TEMPLATE.format(orcid_id=orcid_id)
        try:
            works_resp = session.get(works_url, timeout=timeout)
            works_resp.raise_for_status()
        except requests.RequestException:
            continue

        works_payload = works_resp.json()
        groups = works_payload.get("group") or []
        checked = 0
        for group in groups:
            summaries = group.get("work-summary") or []
            for summary in summaries:
                checked += 1
                if checked > max_works_per_profile:
                    break

                title_payload = summary.get("title") or {}
                title = normalize_whitespace(
                    str((title_payload.get("title") or {}).get("value", ""))
                )
                if not title:
                    continue

                score = match_score(query, title)
                if score < min_score:
                    continue

                doi = extract_doi_from_external_ids(summary.get("external-ids"))
                summary_url = normalize_whitespace(
                    str((summary.get("url") or {}).get("value", ""))
                )
                landing_candidates: list[str] = []
                if doi:
                    landing_candidates.append(f"https://doi.org/{doi}")
                if summary_url:
                    landing_candidates.append(summary_url)

                for landing_url in landing_candidates:
                    resolved = resolve_pdf_from_landing(
                        session, landing_url, doi=doi or "", timeout=timeout
                    )
                    if not resolved:
                        continue
                    pdf_url, source_label = resolved
                    return SearchResult(
                        source=f"orcid-{source_label}",
                        query=query,
                        title=title,
                        pdf_url=pdf_url,
                        score=score,
                    )
            if checked > max_works_per_profile:
                break
    return None


def build_scholar_driver(headless: bool, user_agent: str) -> Any:
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium is not available. Install requirements.txt first.")

    options = ChromeOptions()
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1200")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
    if headless:
        options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(90)
    return driver


def maybe_manual_scholar_login(driver: Any, enabled: bool) -> None:
    if not enabled:
        return
    driver.get(SCHOLAR_BASE_URL)
    manual_checkpoint(
        "Complete Scholar/portal login or CAPTCHA, then press Enter to continue. "
        "No navigation will happen until you press Enter."
    )


def search_google_scholar(
    session: requests.Session,
    driver: Any,
    query: str,
    wait_seconds: int,
    timeout: int,
    min_score: float,
    domain_filter: str | None,
    source_label: str,
) -> SearchResult | None:
    search_query = query if not domain_filter else f"{query} site:{domain_filter}"
    search_url = SCHOLAR_SEARCH_URL_TEMPLATE.format(query=quote_plus(search_query))
    driver.get(search_url)
    time.sleep(max(2, wait_seconds))

    page_title = (driver.title or "").lower()
    if "not a robot" in page_title or "unusual traffic" in page_title:
        print("[WARN] Google Scholar requires verification. Try --scholar-manual-login.")
        return None

    results = driver.find_elements(By.CSS_SELECTOR, "div.gs_r.gs_or.gs_scl")
    candidates: list[dict[str, Any]] = []
    for result in results:
        title_els = result.find_elements(By.CSS_SELECTOR, "h3.gs_rt")
        if not title_els:
            continue
        title_el = title_els[0]
        title = normalize_whitespace(title_el.text or "")
        if not title:
            continue

        link_els = title_el.find_elements(By.CSS_SELECTOR, "a")
        article_url = ""
        if link_els:
            article_url = normalize_whitespace(link_els[0].get_attribute("href") or "")

        pdf_url = ""
        pdf_els = result.find_elements(By.CSS_SELECTOR, "div.gs_or_ggsm a")
        if pdf_els:
            pdf_url = normalize_whitespace(pdf_els[0].get_attribute("href") or "")

        combined_targets = " ".join([article_url, pdf_url]).lower()
        if domain_filter and domain_filter.lower() not in combined_targets:
            continue

        score = match_score(query, title)
        if score < min_score:
            continue
        candidates.append(
            {
                "title": title,
                "article_url": article_url,
                "pdf_url": pdf_url,
                "score": score,
            }
        )

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    for candidate in candidates:
        if candidate["pdf_url"]:
            return SearchResult(
                source=source_label,
                query=query,
                title=str(candidate["title"]),
                pdf_url=str(candidate["pdf_url"]),
                score=float(candidate["score"]),
            )

        article_url = str(candidate["article_url"])
        if not article_url:
            continue
        resolved = resolve_pdf_from_landing(session, article_url, doi="", timeout=timeout)
        if not resolved:
            continue
        pdf_url, _ = resolved
        return SearchResult(
            source=source_label,
            query=query,
            title=str(candidate["title"]),
            pdf_url=pdf_url,
            score=float(candidate["score"]),
        )
    return None


def add_cookies_to_session(session: requests.Session, cookies: list[dict[str, Any]]) -> None:
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", ""))
        if not name:
            continue
        domain = cookie.get("domain")
        path = cookie.get("path", "/")
        if domain:
            session.cookies.set(name, value, domain=domain, path=path)
        else:
            session.cookies.set(name, value, path=path)


def strip_orcid_prefix(source: str) -> str:
    if source.startswith("orcid-"):
        return source[len("orcid-") :]
    return source


def uses_scholar_cookies(source: str) -> bool:
    base = strip_orcid_prefix(source)
    if base in {
        "scholar",
        "elsevier",
        "acs",
        "cns-nature",
        "cns-science",
        "cns-cell",
    }:
        return True
    return source.startswith("orcid-") and base in {"elsevier", "acs", "science", "cell", "nature"}


def download_one(
    result: SearchResult,
    download_dir: Path,
    timeout: int,
    user_agents: dict[str, str],
    ieee_cookies: list[dict[str, Any]],
    scholar_cookies: list[dict[str, Any]],
) -> bool:
    output_path = unique_output_path(download_dir, result.title)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agents.get(result.source, user_agents["default"]),
            "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
        }
    )
    if strip_orcid_prefix(result.source) == "ieee":
        session.headers["Referer"] = IEEE_BASE_URL
        add_cookies_to_session(session, ieee_cookies)
    elif uses_scholar_cookies(result.source):
        session.headers["Referer"] = SCHOLAR_BASE_URL
        add_cookies_to_session(session, scholar_cookies)

    try:
        response = session.get(
            result.pdf_url, timeout=timeout, stream=True, allow_redirects=True
        )
        response.raise_for_status()
        if not is_pdf_response(response):
            ctype = response.headers.get("Content-Type", "unknown")
            print(
                f"[SKIP] Not a direct PDF for '{result.query}' "
                f"from {result.source} (Content-Type: {ctype})."
            )
            return False

        with output_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)

        print(f"[DOWNLOADED] {result.query} [{result.source}] -> {output_path.name}")
        return True
    except requests.RequestException as exc:
        print(f"[ERROR] Download failed for '{result.query}' from {result.source}: {exc}")
        try:
            if output_path.exists():
                output_path.unlink()
        except OSError:
            pass
        return False
    finally:
        session.close()


def default_arxiv_user_agent() -> str:
    return "paper-downloader/1.0 (mailto:your_email@example.com)"


def default_browser_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.env_file:
        env_file = Path(args.env_file)
        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            print(f"[WARN] .env file not found: {env_file}")

    try:
        papers = read_papers(args.papers, args.papers_file)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}")
        return 1

    if not papers:
        print("[ERROR] No paper titles provided.")
        print("Use --papers or --papers-file.")
        return 1

    source = (args.source or _env("PAPER_SOURCE", "both")).strip().lower()
    if source not in {"arxiv", "ieee", "scholar", "orcid", "major", "both"}:
        print("[ERROR] Invalid source. Use arxiv, ieee, scholar, orcid, major, or both.")
        return 1

    use_arxiv = source in {"arxiv", "both"}
    use_ieee = source in {"ieee", "both"}
    use_scholar = source in {"scholar", "both"}
    use_orcid = source in {"orcid", "both"}
    use_major = source in {"major", "both"}

    max_results = args.max_results or parse_int_env("ARXIV_MAX_RESULTS", default=8)
    major_max_results = args.major_max_results or parse_int_env(
        "MAJOR_MAX_RESULTS", default=8
    )
    orcid_max_profiles = args.orcid_max_profiles or parse_int_env(
        "ORCID_MAX_PROFILES", default=5
    )
    orcid_max_works = args.orcid_max_works or parse_int_env(
        "ORCID_MAX_WORKS_PER_PROFILE", default=120
    )
    timeout = args.timeout or parse_int_env("ARXIV_TIMEOUT_SECONDS", default=30)
    min_score = (
        args.min_score
        if args.min_score is not None
        else parse_float_env("MIN_MATCH_SCORE", default=0.9)
    )
    workers = max(1, args.workers)
    download_dir = build_download_dir(args)

    arxiv_user_agent = _env("ARXIV_USER_AGENT", default_arxiv_user_agent())
    ieee_user_agent = _env("IEEE_USER_AGENT", default_browser_user_agent())
    scholar_user_agent = _env("SCHOLAR_USER_AGENT", default_browser_user_agent())
    major_user_agent = _env("MAJOR_USER_AGENT", default_browser_user_agent())
    orcid_user_agent = _env("ORCID_USER_AGENT", default_arxiv_user_agent())
    user_agents = {
        "default": major_user_agent,
        "arxiv": arxiv_user_agent,
        "ieee": ieee_user_agent,
        "scholar": scholar_user_agent,
        "major": major_user_agent,
        "acm": major_user_agent,
        "springer": major_user_agent,
        "elsevier": major_user_agent,
        "acs": major_user_agent,
        "science": major_user_agent,
        "cell": major_user_agent,
        "wiley": major_user_agent,
        "nature": major_user_agent,
        "taylor-francis": major_user_agent,
        "sage": major_user_agent,
        "cns-nature": major_user_agent,
        "cns-science": major_user_agent,
        "cns-cell": major_user_agent,
        "orcid-scholar": scholar_user_agent,
        "orcid-major": major_user_agent,
        "orcid-acm": major_user_agent,
        "orcid-springer": major_user_agent,
        "orcid-elsevier": major_user_agent,
        "orcid-acs": major_user_agent,
        "orcid-science": major_user_agent,
        "orcid-cell": major_user_agent,
        "orcid-wiley": major_user_agent,
        "orcid-nature": major_user_agent,
        "orcid-taylor-francis": major_user_agent,
        "orcid-sage": major_user_agent,
        "orcid-ieee": ieee_user_agent,
    }

    ieee_manual_login = args.ieee_manual_login or parse_bool_env("IEEE_MANUAL_LOGIN")
    ieee_headless = args.ieee_headless or parse_bool_env("IEEE_HEADLESS")
    ieee_wait = args.ieee_wait or parse_int_env("IEEE_WAIT_SECONDS", default=6)
    if ieee_manual_login and ieee_headless:
        print("[WARN] --ieee-manual-login conflicts with headless; disabling headless.")
        ieee_headless = False

    scholar_manual_login = args.scholar_manual_login or parse_bool_env(
        "SCHOLAR_MANUAL_LOGIN"
    )
    scholar_headless = args.scholar_headless or parse_bool_env("SCHOLAR_HEADLESS")
    scholar_wait = args.scholar_wait or parse_int_env("SCHOLAR_WAIT_SECONDS", default=6)
    if scholar_manual_login and scholar_headless:
        print("[WARN] --scholar-manual-login conflicts with headless; disabling headless.")
        scholar_headless = False

    if (use_ieee or use_scholar) and not SELENIUM_AVAILABLE:
        if source in {"ieee", "scholar"}:
            print("[ERROR] IEEE/Scholar source requires selenium. Install requirements.txt.")
            return 1
        print("[WARN] Selenium unavailable, IEEE/Scholar sources disabled.")
        use_ieee = False
        use_scholar = False

    arxiv_session: requests.Session | None = None
    orcid_session: requests.Session | None = None
    scholar_session: requests.Session | None = None
    major_session: requests.Session | None = None
    ieee_driver: Any = None
    scholar_driver: Any = None
    ieee_cookies: list[dict[str, Any]] = []
    scholar_cookies: list[dict[str, Any]] = []
    matches: list[SearchResult] = []

    if use_arxiv:
        arxiv_session = requests.Session()
        arxiv_session.headers.update(
            {
                "User-Agent": arxiv_user_agent,
                "Accept": "application/atom+xml,application/xml,text/xml,*/*;q=0.8",
            }
        )

    if use_major:
        major_session = requests.Session()
        major_session.headers.update(
            {
                "User-Agent": major_user_agent,
                "Accept": "application/json,text/html,application/xml,*/*;q=0.8",
            }
        )

    if use_orcid:
        orcid_session = requests.Session()
        orcid_session.headers.update(
            {
                "User-Agent": orcid_user_agent,
                "Accept": "application/vnd.orcid+json,application/json,*/*;q=0.8",
            }
        )

    if use_scholar:
        scholar_session = requests.Session()
        scholar_session.headers.update(
            {
                "User-Agent": scholar_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

    print(f"[INFO] Source mode: {source}")
    print("[INFO] Priority: arXiv -> IEEE -> Scholar/portals -> ORCID -> major sites")
    print(f"[INFO] Download directory: {download_dir.resolve()}")
    print(f"[INFO] Paper count: {len(papers)}")

    try:
        for paper in papers:
            picked: SearchResult | None = None

            if use_arxiv and arxiv_session is not None:
                print(f"[SEARCH][arXiv] {paper}")
                try:
                    picked = search_arxiv(
                        arxiv_session,
                        paper,
                        max_results=max_results,
                        timeout=timeout,
                        min_score=min_score,
                    )
                    if picked:
                        print(f"[MATCH][arXiv] {paper} -> {picked.title}")
                except requests.RequestException as exc:
                    print(f"[ERROR] arXiv search failed for '{paper}': {exc}")

            if not picked and use_ieee and ieee_driver is not None:
                print(f"[SEARCH][IEEE] {paper}")
                try:
                    picked = search_ieee(
                        ieee_driver,
                        paper,
                        wait_seconds=ieee_wait,
                        min_score=min_score,
                    )
                    if picked:
                        print(f"[MATCH][IEEE] {paper} -> {picked.title}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[ERROR] IEEE search failed for '{paper}': {exc}")
            elif not picked and use_ieee and ieee_driver is None:
                try:
                    ieee_driver = build_ieee_driver(ieee_headless, ieee_user_agent)
                    maybe_manual_ieee_login(ieee_driver, ieee_manual_login)
                    print(f"[SEARCH][IEEE] {paper}")
                    picked = search_ieee(
                        ieee_driver,
                        paper,
                        wait_seconds=ieee_wait,
                        min_score=min_score,
                    )
                    if picked:
                        print(f"[MATCH][IEEE] {paper} -> {picked.title}")
                except Exception as exc:  # noqa: BLE001
                    if source == "ieee":
                        print(f"[ERROR] Failed to initialize/search IEEE: {exc}")
                        return 1
                    print(f"[WARN] IEEE unavailable in this run, disabling IEEE: {exc}")
                    use_ieee = False

            if not picked and use_scholar:
                if scholar_driver is None:
                    try:
                        scholar_driver = build_scholar_driver(
                            scholar_headless, scholar_user_agent
                        )
                        maybe_manual_scholar_login(
                            scholar_driver, scholar_manual_login
                        )
                    except Exception as exc:  # noqa: BLE001
                        if source == "scholar":
                            print(f"[ERROR] Failed to initialize/search Scholar: {exc}")
                            return 1
                        print(
                            "[WARN] Scholar unavailable in this run, disabling "
                            f"Scholar/portal search: {exc}"
                        )
                        use_scholar = False

                if use_scholar and scholar_driver is not None and scholar_session is not None:
                    try:
                        print(f"[SEARCH][Scholar] {paper}")
                        picked = search_google_scholar(
                            session=scholar_session,
                            driver=scholar_driver,
                            query=paper,
                            wait_seconds=scholar_wait,
                            timeout=timeout,
                            min_score=min_score,
                            domain_filter=None,
                            source_label="scholar",
                        )
                        if picked:
                            print(f"[MATCH][Scholar] {paper} -> {picked.title}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[ERROR] Scholar search failed for '{paper}': {exc}")

            if (
                not picked
                and use_scholar
                and scholar_driver is not None
                and scholar_session is not None
            ):
                for portal_label, portal_domain in PORTAL_FILTERS:
                    print(f"[SEARCH][Portal:{portal_label}] {paper}")
                    try:
                        picked = search_google_scholar(
                            session=scholar_session,
                            driver=scholar_driver,
                            query=paper,
                            wait_seconds=scholar_wait,
                            timeout=timeout,
                            min_score=min_score,
                            domain_filter=portal_domain,
                            source_label=portal_label,
                        )
                        if picked:
                            print(
                                f"[MATCH][Portal:{portal_label}] "
                                f"{paper} -> {picked.title}"
                            )
                            break
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"[ERROR] Portal search failed for '{paper}' "
                            f"({portal_label}): {exc}"
                        )

            if not picked and use_orcid and orcid_session is not None:
                print(f"[SEARCH][ORCID] {paper}")
                try:
                    picked = search_orcid(
                        session=orcid_session,
                        query=paper,
                        timeout=timeout,
                        min_score=min_score,
                        max_profiles=orcid_max_profiles,
                        max_works_per_profile=orcid_max_works,
                    )
                    if picked:
                        print(f"[MATCH][ORCID:{picked.source}] {paper} -> {picked.title}")
                except requests.RequestException as exc:
                    print(f"[ERROR] ORCID search failed for '{paper}': {exc}")

            if not picked and use_major and major_session is not None:
                print(f"[SEARCH][Major] {paper}")
                try:
                    picked = search_major_sites(
                        major_session,
                        paper,
                        max_results=major_max_results,
                        timeout=timeout,
                        min_score=min_score,
                    )
                    if picked:
                        print(f"[MATCH][Major:{picked.source}] {paper} -> {picked.title}")
                except requests.RequestException as exc:
                    print(f"[ERROR] Major-site search failed for '{paper}': {exc}")

            if picked:
                matches.append(picked)
            else:
                print(f"No direct PDF link found for {paper}.")
    finally:
        if arxiv_session is not None:
            arxiv_session.close()
        if orcid_session is not None:
            orcid_session.close()
        if scholar_session is not None:
            scholar_session.close()
        if major_session is not None:
            major_session.close()
        if ieee_driver is not None:
            try:
                ieee_cookies = ieee_driver.get_cookies()
            except Exception:
                ieee_cookies = []
            try:
                ieee_driver.quit()
            except Exception:
                pass
        if scholar_driver is not None:
            try:
                scholar_cookies = scholar_driver.get_cookies()
            except Exception:
                scholar_cookies = []
            try:
                scholar_driver.quit()
            except Exception:
                pass

    print(f"[INFO] PDF links found: {len(matches)} / {len(papers)}")
    if not matches:
        print("[INFO] Done.")
        return 0

    if workers == 1:
        for result in matches:
            download_one(
                result=result,
                download_dir=download_dir,
                timeout=timeout,
                user_agents=user_agents,
                ieee_cookies=ieee_cookies,
                scholar_cookies=scholar_cookies,
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    download_one,
                    result,
                    download_dir,
                    timeout,
                    user_agents,
                    ieee_cookies,
                    scholar_cookies,
                ): result.query
                for result in matches
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"[ERROR] Parallel download crashed for '{query}': {exc}")

    print("[INFO] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
