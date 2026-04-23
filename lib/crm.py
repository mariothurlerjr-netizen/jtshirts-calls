"""
CRM layer — runs after classify + enrich.

Responsibilities:
1. Persist call_enrichments and call_objections records from lib.enrich output.
2. Aggregate calls per account (keyed by normalized to_number).
3. Compute account stage using progression rule + most recent call's stage_hint.
4. Compute weighted account score (recent calls weigh more).
5. Compute rep performance snapshots.
"""
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

log = logging.getLogger("crm")

# Ordered from weakest to strongest progression
STAGE_ORDER = [
    "New leads", "Attempted", "Gatekeeper", "DM Reached",
    "Email to Send", "Meeting", "Proposal", "Won",
]
# "Nurture" = dormant lead (graveyard — come back later), manual-only
# "Lost"    = terminal dead lead, manual-only
MANUAL_STAGES = {"Nurture", "Lost", "Won"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_phone(phone: str) -> str:
    """Keep digits only. Drop leading 1 if 11 digits."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _stage_rank(stage: str) -> int:
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage)
    return -1


FOLLOWUP_HOURS = 48  # when an email is "done", auto-create a follow-up call in 48h


def _classify_action_type(description: str) -> str:
    """Rough heuristic to tag action type from free-text next-step."""
    if not description:
        return "other"
    d = description.lower()
    if "email" in d or "e-mail" in d or "send info" in d or "catalog" in d or "link" in d:
        return "send_email"
    if "sample" in d or "quote" in d or "price" in d or "proposal" in d:
        return "send_sample"
    if "call" in d or "callback" in d or "ring" in d or "schedule" in d or "meeting" in d:
        return "follow_up_call"
    return "other"


def upsert_action_from_enrichment(conn: sqlite3.Connection, call_id: str,
                                   account_id: str, rep_name: str,
                                   enrich: dict):
    """If the enrichment extracted a next_step, create/update a pending action."""
    next_step = (enrich.get("next_step_extracted") or "").strip()
    if not next_step:
        return
    due = (enrich.get("next_step_due") or "").strip() or None
    action_type = _classify_action_type(next_step)

    # Idempotent: one action per source_call_id
    existing = conn.execute(
        "SELECT id, status FROM account_actions WHERE source_call_id = ? AND source = 'llm_extracted'",
        (call_id,)
    ).fetchone()
    if existing:
        # Only update the description/due, don't re-open done actions
        if existing[1] == "pending":
            conn.execute("""
                UPDATE account_actions SET
                    description = ?, due_date = ?, action_type = ?, rep_name = ?
                WHERE id = ?
            """, (next_step, due, action_type, rep_name, existing[0]))
        return

    conn.execute("""
        INSERT INTO account_actions
            (account_id, source_call_id, rep_name, action_type, description,
             created_at, due_date, status, source)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (account_id, call_id, rep_name, action_type, next_step,
          _now(), due, "pending", "llm_extracted"))


def set_account_stage(conn: sqlite3.Connection, account_id: str, new_stage: str, by_rep: str = ""):
    """Manually move an account to a new stage. Writes manual_stage_override
    so subsequent aggregate runs don't flip it back."""
    conn.execute("""
        UPDATE accounts
        SET stage = ?, manual_stage_override = ?, updated_at = ?,
            manual_updated_at = ?, manual_updated_by = ?
        WHERE account_id = ?
    """, (new_stage, new_stage, _now(), _now(), by_rep, account_id))


