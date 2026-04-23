#!/usr/bin/env python3
"""
jtcalls.py — J T-Shirts Call Analytics pipeline.

Run `python jtcalls.py --help` for commands.
The big one for daily use: `python jtcalls.py daily`
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tqdm import tqdm

from lib import config, db, classify, whisper, leadgen, enrich, crm
from lib.rc import RingCentralClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jtcalls")


# ─── stages ──────────────────────────────────────────────────────────────────

def cmd_fetch(args):
    """Fetch call metadata from RingCentral into SQLite."""
    config.validate()
    client = RingCentralClient()
    conn = db.connect()
    try:
        # Determine date range
        if args.since:
            date_from = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        elif args.days:
            date_from = datetime.now(timezone.utc) - timedelta(days=args.days)
        else:
            # Incremental: since last fetched call
            last = db.latest_call_time(conn)
            if last:
                # Go back 1 day for safety (in case some calls updated)
                date_from = datetime.fromisoformat(last.replace("Z", "+00:00")) - timedelta(days=1)
            else:
                date_from = config.HISTORY_START.replace(tzinfo=timezone.utc)

        date_to = datetime.now(timezone.utc)
        log.info("Fetching %s → %s", date_from.date(), date_to.date())

        total = 0
        for rec in client.iter_call_log(date_from, date_to):
            db.upsert_call(conn, rec)
            total += 1
            if total % 500 == 0:
                conn.commit()
                log.info("  fetched %d…", total)

        conn.commit()
        db.log_sync(conn, "fetch", date_from, date_to, processed=total)
        conn.commit()
        log.info("✓ Fetched %d call records", total)
    finally:
        conn.close()


def cmd_download(args):
    """Download MP3 recordings for eligible calls."""
    config.validate()
    client = RingCentralClient()
    conn = db.connect()
    try:
        min_dur = args.min_duration or config.MIN_DURATION_FOR_RECORDING_DOWNLOAD
        rows = db.calls_to_download(conn, min_duration=min_dur)
        todo = []
        for row in rows:
            mp3 = config.RECORDINGS_DIR / f"{row['id']}.mp3"
            if not mp3.exists() or mp3.stat().st_size < 1000:
                todo.append(row)
        log.info("To download: %d recordings (of %d eligible)", len(todo), len(rows))
        if not todo:
            return

        ok = fail = 0
        for row in tqdm(todo, desc="Downloading", unit="rec"):
            mp3 = config.RECORDINGS_DIR / f"{row['id']}.mp3"
            try:
                content = client.download_recording(row["recording_id"])
                mp3.write_bytes(content)
                ok += 1
            except Exception as e:
                log.warning("  fail %s: %s", row["id"], str(e)[:80])
                fail += 1

        db.log_sync(conn, "download", processed=ok, failed=fail)
        conn.commit()
        log.info("✓ Download done: ok=%d fail=%d", ok, fail)
    finally:
        conn.close()


def cmd_transcribe(args):
    """Transcribe downloaded MP3s via Whisper."""
    config.validate()
    conn = db.connect()
    try:
        rows = db.calls_to_transcribe(conn)
        todo = []
        for row in rows:
            mp3 = config.RECORDINGS_DIR / f"{row['id']}.mp3"
            if mp3.exists() and mp3.stat().st_size > 1000:
                todo.append(row)
        log.info("To transcribe: %d MP3s", len(todo))
        if not todo:
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def run_one(row):
            call_id = row["id"]
            mp3 = config.RECORDINGS_DIR / f"{call_id}.mp3"
            txt = config.TRANSCRIPTS_DIR / f"{call_id}.txt"
            if txt.exists():
                return call_id, txt.read_text(encoding="utf-8"), None
            try:
                text = whisper.transcribe(mp3)
                txt.write_text(text, encoding="utf-8")
                return call_id, text, None
            except Exception as e:
                return call_id, None, str(e)[:120]

        ok = fail = 0
        with ThreadPoolExecutor(max_workers=5) as exec:
            futures = [exec.submit(run_one, r) for r in todo]
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Transcribing"):
                call_id, text, err = fut.result()
                if text:
                    conn.execute(
                        "UPDATE calls SET transcript=?, transcript_status='ok' WHERE id=?",
                        (text, call_id))
                    ok += 1
                else:
                    conn.execute(
                        "UPDATE calls SET transcript_status=? WHERE id=?",
                        (f"whisper_fail: {err}", call_id))
                    fail += 1
                conn.commit()

        db.log_sync(conn, "transcribe", processed=ok, failed=fail)
        conn.commit()
        log.info("✓ Transcribe done: ok=%d fail=%d", ok, fail)
    finally:
        conn.close()


def cmd_classify(args):
    """Classify transcripts."""
    config.validate()
    conn = db.connect()
    try:
        rows = db.calls_to_classify(conn, reclassify=args.reclassify)
        log.info("To classify: %d calls", len(rows))
        if not rows:
            return

        ok = fail = 0
        for row in tqdm(rows, desc="Classifying"):
            try:
                result = classify.classify_call(
                    transcript=row["transcript"],
                    duration=row["duration"],
                    result=row["result"],
                )
                conn.execute("""
                    UPDATE calls SET
                      classification=?,
                      classification_confidence=?,
                      classification_reason=?,
                      objections_found=?,
                      opener_extract=?
                    WHERE id=?
                """, (
                    result["classification"],
                    result["confidence"],
                    result["reason"],
                    json.dumps(result.get("objections", [])),
                    result.get("opener", ""),
                    row["id"],
                ))
                ok += 1
            except Exception as e:
                log.warning("  classify fail %s: %s", row["id"], str(e)[:80])
                fail += 1
            if ok % 20 == 0:
                conn.commit()

        conn.commit()
        db.log_sync(conn, "classify", processed=ok, failed=fail)
        conn.commit()
        log.info("✓ Classify done: ok=%d fail=%d", ok, fail)
    finally:
        conn.close()


def cmd_enrich(args):
    """Run LLM enrichment on transcripts that don't have enrichment yet."""
    config.validate()
    conn = db.connect()
    try:
        only_classes = None
        if args.only:
            only_classes = [c.strip() for c in args.only.split(",") if c.strip()]
        rows = crm.calls_to_enrich(conn, only_classes=only_classes, reenrich=args.reenrich)
        log.info("To enrich: %d calls", len(rows))
        if args.limit:
            rows = rows[: args.limit]
            log.info("  limiting to %d", len(rows))
        if not rows:
            return

        ok = fail = 0
        for row in tqdm(rows, desc="Enriching", unit="call"):
            try:
                data = enrich.enrich_transcript(
                    transcript=row["transcript"],
                    rep_name=row["from_name"] or "",
                    duration=row["duration"] or 0,
                    result=row["result"] or "",
                    classification=row["classification"] or "",
                    company=row["to_name"] or "",
                )
                crm.save_enrichment(conn, row["id"], dict(row), data)
                ok += 1
                if ok % 10 == 0:
                    conn.commit()
            except Exception as e:
                log.warning("  enrich fail %s: %s", row["id"], str(e)[:120])
                fail += 1

        conn.commit()
        db.log_sync(conn, "enrich", processed=ok, failed=fail)
        conn.commit()
        log.info("✓ Enrich done: ok=%d fail=%d", ok, fail)
    finally:
        conn.close()


