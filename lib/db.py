"""SQLite schema + helper queries."""
import json
import sqlite3
from contextlib import contextmanager

from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    start_time TEXT NOT NULL,
    duration INTEGER,
    direction TEXT,
    action TEXT,
    result TEXT,
    type TEXT,
    from_number TEXT,
    from_name TEXT,
    from_ext_number TEXT,
    to_number TEXT,
    to_name TEXT,
    to_ext_number TEXT,
    extension_id TEXT,
    recording_id TEXT,
    recording_type TEXT,
    raw_json TEXT,
    transcript TEXT,
    transcript_status TEXT,
    classification TEXT,                  -- real_discovery | ivr_hold | gatekeeper | voicemail_left | quick_hangup | wrong_number | unknown
    classification_confidence REAL,
    classification_reason TEXT,
    objections_found TEXT,                -- JSON list
    opener_extract TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_start_time ON calls(start_time);
CREATE INDEX IF NOT EXISTS idx_extension ON calls(extension_id);
CREATE INDEX IF NOT EXISTS idx_duration ON calls(duration);
CREATE INDEX IF NOT EXISTS idx_recording ON calls(recording_id);
CREATE INDEX IF NOT EXISTS idx_classification ON calls(classification);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,                  -- fetch | download | transcribe | classify | dashboard
    ran_at TEXT NOT NULL,
    date_from TEXT,
    date_to TEXT,
    processed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    note TEXT
);
"""


LEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    contact_name TEXT,
    contact_title TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    industry TEXT,
    employee_estimate TEXT,
    website TEXT,
    source TEXT,
    briefing TEXT,
    generated_date TEXT,
    status TEXT DEFAULT 'new',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_date ON leads(generated_date);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city);
"""


CRM_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,           -- normalized phone (digits only)
    primary_phone TEXT NOT NULL,
    company_name TEXT,
    city TEXT,
    state TEXT,
    industry TEXT,
    contact_name TEXT,                     -- from leads spreadsheet
    contact_title TEXT,
    contact_email TEXT,
    contact_email_general TEXT,
    website TEXT,
    lead_source TEXT,                      -- 'master_spreadsheet' | 'rc_only' | 'manual'
    stage TEXT,                            -- Cold | Attempted | Gatekeeper | DM Reached | Email Sent | Meeting | Proposal | Won | Lost
    score INTEGER,                         -- 0-100 weighted account score
    owner_rep TEXT,
    first_touch_at TEXT,
    last_touch_at TEXT,
    last_call_id TEXT,
    total_calls INTEGER DEFAULT 0,
    calls_dm INTEGER DEFAULT 0,
    calls_gatekeeper INTEGER DEFAULT 0,
    calls_voicemail INTEGER DEFAULT 0,
    next_action TEXT,
    next_action_due TEXT,
    notes_aggregated TEXT,
    manual_stage_override TEXT,            -- if rep manually set, don't auto-overwrite
    manual_notes TEXT,                     -- rep's freeform notes ("6 workers, contract ends Oct")
    manual_followup_date TEXT,             -- YYYY-MM-DD: when rep wants to revisit this account
    manual_updated_at TEXT,
    manual_updated_by TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_acc_stage ON accounts(stage);
CREATE INDEX IF NOT EXISTS idx_acc_score ON accounts(score);
CREATE INDEX IF NOT EXISTS idx_acc_owner ON accounts(owner_rep);
CREATE INDEX IF NOT EXISTS idx_acc_last ON accounts(last_touch_at);

CREATE TABLE IF NOT EXISTS call_enrichments (
    call_id TEXT PRIMARY KEY,
    account_id TEXT,
    rep_name TEXT,
    quality_score INTEGER,                 -- 0-100 single-call quality
    reached_whom TEXT,                     -- decision_maker | gatekeeper | voicemail | ivr | wrong_number | unknown
    was_productive INTEGER,                -- 0/1
    deal_signals TEXT,                     -- JSON: {budget, authority, need, timeline, each with 'present' bool and 'evidence'}
    key_moments TEXT,                      -- JSON: {best_line, worst_line, pivot_moment}
    meeting_asked INTEGER,                 -- 0/1 rep asked for meeting/demo/call
    meeting_booked INTEGER,                -- 0/1 prospect agreed to concrete day+time
    meeting_ask_phrase TEXT,               -- exact rep phrase
    meeting_prospect_response TEXT,        -- exact prospect reply
    meeting_why_not TEXT,                  -- not_asked | blocked_by_gk | ... (see VALID_WHY_NOT)
    next_step_extracted TEXT,
    next_step_due TEXT,
    objection_handled INTEGER,             -- 0/1
    coaching_feedback TEXT,                -- JSON list of 3 bullets
    summary TEXT,                          -- 1-2 sentence call recap
    stage_hint TEXT,                       -- suggested stage after this call
    enriched_at TEXT,
    FOREIGN KEY (call_id) REFERENCES calls(id)
);
CREATE INDEX IF NOT EXISTS idx_enrich_account ON call_enrichments(account_id);
CREATE INDEX IF NOT EXISTS idx_enrich_rep ON call_enrichments(rep_name);
CREATE INDEX IF NOT EXISTS idx_enrich_score ON call_enrichments(quality_score);
CREATE INDEX IF NOT EXISTS idx_enrich_reached ON call_enrichments(reached_whom);

