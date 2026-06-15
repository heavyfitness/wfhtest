#!/usr/bin/env python3
"""
Fetch job leads from RemoteOK and We Work Remotely RSS feeds,
filter to entry-level / customer-service / data-entry WFH roles,
and output a pipeline-ready CSV (verified=FALSE by default — Ben reviews before setting TRUE).

Usage:
    python3 fetch_rss_leads.py [--limit N] [--output PATH]

Outputs to rss_leads.csv (or --output path).  All rows start with verified=FALSE
so nothing publishes until Ben marks them TRUE after manual review.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen, Request

# ── Feed definitions ─────────────────────────────────────────────────────────

FEEDS = [
    {
        "name": "remoteok",
        "url": "https://remoteok.com/remote-jobs.rss",
        "parser": "remoteok",
    },
    {
        "name": "wwr_customer_support",
        "url": "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
        "parser": "wwr",
    },
    {
        "name": "wwr_admin",
        "url": "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
        "parser": "wwr",
    },
]

# Keywords that suggest entry-level / our target audience
TARGET_KEYWORDS = [
    "customer service", "customer support", "data entry", "chat support",
    "administrative", "admin", "clerical", "receptionist", "scheduler",
    "transcription", "transcriptionist", "virtual assistant", "bookkeeper",
    "enrollment", "intake", "claims", "billing", "call center",
    "non-phone", "no phone", "remote rep",
]

# Hard-kill words — these are almost always scams or MLM
KILL_KEYWORDS = [
    "envelope stuffing", "multi-level", "mlm", "direct sales",
    "commission only", "commission-only", "pyramid", "cryptocurrency",
    "make money from home", "work from home earning", "passive income",
    "unlimited earning", "be your own boss", "financial freedom",
]


# ── HTML stripping ────────────────────────────────────────────────────────────

class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []

    def handle_data(self, data):
        self.result.append(data)

    def get_data(self):
        return " ".join(self.result).strip()


def strip_html(text: str) -> str:
    s = _Stripper()
    try:
        s.feed(text or "")
    except Exception:
        return text or ""
    return re.sub(r"\s+", " ", s.get_data()).strip()


# ── Feed fetching ─────────────────────────────────────────────────────────────

def fetch_feed(url: str) -> ET.Element | None:
    req = Request(url, headers={"User-Agent": "WFHConnect-Pipeline/1.0 (+https://thewfhconnect.com)"})
    try:
        with urlopen(req, timeout=15) as resp:
            return ET.fromstring(resp.read())
    except Exception as exc:
        print(f"  [warn] Failed to fetch {url}: {exc}", file=sys.stderr)
        return None


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_date(text: str | None) -> str:
    if not text:
        return str(date.today())
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt[:len(fmt)]).date().isoformat()
        except Exception:
            continue
    return str(date.today())


def parse_remoteok(root: ET.Element, source_name: str) -> list[dict]:
    items = root.findall(".//item")
    leads = []
    for item in items:
        title = strip_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        description_raw = item.findtext("description") or ""
        company = strip_html(item.findtext("{https://remoteok.com/}company") or "")
        if not company:
            # Try to extract company from title (often "Company — Role" format)
            parts = re.split(r"\s+(?:at|@|-{1,3}|–|—)\s+", title, maxsplit=1)
            company = parts[0].strip() if len(parts) > 1 else ""
        description = strip_html(description_raw)[:1500]
        pub_date = _parse_date(item.findtext("pubDate"))
        # Category/tag hints
        tags = " ".join(t.text or "" for t in item.findall("category")).lower()
        leads.append({
            "id": "",
            "company": company,
            "title": title,
            "pay": "",
            "remote": "TRUE",
            "employment_type": "FULL_TIME",
            "requirements": "",
            "description": description,
            "apply_url": link,
            "source": source_name,
            "date_found": pub_date,
            "category": _guess_category(title + " " + tags),
            "verified": "FALSE",
            "_raw_text": (title + " " + description + " " + tags).lower(),
        })
    return leads


def parse_wwr(root: ET.Element, source_name: str) -> list[dict]:
    items = root.findall(".//item")
    leads = []
    for item in items:
        title_raw = strip_html(item.findtext("title") or "")
        # WWR title format: "Company: Role at Location"  or "COMPANY: Role"
        link = (item.findtext("link") or "").strip()
        description_raw = item.findtext("description") or ""
        description = strip_html(description_raw)[:1500]
        pub_date = _parse_date(item.findtext("pubDate"))

        company = ""
        role = title_raw
        if ":" in title_raw:
            parts = title_raw.split(":", 1)
            company = parts[0].strip().title()
            role = parts[1].strip()
        # Strip "at Location" suffix
        role = re.sub(r"\s+at\s+.*$", "", role, flags=re.IGNORECASE).strip()

        leads.append({
            "id": "",
            "company": company,
            "title": role,
            "pay": "",
            "remote": "TRUE",
            "employment_type": "FULL_TIME",
            "requirements": "",
            "description": description,
            "apply_url": link,
            "source": source_name,
            "date_found": pub_date,
            "category": _guess_category(title_raw + " " + description[:200]),
            "verified": "FALSE",
            "_raw_text": (title_raw + " " + description).lower(),
        })
    return leads


def _guess_category(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("customer service", "customer support", "client service", "call center")):
        return "customer-service"
    if any(k in t for k in ("data entry", "data-entry", "clerical", "typing")):
        return "data-entry"
    if any(k in t for k in ("chat", "non-phone", "no phone", "messaging")):
        return "non-phone"
    if any(k in t for k in ("transcri",)):
        return "transcription"
    if any(k in t for k in ("admin", "virtual assistant", "scheduling", "receptionist")):
        return "admin-support"
    if any(k in t for k in ("book", "accounting", "billing", "payroll", "finance")):
        return "finance"
    return "remote-jobs"


# ── Filtering ─────────────────────────────────────────────────────────────────

def is_target(lead: dict) -> bool:
    text = lead.get("_raw_text", "")
    # Kill check first
    if any(k in text for k in KILL_KEYWORDS):
        return False
    # Must match at least one target keyword (or be in a targeted category already)
    if lead.get("category") not in ("remote-jobs",):
        return True  # already categorised as something specific
    return any(k in text for k in TARGET_KEYWORDS)


# ── Main ──────────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "id", "company", "title", "pay", "remote", "employment_type",
    "requirements", "description", "apply_url", "source",
    "date_found", "category", "verified",
]


def main():
    parser = argparse.ArgumentParser(description="Fetch RSS leads to CSV")
    parser.add_argument("--limit", type=int, default=20, help="Max leads per feed")
    parser.add_argument("--output", default="rss_leads.csv", help="Output CSV path")
    args = parser.parse_args()

    all_leads: list[dict] = []

    for feed in FEEDS:
        print(f"Fetching {feed['name']}…", file=sys.stderr)
        root = fetch_feed(feed["url"])
        if root is None:
            continue
        if feed["parser"] == "remoteok":
            leads = parse_remoteok(root, feed["name"])
        else:
            leads = parse_wwr(root, feed["name"])
        filtered = [l for l in leads if is_target(l)][:args.limit]
        print(f"  {len(leads)} items → {len(filtered)} after filter", file=sys.stderr)
        all_leads.extend(filtered)

    # Deduplicate by apply_url
    seen: set[str] = set()
    deduped: list[dict] = []
    for lead in all_leads:
        key = lead["apply_url"].strip().lower().rstrip("/")
        if key and key not in seen:
            seen.add(key)
            deduped.append(lead)

    print(f"\nTotal after dedup: {len(deduped)} leads", file=sys.stderr)
    print(f"Writing to {args.output}", file=sys.stderr)
    print("\nAll leads verified=FALSE — review and set to TRUE before pipeline run.\n", file=sys.stderr)

    out = Path(args.output)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    # Print summary table
    print(f"{'#':<4} {'Company':<28} {'Title':<40} {'Category':<18} {'Source'}")
    for i, lead in enumerate(deduped, 1):
        print(
            f"{i:<4} {lead['company'][:27]:<28} {lead['title'][:39]:<40} "
            f"{lead['category']:<18} {lead['source']}"
        )


if __name__ == "__main__":
    main()
