from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from rn_opportunity_radar.browser import BrowserRenderer
from rn_opportunity_radar.config import MAX_DESCRIPTION_CHARS, SOURCE_DEFINITIONS
from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.utils import (
    absolute_url,
    clean_text,
    dedupe_text_list,
    ensure_query_parameter,
    extract_date,
    fetch_json,
    fetch_text,
    keep_best_text,
    parse_loose_mapping,
    stable_lead_key,
    truncate_text,
)


NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    lowered = clean_text(value).lower()
    return NON_WORD_RE.sub("-", lowered).strip("-")


def normalize_location(value: str) -> str:
    normalized = clean_text(value).lower()
    normalized = normalized.replace("us-or-", "")
    normalized = normalized.replace("us-wa-", "")
    normalized = normalized.replace("us-ca-", "")
    normalized = normalized.replace(" united states", "")
    normalized = normalized.replace(";", ",")
    return clean_text(normalized)


def build_providence_detail_url(job: dict[str, Any], origin_host: str) -> str:
    guid = clean_text(str(job.get("guid") or job.get("reqid") or ""))
    title_slug = clean_text(str(job.get("title_slug") or "")) or slugify(str(job.get("title_exact") or "job"))
    city = slugify(str(job.get("city_exact") or ""))
    state = slugify(str(job.get("state_short") or ""))
    base = f"https://{origin_host}"
    if city and state:
        return f"{base}/{city}-{state}/{title_slug}/{guid}/job/"
    return f"{base}/{title_slug}/{guid}/job/"


def _source_class(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("source_class", "official"))).lower() or "official"


def _source_track(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("track", "core_rn_oregon"))).lower() or "core_rn_oregon"


def _source_subtrack(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("subtrack", "health_system"))).lower() or "health_system"


def _source_horizon(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("horizon", "post_reinstatement"))).lower() or "post_reinstatement"


def _source_geo_scope(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("geo_scope", "oregon"))).lower() or "oregon"


def _source_rn_leverage_type(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("rn_leverage_type", "direct_clinical"))).lower() or "direct_clinical"


def _source_relocation_risk(definition: dict[str, object]) -> str:
    return clean_text(str(definition.get("relocation_risk", "none"))).lower() or "none"


def _lead_profile(definition: dict[str, object]) -> dict[str, str]:
    return {
        "track": _source_track(definition),
        "subtrack": _source_subtrack(definition),
        "horizon": _source_horizon(definition),
        "geo_scope": _source_geo_scope(definition),
        "rn_leverage_type": _source_rn_leverage_type(definition),
        "relocation_risk": _source_relocation_risk(definition),
    }


def _extract_numeric_id(value: str) -> str:
    match = re.search(r"(\d{5,})", value)
    return match.group(1) if match else ""


def _flatten_text(node: BeautifulSoup | None) -> str:
    if node is None:
        return ""
    return clean_text(node.get_text(" ", strip=True))


def _extract_location_parts(payload: Any) -> list[str]:
    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            parts.extend(_extract_location_parts(item))
        return parts

    if not isinstance(payload, dict):
        return []

    address = payload.get("address") if payload.get("address") else payload
    if not isinstance(address, dict):
        return []

    ordered = [
        clean_text(str(address.get("addressLocality") or "")),
        clean_text(str(address.get("addressRegion") or "")),
        clean_text(str(address.get("postalCode") or "")),
        clean_text(str(address.get("addressCountry") or "")),
    ]
    return [part for part in ordered if part]


def _source_priority(lead: OpportunityLead) -> int:
    try:
        return int(lead.metadata.get("source_priority", 0))
    except (TypeError, ValueError):
        return 0


def _lead_rank(lead: OpportunityLead) -> tuple[int, int, int]:
    return (_source_priority(lead), len(clean_text(lead.description)), len(clean_text(lead.location)))


def _prefer_lead(current: OpportunityLead, incoming: OpportunityLead) -> OpportunityLead:
    return incoming if _lead_rank(incoming) > _lead_rank(current) else current


def _dedupe_by(leads: list[OpportunityLead], key_builder) -> tuple[list[OpportunityLead], Counter[str]]:
    best: dict[str, OpportunityLead] = {}
    deduped_away: Counter[str] = Counter()
    for lead in leads:
        key = key_builder(lead)
        if not key:
            best[lead.lead_key] = _prefer_lead(best.get(lead.lead_key, lead), lead)
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = lead
            continue

        preferred = _prefer_lead(existing, lead)
        removed = existing if preferred is lead else lead
        deduped_away[removed.source_key] += 1
        best[key] = preferred
    return list(best.values()), deduped_away