CREATE TABLE IF NOT EXISTS call_objections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    account_id TEXT,
    rep_name TEXT,
    call_timestamp TEXT,
    category TEXT NOT NULL,                -- current_vendor | long_term_contract | not_interested | budget | bad_timing | send_info | no_authority | wrong_person | size_fit | tried_before | other
    verbatim TEXT,                         -- exact prospect phrase
    rep_response TEXT,                     -- what the rep said back
    outcome TEXT,                          -- killed_call | continued | converted
    FOREIGN KEY (call_id) REFERENCES calls(id)
);
CREATE INDEX IF NOT EXISTS idx_obj_category ON call_objections(category);
CREATE INDEX IF NOT EXISTS idx_obj_rep ON call_objections(rep_name);
CREATE INDEX IF NOT EXISTS idx_obj_outcome ON call_objections(outcome);
CREATE INDEX IF NOT EXISTS idx_obj_call ON call_objections(call_id);

CREATE TABLE IF NOT EXISTS account_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    source_call_id TEXT,
    parent_action_id INTEGER,              -- follow-up chain
    rep_name TEXT,
    action_type TEXT,                      -- send_email | follow_up_call | send_sample | callback | send_quote | other
    description TEXT,
    created_at TEXT NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending',-- pending | done | skipped
    completed_at TEXT,
    completed_by TEXT,
    source TEXT,                           -- llm_extracted | auto_follow_up | manual
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);
CREATE INDEX IF NOT EXISTS idx_act_account ON account_actions(account_id);
CREATE INDEX IF NOT EXISTS idx_act_status ON account_actions(status);
CREATE INDEX IF NOT EXISTS idx_act_rep ON account_actions(rep_name);
CREATE INDEX IF NOT EXISTS idx_act_due ON account_actions(due_date);
CREATE INDEX IF NOT EXISTS idx_act_source_call ON account_actions(source_call_id);