def cmd_aggregate(args):
    """Rebuild accounts + rep_performance from current calls + enrichments."""
    conn = db.connect()
    try:
        n = crm.aggregate_accounts(conn)
        log.info("✓ Aggregated %d accounts", n)
        crm.compute_rep_performance(conn, period_days=30)
        log.info("✓ Rep performance snapshot refreshed (last 30d)")
        conn.commit()
        db.log_sync(conn, "aggregate", processed=n)
        conn.commit()
    finally:
        conn.close()


def cmd_import_leads(args):
    """Import master leads spreadsheet into accounts (joined by normalized phone)."""
    from pathlib import Path
    import pandas as pd
    from lib.crm import _normalize_phone, _now

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        log.error("File not found: %s", path)
        sys.exit(1)

    log.info("Reading %s", path)
    xl = pd.ExcelFile(path)
    log.info("Sheets: %s", xl.sheet_names)

    # Skip summary sheets (Resumo/Summary), read rest
    SKIP_SHEETS = {"resumo", "summary", "overview", "sumario"}
    frames = []
    for s in xl.sheet_names:
        if s.lower() in SKIP_SHEETS:
            log.info("  skip summary sheet: %s", s)
            continue
        sdf = xl.parse(s)
        sdf["_source_sheet"] = s
        frames.append(sdf)
        log.info("  sheet '%s': %d rows", s, len(sdf))
    if not frames:
        log.error("No data sheets found.")
        sys.exit(1)
    df = pd.concat(frames, ignore_index=True)
    log.info("Total rows: %d", len(df))

    # Auto-detect columns (case/space-insensitive match)
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

    log.info("Column mapping: phone=%s company=%s industry=%s city=%s state=%s contact=%s title=%s email_owner=%s email_gen=%s website=%s",
             col_phone, col_company, col_industry, col_city, col_state, col_contact, col_title, col_email_owner, col_email_general, col_website)

    if not col_phone:
        log.error("No phone column detected — required")
        sys.exit(1)

    conn = db.connect()
    try:
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
                return str(v).strip() or None

            existing = conn.execute(
                "SELECT account_id, company_name FROM accounts WHERE account_id = ?",
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
                    "master_spreadsheet", "Cold", 0, 0, _now(),
                ))
                ins += 1

        conn.commit()
        log.info("✓ Leads imported: inserted=%d updated=%d skipped=%d", ins, upd, skipped)
    finally:
        conn.close()


def cmd_dashboard(args):
    """Launch the Streamlit dashboard locally."""
    import subprocess
    log.info("Launching Streamlit dashboard...")
    log.info("Open http://localhost:8501 in your browser")
    log.info("Ctrl+C to stop")
    subprocess.run(["streamlit", "run", "app.py"])