def dedupe_leads_with_stats(leads: list[OpportunityLead]) -> tuple[list[OpportunityLead], Counter[str]]:
    deduped, stats = _dedupe_by(leads, lambda lead: f"{lead.track}::{clean_text(lead.detail_url).lower()}")
    deduped, next_stats = _dedupe_by(
        deduped,
        lambda lead: (
            f"{lead.track}::{lead.company.lower()}::{clean_text(str(lead.metadata.get('requisition_id') or '')).lower()}"
            if lead.metadata.get("requisition_id")
            else ""
        ),
    )
    stats.update(next_stats)
    deduped, next_stats = _dedupe_by(
        deduped,
        lambda lead: (
            f"{lead.track}::{slugify(lead.company)}::{slugify(lead.title)}::{slugify(normalize_location(lead.location))}"
        ),
    )
    stats.update(next_stats)
    return (
        sorted(deduped, key=lambda lead: (-_source_priority(lead), lead.company.lower(), lead.title.lower())),
        stats,
    )


def dedupe_leads(leads: list[OpportunityLead]) -> list[OpportunityLead]:
    deduped, _ = dedupe_leads_with_stats(leads)
    return deduped


def _make_report(source_key: str, definition: dict[str, object]) -> SourceReport:
    return SourceReport(
        source_key=source_key,
        source_name=str(definition["name"]),
        source_url=str(definition["url"]),
        track=_source_track(definition),
        source_class=_source_class(definition),
    )


def _base_metadata(definition: dict[str, object]) -> dict[str, Any]:
    return {
        "source_priority": int(definition.get("source_priority", 0)),
        "source_class": _source_class(definition),
        "track": _source_track(definition),
        "subtrack": _source_subtrack(definition),
        "horizon": _source_horizon(definition),
        "geo_scope": _source_geo_scope(definition),
        "rn_leverage_type": _source_rn_leverage_type(definition),
        "relocation_risk": _source_relocation_risk(definition),
        "employer_priority": "high" if int(definition.get("source_priority", 0)) >= 96 else "medium",
    }


def _extract_meta_description(soup: BeautifulSoup) -> str:
    selectors = [
        ("meta", {"name": "description"}),
        ("meta", {"property": "og:description"}),
        ("meta", {"name": "twitter:description"}),
    ]
    for tag_name, attrs in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(str(tag["content"]))

    paragraph = soup.find("p")
    return clean_text(paragraph.get_text(" ", strip=True)) if paragraph else ""