def import_leads_from_xlsx(xlsx_path: str, conn: sqlite3.Connection) -> dict:
    """Parse a leads XLSX and upsert into accounts. Returns stats dict.
    Auto-detects columns: Empresa/Company, Segmento/Industry, Cidade/City,
    Estado/State, Telefone/Phone, Owner_Nome/Contact, Owner_Title/Title,
    Email_Owner/Email, Email_Geral/GeneralEmail, Website.
    """
    import pandas as pd
    SKIP_SHEETS = {"resumo", "summary", "overview", "sumario"}

    xl = pd.ExcelFile(xlsx_path)
    frames = []
    for s in xl.sheet_names:
        if s.lower() in SKIP_SHEETS:
            continue
        sdf = xl.parse(s)
        sdf["_source_sheet"] = s
        frames.append(sdf)
    if not frames:
        return {"inserted": 0, "updated": 0, "skipped": 0, "total": 0,
                "error": "No data sheets found"}
    df = pd.concat(frames, ignore_index=True)

    def col_match(keywords):
        for c in df.columns:
            cn = str(c).lower().replace("_", "").replace(" ", "")
            for kw in keywords:
                if kw in cn:
                    return c
        return None

    col_phone = col_match(["phone", "telefone"])
    col_company = col_match(["empresa", "company", "name"])
    col_city = col_match(["cidade", "city"])
    col_state = col_match(["estado", "state"])
    col_industry = col_match(["segmento", "industry", "vertical", "segment", "categoria"])
    col_contact = col_match(["ownernome", "ownername", "contactname", "nomeowner", "contatonome", "dono"])
    col_title = col_match(["ownertitle", "title", "cargo", "position"])
    col_email_owner = col_match(["emailowner", "owneremail", "emailpessoal", "emailpersonal"])
    col_email_general = col_match(["emailgeral", "generalemail", "emailgeneric", "emailinfo"])
    col_website = col_match(["website", "site", "url", "domain"])

    if not col_phone:
        return {"inserted": 0, "updated": 0, "skipped": 0, "total": len(df),
                "error": "Phone column not detected"}

    ins = upd = skipped = 0
    for _, row in df.iterrows():
        phone_raw = str(row[col_phone] or "")
        account_id = _normalize_phone(phone_raw)
        if not account_id or len(account_id) < 7:
            skipped += 1
            continue

        def val(c):
            if not c:
                return None
            v = row.get(c)
            if pd.isna(v):
                return None
            s = str(v).strip()
            return s or None

        existing = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id = ?",
            (account_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE accounts SET
                    company_name = COALESCE(?, company_name),
                    city = COALESCE(?, city),
                    state = COALESCE(?, state),
                    industry = COALESCE(?, industry),
                    contact_name = COALESCE(?, contact_name),
                    contact_title = COALESCE(?, contact_title),
                    contact_email = COALESCE(?, contact_email),
                    contact_email_general = COALESCE(?, contact_email_general),
                    website = COALESCE(?, website),
                    lead_source = COALESCE(lead_source, 'master_spreadsheet'),
                    updated_at = ?
                WHERE account_id = ?
            """, (
                val(col_company), val(col_city), val(col_state), val(col_industry),
                val(col_contact), val(col_title), val(col_email_owner),
                val(col_email_general), val(col_website),
                _now(), account_id,
            ))
            upd += 1
        else:
            conn.execute("""
                INSERT INTO accounts (
                    account_id, primary_phone, company_name, city, state, industry,
                    contact_name, contact_title, contact_email, contact_email_general,
                    website, lead_source, stage, score, total_calls, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                account_id, phone_raw,
                val(col_company), val(col_city), val(col_state), val(col_industry),
                val(col_contact), val(col_title), val(col_email_owner),
                val(col_email_general), val(col_website),
                "master_spreadsheet", "New leads", 0, 0, _now(),
            ))
            ins += 1

    return {"inserted": ins, "updated": upd, "skipped": skipped, "total": len(df)}


def round_robin_assign(conn: sqlite3.Connection, account_ids: list, rep_names: list) -> dict:
    """Distribute account_ids among rep_names in a simple round-robin.
    Sets owner_rep on each account. Returns per-rep counts."""
    if not account_ids or not rep_names:
        return {}
    counts = {r: 0 for r in rep_names}
    n = len(rep_names)
    for i, aid in enumerate(account_ids):
        rep = rep_names[i % n]
        conn.execute(
            "UPDATE accounts SET owner_rep = ?, updated_at = ? WHERE account_id = ?",
            (rep, _now(), aid)
        )
        counts[rep] += 1
    return counts


EDITABLE_ACCOUNT_FIELDS = {
    "company_name", "industry", "city", "state",
    "contact_name", "contact_title", "contact_email",
    "contact_email_general", "website", "primary_phone",
}


def save_account_fields(conn: sqlite3.Connection, account_id: str,
                         updates: dict, by_rep: str = ""):
    """Update editable account fields. `updates` keys must be in EDITABLE_ACCOUNT_FIELDS."""
    filtered = {k: (v if (v is not None and v != "") else None)
                for k, v in updates.items() if k in EDITABLE_ACCOUNT_FIELDS}
    if not filtered:
        return
    sets = [f"{k} = ?" for k in filtered]
    params = list(filtered.values())
    sets.append("manual_updated_at = ?")
    params.append(_now())
    sets.append("manual_updated_by = ?")
    params.append(by_rep)
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(account_id)
    conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE account_id = ?", params)