def cmd_bootstrap(args):
    """First-time full load: fetch all history, download, transcribe, classify."""
    log.info("=== BOOTSTRAP: full history load ===")
    log.info("This will take 30-60 minutes depending on volume.")
    log.info("It's safe to interrupt and re-run — all stages resume.")
    log.info("")

    # Fetch all history
    args.days = None
    args.since = config.HISTORY_START.isoformat()
    cmd_fetch(args)

    # Download recordings
    args.min_duration = config.MIN_DURATION_FOR_RECORDING_DOWNLOAD
    cmd_download(args)

    # Transcribe
    cmd_transcribe(args)

    # Classify
    args.reclassify = False
    cmd_classify(args)

    # Dashboard
    args.open = False
    cmd_dashboard(args)

    log.info("=== BOOTSTRAP DONE ===")
    log.info("Dashboard: %s", config.REPORTS_DIR / "dashboard.html")


def cmd_leadgen(args):
    """Generate qualified leads via web research + AI enrichment."""
    config.validate()
    conn = db.connect()
    db.ensure_leads_table(conn)
    try:
        target = args.count or 50
        leads = leadgen.generate_leads(target=target, conn=conn)
        log.info("=== LEAD GEN DONE: %d leads generated ===", len(leads))

        # Summary
        with_phone = sum(1 for l in leads if l.get("phone"))
        with_contact = sum(1 for l in leads if l.get("contact_name"))
        log.info("  With phone: %d/%d", with_phone, len(leads))
        log.info("  With contact name: %d/%d", with_contact, len(leads))
    finally:
        conn.close()


def cmd_daily(args):
    """Incremental daily run: fetch new, download, transcribe, classify, enrich, aggregate."""
    log.info("=== DAILY PIPELINE ===")
    args.days = 3  # overlap 3 days for safety
    args.since = None
    cmd_fetch(args)

    args.min_duration = config.MIN_DURATION_FOR_RECORDING_DOWNLOAD
    cmd_download(args)
    cmd_transcribe(args)

    args.reclassify = False
    cmd_classify(args)

    # New CRM stages
    args.only = None
    args.reenrich = False
    args.limit = None
    cmd_enrich(args)
    cmd_aggregate(args)

    log.info("=== DAILY DONE ===")
    log.info("Run `streamlit run app.py` to view dashboard locally")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="J T-Shirts call analytics pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Common workflows:
  First time:    python jtcalls.py bootstrap
  Every day:     python jtcalls.py daily
  Quick refresh: python jtcalls.py dashboard
        """
    )
    sub = p.add_subparsers(dest="cmd")

    pf = sub.add_parser("fetch", help="Fetch call metadata from RingCentral")
    pf.add_argument("--days", type=int, help="Fetch last N days")
    pf.add_argument("--since", help="Fetch since YYYY-MM-DD")
    pf.set_defaults(func=cmd_fetch)

    pd = sub.add_parser("download", help="Download MP3 recordings")
    pd.add_argument("--min-duration", type=int, help="Minimum call duration in seconds")
    pd.set_defaults(func=cmd_download)

    pt = sub.add_parser("transcribe", help="Transcribe downloaded MP3s")
    pt.set_defaults(func=cmd_transcribe)

    pc = sub.add_parser("classify", help="Classify transcripts")
    pc.add_argument("--reclassify", action="store_true", help="Re-classify all calls")
    pc.set_defaults(func=cmd_classify)

    pe = sub.add_parser("enrich", help="LLM enrichment: score, BANT, next-step, objections, coaching")
    pe.add_argument("--only", help="Comma-separated classifications to enrich (e.g. real_discovery,gatekeeper)")
    pe.add_argument("--reenrich", action="store_true", help="Re-enrich calls that already have enrichment")
    pe.add_argument("--limit", type=int, help="Max calls to process this run")
    pe.set_defaults(func=cmd_enrich)

    pa = sub.add_parser("aggregate", help="Rebuild accounts + rep performance from calls/enrichments")
    pa.set_defaults(func=cmd_aggregate)

    pil = sub.add_parser("import-leads", help="Import master leads xlsx into accounts (by phone)")
    pil.add_argument("--file", required=True, help="Path to the master xlsx")
    pil.set_defaults(func=cmd_import_leads)

    ph = sub.add_parser("dashboard", help="Generate HTML dashboard")
    ph.add_argument("--open", action="store_true", help="Open in browser after generating")
    ph.set_defaults(func=cmd_dashboard)

    pb = sub.add_parser("bootstrap", help="First-time full history load")
    pb.set_defaults(func=cmd_bootstrap)

    plg = sub.add_parser("leadgen", help="Generate qualified leads (default: 50)")
    plg.add_argument("--count", type=int, default=50, help="Number of leads to generate")
    plg.set_defaults(func=cmd_leadgen)

    pdy = sub.add_parser("daily", help="Daily incremental update + dashboard")
    pdy.set_defaults(func=cmd_daily)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    # Fill in missing attributes for composed commands
    for attr in ("days", "since", "min_duration", "reclassify", "open",
                 "only", "reenrich", "limit", "file"):
        if not hasattr(args, attr):
            setattr(args, attr, None)

    try:
        args.func(args)
    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("FAILED: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
