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


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(SCHEMA)
    # Idempotent migrations for DBs created with earlier schema
    _migrate(conn)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_leads_table(conn):
    """Create the leads table if it doesn't exist."""
    conn.executescript(LEADS_SCHEMA)
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