def create_manual_action(conn: sqlite3.Connection, account_id: str,
                          rep_name: str, action_type: str,
                          description: str, due_date: str = None):
    """Create a pending action manually (not inferred from an LLM call)."""
    if action_type not in {"send_email", "follow_up_call", "send_sample",
                            "callback", "send_quote", "other"}:
        action_type = "other"
    conn.execute("""
        INSERT INTO account_actions
            (account_id, rep_name, action_type, description, created_at,
             due_date, status, source)
        VALUES (?,?,?,?,?,?,?,?)
    """, (account_id, rep_name or "", action_type, description or "",
          _now(), due_date or None, "pending", "manual"))


def save_account_manual(conn: sqlite3.Connection, account_id: str,
                         notes: str = None, followup_date: str = None,
                         by_rep: str = ""):
    """Persist rep's freeform notes and/or revisit date for this account."""
    sets = []
    params = []
    if notes is not None:
        sets.append("manual_notes = ?")
        params.append(notes)
    if followup_date is not None:
        sets.append("manual_followup_date = ?")
        params.append(followup_date or None)
    if not sets:
        return
    sets.append("manual_updated_at = ?")
    params.append(_now())
    sets.append("manual_updated_by = ?")
    params.append(by_rep)
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(account_id)
    conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE account_id = ?", params)


def mark_action_done(conn: sqlite3.Connection, action_id: int, by_rep: str):
    """Mark an action done. If it was send_email, auto-create a follow-up call in 48h."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    row = conn.execute(
        "SELECT id, account_id, rep_name, action_type, description FROM account_actions WHERE id = ?",
        (action_id,)
    ).fetchone()
    if not row:
        return

    conn.execute("""
        UPDATE account_actions
        SET status = 'done', completed_at = ?, completed_by = ?
        WHERE id = ?
    """, (_now(), by_rep or row[2], action_id))

    # Auto-follow-up: after an email send, schedule a follow-up call in 48h
    if row[3] == "send_email":
        due = (_dt.now(_tz.utc) + _td(hours=FOLLOWUP_HOURS)).date().isoformat()
        description = f"Follow-up call — check if prospect received email: \"{row[4][:120]}\""
        conn.execute("""
            INSERT INTO account_actions
                (account_id, source_call_id, parent_action_id, rep_name,
                 action_type, description, created_at, due_date, status, source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (row[1], None, action_id, row[2], "follow_up_call",
              description, _now(), due, "pending", "auto_follow_up"))


