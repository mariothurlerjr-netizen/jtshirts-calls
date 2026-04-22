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

from lib import config, db, classify, whisper, leadgen
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
    """Incremental daily run: fetch new, download, transcribe, classify, dashboard."""
    log.info("=== DAILY PIPELINE ===")
    args.days = 3  # overlap 3 days for safety
    args.since = None
    cmd_fetch(args)

    args.min_duration = config.MIN_DURATION_FOR_RECORDING_DOWNLOAD
    cmd_download(args)
    cmd_transcribe(args)

    args.reclassify = False
    cmd_classify(args)

    args.open = False
    cmd_dashboard(args)

    log.info("=== DAILY DONE ===")
    log.info("Dashboard: %s", config.REPORTS_DIR / "dashboard.html")


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
    for attr in ("days", "since", "min_duration", "reclassify", "open"):
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