def _scrape_signal_page(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    html = fetch_text(session, str(definition["url"]))
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text((soup.find("h1") or soup.find("title")).get_text(" ", strip=True))
    description = truncate_text(_extract_meta_description(soup), MAX_DESCRIPTION_CHARS)

    lead = OpportunityLead(
        lead_key=stable_lead_key(source_key, str(definition["url"])),
        lead_type="signal",
        source_key=source_key,
        source_name=str(definition["name"]),
        company=str(definition["company"]),
        title=title or str(definition["name"]),
        detail_url=str(definition["url"]),
        source_url=str(definition["url"]),
        description=description,
        source_context=clean_text(str(definition.get("source_context", ""))),
        discovered_via=str(definition["name"]),
        **_lead_profile(definition),
        metadata=_base_metadata(definition),
        tags=list(definition.get("tags", [])),
    )

    report.total_fetched = 1
    return [lead], report


def _parse_icims_row(
    row: BeautifulSoup,
    source_key: str,
    definition: dict[str, object],
) -> OpportunityLead | None:
    anchor = row.select_one("div.title a.iCIMS_Anchor")
    if not anchor:
        return None

    title = clean_text(anchor.get_text(" ", strip=True))
    detail_url = clean_text(anchor.get("href"))
    if not title or not detail_url:
        return None

    location_el = row.select_one("div.header.left span:last-child")
    description_el = row.select_one("div.description")
    fields: dict[str, str] = {}
    for tag in row.select("dl.iCIMS_JobHeaderGroup div.iCIMS_JobHeaderTag"):
        label = clean_text(tag.select_one("dt").get_text(" ", strip=True)) if tag.select_one("dt") else ""
        value = clean_text(tag.select_one("dd").get_text(" ", strip=True)) if tag.select_one("dd") else ""
        if label and value:
            fields[label] = value

    requisition_id = fields.get("Requisition ID", "")
    metadata = _base_metadata(definition)
    metadata.update(
        {
            "employment_type": fields.get("Position Type", ""),
            "department": fields.get("Posting Department", ""),
            "position_category": fields.get("Position Category", ""),
            "fte": fields.get("Posting FTE", ""),
            "requisition_id": requisition_id,
        }
    )

    identifier = requisition_id or detail_url or f"{title}|{clean_text(str(location_el.get_text(' ', strip=True)) if location_el else '')}"
    return OpportunityLead(
        lead_key=stable_lead_key(source_key, identifier),
        lead_type="job",
        source_key=source_key,
        source_name=str(definition["name"]),
        company=str(definition["company"]),
        title=title,
        detail_url=detail_url,
        source_url=str(definition["url"]),
        location=clean_text(location_el.get_text(" ", strip=True)) if location_el else "",
        description=truncate_text(
            clean_text(description_el.get_text(" ", strip=True)) if description_el else "",
            MAX_DESCRIPTION_CHARS,
        ),
        source_context=clean_text(str(definition.get("source_context", ""))),
        discovered_via=str(definition["name"]),
        **_lead_profile(definition),
        metadata=metadata,
    )


def _parse_icims_impression_leads(
    html: str,
    source_key: str,
    definition: dict[str, object],
) -> list[OpportunityLead]:
    soup = BeautifulSoup(html, "html.parser")
    anchors_by_id: dict[str, str] = {}
    for anchor in soup.select("a.iCIMS_Anchor"):
        href = clean_text(anchor.get("href"))
        numeric_id = _extract_numeric_id(href)
        if href and numeric_id:
            anchors_by_id[numeric_id] = href

    leads: list[OpportunityLead] = []
    for impression in _parse_legacy_impressions(html):
        raw_id = clean_text(str(impression.get("idRaw") or ""))
        detail_url = anchors_by_id.get(raw_id, "")
        title = clean_text(str(impression.get("title") or ""))
        company = clean_text(str(impression.get("company") or definition["company"]))
        location_payload = impression.get("location") or {}
        location = ", ".join(
            part
            for part in (
                clean_text(str(location_payload.get("city") or "")),
                clean_text(str(location_payload.get("state") or "")),
            )
            if part
        )
        metadata = _base_metadata(definition)
        metadata.update(
            {
                "employment_type": clean_text(str(impression.get("positionType") or "")),
                "department": clean_text(str(impression.get("category") or "")),
                "requisition_id": raw_id,
            }
        )

        identifier = raw_id or detail_url or f"{title}|{location}"
        leads.append(
            OpportunityLead(
                lead_key=stable_lead_key(source_key, identifier),
                lead_type="job",
                source_key=source_key,
                source_name=str(definition["name"]),
                company=company,
                title=title,
                detail_url=detail_url,
                source_url=str(definition["url"]),
                location=location,
                posted_date=extract_date(str(impression.get("postedDate") or "")),
                description="",
                source_context=clean_text(str(definition.get("source_context", ""))),
                discovered_via=str(definition["name"]),
                **_lead_profile(definition),
                metadata=metadata,
                tags=list(definition.get("default_tags", [])),
            )
        )

    return leads


def _scrape_icims(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    leads: list[OpportunityLead] = []
    seen_urls: set[str] = set()
    next_url = str(definition["search_url"])

    while next_url and next_url not in seen_urls and len(seen_urls) < 8:
        seen_urls.add(next_url)
        html = fetch_text(session, next_url)
        soup = BeautifulSoup(html, "html.parser")
        page_leads: list[OpportunityLead] = []

        row_selectors = [
            "div.container-fluid.iCIMS_JobsTable > div.row",
            "ul.container-fluid.iCIMS_JobsTable > li.iCIMS_JobCardItem > div.row",
        ]
        seen_detail_urls: set[str] = set()
        for selector in row_selectors:
            for row in soup.select(selector):
                lead = _parse_icims_row(row, source_key, definition)
                if not lead or lead.detail_url in seen_detail_urls:
                    continue
                seen_detail_urls.add(lead.detail_url)
                page_leads.append(lead)

        if not page_leads:
            page_leads.extend(_parse_icims_impression_leads(html, source_key, definition))

        leads.extend(page_leads)

        next_link = soup.find("link", attrs={"rel": "next"})
        next_url = clean_text(str(next_link.get("href"))) if next_link else ""

    for lead in leads:
        if lead.description or not lead.detail_url:
            continue
        try:
            detail = _parse_structured_job_detail(lead.detail_url, fetch_text(session, lead.detail_url))
        except Exception:  # pragma: no cover - defensive detail isolation
            continue
        lead.title = keep_best_text(clean_text(str(detail.get("title", ""))), lead.title)
        lead.company = keep_best_text(clean_text(str(detail.get("company", ""))), lead.company)
        lead.location = keep_best_text(clean_text(str(detail.get("location", ""))), lead.location)
        lead.posted_date = keep_best_text(clean_text(str(detail.get("posted_date", ""))), lead.posted_date)
        lead.description = truncate_text(clean_text(str(detail.get("description", ""))), MAX_DESCRIPTION_CHARS)
        if detail.get("employment_type"):
            lead.metadata["employment_type"] = clean_text(str(detail["employment_type"]))
        if detail.get("requisition_id"):
            lead.metadata["requisition_id"] = clean_text(str(detail["requisition_id"]))

    report.total_fetched = len(leads)
    return leads, report


def _scrape_providence_api(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    leads: list[OpportunityLead] = []
    api_url = str(definition["api_url"])
    base_params = dict(definition.get("params", {}))
    origin_host = str(definition["origin_host"])
    allowed_states = {clean_text(str(value)).upper() for value in definition.get("allowed_states", [])}
    headers = {
        "accept": "application/json",
        "referer": f"https://{origin_host}/",
        "x-origin": origin_host,
    }

    page = 1
    total_pages = 1
    while page <= total_pages:
        params = dict(base_params)
        params["page"] = page
        payload = fetch_json(session, api_url, params=params, headers=headers)
        jobs = payload.get("jobs", [])
        total_pages = int(payload.get("pagination", {}).get("total_pages", total_pages) or total_pages)

        for job in jobs:
            state_short = clean_text(str(job.get("state_short") or "")).upper()
            if allowed_states and state_short not in allowed_states:
                continue

            other = parse_loose_mapping(str(job.get("other") or ""))
            location = clean_text(
                str(job.get("location_exact") or "")
                or ", ".join(part for part in (job.get("city_exact"), job.get("state_short")) if part)
            )
            detail_url = build_providence_detail_url(job, origin_host)
            identifier = clean_text(str(job.get("reqid") or job.get("guid") or detail_url))
            metadata = _base_metadata(definition)
            metadata.update(
                {
                    "employment_type": clean_text(str(job.get("job_type") or other.get("schedule") or "")),
                    "department": clean_text(str(other.get("department") or job.get("job_category") or "")),
                    "remote_type": clean_text(str(other.get("workplace") or "")),
                    "requisition_id": clean_text(str(job.get("reqid") or "")),
                    "job_shift": clean_text(str(job.get("job_shift") or "")),
                    "job_category": clean_text(str(job.get("job_category") or "")),
                    "job_function": clean_text(str(job.get("job_function") or "")),
                }
            )

            leads.append(
                OpportunityLead(
                    lead_key=stable_lead_key(source_key, identifier),
                    lead_type="job",
                    source_key=source_key,
                    source_name=str(definition["name"]),
                    company=str(job.get("employer_exact") or definition["company"]),
                    title=clean_text(str(job.get("title_exact") or job.get("title") or "")),
                    detail_url=detail_url,
                    source_url=str(definition["url"]),
                    location=location,
                    posted_date=extract_date(str(job.get("date_new") or job.get("date_updated") or "")),
                    description=truncate_text(clean_text(str(job.get("description") or "")), MAX_DESCRIPTION_CHARS),
                    source_context=clean_text(str(definition.get("source_context", ""))),
                    discovered_via=str(definition["name"]),
                    **_lead_profile(definition),
                    metadata=metadata,
                )
            )

        page += 1

    report.total_fetched = len(leads)
    return leads, report


def _extract_jobposting(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("@type") == "JobPosting":
            return payload
        for value in payload.values():
            result = _extract_jobposting(value)
            if result:
                return result
        return None

    if isinstance(payload, list):
        for item in payload:
            result = _extract_jobposting(item)
            if result:
                return result
    return None


def _parse_structured_job_detail(detail_url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    jobposting: dict[str, Any] | None = None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = clean_text(script.string or script.get_text(" ", strip=True))
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        jobposting = _extract_jobposting(parsed)
        if jobposting:
            break

    meta = {
        clean_text(tag.get("name") or tag.get("property")): clean_text(tag.get("content"))
        for tag in soup.find_all("meta")
        if tag.get("content")
    }
    if not jobposting:
        return {
            "detail_url": detail_url,
            "title": keep_best_text(meta.get("og:title", ""), meta.get("description", "")),
            "posted_date": extract_date(meta.get("article:published_time", "") or meta.get("date", "")),
            "requisition_id": _extract_numeric_id(detail_url),
            "description": truncate_text(
                keep_best_text(meta.get("description", ""), _extract_meta_description(soup)),
                MAX_DESCRIPTION_CHARS,
            ),
            "location": "",
            "employment_type": "",
            "company": "",
        }

    title = clean_text(str(jobposting.get("title") or meta.get("og:title") or ""))
    posted_date = extract_date(
        str(jobposting.get("datePosted") or jobposting.get("dateCreated") or meta.get("article:published_time") or "")
    )
    description_html = keep_best_text(
        str(jobposting.get("description") or ""),
        str(jobposting.get("responsibilities") or ""),
        str(jobposting.get("qualifications") or ""),
    )
    description = clean_text(BeautifulSoup(description_html, "html.parser").get_text(" ", strip=True))
    identifier = jobposting.get("identifier") or {}
    requisition_id = ""
    if isinstance(identifier, dict):
        requisition_id = clean_text(str(identifier.get("value") or identifier.get("name") or ""))
    elif isinstance(identifier, str):
        requisition_id = clean_text(identifier)

    employment_type = jobposting.get("employmentType") or ""
    if isinstance(employment_type, list):
        employment_type = ", ".join(clean_text(str(item)) for item in employment_type if clean_text(str(item)))
    employment_type = clean_text(str(employment_type))

    company = ""
    hiring_organization = jobposting.get("hiringOrganization")
    if isinstance(hiring_organization, dict):
        company = clean_text(str(hiring_organization.get("name") or ""))
    elif isinstance(hiring_organization, str):
        company = clean_text(hiring_organization)

    location_parts = dedupe_text_list(_extract_location_parts(jobposting.get("jobLocation")))
    location = ", ".join(location_parts)

    detail_value = clean_text(str(jobposting.get("url") or detail_url)) or detail_url
    return {
        "title": title,
        "detail_url": detail_value,
        "posted_date": posted_date,
        "requisition_id": requisition_id or _extract_numeric_id(detail_value),
        "description": truncate_text(description, MAX_DESCRIPTION_CHARS),
        "location": location,
        "employment_type": employment_type,
        "company": company,
    }


def _parse_legacy_impressions(html: str) -> list[dict[str, Any]]:
    match = re.search(r"var jobImpressions = (\[.*?\]);", html, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [item for item in payload if isinstance(item, dict)]


def _parse_peacehealth_search_page(html: str) -> tuple[list[dict[str, str]], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, str]] = []
    for item in soup.select(".jobs-section__item"):
        title_anchor = item.select_one("h5 a")
        if not title_anchor:
            continue

        columns = item.select(".row > div")
        work_type = ""
        shift = ""
        benefit = ""
        location = ""
        if len(columns) >= 5:
            work_type = _flatten_text(columns[1]).replace("Work Type:", "").strip()
            shift = _flatten_text(columns[2]).replace("Shift:", "").strip()
            benefit = _flatten_text(columns[3]).replace("Benefit Eligibility:", "").strip()
            location = _flatten_text(columns[4]).replace("Location:", "").strip()

        jobs.append(
            {
                "title": _flatten_text(title_anchor),
                "detail_url": clean_text(title_anchor.get("href")),
                "work_type": work_type,
                "shift": shift,
                "benefit_eligibility": benefit,
                "location": location,
            }
        )

    next_pages = sorted(
        {
            clean_text(anchor.get("href"))
            for anchor in soup.select("a[href*='page=']")
            if clean_text(anchor.get("href"))
        }
    )
    return jobs, next_pages


def _parse_kaiser_detail(detail_url: str, html: str) -> dict[str, Any]:
    detail = _parse_structured_job_detail(detail_url, html)
    soup = BeautifulSoup(html, "html.parser")
    meta = {
        clean_text(tag.get("name")): clean_text(tag.get("content"))
        for tag in soup.find_all("meta")
        if tag.get("name") and tag.get("content")
    }
    detail["requisition_id"] = clean_text(detail.get("requisition_id", "")) or meta.get("job-ats-req-id", "")
    return detail


def _scrape_kaiser_search(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    html = fetch_text(session, str(definition["search_url"]))
    soup = BeautifulSoup(html, "html.parser")
    leads: list[OpportunityLead] = []

    selectors = [
        "section#search-results-list ul li a[data-job-id]",
        "section.job-list ul li a[data-job-id]",
    ]
    anchors: list[BeautifulSoup] = []
    for selector in selectors:
        anchors.extend(soup.select(selector))

    seen_urls: set[str] = set()
    for anchor in anchors:
        href = clean_text(anchor.get("href"))
        if not href:
            continue
        detail_url = absolute_url(str(definition["url"]), href)
        if detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        title = clean_text(anchor.get_text(" ", strip=True))
        location_el = anchor.find_next("span")
        location = clean_text(location_el.get_text(" ", strip=True)) if location_el else ""
        detail = _parse_kaiser_detail(detail_url, fetch_text(session, detail_url))

        metadata = _base_metadata(definition)
        metadata.update(
            {
                "employment_type": clean_text(detail.get("employment_type", "")),
                "requisition_id": clean_text(detail.get("requisition_id", "")),
            }
        )

        identifier = clean_text(detail.get("requisition_id", "")) or detail_url
        leads.append(
            OpportunityLead(
                lead_key=stable_lead_key(source_key, identifier),
                lead_type="job",
                source_key=source_key,
                source_name=str(definition["name"]),
                company=str(definition["company"]),
                title=keep_best_text(detail.get("title", ""), title),
                detail_url=clean_text(detail.get("detail_url", "")) or detail_url,
                source_url=str(definition["url"]),
                location=keep_best_text(detail.get("location", ""), location),
                posted_date=clean_text(detail.get("posted_date", "")),
                description=truncate_text(clean_text(detail.get("description", "")), MAX_DESCRIPTION_CHARS),
                source_context=clean_text(str(definition.get("source_context", ""))),
                discovered_via=str(definition["name"]),
                **_lead_profile(definition),
                metadata=metadata,
            )
        )

    report.total_fetched = len(leads)
    return leads, report


def _parse_board_results_page(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for tile in soup.select(".job-main-data"):
        classes = " ".join(tile.get("class", []))
        if "candidate-products-promotion-data" in classes:
            continue

        hidden = {
            clean_text(input_el.get("name")): clean_text(input_el.get("value"))
            for input_el in tile.select("input[type='hidden']")
            if clean_text(input_el.get("name"))
        }
        job_id = hidden.get("job_id", "")
        if not job_id or job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        title_anchor = tile.select_one("a[href*='/job/']")
        jobs.append(
            {
                "job_id": job_id,
                "title": hidden.get("job_Position", "") or _flatten_text(title_anchor),
                "detail_url": clean_text(title_anchor.get("href")) if title_anchor else "",
                "company": hidden.get("job_company", ""),
                "location": hidden.get("job_Location", ""),
                "job_source": hidden.get("job_source", ""),
                "job_upgrades": hidden.get("job_upgrades", ""),
            }
        )

    return jobs


def _extract_json_array(html: str, marker: str) -> list[dict[str, Any]]:
    start = html.find(marker)
    if start == -1:
        return []

    index = start + len(marker)
    depth = 0
    in_string = False
    escape_next = False
    buffer: list[str] = []

    while index < len(html):
        char = html[index]
        buffer.append(char)
        if in_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    break
        index += 1

    try:
        payload = json.loads("".join(buffer))
    except json.JSONDecodeError:
        return []
    return [item for item in payload if isinstance(item, dict)]


def _parse_ashby_job_postings(html: str) -> list[dict[str, Any]]:
    return _extract_json_array(html, '"jobPostings":')


def _scrape_ashby_jobs(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    board_url = str(definition.get("board_url") or definition["url"])
    html = fetch_text(session, board_url)
    jobs = _parse_ashby_job_postings(html)
    leads: list[OpportunityLead] = []

    for job in jobs:
        if not job.get("isListed", True):
            continue

        title = clean_text(str(job.get("title") or ""))
        department = clean_text(str(job.get("departmentExternalName") or job.get("departmentName") or ""))
        team = clean_text(str(job.get("teamExternalName") or job.get("teamName") or ""))
        location = clean_text(str(job.get("locationExternalName") or job.get("locationName") or ""))
        workplace_type = clean_text(str(job.get("workplaceType") or ""))
        employment_type = clean_text(str(job.get("employmentType") or ""))
        compensation = clean_text(str(job.get("compensationTierSummary") or ""))
        job_id = clean_text(str(job.get("jobId") or job.get("id") or ""))
        detail_url = ensure_query_parameter(board_url, "ashby_jid", job_id) if job_id else board_url

        context_parts = [
            clean_text(str(definition.get("source_context", ""))),
            f"Department {department}" if department else "",
            f"Team {team}" if team else "",
            f"Workplace {workplace_type}" if workplace_type else "",
            f"Location {location}" if location else "",
            f"Compensation {compensation}" if compensation else "",
        ]

        metadata = _base_metadata(definition)
        metadata.update(
            {
                "employment_type": employment_type,
                "department": department,
                "team_name": team,
                "workplace_type": workplace_type,
                "requisition_id": clean_text(str(job.get("jobRequisitionId") or "")) or job_id,
                "board_url": board_url,
                "compensation_summary": compensation,
            }
        )

        leads.append(
            OpportunityLead(
                lead_key=stable_lead_key(source_key, metadata["requisition_id"] or detail_url),
                lead_type="job",
                source_key=source_key,
                source_name=str(definition["name"]),
                company=str(definition["company"]),
                title=title or str(definition["name"]),
                detail_url=detail_url,
                source_url=str(definition["url"]),
                location=location,
                posted_date=clean_text(str(job.get("publishedDate") or "")),
                description=truncate_text("; ".join(part for part in context_parts if part), MAX_DESCRIPTION_CHARS),
                source_context=clean_text(str(definition.get("source_context", ""))),
                discovered_via=str(definition["name"]),
                **_lead_profile(definition),
                metadata=metadata,
                tags=list(definition.get("default_tags", [])),
            )
        )

    report.total_fetched = len(leads)
    return leads, report


def _scrape_peacehealth_jobs(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
    browser: BrowserRenderer,
) -> tuple[list[OpportunityLead], SourceReport]:
    del session

    report = _make_report(source_key, definition)
    report.used_browser = True
    report.notes.append("Used browser fallback for PeaceHealth results.")
    leads: list[OpportunityLead] = []
    seen_search_pages: set[str] = set()
    pending_pages: list[str] = [str(definition["search_url"])]
    max_pages = int(definition.get("max_pages", 2))
    allowed_states = {clean_text(str(value)).upper() for value in definition.get("allowed_states", [])}

    while pending_pages and len(seen_search_pages) < max_pages:
        search_url = pending_pages.pop(0)
        if search_url in seen_search_pages:
            continue
        seen_search_pages.add(search_url)

        html = browser.fetch_html(search_url, wait_for_selector=".jobs-section__item", extra_wait_ms=400)
        jobs, next_pages = _parse_peacehealth_search_page(html)
        for next_page in next_pages:
            absolute_page = absolute_url(search_url, next_page)
            if absolute_page not in seen_search_pages and absolute_page not in pending_pages:
                pending_pages.append(absolute_page)

        for job in jobs:
            location = clean_text(job["location"])
            if allowed_states:
                normalized_location = normalize_location(location)
                state_fragment = normalized_location.split(",")[-1].strip().upper() if "," in normalized_location else ""
                if state_fragment not in allowed_states:
                    continue

            detail_url = absolute_url(search_url, job["detail_url"])
            detail: dict[str, Any] = {}
            try:
                detail_html = browser.fetch_html(
                    detail_url,
                    wait_for_selector="script[type='application/ld+json'], h1",
                    extra_wait_ms=250,
                )
                detail = _parse_structured_job_detail(detail_url, detail_html)
            except Exception as exc:  # pragma: no cover - defensive detail isolation
                report.notes.append(f"PeaceHealth detail fallback used partial result for {detail_url}: {exc}")

            metadata = _base_metadata(definition)
            metadata.update(
                {
                    "employment_type": keep_best_text(clean_text(str(detail.get("employment_type", ""))), job["work_type"]),
                    "job_shift": job["shift"],
                    "benefit_eligibility": job["benefit_eligibility"],
                    "requisition_id": clean_text(str(detail.get("requisition_id", ""))) or _extract_numeric_id(detail_url),
                }
            )

            identifier = metadata["requisition_id"] or detail_url
            leads.append(
                OpportunityLead(
                    lead_key=stable_lead_key(source_key, str(identifier)),
                    lead_type="job",
                    source_key=source_key,
                    source_name=str(definition["name"]),
                    company=keep_best_text(clean_text(str(detail.get("company", ""))), str(definition["company"])),
                    title=keep_best_text(clean_text(str(detail.get("title", ""))), job["title"]),
                    detail_url=clean_text(str(detail.get("detail_url", ""))) or detail_url,
                    source_url=str(definition["url"]),
                    location=keep_best_text(clean_text(str(detail.get("location", ""))), location),
                    posted_date=clean_text(str(detail.get("posted_date", ""))),
                    description=truncate_text(clean_text(str(detail.get("description", ""))), MAX_DESCRIPTION_CHARS),
                    source_context=clean_text(str(definition.get("source_context", ""))),
                    discovered_via=str(definition["name"]),
                    **_lead_profile(definition),
                    metadata=metadata,
                    tags=list(definition.get("default_tags", [])),
                )
            )

    report.total_fetched = len(leads)
    return leads, report


def _scrape_board_results(
    session: requests.Session,
    source_key: str,
    definition: dict[str, object],
    browser: BrowserRenderer,
) -> tuple[list[OpportunityLead], SourceReport]:
    del session

    report = _make_report(source_key, definition)
    report.used_browser = True
    report.notes.append("Used browser fallback for board results.")
    results_url = str(definition.get("results_url") or definition["url"])
    html = browser.fetch_html(
        results_url,
        wait_for_selector=".job-main-data",
        extra_wait_ms=400,
    )
    jobs = _parse_board_results_page(html)
    leads: list[OpportunityLead] = []

    for job in jobs:
        detail_url = absolute_url(results_url, job["detail_url"])
        detail: dict[str, Any] = {}
        try:
            detail_html = browser.fetch_html(
                detail_url,
                wait_for_selector="script[type='application/ld+json'], meta[name='description']",
                extra_wait_ms=200,
            )
            detail = _parse_structured_job_detail(detail_url, detail_html)
        except Exception as exc:  # pragma: no cover - defensive detail isolation
            report.notes.append(f"Board detail fallback used partial result for {detail_url}: {exc}")

        metadata = _base_metadata(definition)
        metadata.update(
            {
                "employment_type": clean_text(str(detail.get("employment_type", ""))),
                "requisition_id": clean_text(job["job_id"]) or clean_text(str(detail.get("requisition_id", ""))),
                "job_source": clean_text(job["job_source"]),
                "job_upgrades": clean_text(job["job_upgrades"]),
                "board_source": True,
            }
        )

        identifier = metadata["requisition_id"] or detail_url
        leads.append(
            OpportunityLead(
                lead_key=stable_lead_key(source_key, str(identifier)),
                lead_type="job",
                source_key=source_key,
                source_name=str(definition["name"]),
                company=keep_best_text(clean_text(str(detail.get("company", ""))), job["company"]),
                title=keep_best_text(clean_text(str(detail.get("title", ""))), job["title"]),
                detail_url=detail_url,
                source_url=str(definition["url"]),
                location=keep_best_text(clean_text(str(detail.get("location", ""))), job["location"]),
                posted_date=clean_text(str(detail.get("posted_date", ""))),
                description=truncate_text(clean_text(str(detail.get("description", ""))), MAX_DESCRIPTION_CHARS),
                source_context=clean_text(str(definition.get("source_context", ""))),
                discovered_via=str(definition["name"]),
                **_lead_profile(definition),
                metadata=metadata,
                tags=list(definition.get("default_tags", [])),
            )
        )

    report.total_fetched = len(leads)
    return leads, report


def _scrape_todo(source_key: str, definition: dict[str, object]) -> tuple[list[OpportunityLead], SourceReport]:
    report = _make_report(source_key, definition)
    report.status = "todo"
    report.notes.append(clean_text(str(definition.get("todo_note", "Source intentionally deferred for v1."))))
    return [], report


def scrape_all_sources(
    session: requests.Session,
    browser_path: str | None = None,
    browser_profile_dir: str | None = None,
) -> tuple[list[OpportunityLead], list[SourceReport]]:
    leads: list[OpportunityLead] = []
    reports: list[SourceReport] = []
    browser: BrowserRenderer | None = None
    browser_error: Exception | None = None

    parser_map = {
        "icims": _scrape_icims,
        "providence_api": _scrape_providence_api,
        "kaiser_search": _scrape_kaiser_search,
        "ashby_jobs": _scrape_ashby_jobs,
        "signal_page": _scrape_signal_page,
    }
    browser_parser_map = {
        "peacehealth_jobs": _scrape_peacehealth_jobs,
        "board_results": _scrape_board_results,
    }

    def ensure_browser() -> BrowserRenderer:
        nonlocal browser, browser_error
        if browser_error is not None:
            raise browser_error
        if browser is not None:
            return browser
        try:
            browser = BrowserRenderer(browser_path=browser_path, profile_dir=browser_profile_dir)
            browser.__enter__()
            return browser
        except Exception as exc:  # pragma: no cover - runtime safety net
            browser_error = exc
            raise

    try:
        for source_key, definition in SOURCE_DEFINITIONS.items():
            parser_name = str(definition["parser"])
            if parser_name == "todo":
                source_leads, report = _scrape_todo(source_key, definition)
                reports.append(report)
                continue

            parser = parser_map.get(parser_name)
            browser_parser = browser_parser_map.get(parser_name)
            if parser is None and browser_parser is None:
                report = _make_report(source_key, definition)
                report.status = "error"
                report.errors.append(f"Unknown parser '{parser_name}'.")
                reports.append(report)
                continue

            try:
                if browser_parser is not None:
                    source_leads, report = browser_parser(session, source_key, definition, ensure_browser())
                else:
                    source_leads, report = parser(session, source_key, definition)
                report.status = "ok"
                if not source_leads:
                    report.notes.append("No leads returned in this run.")
                leads.extend(source_leads)
                reports.append(report)
            except Exception as exc:  # pragma: no cover - defensive source isolation
                report = _make_report(source_key, definition)
                report.status = "error"
                report.used_browser = browser_parser is not None
                report.errors.append(str(exc))
                reports.append(report)
    finally:
        if browser is not None:
            browser.__exit__(None, None, None)

    deduped, dedupe_stats = dedupe_leads_with_stats(leads)
    for report in reports:
        report.deduped_away = int(dedupe_stats.get(report.source_key, 0))
    return deduped, reports