def save_enrichment(conn: sqlite3.Connection, call_id: str, call_row: dict, enrich: dict) -> str:
    """
    Persist one call_enrichments row + all call_objections rows.
    Returns account_id (normalized to_number).
    """
    account_id = _normalize_phone(call_row.get("to_number") or "")
    rep_name = call_row.get("from_name") or ""
    call_ts = call_row.get("start_time") or ""

    mt = enrich.get("meeting") or {}
    conn.execute("""
        INSERT OR REPLACE INTO call_enrichments (
            call_id, account_id, rep_name,
            quality_score, reached_whom, was_productive,
            deal_signals, key_moments,
            meeting_asked, meeting_booked, meeting_ask_phrase,
            meeting_prospect_response, meeting_why_not,
            next_step_extracted, next_step_due,
            objection_handled, coaching_feedback,
            summary, stage_hint, enriched_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        call_id,
        account_id,
        rep_name,
        enrich["quality_score"],
        enrich["reached_whom"],
        1 if enrich["was_productive"] else 0,
        json.dumps(enrich["deal_signals"]),
        json.dumps(enrich["key_moments"]),
        1 if mt.get("asked") else 0,
        1 if mt.get("booked") else 0,
        mt.get("ask_phrase", ""),
        mt.get("prospect_response", ""),
        mt.get("why_not", "not_asked"),
        enrich["next_step_extracted"],
        enrich["next_step_due"],
        1 if enrich["objection_handled"] else 0,
        json.dumps(enrich["coaching_feedback"]),
        enrich["summary"],
        enrich["stage_hint"],
        _now(),
    ))

    # Replace objections for this call (idempotent re-enrichment)
    conn.execute("DELETE FROM call_objections WHERE call_id = ?", (call_id,))
    for obj in enrich["objections"]:
        conn.execute("""
            INSERT INTO call_objections (
                call_id, account_id, rep_name, call_timestamp,
                category, verbatim, rep_response, outcome
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (
            call_id, account_id, rep_name, call_ts,
            obj["category"], obj["verbatim"], obj["rep_response"], obj["outcome"],
        ))

    # Create or update the pending action row from this call's next-step
    upsert_action_from_enrichment(conn, call_id, account_id, rep_name, enrich)

    return account_id


def aggregate_accounts(conn: sqlite3.Connection) -> int:
    """
    Rebuild the accounts table from current calls + enrichments.
    Returns count of accounts upserted.
    """
    # Pull all outbound calls with enrichment (or at least classification)
    rows = conn.execute("""
        SELECT
            c.id, c.start_time, c.duration, c.from_name, c.to_number, c.to_name,
            c.classification,
            e.quality_score, e.reached_whom, e.was_productive,
            e.stage_hint, e.next_step_extracted, e.next_step_due,
            e.summary
        FROM calls c
        LEFT JOIN call_enrichments e ON e.call_id = c.id
        WHERE c.direction = 'Outbound'
          AND c.to_number IS NOT NULL AND c.to_number != ''
        ORDER BY c.start_time ASC
    """).fetchall()

    # Group by account_id (normalized phone)
    by_account: dict[str, list[dict]] = {}
    for r in rows:
        acct = _normalize_phone(r["to_number"])
        if not acct:
            continue
        by_account.setdefault(acct, []).append(dict(r))

    n = 0
    for account_id, calls in by_account.items():
        _upsert_account(conn, account_id, calls)
        n += 1

    return n


def _upsert_account(conn: sqlite3.Connection, account_id: str, calls: list[dict]):
    """Compute fields for one account and upsert."""
    calls_sorted = sorted(calls, key=lambda c: c["start_time"] or "")
    first = calls_sorted[0]
    last = calls_sorted[-1]

    # Company name: prefer to_name that isn't just the raw number, fall back
    company = ""
    for c in reversed(calls_sorted):
        nm = (c.get("to_name") or "").strip()
        if nm and not re.fullmatch(r"\+?\d+", nm):
            company = nm
            break
    if not company:
        company = last.get("to_name") or ""

    primary_phone = last.get("to_number") or ""

    # Stage: max progression across calls (enrichment hint OR classification fallback).
    # If the account has at least one call, minimum stage is "Attempted" —
    # "New leads" is reserved for accounts that have never been dialed.
    best_stage = "Attempted"
    best_rank = _stage_rank("Attempted")
    for c in calls_sorted:
        hint = c.get("stage_hint")
        if not hint:
            cls = c.get("classification") or ""
            hint = _classification_to_stage(cls)
        r = _stage_rank(hint)
        if r > best_rank:
            best_rank = r
            best_stage = hint

    # Keep manual override if it exists
    existing = conn.execute(
        "SELECT manual_stage_override FROM accounts WHERE account_id = ?",
        (account_id,)
    ).fetchone()
    if existing and existing[0]:
        override = existing[0]
        # Manual-only stages (Nurture, Lost, Won): never auto-overwrite
        if override in MANUAL_STAGES:
            best_stage = override
        else:
            # Otherwise: manual wins if we haven't progressed further
            override_rank = _stage_rank(override)
            if override_rank >= best_rank:
                best_stage = override

    # Score: weighted avg of quality_scores, recency-weighted
    scored = [(c["start_time"], c["quality_score"]) for c in calls_sorted
              if c["quality_score"] is not None]
    if scored:
        weights = [i + 1 for i in range(len(scored))]
        total_w = sum(weights)
        score = int(sum(s * w for (_t, s), w in zip(scored, weights)) / total_w)
    else:
        score = 0

    # Owner rep = rep with most calls on this account, tiebreak by most recent
    rep_counts = Counter(c["from_name"] for c in calls_sorted if c.get("from_name"))
    if rep_counts:
        owner_rep = rep_counts.most_common(1)[0][0]
    else:
        owner_rep = last.get("from_name") or ""

    # Counts
    total_calls = len(calls_sorted)
    calls_dm = sum(1 for c in calls_sorted if c.get("reached_whom") == "decision_maker")
    calls_gk = sum(1 for c in calls_sorted if c.get("reached_whom") == "gatekeeper")
    calls_vm = sum(1 for c in calls_sorted if c.get("reached_whom") == "voicemail")

    # Next action: most recent non-empty next_step
    next_action = ""
    next_action_due = ""
    for c in reversed(calls_sorted):
        if c.get("next_step_extracted"):
            next_action = c["next_step_extracted"]
            next_action_due = c.get("next_step_due") or ""
            break

    # Notes aggregated = summaries of last 3 calls
    summaries = [c.get("summary") for c in reversed(calls_sorted) if c.get("summary")]
    notes_aggregated = " | ".join(summaries[:3])

    conn.execute("""
        INSERT INTO accounts (
            account_id, primary_phone, company_name, stage, score, owner_rep,
            first_touch_at, last_touch_at, last_call_id,
            total_calls, calls_dm, calls_gatekeeper, calls_voicemail,
            next_action, next_action_due, notes_aggregated, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_id) DO UPDATE SET
            primary_phone=excluded.primary_phone,
            company_name=excluded.company_name,
            stage=excluded.stage,
            score=excluded.score,
            owner_rep=excluded.owner_rep,
            first_touch_at=excluded.first_touch_at,
            last_touch_at=excluded.last_touch_at,
            last_call_id=excluded.last_call_id,
            total_calls=excluded.total_calls,
            calls_dm=excluded.calls_dm,
            calls_gatekeeper=excluded.calls_gatekeeper,
            calls_voicemail=excluded.calls_voicemail,
            next_action=excluded.next_action,
            next_action_due=excluded.next_action_due,
            notes_aggregated=excluded.notes_aggregated,
            updated_at=excluded.updated_at
    """, (
        account_id, primary_phone, company, best_stage, score, owner_rep,
        first["start_time"], last["start_time"], last["id"],
        total_calls, calls_dm, calls_gk, calls_vm,
        next_action, next_action_due, notes_aggregated, _now(),
    ))


def _classification_to_stage(cls: str) -> str:
    """Map old classification to stage for backfill before enrichment exists.
    Any real call attempt = at least 'Attempted'. 'New leads' is reserved for
    accounts that have never been dialed at all."""
    return {
        "real_discovery": "DM Reached",
        "gatekeeper": "Gatekeeper",
        "voicemail_left": "Attempted",
        "ivr_hold": "Attempted",
        "quick_hangup": "Attempted",
        "wrong_number": "Attempted",
        "unknown": "Attempted",
    }.get(cls, "Attempted")


def compute_rep_performance(conn: sqlite3.Connection, period_days: int = 30):
    """Refresh rep_performance snapshots for the last N days."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=period_days)

    rows = conn.execute("""
        SELECT
            c.from_name AS rep,
            c.id, c.duration,
            e.quality_score, e.reached_whom,
            e.meeting_asked, e.meeting_booked
        FROM calls c
        LEFT JOIN call_enrichments e ON e.call_id = c.id
        WHERE c.direction = 'Outbound'
          AND c.start_time >= ?
          AND c.from_name IS NOT NULL AND c.from_name != ''
    """, (start.isoformat(),)).fetchall()

    by_rep: dict[str, list] = {}
    for r in rows:
        by_rep.setdefault(r["rep"], []).append(r)

    # Top objection per rep
    top_obj_rows = conn.execute("""
        SELECT rep_name, category, COUNT(*) AS n
        FROM call_objections
        WHERE call_timestamp >= ?
        GROUP BY rep_name, category
        ORDER BY rep_name, n DESC
    """, (start.isoformat(),)).fetchall()
    top_obj: dict[str, str] = {}
    for r in top_obj_rows:
        if r["rep_name"] not in top_obj:
            top_obj[r["rep_name"]] = r["category"]

    for rep, rs in by_rep.items():
        dials = len(rs)
        dm = sum(1 for r in rs if r["reached_whom"] == "decision_maker")
        gk = sum(1 for r in rs if r["reached_whom"] == "gatekeeper")
        meetings = sum(1 for r in rs if r["meeting_booked"] == 1)
        scores = [r["quality_score"] for r in rs if r["quality_score"] is not None]
        avg_score = sum(scores) / len(scores) if scores else None
        durations = [r["duration"] for r in rs if r["duration"]]
        avg_talk = int(sum(durations) / len(durations)) if durations else None

        conn.execute("""
            INSERT OR REPLACE INTO rep_performance (
                rep_name, period_start, period_end,
                dials, dm_reached, gatekeeper_passed, discoveries, meetings_booked,
                avg_call_score, avg_talk_time, top_objection, computed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            rep, start.isoformat(), now.isoformat(),
            dials, dm, gk, dm, meetings,
            avg_score, avg_talk, top_obj.get(rep, ""), _now(),
        ))


def calls_to_enrich(conn: sqlite3.Connection, only_classes=None, reenrich: bool = False):
    """Return calls that have a transcript but no enrichment yet."""
    base_sql = """
        SELECT c.id, c.transcript, c.duration, c.from_name, c.to_number, c.to_name,
               c.result, c.classification, c.start_time
        FROM calls c
        LEFT JOIN call_enrichments e ON e.call_id = c.id
        WHERE c.transcript IS NOT NULL AND c.transcript != ''
          AND c.direction = 'Outbound'
    """
    params: list = []
    if not reenrich:
        base_sql += " AND e.call_id IS NULL"
    if only_classes:
        placeholders = ",".join("?" * len(only_classes))
        base_sql += f" AND c.classification IN ({placeholders})"
        params.extend(only_classes)
    base_sql += " ORDER BY c.start_time DESC"
    return conn.execute(base_sql, params).fetchall()
