"""
Lead generation pipeline for J T-Shirts.

Uses DuckDuckGo search to find ICP-fit businesses, then enriches
each lead with decision-maker info via OpenAI GPT-4o-mini.

No extra API keys needed — uses existing OPENAI_API_KEY + free DDG search.
"""
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import requests as _requests
from duckduckgo_search import DDGS

from . import config

log = logging.getLogger("leadgen")

# ── ICP configuration ────────────────────────────────────────────────────

INDUSTRIES = [
    "commercial cleaning company",
    "janitorial service",
    "HVAC contractor",
    "plumbing company",
    "electrical contractor",
    "general contractor construction",
    "landscaping company",
    "property maintenance company",
    "auto repair shop",
    "restaurant",
    "pest control company",
    "roofing contractor",
    "painting contractor",
    "moving company",
    "courier delivery service",
]

# Markets ranked by discovery rate from call data
MARKETS = [
    ("Phoenix", "AZ"),
    ("Scottsdale", "AZ"),
    ("Mesa", "AZ"),
    ("Boston", "MA"),
    ("Worcester", "MA"),
    ("Detroit", "MI"),
    ("Ann Arbor", "MI"),
    ("Dallas", "TX"),
    ("Fort Worth", "TX"),
    ("Plano", "TX"),
    ("Cleveland", "OH"),
    ("Akron", "OH"),
    ("Houston", "TX"),
    ("Atlanta", "GA"),
    ("Marietta", "GA"),
    ("Nashville", "TN"),
    ("Charlotte", "NC"),
    ("Tampa", "FL"),
    ("Orlando", "FL"),
    ("Denver", "CO"),
]


# ── Helpers ──────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str | None:
    """Normalize a US phone string to +1XXXXXXXXXX or None."""
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == '1':
        return f"+{digits}"
    return None


def _is_tollfree(phone: str) -> bool:
    if not phone or len(phone) < 5:
        return False
    digits = re.sub(r'\D', '', phone)
    if len(digits) >= 10:
        ac = digits[-10:-7]
        return ac in ("800", "888", "877", "866", "855", "844", "833")
    return False


def _ddg_search(query: str, max_results: int = 8) -> list[dict]:
    """Search DuckDuckGo. Returns list of {title, body, href}."""
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        log.warning("DDG search failed for '%s': %s", query[:60], str(e)[:100])
        return []


def _extract_phones_from_text(text: str) -> list[str]:
    """Pull all US phone numbers from a text block."""
    patterns = [
        r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
    ]
    phones = []
    for p in patterns:
        for m in re.finditer(p, text):
            norm = _normalize_phone(m.group())
            if norm and not _is_tollfree(norm):
                phones.append(norm)
    return list(dict.fromkeys(phones))  # dedupe preserving order


# ── OpenAI calls ─────────────────────────────────────────────────────────

def _call_openai(prompt: str, max_tokens: int = 2000) -> str:
    r = _requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI [{r.status_code}]: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


EXTRACT_PROMPT = """You are building a B2B lead list for a uniform company.
From these search results, extract REAL, DISTINCT businesses (not directory sites, not aggregator pages).

For each business return:
- company_name: exact legal/trade name
- phone: their direct phone (NOT a directory's general number). Leave empty if uncertain.
- address: physical address
- industry: cleaning, HVAC, plumbing, electrical, construction, restaurant, landscaping, auto repair, pest control, roofing, painting, moving, courier
- employee_estimate: integer guess (10-200 range for these businesses)
- website: their actual website domain

IMPORTANT:
- Each business must have a UNIQUE phone number. If two businesses share a phone, keep only the first.
- Skip any business that looks like a directory listing or aggregator (Yelp, Angi, HomeAdvisor, etc.)
- Skip franchises of national chains (ServiceMaster, Cintas, Terminix, etc.) — they already have uniform contracts.
- Only include businesses that would realistically need 10+ uniforms.

Return JSON: {{"businesses": [...]}}
If no valid businesses found, return {{"businesses": []}}

Search results:
"""

ENRICH_PROMPT = """Research this company and find the OWNER or GENERAL MANAGER's name.

Company: {company}
Location: {city}, {state}
Industry: {industry}

Search results about them:
{results}

Extract:
- contact_name: First and Last name of the owner, president, GM, or operations manager. Look for "owned by", "founded by", "managed by", "president", "CEO", names in About Us pages. If truly not found, return empty string.
- contact_title: Their title (Owner, President, GM, Operations Manager)
- briefing: One sentence a cold-caller can use. Include: what they do, how long in business, size clues, any notable detail. Example: "Family-owned HVAC company since 2008, serves residential & commercial in Phoenix metro, ~15 techs, 4.8 stars on Google."

Return ONLY JSON with those 3 keys."""


def _extract_businesses(search_results: list[dict]) -> list[dict]:
    """Use GPT to extract structured business data from search snippets."""
    if not search_results:
        return []

    formatted = "\n\n".join(
        f"Title: {r.get('title', '')}\nSnippet: {r.get('body', '')}\nURL: {r.get('href', '')}"
        for r in search_results
    )

    try:
        content = _call_openai(EXTRACT_PROMPT + formatted)
        data = json.loads(content)
        if isinstance(data, dict):
            return data.get("businesses", [])
        return []
    except Exception as e:
        log.warning("Extract failed: %s", str(e)[:100])
        return []


