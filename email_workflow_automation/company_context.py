"""Gather person + company facts from the live LinkedIn/Apollo screen and the web.

Never invent a company summary. If search/fetch fails, company_summary is empty
and needs_company_info=True.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from email_workflow_automation.target import capture_current_profile

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0
        self.meta_description = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag == "meta":
            ad = {k.lower(): (v or "") for k, v in attrs}
            name = (ad.get("name") or ad.get("property") or "").lower()
            if name in ("description", "og:description") and ad.get("content"):
                self.meta_description = ad["content"].strip()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        t = data.strip()
        if t:
            self._chunks.append(t)

    def text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(raw: str) -> str:
    p = _StripHTML()
    try:
        p.feed(raw)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)
    if p.meta_description:
        return p.meta_description + " " + p.text()
    return p.text()


def _http_get(url: str, timeout: int = 12) -> str:
    import requests

    r = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/json"},
        timeout=timeout,
        allow_redirects=True,
    )
    r.raise_for_status()
    return r.text if r.text else (r.content.decode("utf-8", errors="ignore") if r.content else "")


def _first_name(full: str) -> str:
    full = (full or "").strip()
    if not full or full.startswith("("):
        return ""
    return full.split()[0]


def _split_title_company(headline: str) -> tuple[str, str]:
    """Parse 'Title at Company' / 'Title | Company' / 'Title - Company'."""
    h = re.sub(r"\s+", " ", (headline or "").strip())
    if not h:
        return "", ""
    for sep in (" at ", " @ ", " | ", " – ", " — ", " - "):
        if sep in h:
            left, right = h.split(sep, 1)
            return left.strip(), right.strip()
    return h, ""


def _extract_from_linkedin_dom() -> dict:
    """Best-effort name/title/company from the LinkedIn profile DOM."""
    out = {"full_name": "", "title": "", "company": "", "source": ""}
    try:
        from email_workflow_automation.browser_util import active_page, connect

        if not connect(timeout=5):
            return out
        page = active_page()
        if page is None:
            return out

        name = ""
        for sel in ("h1.text-heading-xlarge", "h1.inline.t-24", "main h1", "h1"):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    t = (loc.inner_text(timeout=2000) or "").strip().split("\n")[0]
                    if t and len(t) < 120:
                        name = t
                        break
            except Exception:
                continue

        headline = ""
        for sel in (
            "div.text-body-medium.break-words",
            ".pv-text-details__left-panel .text-body-medium",
            "main .text-body-medium",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    t = (loc.inner_text(timeout=2000) or "").strip().split("\n")[0]
                    if t and 4 < len(t) < 200:
                        headline = t
                        break
            except Exception:
                continue

        title, company = _split_title_company(headline)
        # Current-company card often has a company link in experience
        if not company:
            try:
                loc = page.locator(
                    "a[href*='/company/'], span[class*='hoverable-link-text']"
                ).first
                if loc.count() > 0:
                    c = (loc.inner_text(timeout=1500) or "").strip().split("\n")[0]
                    if c and len(c) < 80 and c.lower() not in (name or "").lower():
                        company = c
            except Exception:
                pass

        out.update({
            "full_name": name,
            "title": title,
            "company": company,
            "headline": headline,
            "source": "linkedin_dom",
        })
    except Exception as e:
        out["source"] = f"linkedin_dom_error:{e}"
    return out


def _extract_from_vision() -> dict:
    """Read name/title/company from a focus-free screenshot (Apollo panel or LinkedIn)."""
    out = {"full_name": "", "title": "", "company": "", "source": ""}
    try:
        from email_workflow_automation.apollo import (
            _call_vision_json,
            capture_fullscreen_raw_no_focus,
        )
    except Exception as e:
        out["source"] = f"vision_import:{e}"
        return out

    prompt = (
        "Look at this screenshot. Identify the LinkedIn person currently shown "
        "(profile page and/or Apollo.io contact panel). "
        "Return ONLY JSON: "
        '{"full_name": "...", "title": "...", "company": "...", "what_you_see": "..."}. '
        "Use empty strings if unknown. Do not invent a company."
    )
    try:
        path, _meta = capture_fullscreen_raw_no_focus()
        obj, err = _call_vision_json(path, prompt, max_tokens=250)
        if obj is None:
            out["source"] = f"vision_fail:{err}"
            return out
        out["full_name"] = str(
            obj.get("full_name") or obj.get("name") or obj.get("person") or ""
        ).strip()
        out["title"] = str(obj.get("title") or obj.get("headline") or "").strip()
        if " at " in out["title"] and not obj.get("company"):
            t, c = _split_title_company(out["title"])
            out["title"] = t
            out["company"] = c
        out["company"] = str(obj.get("company") or out.get("company") or "").strip()
        out["what_you_see"] = str(obj.get("what_you_see") or "")[:240]
        out["source"] = "vision"
        print(
            f"  [company] vision name={out['full_name']!r} title={out['title']!r} "
            f"company={out['company']!r} see={out['what_you_see']!r}"
        )
    except Exception as e:
        out["source"] = f"vision_error:{e}"
    return out


def _ddg_search(query: str, n: int = 5) -> list[dict]:
    """DuckDuckGo HTML search. Returns [{title, url, snippet}]."""
    import requests
    from urllib.parse import unquote

    results: list[dict] = []
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )
    r.raise_for_status()
    html = r.text
    # result links: uddg= encoded url
    blocks = re.split(r'class="result', html)[1:]
    for block in blocks[: n + 2]:
        um = re.search(r'uddg=([^"&]+)', block)
        tm = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', block, re.S)
        if not um:
            continue
        url = unquote(um.group(1))
        title = _strip_html(tm.group(1)) if tm else ""
        snippet = _strip_html(sm.group(1)) if sm else ""
        if url.startswith("http"):
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= n:
            break
    return results


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _about_company(text: str, company: str) -> bool:
    """True if text is actually about this company, not a fuzzy Wikipedia miss."""
    c = _norm_name(company)
    t = _norm_name(text)
    if len(c) < 4:
        return False
    return c in t


def _wikipedia_summary(company: str) -> tuple[str, str]:
    import json
    import urllib.parse

    def _rest(slug: str) -> tuple[str, str]:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(slug)}"
        raw = _http_get(url, timeout=10)
        obj = json.loads(raw)
        extract = (obj.get("extract") or "").strip()
        title = (obj.get("title") or slug).strip()
        src = obj.get("content_urls", {}).get("desktop", {}).get("page") or url
        if obj.get("type") == "disambiguation":
            return "", ""
        if extract and (_about_company(title, company) or _about_company(extract[:200], company)):
            return extract, src
        return "", ""

    try:
        extract, src = _rest(company.replace(" ", "_"))
        if extract:
            return extract, src
    except Exception:
        pass

    # Opensearch only if the title clearly matches the company name
    try:
        q = urllib.parse.quote(company)
        raw = _http_get(
            "https://en.wikipedia.org/w/api.php?action=opensearch&limit=5"
            f"&namespace=0&format=json&search={q}",
            timeout=10,
        )
        data = json.loads(raw)
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        for title in titles:
            if not _about_company(str(title), company):
                continue
            extract, src = _rest(str(title).replace(" ", "_"))
            if extract:
                return extract, src
    except Exception:
        pass
    return "", ""


def _ddg_instant(company: str) -> tuple[str, str]:
    import json
    import urllib.parse

    q = urllib.parse.quote(f"{company} company")
    url = (
        f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
    )
    try:
        obj = json.loads(_http_get(url, timeout=10))
        abstract = (obj.get("AbstractText") or "").strip()
        src = (obj.get("AbstractURL") or "").strip()
        heading = (obj.get("Heading") or "").strip()
        if abstract and (
            _about_company(heading, company) or _about_company(abstract[:240], company)
        ):
            return abstract, src or url
    except Exception:
        pass
    return "", ""


def _fetch_homepage_blurb(url: str) -> str:
    try:
        html = _http_get(url, timeout=12)
    except Exception:
        return ""
    text = _strip_html(html)
    text = re.sub(r"\s+", " ", text).strip()
    # Skip cookie walls / tiny pages
    if len(text) < 80:
        return ""
    return text[:900]


def search_company_summary(company: str) -> tuple[str, list[str], str]:
    """Return (summary, sources, how). Empty summary if nothing real was found."""
    company = (company or "").strip()
    if not company:
        return "", [], ""

    sources: list[str] = []

    wiki, wiki_src = _wikipedia_summary(company)
    if wiki:
        sources.append(wiki_src)
        return wiki[:800], sources, "wikipedia"

    instant, instant_src = _ddg_instant(company)
    if instant:
        if instant_src:
            sources.append(instant_src)
        return instant[:800], sources, "duckduckgo_instant"

    snippets: list[str] = []
    homepage = ""
    try:
        hits = _ddg_search(f"{company} official website what they do", n=5)
    except Exception as e:
        print(f"  [company] web search failed: {e}")
        hits = []

    skip = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "youtube.com",
            "crunchbase.com", "glassdoor.com", "indeed.com")
    for hit in hits:
        url = hit.get("url") or ""
        sources.append(url)
        sn = (hit.get("snippet") or "").strip()
        if sn:
            snippets.append(sn)
        if not homepage and url.startswith("http"):
            if not any(s in url.lower() for s in skip):
                slug = _norm_name(company)
                host = _norm_name(url.split("/")[2] if "://" in url else url)
                if slug and slug[:6] in host:
                    homepage = url
                elif not homepage:
                    homepage = url

    if homepage:
        blurb = _fetch_homepage_blurb(homepage)
        if blurb:
            sources.insert(0, homepage)
            combined = blurb
            if snippets:
                combined = blurb[:500] + " " + snippets[0]
            return combined[:800], sources[:5], f"homepage:{homepage}"

    if snippets:
        return " ".join(snippets)[:800], sources[:5], "duckduckgo_snippets"

    # Last resort: try obvious homepages. Keep only if the page names the company.
    slug = re.sub(r"[^a-z0-9]+", "", company.lower())
    if len(slug) >= 4:
        guesses = [
            f"https://www.{slug}.com",
            f"https://{slug}.com",
            f"https://www.{slug}.ai",
            f"https://{slug}.ai",
            f"https://www.{slug}.io",
        ]
        for url in guesses:
            blurb = _fetch_homepage_blurb(url)
            if blurb and _about_company(blurb[:400], company):
                sources.append(url)
                return blurb[:800], sources[:5], f"homepage_guess:{url}"

    return "", sources[:5], ""


def gather_company_context(
    profile_url: str | None = None,
    *,
    current_page: bool = True,
) -> dict[str, Any]:
    """Name/title/company from LinkedIn/Apollo + a real web summary of the company."""
    print("  [company] gathering person + company context")

    captured = None
    cdp_ok = False
    try:
        from email_workflow_automation import config as ewa_config
        from email_workflow_automation.browser_util import cdp_debug_info

        if ewa_config.REQUIRE_DEBUG_CHROME:
            cdp_ok, cdp_detail = cdp_debug_info()
            if not cdp_ok:
                print(f"  [company] skip LinkedIn DOM ({cdp_detail})")
        else:
            print("  [company] vision-first (REQUIRE_DEBUG_CHROME=False) — skip CDP DOM")
    except Exception as e:
        print(f"  [company] CDP probe failed: {e}")

    if current_page and cdp_ok:
        try:
            captured = capture_current_profile()
        except Exception as e:
            print(f"  [company] profile capture skipped: {e}")
    elif current_page and not cdp_ok:
        try:
            captured = capture_current_profile()
        except Exception as e:
            print(f"  [company] vision profile capture skipped: {e}")

    full_name = ""
    title = ""
    company = ""
    sources_person: list[str] = []

    if captured:
        full_name = captured.get("person") or ""
        if full_name.startswith("("):
            full_name = ""
        profile_url = profile_url or captured.get("profile_url")
        sources_person.append(captured.get("source") or "target")

    if cdp_ok:
        dom = _extract_from_linkedin_dom()
        if dom.get("full_name") and not full_name:
            full_name = dom["full_name"]
        title = title or (dom.get("title") or "")
        company = company or (dom.get("company") or "")
        if dom.get("source"):
            sources_person.append(dom["source"])

    if not (full_name and title and company):
        vis = _extract_from_vision()
        if vis.get("full_name") and not full_name:
            full_name = vis["full_name"]
        if vis.get("title") and not title:
            title = vis["title"]
        if vis.get("company") and not company:
            company = vis["company"]
        if vis.get("source"):
            sources_person.append(vis["source"])

    summary, web_sources, how = search_company_summary(company)
    needs = not bool(summary.strip()) if company else True
    if not company:
        needs = True

    ctx = {
        "first_name": _first_name(full_name) or "there",
        "full_name": full_name,
        "title": title,
        "company": company,
        "company_summary": summary.strip(),
        "needs_company_info": needs,
        "sources": web_sources,
        "person_sources": sources_person,
        "summary_source": how,
        "profile_url": profile_url or "",
    }
    print(
        f"  [company] name={ctx['full_name']!r} title={ctx['title']!r} "
        f"company={ctx['company']!r} summary_chars={len(ctx['company_summary'])} "
        f"needs_company_info={needs} via={how or 'none'}"
    )
    return ctx