CREATE TABLE IF NOT EXISTS rep_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_name TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dials INTEGER DEFAULT 0,
    dm_reached INTEGER DEFAULT 0,
    gatekeeper_passed INTEGER DEFAULT 0,
    discoveries INTEGER DEFAULT 0,
    meetings_booked INTEGER DEFAULT 0,
    avg_call_score REAL,
    avg_talk_time INTEGER,
    top_objection TEXT,
    computed_at TEXT,
    UNIQUE(rep_name, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS idx_rp_rep ON rep_performance(rep_name);
CREATE INDEX IF NOT EXISTS idx_rp_period ON rep_performance(period_start, period_end);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    # Allow concurrent readers/writers
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")  # ms
    conn.executescript(SCHEMA)
    conn.executescript(CRM_SCHEMA)
    # Idempotent migrations for DBs created with earlier schema
    _migrate(conn)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_leads_table(conn):
    """Create the leads table if it doesn't exist."""
    conn.executescript(LEADS_SCHEMA)
    conn.commit()


def ensure_crm_tables(conn):
    """Create CRM tables if they don't exist."""
    conn.executescript(CRM_SCHEMA)
    conn.commit()


def _migrate(conn):
    """Add any missing columns to existing DBs. Safe to run repeatedly."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(calls)").fetchall()}
    migrations = [
        ("transcript", "TEXT"),
        ("transcript_status", "TEXT"),
        ("classification", "TEXT"),
        ("classification_confidence", "REAL"),
        ("classification_reason", "TEXT"),
        ("objections_found", "TEXT"),
        ("opener_extract", "TEXT"),
        ("fetched_at", "TEXT"),
    ]
    for col, dtype in migrations:
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE calls ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass

    # Accounts table CRM additions (lead integration columns)
    acc_cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if acc_cols:
        acc_migrations = [
            ("contact_name", "TEXT"),
            ("contact_title", "TEXT"),
            ("contact_email", "TEXT"),
            ("contact_email_general", "TEXT"),
            ("website", "TEXT"),
            ("lead_source", "TEXT"),
            ("manual_stage_override", "TEXT"),
            ("manual_notes", "TEXT"),
            ("manual_followup_date", "TEXT"),
            ("manual_updated_at", "TEXT"),
            ("manual_updated_by", "TEXT"),
        ]
        for col, dtype in acc_migrations:
            if col not in acc_cols:
                try:
                    conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {dtype}")
                except sqlite3.OperationalError:
                    pass

    # account_actions indexes depend on the table being created (in CRM_SCHEMA)
    # Nothing else to migrate here yet.

    # Call enrichments — meeting fields
    enr_cols = {row[1] for row in conn.execute("PRAGMA table_info(call_enrichments)").fetchall()}
    if enr_cols:
        enr_migrations = [
            ("meeting_asked", "INTEGER"),
            ("meeting_booked", "INTEGER"),
            ("meeting_ask_phrase", "TEXT"),
            ("meeting_prospect_response", "TEXT"),
            ("meeting_why_not", "TEXT"),
        ]
        for col, dtype in enr_migrations:
            if col not in enr_cols:
                try:
                    conn.execute(f"ALTER TABLE call_enrichments ADD COLUMN {col} {dtype}")
                except sqlite3.OperationalError:
                    pass

        # Indexes that depend on the newly-migrated columns
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_enrich_meeting ON call_enrichments(meeting_booked)",
            "CREATE INDEX IF NOT EXISTS idx_enrich_asked ON call_enrichments(meeting_asked)",
        ]:
            try:
                conn.execute(idx_sql)
            except sqlite3.OperationalError:
                pass

    conn.commit()


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_call(conn, rec: dict):
    """Insert-or-replace a call record from a RingCentral API payload."""
    r_from = rec.get("from") or {}
    r_to = rec.get("to") or {}
    recording = rec.get("recording") or {}
    from datetime import datetime, timezone
    conn.execute("""
        INSERT INTO calls
        (id, session_id, start_time, duration, direction, action, result, type,
         from_number, from_name, from_ext_number,
         to_number, to_name, to_ext_number,
         extension_id, recording_id, recording_type, raw_json, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          duration=excluded.duration,
          result=excluded.result,
          action=excluded.action,
          recording_id=excluded.recording_id,
          recording_type=excluded.recording_type,
          raw_json=excluded.raw_json,
          fetched_at=excluded.fetched_at
    """, (
        rec.get("id"),
        rec.get("sessionId"),
        rec.get("startTime"),
        rec.get("duration"),
        rec.get("direction"),
        rec.get("action"),
        rec.get("result"),
        rec.get("type"),
        r_from.get("phoneNumber"),
        r_from.get("name"),
        r_from.get("extensionNumber"),
        r_to.get("phoneNumber"),
        r_to.get("name"),
        r_to.get("extensionNumber"),
        str((rec.get("extension") or {}).get("id", "")),
        recording.get("id"),
        recording.get("type"),
        json.dumps(rec),
        datetime.now(timezone.utc).isoformat(),
    ))


def log_sync(conn, stage: str, date_from=None, date_to=None, processed=0, failed=0, note=""):
    from datetime import datetime, timezone
    conn.execute("""
        INSERT INTO sync_log (stage, ran_at, date_from, date_to, processed, failed, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (stage, datetime.now(timezone.utc).isoformat(),
          date_from.isoformat() if date_from else None,
          date_to.isoformat() if date_to else None,
          processed, failed, note))


def latest_call_time(conn):
    """Return the timestamp of the most recent call in DB, or None."""
    row = conn.execute("SELECT MAX(start_time) FROM calls").fetchone()
    return row[0] if row and row[0] else None


def calls_to_download(conn, min_duration=None):
    """Calls ≥ min_duration that have a recording but no MP3 on disk yet."""
    from . import config as cfg
    min_dur = min_duration or cfg.MIN_DURATION_FOR_RECORDING_DOWNLOAD
    return conn.execute("""
        SELECT id, recording_id, duration, from_name, to_number
        FROM calls
        WHERE recording_id IS NOT NULL
          AND duration >= ?
          AND direction = 'Outbound'
        ORDER BY start_time DESC
    """, (min_dur,)).fetchall()


def calls_to_transcribe(conn):
    """Calls with MP3 on disk but no transcript yet."""
    return conn.execute("""
        SELECT id FROM calls
        WHERE recording_id IS NOT NULL
          AND duration >= ?
          AND direction = 'Outbound'
          AND (transcript IS NULL OR transcript = '')
        ORDER BY start_time DESC
    """, (config.MIN_DURATION_FOR_RECORDING_DOWNLOAD,)).fetchall()


def calls_to_classify(conn, reclassify=False):
    """Calls with transcript but no classification yet (or all if reclassify)."""
    if reclassify:
        return conn.execute("""
            SELECT id, transcript, duration, from_name, to_number, result
            FROM calls WHERE transcript IS NOT NULL AND transcript != ''
        """).fetchall()
    return conn.execute("""
        SELECT id, transcript, duration, from_name, to_number, result
        FROM calls
        WHERE transcript IS NOT NULL AND transcript != ''
          AND (classification IS NULL OR classification = '')
    """).fetchall()
