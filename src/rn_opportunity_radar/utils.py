from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from rn_opportunity_radar.config import DEFAULT_HEADERS, REQUEST_TIMEOUT_SECONDS, TIMEZONE


WHITESPACE_RE = re.compile(r"\s+")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
SHORT_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
LONG_DATE_RE = re.compile(
    r"\b("
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{1,2},\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.replace("\xa0", " ").replace("\u202f", " ")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip(" |")


def truncate_text(value: str, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def fetch_text(session: requests.Session, url: str, **kwargs: Any) -> str:
    response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    response.raise_for_status()
    return response.text


def fetch_json(session: requests.Session, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    response.raise_for_status()
    return response.json()


def absolute_url(base_url: str, maybe_relative: str) -> str:
    return urljoin(base_url, maybe_relative)


def ensure_query_parameter(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params[key] = value
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def stable_lead_key(source_key: str, identifier: str) -> str:
    digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()
    return f"{source_key}:{digest}"


def today_local() -> datetime:
    return datetime.now(TIMEZONE)


def today_iso() -> str:
    return today_local().date().isoformat()


def now_iso() -> str:
    return today_local().isoformat()


def extract_date(value: str) -> str:
    text = clean_text(value)
    for pattern in (DATE_RE, SHORT_DATE_RE, LONG_DATE_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def keep_best_text(*values: str) -> str:
    cleaned = [clean_text(value) for value in values if clean_text(value)]
    if not cleaned:
        return ""
    return max(cleaned, key=len)


def parse_loose_mapping(value: str | None) -> dict[str, Any]:
    raw = clean_text(value)
    if not raw:
        return {}

    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(raw)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return {str(key): parsed[key] for key in parsed}
    return {}


def dedupe_text_list(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped


def repo_path(root: Path, *parts: str) -> Path:
    return root.joinpath(*parts)