def _enrich_lead(company_name: str, city: str, state: str, industry: str) -> dict:
    """Search for decision maker name and build briefing."""
    empty = {"contact_name": "", "contact_title": "", "briefing": ""}

    # Try multiple search queries to find the owner
    queries = [
        f'"{company_name}" {city} {state} owner founded',
        f'"{company_name}" {city} about us team',
    ]

    all_results = []
    for q in queries:
        results = _ddg_search(q, max_results=5)
        all_results.extend(results)
        if all_results:
            break
        time.sleep(0.3)

    if not all_results:
        return empty

    formatted = "\n\n".join(
        f"Title: {r.get('title', '')}\nSnippet: {r.get('body', '')}\nURL: {r.get('href', '')}"
        for r in all_results[:8]
    )

    prompt = ENRICH_PROMPT.replace("{company}", company_name) \
        .replace("{city}", city).replace("{state}", state) \
        .replace("{industry}", industry).replace("{results}", formatted)

    try:
        content = _call_openai(prompt, max_tokens=500)
        data = json.loads(content)
        return {
            "contact_name": (data.get("contact_name") or "").strip(),
            "contact_title": (data.get("contact_title") or "").strip(),
            "briefing": (data.get("briefing") or "").strip(),
        }
    except Exception as e:
        log.warning("Enrich failed for %s: %s", company_name, str(e)[:80])
        return empty


# ── Main pipeline ────────────────────────────────────────────────────────

def generate_leads(target: int = 50, conn=None) -> list[dict]:
    """Generate `target` new leads. Deduplicates against existing DB."""
    from . import db as _db

    if conn is None:
        conn = _db.connect()
        _db.ensure_leads_table(conn)

    leads = []
    seen_phones = set()
    seen_names = set()

    # Load existing leads to skip dupes
    try:
        for row in conn.execute("SELECT phone, company_name FROM leads"):
            if row[0]:
                seen_phones.add(row[0])
            if row[1]:
                seen_names.add(row[1].lower().strip())
    except Exception:
        pass

    # Build search combos and shuffle for variety
    import random
    search_combos = [(ind, city, st) for ind in INDUSTRIES for city, st in MARKETS]
    random.shuffle(search_combos)

    log.info("Target: %d leads. Searching...", target)

    combo_idx = 0
    consecutive_empty = 0

    while len(leads) < target and combo_idx < len(search_combos):
        if consecutive_empty > 10:
            log.info("  10 consecutive empty searches, moving on...")
            consecutive_empty = 0

        industry, city, state = search_combos[combo_idx]
        combo_idx += 1

        log.info("  [%d/%d] %s in %s, %s", len(leads), target, industry, city, state)

        # Step 1: Search for businesses
        raw = _ddg_search(f"{industry} {city} {state} phone", max_results=10)
        if not raw:
            consecutive_empty += 1
            time.sleep(1)
            continue

        # Step 2: Extract structured data via GPT
        businesses = _extract_businesses(raw)
        if not businesses:
            consecutive_empty += 1
            continue

        consecutive_empty = 0
        added_this_round = 0

        for biz in businesses:
            if len(leads) >= target:
                break

            name = (biz.get("company_name") or "").strip()
            if not name or len(name) < 3:
                continue

            # Normalize and deduplicate by name
            name_key = name.lower().strip()
            if name_key in seen_names:
                continue

            # Normalize phone
            raw_phone = biz.get("phone", "")
            phone = _normalize_phone(raw_phone) if raw_phone else None

            # Skip toll-free
            if phone and _is_tollfree(phone):
                continue

            # Deduplicate by phone
            if phone and phone in seen_phones:
                continue

            seen_names.add(name_key)
            if phone:
                seen_phones.add(phone)

            # Step 3: Enrich — find decision maker + briefing
            log.info("    Enriching: %s", name)
            enrichment = _enrich_lead(name, city, state, biz.get("industry", industry))
            time.sleep(0.3)

            lead = {
                "id": str(uuid.uuid4()),
                "company_name": name,
                "contact_name": enrichment["contact_name"],
                "contact_title": enrichment["contact_title"],
                "phone": phone or "",
                "address": biz.get("address", ""),
                "city": city,
                "state": state,
                "industry": biz.get("industry", industry),
                "employee_estimate": str(biz.get("employee_estimate", "")),
                "website": biz.get("website", ""),
                "source": "ddg+openai",
                "briefing": enrichment["briefing"],
                "generated_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "status": "new",
                "notes": "",
            }
            leads.append(lead)
            added_this_round += 1

            contact_str = enrichment["contact_name"] or "no contact"
            phone_str = phone or "no phone"
            log.info("    + Lead #%d: %s — %s — %s", len(leads), name, contact_str, phone_str)

    # Insert into DB
    inserted = 0
    for lead in leads:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO leads
                (id, company_name, contact_name, contact_title, phone, address,
                 city, state, industry, employee_estimate, website, source,
                 briefing, generated_date, status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                lead["id"], lead["company_name"], lead["contact_name"],
                lead["contact_title"], lead["phone"], lead["address"],
                lead["city"], lead["state"], lead["industry"],
                lead["employee_estimate"], lead["website"], lead["source"],
                lead["briefing"], lead["generated_date"], lead["status"],
                lead["notes"],
            ))
            inserted += 1
        except Exception as e:
            log.warning("  DB insert failed for %s: %s", lead["company_name"], str(e)[:80])

    conn.commit()

    with_phone = sum(1 for l in leads if l.get("phone"))
    with_contact = sum(1 for l in leads if l.get("contact_name"))
    with_briefing = sum(1 for l in leads if l.get("briefing"))
    log.info("Generated %d leads: %d with phone, %d with contact name, %d with briefing",
             len(leads), with_phone, with_contact, with_briefing)
    return leads
