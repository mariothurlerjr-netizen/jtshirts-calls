"""
Streamlit dashboard — J T-Shirts Call Analytics + Sales Playbook.

Public URL (no login). Reads directly from the SQLite DB that jtcalls.py populates.
Run locally:   streamlit run app.py
Deploy:        push to GitHub → connect to streamlit.io/cloud
"""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# ─── Timezone ───────────────────────────────────────────────────────────────
# Convert all times to display timezone (default: America/New_York = ET)
import os
DISPLAY_TZ_NAME = os.environ.get("DISPLAY_TIMEZONE", "America/Boise")

# Manager access (Mario / Alex / Gabriela). Read from env or Streamlit secrets.
def _mgr_pw():
    try:
        if hasattr(st, "secrets") and "MANAGER_PASSWORD" in st.secrets:
            return st.secrets["MANAGER_PASSWORD"]
    except Exception:
        pass
    return os.environ.get("MANAGER_PASSWORD", "jtshirts2026")
MANAGER_PASSWORD = _mgr_pw()
DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_NAME)
_tz_labels = {"New_York":"ET","Chicago":"CT","Denver":"MT","Boise":"MT","Los_Angeles":"PT","Phoenix":"MST"}
TZ_LABEL = _tz_labels.get(DISPLAY_TZ_NAME.split("/")[-1], DISPLAY_TZ_NAME.split("/")[-1])

# ─── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="J T-Shirts · Call Analytics",
    page_icon=None,
    layout="wide",
)

DB_PATH = Path(__file__).parent / "data" / "calls.db"


# ─── Helpers ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    df = pd.read_sql("SELECT * FROM calls", conn, parse_dates=["start_time"])
    # Convert start_time to display timezone
    if not df.empty and df["start_time"].dt.tz is None:
        df["start_time"] = df["start_time"].dt.tz_localize("UTC")
    if not df.empty:
        df["start_time"] = df["start_time"].dt.tz_convert(DISPLAY_TZ)
    return {
        "calls": df,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


@st.cache_data(ttl=60)
def load_crm_data():
    """Load accounts, call_enrichments, call_objections, rep_performance, account_actions."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    out = {}
    try:
        out["accounts"] = pd.read_sql("SELECT * FROM accounts", conn,
                                      parse_dates=["first_touch_at", "last_touch_at", "updated_at"])
    except Exception:
        out["accounts"] = pd.DataFrame()
    try:
        out["enrich"] = pd.read_sql("SELECT * FROM call_enrichments", conn,
                                    parse_dates=["enriched_at"])
    except Exception:
        out["enrich"] = pd.DataFrame()
    try:
        out["objections"] = pd.read_sql("SELECT * FROM call_objections", conn,
                                        parse_dates=["call_timestamp"])
    except Exception:
        out["objections"] = pd.DataFrame()
    try:
        out["rep_perf"] = pd.read_sql("SELECT * FROM rep_performance", conn,
                                      parse_dates=["period_start", "period_end", "computed_at"])
    except Exception:
        out["rep_perf"] = pd.DataFrame()
    try:
        out["actions"] = pd.read_sql("SELECT * FROM account_actions", conn,
                                     parse_dates=["created_at", "due_date", "completed_at"])
    except Exception:
        out["actions"] = pd.DataFrame()
    conn.close()
    return out


def _set_stage(account_id: str, new_stage: str, by_rep: str = ""):
    """Move an account to a new stage manually. Persists as override."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import set_account_stage
    set_account_stage(conn, account_id, new_stage, by_rep)
    conn.commit()
    conn.close()
    st.cache_data.clear()


def _save_manual(account_id: str, notes: str = None,
                 followup_date: str = None, by_rep: str = ""):
    """Save rep's freeform notes + revisit date for an account."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import save_account_manual
    save_account_manual(conn, account_id, notes=notes,
                        followup_date=followup_date, by_rep=by_rep)
    conn.commit()
    conn.close()
    st.cache_data.clear()


def _upload_leads_file(uploaded_file) -> dict:
    """Save the uploaded xlsx to a temp path and import into accounts."""
    import sqlite3 as _sql
    import tempfile
    suffix = ".xlsx" if uploaded_file.name.lower().endswith(".xlsx") else ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(uploaded_file.getbuffer())
        tmp_path = tmp.name
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import import_leads_from_xlsx
    stats = import_leads_from_xlsx(tmp_path, conn)
    conn.commit()
    conn.close()
    st.cache_data.clear()
    return stats


def _save_fields(account_id: str, updates: dict, by_rep: str = ""):
    """Persist edited company/contact fields on an account."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import save_account_fields
    save_account_fields(conn, account_id, updates, by_rep)
    conn.commit()
    conn.close()
    st.cache_data.clear()


def _create_action(account_id: str, rep_name: str, action_type: str,
                    description: str, due_date: str = None):
    """Create a manual pending action on an account."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import create_manual_action
    create_manual_action(conn, account_id, rep_name, action_type, description, due_date)
    conn.commit()
    conn.close()
    st.cache_data.clear()


def _round_robin(account_ids: list, rep_names: list) -> dict:
    """Assign accounts round-robin to a list of reps."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from lib.crm import round_robin_assign
    counts = round_robin_assign(conn, account_ids, rep_names)
    conn.commit()
    conn.close()
    st.cache_data.clear()
    return counts


def _complete_action(action_id: int, by_rep: str) -> bool:
    """Marks action done + schedules follow-up if applicable. Returns True if a follow-up was auto-created."""
    import sqlite3 as _sql
    conn = _sql.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    # Check the action type BEFORE marking done (so we can tell caller about follow-up)
    row = conn.execute("SELECT action_type FROM account_actions WHERE id = ?", (action_id,)).fetchone()
    was_email = bool(row and row[0] == "send_email")
    from lib.crm import mark_action_done
    mark_action_done(conn, action_id, by_rep)
    conn.commit()
    conn.close()
    st.cache_data.clear()
    return was_email


STAGE_COLORS = {
    "New leads": "#6b7280",
    "Attempted": "#94a3b8",
    "Gatekeeper": "#f59e0b",
    "DM Reached": "#3b82f6",
    "Email to Send": "#8b5cf6",
    "Meeting": "#ec4899",
    "Proposal": "#f97316",
    "Won": "#10b981",
    "Nurture": "#64748b",
    "Lost": "#ef4444",
}
STAGE_ORDER = ["New leads", "Attempted", "Gatekeeper", "DM Reached",
               "Email to Send", "Meeting", "Proposal", "Won",
               "Nurture", "Lost"]
KANBAN_STAGES = ["New leads", "Attempted", "Gatekeeper", "DM Reached",
                 "Email to Send", "Meeting", "Proposal", "Won",
                 "Nurture", "Lost"]

OBJECTION_LABELS = {
    "current_vendor": "Already have a vendor",
    "long_term_contract": "Long-term contract",
    "not_interested": "Not interested",
    "budget": "Price / Budget",
    "bad_timing": "Bad timing",
    "send_info": "Send me info",
    "no_authority": "I don't decide",
    "wrong_person": "Not my department",
    "size_fit": "Size mismatch",
    "tried_before": "Tried before",
    "other": "Other",
}
OBJECTION_LABELS_PT = OBJECTION_LABELS  # backwards compat alias


def ac_city(ac):
    MAP = {
        "216":"Cleveland OH","440":"Cleveland OH","330":"Akron OH",
        "214":"Dallas TX","469":"Dallas TX","972":"Dallas TX","817":"Fort Worth TX",
        "718":"NYC","212":"NYC","646":"NYC","347":"NYC",
        "617":"Boston MA","857":"Boston MA","781":"Boston MA",
        "602":"Phoenix AZ","480":"Phoenix AZ","623":"Phoenix AZ",
        "215":"Philadelphia PA","267":"Philadelphia PA",
        "404":"Atlanta GA","470":"Atlanta GA","678":"Atlanta GA","770":"Atlanta GA",
        "313":"Detroit MI","248":"Detroit MI",
        "305":"Miami FL","786":"Miami FL","954":"Fort Lauderdale FL",
        "312":"Chicago IL","773":"Chicago IL",
        "713":"Houston TX","281":"Houston TX","832":"Houston TX",
        "202":"DC","703":"N. Virginia","571":"N. Virginia",
        "323":"LA CA","213":"LA CA","818":"LA CA","310":"LA CA",
        "702":"Las Vegas NV","512":"Austin TX","737":"Austin TX",
        "813":"Tampa FL","503":"Portland OR","206":"Seattle WA","425":"Seattle WA",
        "615":"Nashville TN","704":"Charlotte NC","407":"Orlando FL","321":"Orlando FL",
    }
    return MAP.get(ac, "Other")


def _metro(ac):
    """Group area codes into metro regions."""
    METRO = {
        "216":"Cleveland","440":"Cleveland","330":"Cleveland",
        "214":"Dallas-FW","469":"Dallas-FW","972":"Dallas-FW","817":"Dallas-FW",
        "718":"NYC","212":"NYC","646":"NYC","347":"NYC",
        "617":"Boston","857":"Boston","781":"Boston",
        "602":"Phoenix","480":"Phoenix","623":"Phoenix",
        "215":"Philadelphia","267":"Philadelphia",
        "404":"Atlanta","470":"Atlanta","678":"Atlanta","770":"Atlanta",
        "313":"Detroit","248":"Detroit",
        "713":"Houston","281":"Houston","832":"Houston",
    }
    return METRO.get(ac, ac_city(ac) or ac)


# ─── Header ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
  /* Sidebar — narrower + compact, all items fit on one screen */
  section[data-testid="stSidebar"] {
    background: #F8FAFC;
    border-right: 1px solid #e2e8f0;
    width: 200px !important;
    min-width: 200px !important;
  }
  section[data-testid="stSidebar"] > div:first-child { padding-top: 0.5rem; }
  section[data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start; text-align: left; padding: 3px 8px;
    font-size: 12px; font-weight: 500; border-radius: 4px; margin: 0;
    min-height: 26px; line-height: 1.2;
  }
  section[data-testid="stSidebar"] .stButton { margin-bottom: 1px; }
  section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: #0F1B2D; color: #fff; border-color: #0F1B2D;
  }
  section[data-testid="stSidebar"] .stButton > button[kind="secondary"] {
    background: transparent; color: #334155; border: none; box-shadow: none;
  }
  section[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {
    background: #e2e8f0; color: #0F1B2D;
  }
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
  /* Tighter main content */
  .main .block-container { padding-top: 1.2rem; max-width: 1500px; padding-left: 2rem; padding-right: 2rem; }
  /* Metric polish */
  [data-testid="stMetric"] {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 10px 14px;
  }
  /* Popover button — compact */
  [data-testid="stPopover"] button {
    border: 1px solid #e2e8f0; background: #fff; color: #334155;
    font-size: 12.5px; font-weight: 500; padding: 4px 12px;
  }
  /* Kanban — tighter cards & columns */
  div[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(10)) {
    overflow-x: auto !important;
    flex-wrap: nowrap !important;
    padding-bottom: 10px;
    gap: 6px !important;
  }
  div[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(10)) > [data-testid="stColumn"] {
    min-width: 195px !important;
    max-width: 195px !important;
    flex: 0 0 195px !important;
  }
  /* Kanban card as a button — clickable whole surface, fixed height so columns align */
  .kb-card button {
    width: 100% !important;
    text-align: left !important;
    justify-content: flex-start !important;
    align-items: flex-start !important;
    padding: 8px 10px !important;
    font-size: 12px !important;
    line-height: 1.45 !important;
    white-space: pre-line !important;
    background: #fff !important;
    border: 1px solid #e2e8f0 !important;
    color: #0F1B2D !important;
    font-weight: 500 !important;
    min-height: 68px !important;
    height: 68px !important;
    overflow: hidden !important;
  }
  .kb-card button > div,
  .kb-card button > div > p {
    white-space: pre-line !important;
    text-align: left !important;
    line-height: 1.45 !important;
    margin: 0 !important;
  }
  .kb-card button:hover {
    background: #F8FAFC !important;
    border-color: #cbd5e1 !important;
  }
  /* Move → dropdown — compact */
  .kb-card + div [data-baseweb="select"] > div {
    min-height: 28px !important;
    font-size: 11px !important;
  }
</style>
""", unsafe_allow_html=True)

data = load_data()

if data is None:
    st.error("No database found. Run `python jtcalls.py bootstrap` first to populate data.")
    st.code("python jtcalls.py bootstrap", language="bash")
    st.stop()

df = data["calls"]

if df.empty:
    st.warning("Database is empty. Run `python jtcalls.py fetch --days 30` to load data.")
    st.stop()

crm = load_crm_data() or {"accounts": pd.DataFrame(), "enrich": pd.DataFrame(),
                           "objections": pd.DataFrame(), "rep_perf": pd.DataFrame()}

# ─── Sidebar navigation (CRM-style left nav) ────────────────────────────────

PUBLIC_NAV = [
    ("SALES",    [("Overview",            "Overview"),
                  ("CRM",                 "CRM"),
                  ("Hot Accounts",        "Hot Accounts"),
                  ("Meeting Conversion",  "Meeting Conversion")]),
    ("COACHING", [("Coaching",            "Coaching")]),
    ("RESOURCES",[("Winning Calls",       "Winning Calls"),
                  ("Sales Playbook",      "Sales Playbook"),
                  ("Lead List",           "Lead List"),
                  ("Call Explorer",       "Call Explorer")]),
]

MANAGER_NAV = [
    ("MANAGER",  [("Rep Performance",     "Rep Performance"),
                  ("Scorecard",           "Scorecard"),
                  ("Strategic Diagnostic","Strategic Diagnostic")]),
]

MANAGER_ONLY_PAGES = {"Rep Performance", "Scorecard", "Strategic Diagnostic"}

PAGES_WITH_FILTERS = {"Overview", "Winning Calls", "Strategic Diagnostic",
                      "Sales Playbook", "Rep Performance", "Call Explorer"}

# Manager gating disabled — all pages are public for now.
# To restore, set this to: st.session_state.get("is_manager", False)
is_manager = True
NAV_SECTIONS = PUBLIC_NAV + (MANAGER_NAV if is_manager else [])

with st.sidebar:
    st.markdown("""
    <div style="padding:4px 0 20px 0;">
      <div style="font-size:16px; font-weight:600; color:#0F1B2D; letter-spacing:-0.2px;">J T-Shirts</div>
      <div style="font-size:10px; color:#94a3b8; letter-spacing:1.4px; font-weight:500; margin-top:2px;">CALL INTELLIGENCE</div>
    </div>
    """, unsafe_allow_html=True)

    all_pages = []
    for group_name, items in NAV_SECTIONS:
        st.markdown(f"<div style='font-size:10px; font-weight:600; color:#94a3b8; letter-spacing:1.4px; margin:14px 0 6px 0;'>{group_name}</div>", unsafe_allow_html=True)
        for label, page_id in items:
            all_pages.append(page_id)
        if "current_page" not in st.session_state:
            st.session_state["current_page"] = all_pages[0]
        for label, page_id in items:
            is_current = st.session_state["current_page"] == page_id
            btn_style = "primary" if is_current else "secondary"
            if st.button(label, key=f"nav_{page_id}", use_container_width=True, type=btn_style):
                st.session_state["current_page"] = page_id
                st.rerun()

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    st.caption(f"{len(df):,} calls · {data['generated_at']}")

page = st.session_state.get("current_page", "Overview")

# ─── Filters — popover button, only on pages that need them ────────────────

min_date = df["start_time"].min().date()
max_date = df["start_time"].max().date()
default_start = max(min_date, max_date - timedelta(days=30))

# Reps = outbound callers only (actual SDRs, not inbound prospect names)
reps_available = sorted([
    r for r in df[df["direction"] == "Outbound"]["from_name"].dropna().unique()
    if r and len(str(r).strip()) > 1
])
classes_avail = sorted([c for c in df["classification"].dropna().unique() if c])

# Default values (used on pages without filter UI)
date_range = (default_start, max_date)
reps_sel = reps_available
classes_sel = classes_avail

# Page title bar
needs_filters = page in PAGES_WITH_FILTERS
title_cols = st.columns([6, 1]) if needs_filters else st.columns([1])
with title_cols[0]:
    st.markdown(
        f"<div style='display:flex; align-items:baseline; gap:14px; margin:4px 0 14px 0;'>"
        f"<h2 style='margin:0; font-size:22px; font-weight:600; color:#0F1B2D; letter-spacing:-0.3px;'>{page}</h2>"
        f"<div style='font-size:10px; color:#94a3b8; letter-spacing:1px;'>FUSO {TZ_LABEL} · IDAHO</div>"
        f"</div>", unsafe_allow_html=True
    )

if needs_filters:
    with title_cols[1]:
        # Show active filter count as a badge
        active_count = 0
        if "active_date_range" in st.session_state and st.session_state["active_date_range"] != (default_start, max_date):
            active_count += 1
        if "active_reps" in st.session_state and set(st.session_state.get("active_reps", reps_available)) != set(reps_available):
            active_count += 1
        label = f"Filters ({active_count})" if active_count else "Filters"
        with st.popover(label, use_container_width=True):
            date_range = st.date_input(
                "Period",
                value=st.session_state.get("active_date_range", (default_start, max_date)),
                min_value=min_date,
                max_value=max_date,
                format="YYYY-MM-DD",
                key="flt_date",
            )
            reps_sel = st.multiselect(
                "Reps",
                reps_available,
                default=st.session_state.get("active_reps", reps_available),
                key="flt_reps",
            )
            if classes_avail:
                classes_sel = st.multiselect(
                    "Call type",
                    classes_avail,
                    default=st.session_state.get("active_classes", classes_avail),
                    key="flt_classes",
                )
            st.session_state["active_date_range"] = date_range
            st.session_state["active_reps"] = reps_sel
            st.session_state["active_classes"] = classes_sel

# Apply filters
if isinstance(date_range, tuple) and len(date_range) == 2:
    d_from, d_to = date_range
    mask = (df["start_time"].dt.date >= d_from) & (df["start_time"].dt.date <= d_to)
    fdf = df[mask]
else:
    fdf = df

fdf = fdf[fdf["direction"] == "Outbound"]
if reps_sel:
    fdf = fdf[fdf["from_name"].isin(reps_sel)]
fdf_class = fdf[fdf["classification"].isin(classes_sel)] if classes_sel else fdf

# ─── Precompute shared metrics ──────────────────────────────────────────────

def met_row(row_df):
    return {
        "dials": len(row_df),
        "connected": (row_df["result"].isin(["Call connected","Accepted"])).sum(),
        "over_2min": (row_df["duration"] >= 120).sum(),
        "real_disc": (row_df["classification"] == "real_discovery").sum(),
        "ivr": (row_df["classification"] == "ivr_hold").sum(),
        "gatekeeper": (row_df["classification"] == "gatekeeper").sum(),
        "wrong_num": (row_df["result"] == "Wrong Number").sum(),
    }

f_metrics = met_row(fdf)

# Collect all objections (from all classifications)
all_objections = Counter()
disc_objections = Counter()
for _, r in fdf.iterrows():
    objs_json = r.get("objections_found")
    if not objs_json or not isinstance(objs_json, str):
        continue
    try:
        for obj in json.loads(objs_json):
            if isinstance(obj, str) and obj.strip():
                clean = obj.strip().lower()
                all_objections[clean] += 1
                if r.get("classification") == "real_discovery":
                    disc_objections[clean] += 1
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════

if page == "Overview":

    # ═══════════════════════════════════════════════════════════════════
    # SECTION 1 — Leads pipeline: how much raw fuel do we have left?
    # ═══════════════════════════════════════════════════════════════════
    acc_all = crm["accounts"]
    total_leads = len(acc_all) if not acc_all.empty else 0
    contacted = int((acc_all["total_calls"].fillna(0) > 0).sum()) if not acc_all.empty else 0
    uncontacted = total_leads - contacted
    contact_rate = (contacted / max(total_leads, 1)) * 100

    st.markdown("### Lead pipeline")
    L1, L2, L3, L4 = st.columns(4)
    L1.metric("Total leads in CRM", f"{total_leads:,}",
              help="All accounts: from uploaded spreadsheets + numbers dialed by the team.")
    L2.metric("Leads contacted", f"{contacted:,}",
              help="Accounts where at least one call has been placed.")
    L3.metric("Contact rate", f"{contact_rate:.1f}%",
              help="Contacted ÷ Total leads. How much of our list we've actually dialed.")
    L4.metric("Still to contact", f"{uncontacted:,}",
              help="Leads on the list with zero dials yet. This is the inventory left.")

    # Lead source breakdown — explains where the numbers come from
    if not acc_all.empty:
        from_sheet = int((acc_all["lead_source"] == "master_spreadsheet").sum()) if "lead_source" in acc_all.columns else 0
        from_rc_only = total_leads - from_sheet
        sheet_contacted = int(
            ((acc_all.get("lead_source") == "master_spreadsheet") &
             (acc_all["total_calls"].fillna(0) > 0)).sum()
        ) if "lead_source" in acc_all.columns else 0
        sheet_untouched = from_sheet - sheet_contacted

        with st.expander("Where are these leads coming from?"):
            st.markdown(
                f"- **{from_sheet:,}** leads from the master spreadsheet you uploaded "
                f"(JTShirts_SDR_FINAL.xlsx — curated list with full contact info: name, email, "
                f"industry, city, state).\n"
                f"   - **{sheet_contacted:,}** already dialed\n"
                f"   - **{sheet_untouched:,}** never dialed (this is the fresh inventory)\n"
                f"- **{from_rc_only:,}** leads from RingCentral alone — numbers the team dialed but "
                f"that are **not** in your master spreadsheet. Most likely from older lead lists, "
                f"speculative dialing, or inbound numbers."
            )
            st.caption(
                "To add more curated leads, go to the CRM tab and click **Upload leads**. "
                "New accounts land in the *New leads* stage, where they wait for the first dial."
            )

    st.divider()

    # ═══════════════════════════════════════════════════════════════════
    # SECTION 2 — Daily rhythm: is the team dialing enough?
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("### Daily output")
    st.caption("Based on the last 14 days of outbound activity.")

    full_ob_all = df[df["direction"] == "Outbound"]
    cutoff14 = datetime.now(timezone.utc) - timedelta(days=14)
    last14 = full_ob_all[full_ob_all["start_time"] >= cutoff14]
    n_active_days = max(last14["start_time"].dt.date.nunique(), 1)
    dials_per_day = len(last14) / n_active_days
    # Per-rep rhythm
    per_rep_df = last14.groupby("from_name").agg(
        dials=("id", "count"),
        days=("start_time", lambda s: s.dt.date.nunique()),
    )
    per_rep_df["per_day"] = (per_rep_df["dials"] / per_rep_df["days"].clip(lower=1)).round(1)
    per_rep_df = per_rep_df.sort_values("per_day", ascending=False)

    # Leads per rep still to work (assigned but not dialed)
    if not acc_all.empty:
        remaining_by_rep = (
            acc_all[(acc_all["owner_rep"].notna()) & (acc_all["owner_rep"] != "") &
                    (acc_all["total_calls"].fillna(0) == 0)]
            .groupby("owner_rep").size()
        )
    else:
        remaining_by_rep = pd.Series(dtype=int)

    D1, D2, D3 = st.columns(3)
    D1.metric("Team dials / day", f"{dials_per_day:.0f}",
              help=f"Across {n_active_days} active days in the last 14 — all reps combined.")
    avg_per_rep = per_rep_df["per_day"].mean() if not per_rep_df.empty else 0
    D2.metric("Avg rep dials / day", f"{avg_per_rep:.0f}",
              help="Average dials per active rep per day. Each rep should be well above this to be pulling weight.")
    n_reps = len(per_rep_df)
    D3.metric("Active reps", f"{n_reps}",
              help="Reps with at least one outbound call in the last 14 days.")

    # Per-rep breakdown
    if not per_rep_df.empty:
        rep_rhythm = per_rep_df.reset_index()[["from_name", "dials", "days", "per_day"]]
        rep_rhythm.columns = ["Rep", "Dials (14d)", "Days active", "Avg dials / day"]
        rep_rhythm["Leads still to dial"] = rep_rhythm["Rep"].map(remaining_by_rep).fillna(0).astype(int)
        st.dataframe(
            rep_rhythm, hide_index=True, use_container_width=True,
            column_config={
                "Rep":                st.column_config.Column("Rep"),
                "Dials (14d)":        st.column_config.NumberColumn("Dials (14d)", help="Total dials over the last 14 days."),
                "Days active":        st.column_config.NumberColumn("Days active", help="Days the rep made at least one call."),
                "Avg dials / day":    st.column_config.NumberColumn("Avg dials / day", help="Dials ÷ Days active. The real daily pace."),
                "Leads still to dial":st.column_config.NumberColumn("Leads still to dial", help="Leads currently assigned to this rep that have zero calls yet."),
            }
        )

    st.divider()

    # ═══════════════════════════════════════════════════════════════════
    # SECTION 3 — Conversion goal: are we booking meetings?
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("### Meetings vs target")
    st.caption("Goal: every active rep books at least 1 meeting per day. Below is the last 7 days.")

    enr_all = crm["enrich"]
    cutoff7 = datetime.now(timezone.utc) - timedelta(days=7)
    if not enr_all.empty:
        enr_7d = enr_all[pd.to_datetime(enr_all["enriched_at"], utc=True, errors="coerce") >= cutoff7]
        dm_7d = int((enr_7d["reached_whom"] == "decision_maker").sum())
        meetings_7d = int((enr_7d["meeting_booked"] == 1).sum()) if "meeting_booked" in enr_7d.columns else 0
    else:
        dm_7d = meetings_7d = 0

    # Target: active reps * 7 days * 1 meeting
    active_reps_7d = int(full_ob_all[full_ob_all["start_time"] >= cutoff7]["from_name"].dropna().nunique())
    meeting_target = active_reps_7d * 7
    target_hit = (meetings_7d / max(meeting_target, 1)) * 100

    M1, M2, M3, M4 = st.columns(4)
    M1.metric("DM reached (7d)", f"{dm_7d:,}",
              help="Calls where rep spoke with the decision-maker in the last 7 days.")
    M2.metric("Meetings booked (7d)", f"{meetings_7d}",
              help="Meetings where a concrete day/time was agreed.")
    M3.metric("Weekly target", f"{meeting_target}",
              help=f"{active_reps_7d} active reps × 7 days × 1 meeting/day = {meeting_target}.")
    M4.metric("% of target", f"{target_hit:.0f}%",
              help="Meetings booked ÷ Weekly target. Above 100% = exceeding goal.")

    if meeting_target > 0:
        st.progress(min(target_hit / 100, 1.0),
                    text=f"{meetings_7d} of {meeting_target} meetings booked this week")

    st.divider()

    # ═══════════════════════════════════════════════════════════════════
    # SECTION 4 — Funnel breakdown (period-scoped, percentages highlighted)
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("### Conversion funnel · selected period")
    st.caption("Each step shows the survival rate from the previous step. Dials are the fuel, Meeting Booked is the outcome.")

    m_dials = len(fdf)
    m_connected = (fdf["result"].isin(["Call connected", "Accepted"])).sum()
    enr_df_in_period = crm["enrich"].copy()
    period_call_ids = set(fdf["id"])
    enr_in_period = enr_df_in_period[enr_df_in_period["call_id"].isin(period_call_ids)] if not enr_df_in_period.empty else enr_df_in_period
    m_dm = int((enr_in_period["reached_whom"] == "decision_maker").sum()) if not enr_in_period.empty else 0
    if not enr_in_period.empty and "next_step_extracted" in enr_in_period.columns:
        m_advanced = int((
            (enr_in_period["reached_whom"] == "decision_maker") &
            (enr_in_period["next_step_extracted"].fillna("").str.len() > 0)
        ).sum())
    else:
        m_advanced = 0
    m_booked = int((enr_in_period["meeting_booked"] == 1).sum()) if not enr_in_period.empty and "meeting_booked" in enr_in_period.columns else 0

    not_completed = m_dials - m_connected

    def pct(n, d):
        return f"{(100*n/max(d,1)):.1f}%" if d else "—"

    fm1, fm2, fm3, fm4, fm5 = st.columns(5)
    fm1.metric("1. Dials", f"{m_dials:,}",
               help=f"Total outbound calls placed. {not_completed:,} didn't connect ({pct(not_completed, m_dials)}).")
    fm2.metric("2. Connected", f"{m_connected:,}", pct(m_connected, m_dials),
               help="Someone answered. Percentage over Dials.")
    fm3.metric("3. DM Reached", f"{m_dm:,}", pct(m_dm, m_connected),
               help="Rep spoke with the decision-maker. Percentage over Connected.")
    fm4.metric("4. Advanced", f"{m_advanced:,}", pct(m_advanced, m_dm),
               help="DM call that captured a concrete next-step. Percentage over DM Reached.")
    fm5.metric("5. Meeting Booked", f"{m_booked:,}", pct(m_booked, m_dm),
               help="Meeting booked with day/time. Percentage over DM Reached — the metric that matters.")

    # Horizontal drop-off bars — each bar scaled to stage before (visually shows drop-off)
    # Instead of absolute counts (where DM=35 vs Dials=1800 is invisible), we render
    # normalized horizontal bars with the count as label.
    stages = [
        ("Dials",           m_dials,     "#0F1B2D"),
        ("Connected",       m_connected, "#3b82f6"),
        ("DM Reached",      m_dm,        "#8b5cf6"),
        ("Advanced",        m_advanced,  "#ec4899"),
        ("Meeting Booked",  m_booked,    "#10b981"),
    ]
    max_val = max(s[1] for s in stages) or 1
    for i, (label, val, color) in enumerate(stages):
        w = (val / max_val) * 100
        prev = stages[i-1][1] if i > 0 else val
        drop_pct = pct(val, prev) if i > 0 else "100%"
        st.markdown(
            f"<div style='margin:6px 0;'>"
            f"<div style='display:flex; justify-content:space-between; font-size:12px; margin-bottom:2px;'>"
            f"<span><b>{label}</b></span>"
            f"<span style='color:#64748b;'>{val:,} · {drop_pct} from previous</span>"
            f"</div>"
            f"<div style='background:#f1f5f9; height:22px; border-radius:4px; overflow:hidden;'>"
            f"<div style='background:{color}; height:100%; width:{w}%; transition:width 0.3s;'></div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # Yesterday vs 7d avg
    st.markdown("### Yesterday vs 7-day Average")
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    full_ob = df[df["direction"] == "Outbound"]
    y_df = full_ob[full_ob["start_time"].dt.date == yesterday]
    w_df = full_ob[(full_ob["start_time"].dt.date >= week_ago) & (full_ob["start_time"].dt.date < today)]

    y = met_row(y_df)
    w = met_row(w_df)
    w_avg = {k: v/7 for k, v in w.items()}

    def delta_pct(cur, avg):
        if avg == 0: return None
        return (cur - avg) / avg * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        d = delta_pct(y["dials"], w_avg["dials"])
        st.metric("Dials yesterday", f"{y['dials']:,}", f"{d:+.0f}%" if d is not None else None,
                  help=f"7d avg: {w_avg['dials']:.0f}/day")
    with c2:
        d = delta_pct(y["connected"], w_avg["connected"])
        st.metric("Connected", f"{y['connected']:,}", f"{d:+.0f}%" if d is not None else None,
                  help=f"Prospect answered the phone. 7d avg: {w_avg['connected']:.0f}/day")
    with c3:
        d = delta_pct(y["real_disc"], w_avg["real_disc"])
        st.metric("DM Reached", f"{y['real_disc']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="Real productive conversation with the decision-maker (excludes IVR, voicemail, gatekeeper blocks).")
    with c4:
        d = delta_pct(y["ivr"], w_avg["ivr"])
        st.metric("IVR Traps", f"{y['ivr']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="Rep stuck in an automated menu / virtual assistant. High value = list full of big companies with phone trees.",
                  delta_color="inverse")
    with c5:
        d = delta_pct(y["wrong_num"], w_avg["wrong_num"])
        st.metric("Wrong #", f"{y['wrong_num']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="Number flagged as wrong by RingCentral. High value = list hygiene problem.",
                  delta_color="inverse")

    # Period summary
    st.markdown(f"### Period Summary · {len(fdf):,} outbound dials")
    st.caption(
        "**Metric meanings** — "
        "**Dials**: total outbound calls placed · "
        "**Connected**: someone answered · "
        "**>2 min**: calls longer than 2 minutes (proxy for real talk) · "
        "**DM Reached**: rep spoke with the actual decision-maker · "
        "**IVR Traps**: rep stuck in an automated menu · "
        "**Wrong #**: number flagged incorrect — list hygiene issue."
    )

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Dials", f"{f_metrics['dials']:,}",
              help="Total outbound calls placed in the selected period.")
    m2.metric("Connected", f"{f_metrics['connected']:,}",
              f"{100*f_metrics['connected']/max(f_metrics['dials'],1):.0f}%",
              help="Someone answered the phone. % is over Dials.")
    m3.metric(">2 min", f"{f_metrics['over_2min']:,}",
              f"{100*f_metrics['over_2min']/max(f_metrics['dials'],1):.1f}%",
              help="Calls that lasted more than 2 minutes. Proxy for real conversation (though some are IVR traps).")
    m4.metric("DM Reached", f"{f_metrics['real_disc']:,}",
              f"{100*f_metrics['real_disc']/max(f_metrics['dials'],1):.1f}%",
              help="Rep spoke with the decision-maker. The productivity number.")
    m5.metric("IVR Traps", f"{f_metrics['ivr']:,}",
              help="Rep stuck in automated menu / virtual assistant.")
    m6.metric("Wrong #", f"{f_metrics['wrong_num']:,}",
              f"{100*f_metrics['wrong_num']/max(f_metrics['dials'],1):.1f}%",
              help="Number flagged as wrong. List hygiene issue.")

    st.divider()

    # Charts
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown("#### Call Classification Distribution")
        class_counts = fdf["classification"].value_counts()
        if not class_counts.empty:
            st.bar_chart(class_counts, height=300)
        else:
            st.info("No classified calls in this period.")

    with col_right:
        st.markdown("#### Daily Volume (outbound)")
        daily = fdf.groupby(fdf["start_time"].dt.date).agg(
            dials=("id","count"),
            real_disc=("classification", lambda x: (x == "real_discovery").sum()),
            ivr=("classification", lambda x: (x == "ivr_hold").sum()),
        ).reset_index()
        daily.columns = ["date","Dials","DM Reached","IVR Traps"]
        if not daily.empty:
            st.line_chart(daily.set_index("date"), height=300)

    st.divider()

    # Time patterns — manager-only to avoid judging reps' hours
    if is_manager:
        st.markdown("##### Manager-only section")
        col_a, col_b = st.columns(2)

        # Enrich-based per-hour productivity (what hour ACTUALLY converts)
        enr_for_hour = crm["enrich"][["call_id", "reached_whom", "meeting_booked"]].copy() if not crm["enrich"].empty else pd.DataFrame(columns=["call_id", "reached_whom", "meeting_booked"])
        fdf_enr = fdf.merge(enr_for_hour, how="left", left_on="id", right_on="call_id")

        with col_a:
            st.markdown("#### Best hour of day · productive conversations")
            st.caption("Counts DM-reached calls per hour. Dial volume doesn't matter — what matters is when the conversation actually happens.")
            fdf_h = fdf_enr.copy()
            fdf_h["hour"] = fdf_h["start_time"].dt.hour
            hour_stats = fdf_h.groupby("hour").agg(
                DM_Reached=("reached_whom", lambda x: (x == "decision_maker").sum()),
                Gatekeeper=("reached_whom", lambda x: (x == "gatekeeper").sum()),
                Dials=("id", "count"),
            ).reset_index()
            hour_stats = hour_stats.set_index("hour").reindex(range(24), fill_value=0).reset_index()
            st.bar_chart(hour_stats.set_index("hour")[["DM_Reached", "Gatekeeper"]], height=300)

        with col_b:
            st.markdown("#### Best day · productive conversations")
            st.caption("Counts DM reached by day of the week. Shows when the funnel actually advances.")
            fdf_d = fdf_enr.copy()
            dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            fdf_d["dow"] = fdf_d["start_time"].dt.dayofweek.apply(lambda i: dow_names[i])
            dow_stats = fdf_d.groupby("dow").agg(
                DM_Reached=("reached_whom", lambda x: (x == "decision_maker").sum()),
                Gatekeeper=("reached_whom", lambda x: (x == "gatekeeper").sum()),
                Dials=("id", "count"),
            ).reindex(dow_names).reset_index()
            st.bar_chart(dow_stats.set_index("dow")[["DM_Reached", "Gatekeeper"]], height=300)

        st.divider()

    # Area codes
    st.markdown("### Performance by Area Code")
    st.caption(
        "Each US area code you dialed. "
        "**DM Reached** = the rep spoke with the actual decision-maker · "
        "**IVR Stuck** = rep got trapped in an automated menu · "
        "**Wrong Number** = RingCentral flagged the number as invalid. "
        "The three percentages are each metric divided by total Dials."
    )
    fdf_ac = fdf[fdf["to_number"].str.startswith("+1", na=False)].copy()
    fdf_ac["ac"] = fdf_ac["to_number"].str[2:5]
    ac_stats = fdf_ac.groupby("ac").agg(
        Dials=("id","count"),
        Over2min=("duration", lambda x: (x >= 120).sum()),
        RealDisc=("classification", lambda x: (x == "real_discovery").sum()),
        IVR=("classification", lambda x: (x == "ivr_hold").sum()),
        Wrongs=("result", lambda x: (x == "Wrong Number").sum()),
    ).reset_index().sort_values("Dials", ascending=False).head(20)

    ac_stats["City"] = ac_stats["ac"].apply(ac_city)
    ac_stats["DM Reached %"] = (100 * ac_stats["RealDisc"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats["IVR Stuck %"] = (100 * ac_stats["IVR"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats["Wrong Number %"] = (100 * ac_stats["Wrongs"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats = ac_stats[["ac","City","Dials","Over2min","RealDisc","DM Reached %","IVR","IVR Stuck %","Wrongs","Wrong Number %"]]
    ac_stats.columns = ["Area code","City","Dials","Calls over 2 min","DM Reached","DM Reached %","IVR Stuck","IVR Stuck %","Wrong Number","Wrong Number %"]

    st.dataframe(
        ac_stats, use_container_width=True, hide_index=True,
        column_config={
            "Area code":         st.column_config.Column("Area code",
                                                         help="3-digit US area code dialed (first 3 digits after the country code)."),
            "City":              st.column_config.Column("City",
                                                         help="Main city the area code maps to."),
            "Dials":             st.column_config.NumberColumn("Dials",
                                                               help="Total outbound calls placed to this area code."),
            "Calls over 2 min":  st.column_config.NumberColumn("Calls over 2 min",
                                                               help="How many of those calls lasted more than 2 minutes (proxy for real talk)."),
            "DM Reached":        st.column_config.NumberColumn("DM Reached",
                                                               help="Calls where the rep spoke with the actual decision-maker."),
            "DM Reached %":      st.column_config.ProgressColumn("DM Reached %", format="%.1f%%", min_value=0, max_value=10,
                                                                 help="DM Reached ÷ Dials. Benchmark: 2–5% for cold SMB."),
            "IVR Stuck":         st.column_config.NumberColumn("IVR Stuck",
                                                               help="Calls where the rep got trapped in an automated menu."),
            "IVR Stuck %":       st.column_config.ProgressColumn("IVR Stuck %", format="%.1f%%", min_value=0, max_value=100,
                                                                 help="IVR Stuck ÷ Dials. High = this area code is full of big companies with phone trees."),
            "Wrong Number":      st.column_config.NumberColumn("Wrong Number",
                                                               help="Calls RingCentral flagged as wrong numbers (disconnected, changed owner, etc.)."),
            "Wrong Number %":    st.column_config.ProgressColumn("Wrong Number %", format="%.1f%%", min_value=0, max_value=30,
                                                                 help="Wrong Number ÷ Dials. High = bad list hygiene for this area."),
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — WINNING CALLS
# ═══════════════════════════════════════════════════════════════════════════

if page == "Winning Calls":

    st.markdown("""
<div style="background:linear-gradient(135deg,#0d2818 0%,#1a2847 100%); padding:28px 36px; border-radius:12px; margin-bottom:20px">
  <h2 style="color:#2ecc71; margin:0 0 8px 0">Winning Calls — What's Actually Working</h2>
  <p style="color:rgba(255,255,255,.5); margin:0; font-size:14px">Every real discovery call with date, time, rep, duration, opener, and objections faced</p>
</div>
""", unsafe_allow_html=True)

    # Get discovery calls
    disc_calls = fdf[fdf["classification"] == "real_discovery"].copy()

    if disc_calls.empty:
        st.info("No real discovery calls in the selected period.")
    else:
        # Best times section
        st.markdown("### Best Times to Call")
        st.caption("When discovery calls actually happened — concentrate dials in these windows.")

        disc_calls["hour"] = disc_calls["start_time"].dt.hour
        disc_calls["dow_num"] = disc_calls["start_time"].dt.dayofweek
        dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        disc_calls["dow"] = disc_calls["dow_num"].apply(lambda i: dow_names[i])

        wc1, wc2 = st.columns(2)

        with wc1:
            st.markdown(f"#### By Hour ({TZ_LABEL})")
            hour_disc = disc_calls.groupby("hour").size().reset_index(name="Discoveries")
            # Also show total dials per hour for context
            fdf_h2 = fdf.copy()
            fdf_h2["hour"] = fdf_h2["start_time"].dt.hour
            hour_total = fdf_h2.groupby("hour").size().reset_index(name="Total Dials")
            hour_merged = hour_total.merge(hour_disc, on="hour", how="left").fillna(0)
            hour_merged["Discovery %"] = (100 * hour_merged["Discoveries"] / hour_merged["Total Dials"].clip(lower=1)).round(1)
            hour_merged = hour_merged[hour_merged["Total Dials"] >= 5]

            st.dataframe(
                hour_merged.sort_values("Discoveries", ascending=False),
                use_container_width=True, hide_index=True,
                column_config={
                    "hour": st.column_config.NumberColumn("Hour", format="%d:00"),
                    "Discovery %": st.column_config.ProgressColumn("Disc %", format="%.1f%%", min_value=0, max_value=10),
                }
            )

        with wc2:
            st.markdown("#### By Day of Week")
            dow_disc = disc_calls.groupby("dow").size().reindex(dow_names[:5]).reset_index()
            dow_disc.columns = ["Day", "Discoveries"]
            dow_total = fdf.copy()
            dow_total["dow"] = dow_total["start_time"].dt.dayofweek.apply(lambda i: dow_names[i])
            dow_t = dow_total.groupby("dow").size().reindex(dow_names[:5]).reset_index()
            dow_t.columns = ["Day", "Total Dials"]
            dow_merged = dow_t.merge(dow_disc, on="Day", how="left").fillna(0)
            dow_merged["Discovery %"] = (100 * dow_merged["Discoveries"] / dow_merged["Total Dials"].clip(lower=1)).round(1)

            st.dataframe(
                dow_merged.sort_values("Discoveries", ascending=False),
                use_container_width=True, hide_index=True,
                column_config={
                    "Discovery %": st.column_config.ProgressColumn("Disc %", format="%.1f%%", min_value=0, max_value=10),
                }
            )

        # Heat map summary
        best_hours = hour_merged.sort_values("Discoveries", ascending=False).head(3)
        best_days = dow_merged.sort_values("Discoveries", ascending=False).head(2)

        if not best_hours.empty and not best_days.empty:
            top_h = ", ".join(f"{int(h)}:00" for h in best_hours["hour"])
            top_d = ", ".join(best_days["Day"])
            sweet = top_h.split(",")[0]
            html_peak = (
                '<div style="background:#0d2818; padding:20px 28px; border-radius:10px; border-left:4px solid #2ecc71; margin:16px 0">'
                '<h4 style="color:#2ecc71; margin:0 0 8px 0">Peak Discovery Windows</h4>'
                '<p style="color:#ccc; margin:0; font-size:16px; line-height:1.8">'
                f'<b style="color:white">Best hours:</b> {top_h} {TZ_LABEL}<br>'
                f'<b style="color:white">Best days:</b> {top_d}<br>'
                f'<b style="color:white">Sweet spot:</b> {top_d} between {sweet} {TZ_LABEL}'
                '</p></div>'
            )
            st.markdown(html_peak, unsafe_allow_html=True)

        st.divider()

        # Individual winning calls
        st.markdown("### All Discovery Calls — Chronological")
        st.caption("Every productive conversation. Study the openers that worked and the objections that were overcome.")

        disc_sorted = disc_calls.sort_values("start_time", ascending=False)

        for _, call in disc_sorted.iterrows():
            dur_min = call["duration"] // 60
            dur_sec = call["duration"] % 60

            date_str = ""
            try:
                date_str = call["start_time"].strftime("%a %b %d, %H:%M " + TZ_LABEL)
            except Exception:
                pass

            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                with h1:
                    st.markdown(f"**{call.get('from_name', 'Rep')}** → {call.get('to_name') or call.get('to_number', '?')}")
                    st.caption(date_str)
                with h2:
                    st.metric("Duration", f"{dur_min}m {dur_sec}s")
                with h3:
                    conf = call.get("classification_confidence", 0)
                    st.metric("Confidence", f"{conf:.0%}" if conf else "—")

                opener = call.get("opener_extract", "")
                if opener:
                    st.success(f"**Opener:** *\"{opener}\"*")

                objs = call.get("objections_found", "")
                if objs:
                    try:
                        parsed = json.loads(objs)
                        if parsed:
                            st.warning(f"**Objections faced:** {', '.join(parsed)}")
                            # Show handling guide for each objection
                            _obj_responses = {
                                "not interested": "\"Totally fair. But quick question: are you paying more than $12 per shirt on your uniforms right now?\"",
                                "we have a vendor": "\"Most of our clients did too. They switched because we saved them 30% on better quality. Would you be open to a side-by-side comparison?\"",
                                "i have a company": "\"Most of our clients did too. They switched because we saved them 30% on better quality. Would you be open to a side-by-side comparison?\"",
                                "send me info": "\"For sure. So I send the right stuff — are you guys mostly using polos, t-shirts, or work jackets?\"",
                                "email me": "\"For sure. So I send the right stuff — are you guys mostly using polos, t-shirts, or work jackets?\"",
                                "too busy": "\"Respect that. Quick yes or no: are you paying more than $12 per piece right now?\"",
                                "don't have 15 minutes": "\"Respect that. Quick yes or no: are you paying more than $12 per piece right now?\"",
                                "i don't even have 15 minutes": "\"Respect that. Quick yes or no: are you paying more than $12 per piece right now?\"",
                                "can't beat my prices": "\"Maybe, maybe not. What are you paying per piece? If I can't beat it, I'll tell you straight.\"",
                                "i highly doubt you can beat the prices": "\"Maybe, maybe not. What are you paying per piece? If I can't beat it, I'll tell you straight.\"",
                                "we do in-house": "\"How many hours a month does someone spend on orders, tracking, replacements? We usually save 5-10 hours on that alone.\"",
                                "talk to the owner": "\"Totally. What's their name and direct line so I can mention you referred me?\"",
                                "just got uniforms": "\"No worries. When do you usually reorder? I'll reach out with pricing before your next buy.\"",
                                "i don't need the uniform now": "\"Not asking you to buy now. When's your next reorder? I'll send pricing so you can compare.\"",
                                "keep you in mind": "\"Appreciate that. What would need to change for you to look at alternatives?\"",
                                "i'll pass": "\"Before I go — what would need to change about your current setup for you to consider alternatives?\"",
                                "i don't pay that much": "\"Good — what are you paying? If we can match or beat it with better quality, worth 2 minutes?\"",
                                "my day is very busy": "\"I hear you. I'll be quick: are you happy with your current uniform quality and price? Yes or no.\"",
                                "i can't change nothing": "\"Not asking you to change today. Just want to show you what's out there so you have options.\"",
                                "just give me your number": "\"Sure — it's [NUMBER]. But honestly, I'll follow up [DAY] with a quick text and 3 styles with pricing. Fair?\"",
                            }
                            for obj in parsed:
                                obj_lower = obj.strip().lower()
                                response = None
                                for key, val in _obj_responses.items():
                                    if key in obj_lower or obj_lower in key:
                                        response = val
                                        break
                                if response:
                                    st.info(f"**How to handle \"{obj}\":** {response}")
                    except Exception:
                        pass

                reason = call.get("classification_reason", "")
                if reason:
                    st.caption(f"Why this was a discovery: {reason}")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — STRATEGIC DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════

if page == "Strategic Diagnostic" and is_manager:

    total_dials = f_metrics["dials"]
    total_disc = f_metrics["real_disc"]
    total_ivr = f_metrics["ivr"]
    total_gate = f_metrics["gatekeeper"]
    disc_rate = 100 * total_disc / max(total_dials, 1)
    ivr_rate = 100 * total_ivr / max(total_dials, 1)

    # ── Executive Summary ────────────────────────────────────────────────

    st.markdown("### Executive Summary")

    st.markdown(f"""
<div style="background:#1a1a2e; padding:24px 32px; border-radius:12px; border-left:4px solid #e74c3c; margin-bottom:20px">
  <h3 style="color:#e74c3c; margin:0 0 12px 0; font-size:20px">Critical Finding: {disc_rate:.1f}% Discovery Rate</h3>
  <p style="color:#ccc; margin:0; font-size:15px; line-height:1.7">
    Of <b style="color:white">{total_dials:,}</b> outbound dials, only <b style="color:white">{total_disc}</b> resulted in a real discovery conversation.
    B2B cold call benchmark is 5-8%. <b style="color:white">The team is operating at 3-4x below industry standard.</b><br><br>
    <b style="color:#e74c3c">{total_ivr}</b> calls ({ivr_rate:.0f}% of classified) were IVR traps — reps stuck in phone menus of companies that are not the target customer.
    This is the single largest source of wasted time.
  </p>
</div>
""", unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Discovery Rate", f"{disc_rate:.1f}%", help="Benchmark: 5-8%")
    d2.metric("IVR Waste", f"{ivr_rate:.0f}%", help="Calls lost to phone menus")
    d3.metric("Gatekeeper Block", f"{100*total_gate/max(total_dials,1):.0f}%")
    d4.metric("Productive Minutes / Day",
              f"{total_disc * 4:.0f} min",
              help="Approx. 4 min avg per discovery call")

    st.divider()

    # ── Problem 1: ICP ───────────────────────────────────────────────────

    st.markdown("### Problem #1: Wrong Companies Being Called (ICP)")

    st.markdown("""
The prospecting list has **no ICP filter**. SDRs are dialing hospitals, alarm companies,
furniture e-commerce, and toll-free corporate numbers. These companies will **never buy custom uniforms**.

**Evidence from the data:**
""")

    # Show IVR trap examples
    ivr_calls = fdf[fdf["classification"] == "ivr_hold"].copy()
    if not ivr_calls.empty:
        ivr_by_company = ivr_calls.groupby("to_name").agg(
            Calls=("id", "count"),
            AvgDur=("duration", "mean"),
        ).reset_index().sort_values("AvgDur", ascending=False).head(10)
        ivr_by_company["AvgDur"] = ivr_by_company["AvgDur"].round(0).astype(int)
        ivr_by_company.columns = ["Company Called", "Times Called", "Avg Duration (s)"]
        ivr_by_company = ivr_by_company[ivr_by_company["Company Called"].notna() & (ivr_by_company["Company Called"] != "")]

        col_ivr1, col_ivr2 = st.columns([1,1])
        with col_ivr1:
            st.markdown("**Worst IVR Traps (longest avg duration)**")
            if not ivr_by_company.empty:
                st.dataframe(ivr_by_company, use_container_width=True, hide_index=True)
            else:
                st.info("Company names not available for IVR calls.")

        with col_ivr2:
            # Toll-free analysis
            st.markdown("**Toll-free numbers (800/888/877/866) — always IVR**")
            tollfree = fdf[fdf["to_number"].str.startswith("+1", na=False)].copy()
            tollfree["ac"] = tollfree["to_number"].str[2:5]
            tollfree_mask = tollfree["ac"].isin(["800","888","877","866","855","844","833"])
            tf_total = tollfree_mask.sum()
            tf_disc = (tollfree[tollfree_mask]["classification"] == "real_discovery").sum()
            tf_ivr = (tollfree[tollfree_mask]["classification"] == "ivr_hold").sum()

            st.markdown(f"""
- **{tf_total}** calls to toll-free numbers
- **{tf_disc}** discoveries (0%)
- **{tf_ivr}** were IVR traps
- **Recommendation: remove ALL toll-free numbers from the list**
""")

    st.markdown("""
<div style="background:#0d1f0d; padding:20px 28px; border-radius:10px; border-left:4px solid #2ecc71; margin:16px 0">
  <h4 style="color:#2ecc71; margin:0 0 8px 0">Ideal Customer Profile (ICP)</h4>
  <table style="color:#ccc; font-size:14px; line-height:1.8; border-collapse:collapse; width:100%">
    <tr><td style="padding:4px 16px 4px 0; color:#2ecc71; font-weight:600">Industry</td><td>Cleaning, HVAC, Construction, Restaurant, Landscaping, Logistics</td></tr>
    <tr><td style="padding:4px 16px 4px 0; color:#2ecc71; font-weight:600">Company size</td><td>10-100 employees (owner answers the phone)</td></tr>
    <tr><td style="padding:4px 16px 4px 0; color:#2ecc71; font-weight:600">Phone type</td><td>Local number (NOT toll-free 800/888). Direct line or cell preferred</td></tr>
    <tr><td style="padding:4px 16px 4px 0; color:#2ecc71; font-weight:600">Decision maker</td><td>Owner, GM, Operations Manager, Office Manager</td></tr>
    <tr><td style="padding:4px 16px 4px 0; color:#e74c3c; font-weight:600">EXCLUDE</td><td>Hospitals, enterprise corps, government, schools, toll-free numbers</td></tr>
  </table>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ── Problem 2: Script ────────────────────────────────────────────────

    st.markdown("### Problem #2: No Consistent Script")

    st.markdown("""
Every call uses a different opener. The company name changes between calls ("JT Shirts",
"JT Shoes", "JTCS", "J T-Shirts"). Reps default to a **30+ second monologue** before asking
a single question.
""")

    # Show actual openers
    all_openers = fdf[fdf["opener_extract"].notna() & (fdf["opener_extract"] != "")].copy()
    if not all_openers.empty:
        col_s1, col_s2 = st.columns(2)

        with col_s1:
            st.markdown("**Openers that LED to discovery:**")
            disc_openers = all_openers[all_openers["classification"] == "real_discovery"].sort_values("duration", ascending=False)
            for _, r in disc_openers.head(6).iterrows():
                st.success(f"**{r['from_name']}** ({r['duration']}s) — *\"{r['opener_extract'][:150]}\"*")

        with col_s2:
            st.markdown("**Openers that FAILED (gatekeeper/IVR):**")
            fail_openers = all_openers[all_openers["classification"].isin(["gatekeeper","ivr_hold"])].sort_values("duration", ascending=False)
            for _, r in fail_openers.head(6).iterrows():
                st.error(f"**{r['from_name']}** ({r['duration']}s) — *\"{r['opener_extract'][:150]}\"*")

    st.divider()

    # ── Problem 3: Regions ───────────────────────────────────────────────

    st.markdown("### Problem #3: Territory Misallocation")

    # Metro-level analysis
    fdf_metro = fdf[fdf["to_number"].str.startswith("+1", na=False)].copy()
    fdf_metro["ac"] = fdf_metro["to_number"].str[2:5]
    fdf_metro["metro"] = fdf_metro["ac"].apply(_metro)

    metro_stats = fdf_metro.groupby("metro").agg(
        Dials=("id", "count"),
        DM=("classification", lambda x: (x == "real_discovery").sum()),
        IVR=("classification", lambda x: (x == "ivr_hold").sum()),
    ).reset_index()
    metro_stats = metro_stats[metro_stats["Dials"] >= 10].sort_values("Dials", ascending=False)
    metro_stats["DM Reached %"] = (100 * metro_stats["DM"] / metro_stats["Dials"]).round(1)
    metro_stats["IVR Stuck %"] = (100 * metro_stats["IVR"] / metro_stats["Dials"]).round(1)

    col_r1, col_r2 = st.columns([1, 1])

    with col_r1:
        st.markdown("**Volume vs. Results by Metro**")
        st.caption("One row per metro area. DM Reached = rep spoke with the decision-maker. IVR Stuck = rep trapped in automated menu.")
        st.dataframe(
            metro_stats.rename(columns={"metro":"Metro", "DM": "DM Reached"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Metro":         st.column_config.Column("Metro", help="Metro area grouped from area codes."),
                "Dials":         st.column_config.NumberColumn("Dials", help="Total outbound calls to this metro."),
                "DM Reached":    st.column_config.NumberColumn("DM Reached", help="Calls where rep spoke with the decision-maker."),
                "IVR":           st.column_config.NumberColumn("IVR", help="Calls where rep got stuck in automated menu."),
                "DM Reached %":  st.column_config.ProgressColumn("DM Reached %", format="%.1f%%", min_value=0, max_value=10,
                                                                  help="DM Reached ÷ Dials. Higher is better."),
                "IVR Stuck %":   st.column_config.ProgressColumn("IVR Stuck %", format="%.1f%%", min_value=0, max_value=100,
                                                                  help="IVR ÷ Dials. Higher = more big companies with phone trees."),
            }
        )

    with col_r2:
        st.markdown("**Recommended Actions:**")
        high_vol_low_conv = metro_stats[(metro_stats["Dials"] >= 30) & (metro_stats["DM Reached %"] <= 1)]
        high_conv = metro_stats[metro_stats["DM Reached %"] >= 3].sort_values("DM Reached %", ascending=False)

        if not high_conv.empty:
            st.markdown("**Scale UP** (high DM Reached rate):")
            for _, r in high_conv.iterrows():
                st.markdown(f"- **{r['metro']}** — {r['DM Reached %']}% DM Reached, {r['Dials']} dials")

        if not high_vol_low_conv.empty:
            st.markdown("**Scale DOWN or fix list** (high volume, near-zero results):")
            for _, r in high_vol_low_conv.iterrows():
                st.markdown(f"- **{r['metro']}** — {r['DM Reached %']}% DM Reached despite {r['Dials']} dials")

    st.divider()

    # ── Problem 4: Timing ────────────────────────────────────────────────

    st.markdown("### Problem #4: Suboptimal Dial Times")

    fdf_time = fdf.copy()
    fdf_time["hour"] = fdf_time["start_time"].dt.hour
    hour_perf = fdf_time.groupby("hour").agg(
        Dials=("id", "count"),
        Disc=("classification", lambda x: (x == "real_discovery").sum()),
    ).reset_index()
    hour_perf["Rate"] = (100 * hour_perf["Disc"] / hour_perf["Dials"].clip(lower=1)).round(1)

    best_hours = hour_perf[hour_perf["Dials"] >= 20].sort_values("Rate", ascending=False)

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("**Discovery Rate by Hour**")
        if not hour_perf.empty:
            chart_data = hour_perf.set_index("hour")[["Dials", "Rate"]].rename(columns={"Rate":"Discovery %"})
            st.bar_chart(chart_data, height=300)
    with col_t2:
        st.markdown("**Recommendations:**")
        if not best_hours.empty:
            top_hour = best_hours.iloc[0]
            st.markdown(f"""
- **Peak hour:** {int(top_hour['hour'])}:00 ({top_hour['Rate']}% discovery rate)
- **Concentrate dials** in the hours with highest discovery rate above
- **Avoid** early morning and late afternoon (prospects gone for the day)
- **Best days:** Wednesday (highest %) and Thursday
- **Worst days:** Monday and Friday (meeting-heavy days for prospects)
""")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — SALES PLAYBOOK
# ═════════════════════════════════════════════════════════════════════════���═

if page == "Sales Playbook":

    st.markdown("""
<div style="background:linear-gradient(135deg,#1a2847 0%,#0F1B2D 100%); padding:28px 36px; border-radius:12px; margin-bottom:20px">
  <h2 style="color:#C5A572; margin:0 0 8px 0">J T-Shirts — Sales Playbook</h2>
  <p style="color:rgba(255,255,255,.6); margin:0; font-size:14px">Based on analysis of {total_dials:,} calls · Generated from real call data</p>
</div>
""".format(total_dials=total_dials), unsafe_allow_html=True)

    # ── Section 1: ICP ───────────────────────────────────────────────────

    st.markdown("### 1. Who to Call (ICP)")

    col_icp1, col_icp2 = st.columns(2)

    with col_icp1:
        st.markdown("""
<div style="background:#0d1f0d; padding:20px; border-radius:10px; border:1px solid #2ecc7133; color:#e0e0e0">
<h4 style="color:#2ecc71; margin:0 0 12px 0">CALL THESE</h4>

<p style="color:#e0e0e0"><b style="color:#fff">Industries:</b></p>
<ul style="color:#e0e0e0">
<li>Cleaning / Janitorial companies</li>
<li>HVAC / Plumbing / Electrical contractors</li>
<li>Construction companies</li>
<li>Restaurants / Food service</li>
<li>Landscaping / Property maintenance</li>
<li>Logistics / Delivery services</li>
<li>Auto shops / Mechanics</li>
</ul>

<p style="color:#e0e0e0"><b style="color:#fff">Company profile:</b></p>
<ul style="color:#e0e0e0">
<li>10-100 employees</li>
<li>Local phone number (direct line or cell)</li>
<li>Owner or Operations Manager reachable</li>
<li>Currently buying uniforms from someone</li>
</ul>

<p style="color:#e0e0e0"><b style="color:#fff">Best regions (from data):</b></p>
<ul style="color:#e0e0e0">
<li>Phoenix AZ — highest discovery rate</li>
<li>Boston MA — strong engagement</li>
<li>Detroit MI — responsive market</li>
<li>Dallas TX (469 area) — good results</li>
</ul>
</div>
""", unsafe_allow_html=True)

    with col_icp2:
        st.markdown("""
<div style="background:#1f0d0d; padding:20px; border-radius:10px; border:1px solid #e74c3c33; color:#e0e0e0">
<h4 style="color:#e74c3c; margin:0 0 12px 0">DO NOT CALL THESE</h4>

<p style="color:#e0e0e0"><b style="color:#fff">Automatic disqualifiers:</b></p>
<ul style="color:#e0e0e0">
<li>Toll-free numbers (800, 888, 877, 866, 855, 844, 833)</li>
<li>Hospitals and healthcare systems</li>
<li>Enterprise corporations (Wayfair, ADT, etc.)</li>
<li>Government offices</li>
<li>Schools / Universities</li>
<li>Companies with &lt; 5 employees</li>
<li>Residential numbers</li>
</ul>

<p style="color:#e0e0e0"><b style="color:#fff">Dead-zone regions (from data):</b></p>
<ul style="color:#e0e0e0">
<li>NYC (718/212/347) — 110+ calls, 0 discoveries</li>
<li>Philadelphia (215/267) — 40 calls, 0 discoveries</li>
<li>Portland (503) — 17 calls, 0 discoveries, avg 26s</li>
</ul>

<p style="color:#e0e0e0"><b style="color:#fff">Red flags in caller ID:</b></p>
<ul style="color:#e0e0e0">
<li>"HOSPITAL", "MEDICAL", "HEALTH"</li>
<li>"UNIVERSITY", "SCHOOL", "COLLEGE"</li>
<li>"COUNTY", "STATE", "CITY OF"</li>
</ul>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ── Section 2: The Script ────────────────────────────────────────────

    st.markdown("### 2. The Call Script")

    st.markdown("""
<div style="background:#1a1a2e; padding:24px 28px; border-radius:12px; border:1px solid #C5A57233; margin-bottom:16px">
<p style="color:#C5A572; font-weight:700; margin:0 0 4px 0; font-size:12px; letter-spacing:1px">WHY THIS SCRIPT WORKS</p>
<p style="color:#ccc; margin:0; font-size:14px; line-height:1.6">
The data shows that calls where the rep <b style="color:white">asked a question in the first 10 seconds</b> converted
2x better than calls where the rep started with a monologue. Luis consistently outperforms Ron because he
qualifies before pitching. This script mirrors what works.
</p>
</div>
""", unsafe_allow_html=True)

    st.markdown("#### Step 1: Opener (5 seconds)")
    st.code("""
"Hey [NAME], this is [REP] with J T-Shirts — quick question,
 are you the one handling uniforms for the team?"
""", language=None)
    st.caption("Always use **J T-Shirts** (never JT Shoes, JTCS, or JT Shirts). Ask a question immediately — don't pitch.")

    st.markdown("#### Step 2: Qualify (10 seconds)")
    st.markdown("""
| If they say... | You say... |
|---|---|
| **"Yes, I handle it"** | *"Cool. How many people are you outfitting right now?"* |
| **"No, that's not me"** | *"Got it — who would that be? I'll try them directly."* (get name + direct line) |
| **"Who is this?"** | *"J T-Shirts — we do uniforms for [cleaning/HVAC/restaurant] companies in [their city]. You the right person?"* |
""")

    st.markdown("#### Step 3: Hook (10 seconds)")
    st.code("""
"The reason I'm calling — we work with [SIMILAR INDUSTRY] companies
 in [THEIR AREA] and we've been cutting their uniform cost by about
 30% with better quality stuff that lasts longer.
 Not sure if that's something you'd want to look at?"
""", language=None)
    st.caption("""
**Keys:** (1) Mention THEIR industry. (2) Mention THEIR area. (3) Lead with the savings number.
(4) End with a question, not a statement. (5) Total time to this point: under 25 seconds.
""")

    st.markdown("#### Step 4: Close for Next Step")
    st.code("""
Option A (meeting):
"I don't want to take up your whole day. Can I send you 3 styles
 with pricing and follow up [DAY]? Takes 2 minutes to review."

Option B (in-person — when they ask):
"For sure — our closest rep can swing by [DAY]. What time works best,
 morning or afternoon?"

Option C (they're interested NOW):
"Tell you what — how many people and what kind of work gear?
 I'll get you a quote before end of day."
""", language=None)

    st.divider()

    # ── Section 3: Objection Handling ────────────────────────────────────

    st.markdown("### 3. Objection Handling Guide")

    st.markdown(f"""
<div style="background:#1a1a2e; padding:16px 24px; border-radius:10px; margin-bottom:16px">
<p style="color:#888; margin:0; font-size:13px">Based on <b style="color:white">{sum(all_objections.values())}</b> objections extracted from real calls. Each response follows the pattern:
<b style="color:#C5A572">Acknowledge → Reframe → Ask</b></p>
</div>
""", unsafe_allow_html=True)

    # Define objection categories and responses based on what we found
    objection_playbook = [
        {
            "objection": "Not interested",
            "frequency": all_objections.get("not interested", 0),
            "what_it_means": "They haven't heard enough to decide. This is a reflex, not a real objection.",
            "response": '"Totally fair — I wouldn\'t be interested in a random call either. But real quick: are you guys currently spending more than $12 per shirt on your uniforms?"',
            "why_it_works": "Redirects from emotional rejection to a concrete number. If they're paying more, curiosity kicks in.",
        },
        {
            "objection": "We already have a vendor / I have a company",
            "frequency": all_objections.get("i have a company who provide it", 0) + all_objections.get("i'm already locked in", 0) + all_objections.get("i've been with them forever", 0),
            "what_it_means": "They have a provider. Doesn't mean they're happy or getting a good deal.",
            "response": '"That\'s actually why I\'m calling — most companies we work with already had a vendor. They switched because we saved them 30% on better quality stuff. I\'m not asking you to switch today. Would you be open to seeing a side-by-side price comparison? Takes 2 minutes."',
            "why_it_works": "Normalizes having a vendor. Positions JT as the upgrade, not the replacement. Low commitment ask.",
        },
        {
            "objection": "Send me info / Email me",
            "frequency": all_objections.get("can you send me your information and i will check it later", 0) + all_objections.get("can you email me some of your information?", 0),
            "what_it_means": "Polite brush-off. 95% of 'send info' emails never get opened.",
            "response": '"For sure, I\'ll send it over. So I send you the right stuff — are you guys mostly using polos, t-shirts, or something heavier like work jackets?"',
            "why_it_works": "Agrees to their request (removes friction), then asks a qualifying question that re-engages the conversation.",
        },
        {
            "objection": "I'm too busy / Don't have 15 minutes",
            "frequency": all_objections.get("we're in the middle of a workday", 0) + all_objections.get("i don't even have 15 minutes", 0),
            "what_it_means": "Timing is wrong, not necessarily the offer. They may be genuinely busy.",
            "response": '"I respect that — you\'re running a business. I won\'t take 15 minutes. Quick yes or no: are you paying more than $12 per piece on your uniforms right now?"',
            "why_it_works": "Respects their time. Reduces the ask from 15 min to a yes/no. If they answer, you're in a conversation.",
        },
        {
            "objection": "I highly doubt you can beat my prices",
            "frequency": all_objections.get("i highly doubt you can beat the prices i get", 0),
            "what_it_means": "This is actually a HOT lead. They're price-conscious and willing to compare.",
            "response": '"Maybe, maybe not — but I\'d love to try. What are you paying per piece right now? If I can\'t beat it, I\'ll tell you straight and we both move on."',
            "why_it_works": "Turns a challenge into a pricing conversation. Once they share their current price, you have leverage.",
        },
        {
            "objection": "We do uniforms in-house / We don't use a service",
            "frequency": all_objections.get("we do them in-house", 0) + all_objections.get("not something that we currently do", 0),
            "what_it_means": "They handle it themselves. Often means someone is wasting time ordering from multiple places.",
            "response": '"Got it — a lot of our clients started that way too. Quick question: how much time per month does someone spend handling orders, tracking inventory, chasing replacements? We usually save 5-10 hours a month just on that."',
            "why_it_works": "Shifts the value prop from price to time savings. Owners hate spending time on non-revenue tasks.",
        },
        {
            "objection": "I need to talk to the owner / That's not my decision",
            "frequency": all_objections.get("i'll have to speak to the owner", 0),
            "what_it_means": "You're talking to the wrong person. But they can connect you.",
            "response": '"Totally understand. What\'s the best way to reach them directly? And what\'s their name so I can mention you referred me?"',
            "why_it_works": "Gets the decision-maker's name and direct contact. The referral mention gets you past the gatekeeper next time.",
        },
        {
            "objection": "We just got uniforms / Not right now",
            "frequency": all_objections.get("we just got uniforms yesterday", 0) + all_objections.get("we may need to get some later on", 0),
            "what_it_means": "Timing is off but there's future potential. These are reorder opportunities.",
            "response": '"No worries at all — when do you usually reorder? I\'ll reach out closer to that time with pricing so you can compare before your next buy."',
            "why_it_works": "Establishes a follow-up reason. Gets their reorder cycle. Positions you for the next purchase.",
        },
        {
            "objection": "I'll keep you in mind / I'll pass",
            "frequency": all_objections.get("i'll keep you in mind for the future", 0) + all_objections.get("i'll pass on that", 0),
            "what_it_means": "Polite rejection. They won't keep you in mind.",
            "response": '"I appreciate that. Real quick before I let you go — what would need to change about your current setup for you to look at alternatives? Just so I know if it\'s even worth following up."',
            "why_it_works": "Gets the real objection behind the polite one. Their answer tells you whether to follow up or move on.",
        },
        {
            "objection": "We have good quality stuff already",
            "frequency": all_objections.get("we have pretty good quality stuff", 0),
            "what_it_means": "They're satisfied. But 'pretty good' isn't 'great'.",
            "response": '"That\'s good to hear — how often are you replacing pieces? A lot of companies we work with thought their stuff was solid until they saw ours last 2x longer. Would it be worth a quick look?"',
            "why_it_works": "Questions their assumption without challenging them. Replacement frequency is a concrete metric they can evaluate.",
        },
        {
            "objection": "We don't wear uniforms",
            "frequency": all_objections.get("we don't wear uniforms", 0),
            "what_it_means": "Wrong target. This company shouldn't be on the list.",
            "response": '"Got it — appreciate your time. This one\'s on me. Have a good one."',
            "why_it_works": "Don't waste time. Mark the lead as disqualified. Move on.",
        },
    ]

    for item in objection_playbook:
        freq_badge = f" ({item['frequency']}x in data)" if item['frequency'] > 0 else ""
        with st.expander(f"**\"{item['objection']}\"**{freq_badge}", expanded=False):
            st.markdown(f"**What it really means:** {item['what_it_means']}")
            st.markdown("**Your response:**")
            st.info(item["response"])
            st.caption(f"Why it works: {item['why_it_works']}")

    st.divider()

    # ── Section 4: Gatekeeper Tactics ────────────────────────────────────

    st.markdown("### 4. Getting Past the Gatekeeper")

    st.markdown(f"""
**{total_gate} calls** in this period were blocked by a gatekeeper. Here's how to handle them:
""")

    st.markdown("""
| Gatekeeper says... | You say... |
|---|---|
| **"Who's calling?"** | *"It's [NAME] from J T-Shirts — [BOSS NAME] is expecting my call about the uniform program."* (only if you have the name) |
| **"What is this about?"** | *"It's about their uniform budget — they'll know what it's about."* (confident, not salesy) |
| **"They're not available"** | *"No problem. What's the best time to catch them? And what's their direct line so I don't bother you again?"* |
| **"Can I take a message?"** | *"Sure — [NAME] from J T-Shirts, [YOUR NUMBER]. But honestly, what's their cell or direct line? I don't want to keep calling the front desk."* |
| **"They're in a meeting"** | *"Got it. What time does that wrap up? I'll try back right after."* (call back exactly when they said) |
| **"Send an email"** | *"Will do — what's their direct email? Not the info@ — their actual inbox."* |
""")

    st.markdown("""
<div style="background:#1a1a2e; padding:16px 24px; border-radius:10px; margin-top:12px">
<p style="color:#C5A572; font-weight:600; margin:0 0 6px 0">Pro tip from the data:</p>
<p style="color:#ccc; margin:0; font-size:14px">
Calls where the rep <b>asked for a specific person by name</b> got through gatekeepers more often.
Before calling, spend 30 seconds on LinkedIn or Google to find the owner/manager's name.
"Can I speak with [FIRST NAME]?" works 3x better than "Can I speak with the person who handles uniforms?"
</p>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ── Section 5: Daily Routine ─────────────────────────────────────────

    st.markdown("### 5. Optimal Daily Routine")

    st.markdown("""
| Time Block | Activity | Why |
|---|---|---|
| **8:00 - 8:30** | Prep: review list, research top 10 prospects on LinkedIn/Google | Calling without research = IVR traps |
| **8:30 - 9:00** | Follow-ups from yesterday's "call me back" and "send info" | Warm leads first while energy is high |
| **9:00 - 11:30** | **Power Block 1:** Cold calls (target: 40 dials) | Morning = decision makers at their desk |
| **11:30 - 12:00** | Update CRM, send promised emails/quotes | Don't let follow-ups pile up |
| **12:00 - 13:00** | Lunch | |
| **13:00 - 13:30** | Follow-ups from morning calls | Strike while warm |
| **13:30 - 16:00** | **Power Block 2:** Cold calls (target: 30 dials) | Afternoon = second peak from data |
| **16:00 - 16:30** | Wrap: update CRM, prep tomorrow's list | End organized |

**Daily targets per SDR:**
- 70 dials (not 70 random numbers — 70 ICP-qualified prospects)
- 5+ real conversations (currently at ~1-2)
- 1 meeting booked (currently near 0)
""")

    st.divider()

    # ── Section 6: Quick Reference ───────────────────────────────────────

    st.markdown("### 6. Quick Reference Card")

    st.markdown("""
<div style="background:linear-gradient(135deg,#1a2847 0%,#0F1B2D 100%); padding:24px 28px; border-radius:12px; border:1px solid #C5A57244">
<h4 style="color:#C5A572; margin:0 0 16px 0">Print this. Tape it to your monitor.</h4>
<div style="color:#ccc; font-size:14px; line-height:2">

**THE SCRIPT IN 4 LINES:**

1. "Hey [NAME], [REP] with J T-Shirts — are you the one handling uniforms?"

2. "Cool. How many people are you outfitting?"

3. "We work with [INDUSTRY] companies in [AREA] and save them about 30% on better quality uniforms."

4. "Can I send you 3 styles with pricing and follow up [DAY]?"

---

**THE 3 RULES:**

1. **QUALIFY FIRST.** Ask a question before you say anything about J T-Shirts.

2. **15 SECONDS MAX** before you pause and let them talk. If you're monologuing, you're losing.

3. **IF IT'S IVR, HANG UP.** Don't wait on hold. Mark the number, move on, try a direct line later.

</div>
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — LEAD LIST
# ═══════════════════════════════════════════════════════════════════════════

if page == "Lead List":

    st.markdown("""
<div style="background:linear-gradient(135deg,#1a2847 0%,#0F1B2D 100%); padding:24px 32px; border-radius:12px; margin-bottom:20px">
  <h2 style="color:#C5A572; margin:0 0 8px 0">Daily Lead List</h2>
  <p style="color:rgba(255,255,255,.5); margin:0; font-size:14px">ICP-qualified prospects with decision-maker research. Generate new leads: <code>python jtcalls.py leadgen</code></p>
</div>
""", unsafe_allow_html=True)

    # Load leads
    leads_df = pd.DataFrame()
    try:
        conn_leads = sqlite3.connect(DB_PATH)
        leads_df = pd.read_sql("SELECT * FROM leads ORDER BY generated_date DESC, company_name", conn_leads)
        conn_leads.close()
    except Exception:
        pass

    if leads_df.empty:
        st.info("No leads generated yet. Run `python jtcalls.py leadgen` to generate 50 qualified leads.")
    else:
        # Summary metrics
        l1, l2, l3, l4 = st.columns(4)
        total_leads = len(leads_df)
        with_phone = (leads_df["phone"].notna() & (leads_df["phone"] != "")).sum()
        with_contact = (leads_df["contact_name"].notna() & (leads_df["contact_name"] != "")).sum()
        top_priority = (leads_df["priority"].str.contains("TOP", na=False)).sum() if "priority" in leads_df.columns else 0

        l1.metric("Total Leads", f"{total_leads:,}")
        l2.metric("With Phone", f"{with_phone:,}", f"{100*with_phone/max(total_leads,1):.0f}%")
        l3.metric("With Contact", f"{with_contact:,}", f"{100*with_contact/max(total_leads,1):.0f}%")
        l4.metric("TOP Priority", f"{top_priority:,}")

        st.divider()

        # Filters
        fl1, fl2, fl3, fl4 = st.columns(4)
        with fl1:
            lead_cities = ["All"] + sorted(leads_df["city"].dropna().unique())
            lead_city_filter = st.selectbox("City", lead_cities)
        with fl2:
            lead_segments = ["All"] + sorted(leads_df["segment"].dropna().unique()) if "segment" in leads_df.columns else ["All"]
            lead_segment_filter = st.selectbox("Segment", lead_segments)
        with fl3:
            prio_options = ["All"] + sorted(leads_df["priority"].dropna().unique().tolist()) if "priority" in leads_df.columns else ["All"]
            lead_prio_filter = st.selectbox("Priority", prio_options)
        with fl4:
            phone_filter = st.selectbox("Phone", ["All", "With phone only", "Missing phone"])

        filtered_leads = leads_df.copy()
        if lead_city_filter != "All":
            filtered_leads = filtered_leads[filtered_leads["city"] == lead_city_filter]
        if lead_segment_filter != "All":
            filtered_leads = filtered_leads[filtered_leads["segment"] == lead_segment_filter]
        if lead_prio_filter != "All":
            filtered_leads = filtered_leads[filtered_leads["priority"] == lead_prio_filter]
        if phone_filter == "With phone only":
            filtered_leads = filtered_leads[filtered_leads["phone"].notna() & (filtered_leads["phone"] != "")]
        elif phone_filter == "Missing phone":
            filtered_leads = filtered_leads[filtered_leads["phone"].isna() | (filtered_leads["phone"] == "")]

        st.markdown(f"**{len(filtered_leads)} leads**")

        # Main data table with all key columns
        display_cols = []
        col_config = {}
        for col in ["company_name","segment","city","state","phone","website","contact_name","title","size","priority","notes","angle"]:
            if col in filtered_leads.columns:
                display_cols.append(col)

        if display_cols:
            rename_map = {
                "company_name": "Company",
                "segment": "Segment",
                "city": "City",
                "state": "ST",
                "phone": "Phone",
                "website": "Website",
                "contact_name": "Contact",
                "title": "Title",
                "size": "Size",
                "priority": "Priority",
                "notes": "Pre-call Notes",
                "angle": "Angle / Hook",
            }
            table_df = filtered_leads[display_cols].rename(columns=rename_map)
            st.dataframe(
                table_df,
                use_container_width=True,
                hide_index=True,
                height=600,
                column_config={
                    "Phone": st.column_config.TextColumn("Phone", width="medium"),
                    "Company": st.column_config.TextColumn("Company", width="medium"),
                    "Contact": st.column_config.TextColumn("Contact", width="medium"),
                    "Pre-call Notes": st.column_config.TextColumn("Pre-call Notes", width="large"),
                    "Angle / Hook": st.column_config.TextColumn("Angle / Hook", width="large"),
                    "Website": st.column_config.TextColumn("Website", width="medium"),
                }
            )

        # Distribution charts
        st.divider()
        col_ld1, col_ld2 = st.columns(2)
        with col_ld1:
            st.markdown("**Leads by City**")
            city_counts = filtered_leads["city"].value_counts().head(15)
            if not city_counts.empty:
                st.bar_chart(city_counts)
        with col_ld2:
            st.markdown("**Leads by Segment**")
            seg_counts = filtered_leads["segment"].value_counts().head(15) if "segment" in filtered_leads.columns else pd.Series()
            if not seg_counts.empty:
                st.bar_chart(seg_counts)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — REP PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════

if page == "Rep Performance" and is_manager:

    st.markdown("### Rep Performance Comparison")
    with st.expander("How the Score is computed", expanded=False):
        st.markdown("""
Each call gets a **quality_score (0–100)** from the LLM, judging the rep's performance (not the outcome).
The score looks at:
- **Opener quality** — did the rep introduce themselves and the reason clearly?
- **Qualification** — did they ask the right questions before pitching?
- **Pitch delivery** — was the value proposition clear and concise?
- **Objection handling** — did they acknowledge → reframe → re-engage?
- **Meeting ask** — did they explicitly ask for a concrete next step?

A rep can do a great job and still get blocked by a gatekeeper — that call still scores 70+.
A rep fumbling a DM conversation gets a low score even if the call lasted 10 minutes.

**Rep Score (below) = average of quality_score across that rep's enriched calls.**
- **0–30**: weak performance — urgent coaching needed
- **30–60**: average — opportunities for handling/closing
- **60+**: strong — these reps' phrases are in the winning examples
        """)

    rep_stats = fdf.groupby("from_name").agg(
        Dials=("id","count"),
        Connected=("result", lambda x: x.isin(["Call connected","Accepted"]).sum()),
        Over2min=("duration", lambda x: (x >= 120).sum()),
        Real_Discovery=("classification", lambda x: (x == "real_discovery").sum()),
        IVR=("classification", lambda x: (x == "ivr_hold").sum()),
        Gatekeeper=("classification", lambda x: (x == "gatekeeper").sum()),
        Wrong_Num=("result", lambda x: (x == "Wrong Number").sum()),
        Avg_Dur=("duration", "mean"),
    ).reset_index()

    rep_stats = rep_stats[rep_stats["Dials"] >= 10].sort_values("Dials", ascending=False)
    rep_stats["Connected %"] = (100 * rep_stats["Connected"] / rep_stats["Dials"]).round(1)
    rep_stats["DM %"] = (100 * rep_stats["Real_Discovery"] / rep_stats["Dials"]).round(1)
    rep_stats["IVR %"] = (100 * rep_stats["IVR"] / rep_stats["Dials"]).round(1)
    rep_stats["Avg Dur (s)"] = rep_stats["Avg_Dur"].round(0).astype(int)

    rep_stats = rep_stats.rename(columns={
        "from_name": "Rep",
        "Over2min": "Over 2min",
        "Real_Discovery": "DM Reached",
        "Wrong_Num": "Wrong #",
    })
    display_cols = ["Rep", "Dials", "Connected", "Connected %", "Over 2min",
                    "DM Reached", "DM %", "IVR", "IVR %", "Gatekeeper", "Wrong #", "Avg Dur (s)"]
    rep_stats = rep_stats[display_cols]

    # Always-visible legend
    st.caption(
        "**Column meanings** — "
        "**Dials**: total outbound calls · "
        "**Connected**: someone answered · "
        "**Connected %**: answer rate (benchmark 30–40%) · "
        "**Over 2min**: calls longer than 2 minutes · "
        "**DM Reached**: spoke with the decision-maker · "
        "**DM %**: DM Reached ÷ Dials (benchmark 2–5% SMB cold) · "
        "**IVR**: stuck in automated menu / virtual assistant · "
        "**IVR %**: % of calls burned on IVR (lower is better) · "
        "**Gatekeeper**: receptionist blocked the rep · "
        "**Wrong #**: number flagged incorrect (list hygiene issue) · "
        "**Avg Dur (s)**: average call duration in seconds."
    )

    st.dataframe(
        rep_stats,
        use_container_width=True, hide_index=True,
        column_config={
            "Dials":        st.column_config.NumberColumn("Dials", help="Total outbound calls in the filtered period"),
            "Connected":    st.column_config.NumberColumn("Connected", help="Someone answered — not voicemail, not no-answer"),
            "Connected %":  st.column_config.ProgressColumn("Connected %", help="Connected ÷ Dials · answer rate", format="%.1f%%", min_value=0, max_value=100),
            "Over 2min":    st.column_config.NumberColumn("Over 2min", help="Calls that lasted more than 2 minutes"),
            "DM Reached":   st.column_config.NumberColumn("DM Reached", help="Calls where rep spoke with the decision-maker"),
            "DM %":         st.column_config.ProgressColumn("DM %", help="DM Reached ÷ Dials · the productivity number", format="%.1f%%", min_value=0, max_value=10),
            "IVR":          st.column_config.NumberColumn("IVR", help="Stuck in automated menu / virtual assistant"),
            "IVR %":        st.column_config.ProgressColumn("IVR %", help="% of calls wasted on IVR — lower is better", format="%.1f%%", min_value=0, max_value=100),
            "Gatekeeper":   st.column_config.NumberColumn("Gatekeeper", help="Human receptionist blocked the rep"),
            "Wrong #":      st.column_config.NumberColumn("Wrong #", help="RingCentral flagged as wrong number"),
            "Avg Dur (s)":  st.column_config.NumberColumn("Avg Dur (s)", help="Average call duration in seconds"),
        }
    )

    st.divider()

    # ═══════════════════════════════════════════════════
    # Visual comparisons — volume, hour, completed, duration
    # ═══════════════════════════════════════════════════

    fdf_reps_only = fdf[fdf["from_name"].isin(rep_stats["Rep"].tolist())].copy()

    if fdf_reps_only.empty:
        st.info("No rep data in the filtered period.")
    else:
        # 1 — Calls per day per rep
        st.markdown("#### Calls per day · by rep")
        st.caption("Daily volume. Compare effort over time.")
        daily_rep = fdf_reps_only.copy()
        daily_rep["date"] = daily_rep["start_time"].dt.date
        daily_rep_pivot = daily_rep.groupby(["date", "from_name"]).size().unstack(fill_value=0)
        if not daily_rep_pivot.empty:
            st.line_chart(daily_rep_pivot, height=340)

            # Daily averages per rep
            days_active = daily_rep.groupby("from_name")["date"].nunique()
            total_calls = daily_rep.groupby("from_name").size()
            avg_per_day = (total_calls / days_active).round(1).reset_index()
            avg_per_day.columns = ["Rep", "Avg calls/day"]
            avg_per_day["Active days"] = days_active.values
            avg_per_day["Total calls"] = total_calls.values
            avg_per_day = avg_per_day.sort_values("Avg calls/day", ascending=False)
            st.dataframe(avg_per_day, hide_index=True, use_container_width=True)

        st.divider()

        # 2 — Calls por hora por rep
        st.markdown("#### Calls per hour · by rep")
        st.caption("When each rep is dialing. Useful to see who sticks to power hours.")
        hour_rep = fdf_reps_only.copy()
        hour_rep["hour"] = hour_rep["start_time"].dt.hour
        hour_rep_pivot = hour_rep.groupby(["hour", "from_name"]).size().unstack(fill_value=0)
        if not hour_rep_pivot.empty:
            # Make sure we show 0-23
            hour_rep_pivot = hour_rep_pivot.reindex(range(24), fill_value=0)
            st.bar_chart(hour_rep_pivot, height=340)

        st.divider()

        # 3 — Completed vs not completed
        st.markdown("#### Completed vs not completed · by rep")
        st.caption("Completed = prospect answered and call lasted ≥15s. Filters out instant-IVR and no-pickup.")

        def classify_outcome(row):
            dur = row["duration"] or 0
            res = row.get("result") or ""
            if res == "Wrong Number":
                return "Wrong Number"
            if res in ("Call connected", "Accepted") and dur >= 15:
                return "Completed"
            if dur < 15:
                return "No pickup / instant hangup"
            return "Other"

        comp = fdf_reps_only.copy()
        comp["outcome"] = comp.apply(classify_outcome, axis=1)
        comp_pivot = comp.groupby(["from_name", "outcome"]).size().unstack(fill_value=0)
        if not comp_pivot.empty:
            # Reorder columns: Completed first
            order = [c for c in ["Completed", "No pickup / instant hangup", "Wrong Number", "Other"] if c in comp_pivot.columns]
            comp_pivot = comp_pivot[order]
            st.bar_chart(comp_pivot, height=340)

            # Table with % completion
            comp_tbl = comp.groupby("from_name").agg(
                Total=("id", "count"),
                Completed=("outcome", lambda x: (x == "Completed").sum()),
            ).reset_index()
            comp_tbl["% Completion"] = (100 * comp_tbl["Completed"] / comp_tbl["Total"].clip(lower=1)).round(1)
            comp_tbl.columns = ["Rep", "Total", "Completed", "% Completion"]
            comp_tbl = comp_tbl.sort_values("% Completion", ascending=False)
            st.dataframe(
                comp_tbl, hide_index=True, use_container_width=True,
                column_config={"% Completion": st.column_config.ProgressColumn(
                    "% Completion", format="%.1f%%", min_value=0, max_value=100)}
            )

        st.divider()

        # 4 — Average call duration per rep
        st.markdown("#### Average call duration · by rep")
        st.caption("Completed only (≥15s). Very long calls may be IVR traps — cross-check with Overview.")

        dur_df = fdf_reps_only[fdf_reps_only["duration"].fillna(0) >= 15].copy()
        if not dur_df.empty:
            dur_stats = dur_df.groupby("from_name").agg(
                Calls=("id", "count"),
                Avg_sec=("duration", "mean"),
                Median_sec=("duration", "median"),
                Min_sec=("duration", "min"),
                Max_sec=("duration", "max"),
            ).reset_index()
            dur_stats["Avg"] = dur_stats["Avg_sec"].apply(lambda s: f"{int(s//60)}m {int(s%60)}s")
            dur_stats["Median"] = dur_stats["Median_sec"].apply(lambda s: f"{int(s//60)}m {int(s%60)}s")
            dur_stats["Max"] = dur_stats["Max_sec"].apply(lambda s: f"{int(s//60)}m {int(s%60)}s")
            dur_stats = dur_stats[["from_name", "Calls", "Avg", "Median", "Max"]]
            dur_stats.columns = ["Rep", "Calls ≥15s", "Avg", "Median", "Longest"]
            dur_stats = dur_stats.sort_values("Calls ≥15s", ascending=False)
            st.dataframe(dur_stats, hide_index=True, use_container_width=True)

            # Avg duration bar chart (seconds)
            avg_chart = dur_df.groupby("from_name")["duration"].mean().round(0).astype(int)
            avg_chart.name = "Average duration (seconds)"
            st.bar_chart(avg_chart, height=280)

    st.divider()

    # Per-rep coaching cards
    st.markdown("### Individual Coaching Notes")

    for _, rep in rep_stats.iterrows():
        rep_name = rep["Rep"]
        if not rep_name:
            continue

        rep_calls = fdf[fdf["from_name"] == rep_name]
        rep_disc = rep_calls[rep_calls["classification"] == "real_discovery"]
        rep_ivr = rep_calls[rep_calls["classification"] == "ivr_hold"]

        with st.expander(f"**{rep_name}** — {rep['Dials']} dials, {rep['DM Reached']} DM-reached ({rep['DM %']}%)", expanded=True):
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Dials", int(rep["Dials"]),
                       help="Total outbound calls placed by this rep.")
            cc2.metric("DM Reached %", f"{rep['DM %']}%",
                       help="Percentage of calls where the rep spoke with the decision-maker.")
            cc3.metric("IVR Stuck %", f"{rep['IVR %']}%",
                       help="Percentage of calls where the rep was trapped in an automated menu.")
            cc4.metric("Average duration", f"{rep['Avg Dur (s)']}s",
                       help="Average length of the rep's calls in seconds.")

            # Openers analysis
            rep_openers = rep_calls[rep_calls["opener_extract"].notna() & (rep_calls["opener_extract"] != "")]
            if not rep_openers.empty:
                st.markdown("**Sample openers used:**")
                for _, o in rep_openers.sort_values("duration", ascending=False).head(3).iterrows():
                    icon = "+" if o["classification"] == "real_discovery" else "-"
                    st.markdown(f"  {icon} [{o['classification']}] *\"{o['opener_extract'][:150]}\"*")

            # Objections faced
            rep_objs = Counter()
            for objs_json in rep_calls["objections_found"].dropna():
                try:
                    for obj in json.loads(objs_json):
                        if isinstance(obj, str) and obj.strip():
                            rep_objs[obj.strip().lower()] += 1
                except Exception:
                    pass

            if rep_objs:
                st.markdown("**Top objections faced:**")
                for obj, cnt in rep_objs.most_common(5):
                    st.markdown(f"  - \"{obj}\" ({cnt}x)")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — CALL EXPLORER
# ═══════════════════════════════════════════════════════════════════════════

if page == "Call Explorer":

    st.markdown("### Call Explorer")
    st.caption("Browse individual call transcripts. Click any row to see the full transcript.")

    # Filter options for this tab
    ce1, ce2 = st.columns(2)
    with ce1:
        class_filter = st.selectbox(
            "Classification",
            ["All"] + classes_avail,
        )
    with ce2:
        sort_by = st.selectbox("Sort by", ["Longest first", "Most recent first", "Shortest first"])

    explorer_df = fdf[fdf["transcript"].notna() & (fdf["transcript"] != "")].copy()

    if class_filter != "All":
        explorer_df = explorer_df[explorer_df["classification"] == class_filter]

    if sort_by == "Longest first":
        explorer_df = explorer_df.sort_values("duration", ascending=False)
    elif sort_by == "Shortest first":
        explorer_df = explorer_df.sort_values("duration", ascending=True)
    else:
        explorer_df = explorer_df.sort_values("start_time", ascending=False)

    st.markdown(f"**{len(explorer_df)} calls with transcripts**")

    for _, call in explorer_df.head(25).iterrows():
        class_label = call.get("classification", "?")
        color_map = {
            "real_discovery": "#2ecc71",
            "ivr_hold": "#e74c3c",
            "gatekeeper": "#f39c12",
            "voicemail_left": "#9b59b6",
            "quick_hangup": "#95a5a6",
            "wrong_number": "#e67e22",
            "unknown": "#7f8c8d",
        }
        color = color_map.get(class_label, "#7f8c8d")

        label = f"**{call['from_name'] or 'Rep'}** → {call['to_name'] or call['to_number'] or '?'} · {call['duration']}s · "
        label += f":{class_label}: "

        date_str = ""
        try:
            date_str = call["start_time"].strftime("%b %d %H:%M")
        except Exception:
            pass

        with st.expander(f"{date_str} · **{call['from_name'] or 'Rep'}** → {call['to_name'] or call['to_number'] or '?'} · {call['duration']}s · `{class_label}`"):
            meta1, meta2, meta3 = st.columns(3)
            meta1.markdown(f"**Classification:** `{class_label}` ({call.get('classification_confidence', 0):.0%})")
            meta2.markdown(f"**Reason:** {call.get('classification_reason', '-')}")
            meta3.markdown(f"**Duration:** {call['duration']}s")

            opener = call.get("opener_extract", "")
            if opener:
                st.markdown(f"**Opener:** *\"{opener}\"*")

            objs = call.get("objections_found", "")
            if objs:
                try:
                    parsed = json.loads(objs)
                    if parsed:
                        st.markdown(f"**Objections:** {', '.join(parsed)}")
                except Exception:
                    pass

            st.markdown("**Full Transcript:**")
            st.text(call.get("transcript", "No transcript available."))


# ═══════════════════════════════════════════════════════════════════════════
# TAB · CRM
# ═══════════════════════════════════════════════════════════════════════════

if page == "CRM":
    acc_df = crm["accounts"]

    # ─── UPLOAD + ROUND-ROBIN bar ───────────────────────────────────────────
    header_cols = st.columns([1.3, 1.3, 5])
    with header_cols[0]:
        with st.popover("Upload leads", use_container_width=True):
            st.caption("Upload an XLSX/CSV. Column names are auto-detected (Empresa/Company, Telefone/Phone, Segmento/Industry, Owner_Nome, Email_Owner, Website, etc.).")
            uploaded = st.file_uploader("Pick a file", type=["xlsx", "csv"],
                                        key="crm_upload", label_visibility="collapsed")
            if uploaded is not None:
                if st.button("Import now", key="do_upload", type="primary", use_container_width=True):
                    with st.spinner("Parsing and importing..."):
                        stats = _upload_leads_file(uploaded)
                    if stats.get("error"):
                        st.error(f"Error: {stats['error']}")
                    else:
                        st.success(
                            f"Imported: {stats['inserted']} new · "
                            f"{stats['updated']} updated · {stats['skipped']} skipped "
                            f"(total rows: {stats['total']})"
                        )
                        st.rerun()

    with header_cols[1]:
        with st.popover("Assign round-robin", use_container_width=True):
            st.caption("Distribute unassigned leads evenly across selected reps.")
            # Find unassigned accounts (no owner_rep or no calls yet)
            if not acc_df.empty:
                unassigned_mask = (
                    (acc_df["owner_rep"].isna() | (acc_df["owner_rep"] == "")) &
                    (acc_df["total_calls"].fillna(0) == 0)
                )
                unassigned_count = int(unassigned_mask.sum())
                st.markdown(f"**{unassigned_count}** leads currently have no owner and no calls.")
                # Pool selector
                pool_choice = st.radio(
                    "Pool to distribute",
                    ["Unassigned (never dialed, no owner)", "All New leads", "Custom selection above"],
                    key="rr_pool",
                )
                # Rep selector (only SDRs with outbound activity OR manually added)
                known_reps = sorted(set([r for r in acc_df["owner_rep"].dropna().unique() if r] +
                                         list(reps_available)))
                # Default to Hebron + Aleksa if present (user mentioned)
                default_reps = [r for r in known_reps if r in ("Hebron Fekreselassie", "Aleksa Zdravkovic")]
                if not default_reps:
                    default_reps = known_reps[:2]
                selected_reps = st.multiselect(
                    "Reps to assign to",
                    known_reps, default=default_reps,
                    key="rr_reps",
                )
                max_per_rep = st.number_input(
                    "Max leads per rep (0 = no cap)", min_value=0, max_value=500, value=50, step=5,
                    key="rr_max",
                )
                if st.button("Distribute now", key="do_rr", type="primary", use_container_width=True):
                    if not selected_reps:
                        st.warning("Pick at least one rep.")
                    else:
                        if pool_choice.startswith("Unassigned"):
                            pool_df = acc_df[unassigned_mask]
                        else:
                            pool_df = acc_df[acc_df["stage"] == "New leads"]
                        total_cap = (max_per_rep * len(selected_reps)) if max_per_rep > 0 else len(pool_df)
                        aids = pool_df["account_id"].tolist()[:total_cap]
                        if not aids:
                            st.info("No leads in the selected pool.")
                        else:
                            counts = _round_robin(aids, selected_reps)
                            summary = " · ".join([f"{r}: {n}" for r, n in counts.items()])
                            st.success(f"Assigned {len(aids)} leads — {summary}")
                            st.rerun()

    if acc_df.empty:
        st.info("No accounts yet. Click **Upload leads** above to import your spreadsheet.")
        st.stop()
    else:
        total_acc = len(acc_df)
        by_stage = acc_df["stage"].value_counts().to_dict()

        # KPI row
        kc1, kc2, kc3, kc4, kc5 = st.columns(5)
        kc1.metric("Total accounts", f"{total_acc:,}")
        kc2.metric("DM Reached+", f"{sum(by_stage.get(s,0) for s in ['DM Reached','Email Sent','Meeting','Proposal','Won']):,}")
        kc3.metric("Nurture", f"{by_stage.get('Nurture', 0):,}",
                   help="Dormant leads — come back later")
        kc4.metric("Won", f"{by_stage.get('Won', 0):,}")
        kc5.metric("Lost", f"{by_stage.get('Lost', 0):,}")

        st.divider()

        # View toggle + quick filter
        v_cols = st.columns([2, 3, 3])
        with v_cols[0]:
            view_mode = st.radio(
                "View", ["Kanban", "List"],
                horizontal=True, label_visibility="collapsed",
                key="crm_view_mode",
            )
        with v_cols[1]:
            quick_filter = st.radio(
                "Quick filter", ["All accounts", "New leads only", "My pipeline"],
                horizontal=True, label_visibility="collapsed",
                key="crm_quick_filter",
                help="New leads = assigned to a rep but not dialed yet. My pipeline = narrow to a rep (picked below).",
            )
        with v_cols[2]:
            my_rep = None
            if quick_filter == "My pipeline":
                reps_for_mine = sorted([r for r in acc_df["owner_rep"].dropna().unique() if r])
                my_rep = st.selectbox(
                    "Rep", reps_for_mine,
                    key="crm_my_rep", label_visibility="collapsed",
                )

        # Browse + filters (popover, same pattern as Overview)
        rep_opts = sorted([r for r in acc_df["owner_rep"].dropna().unique() if r])
        industry_opts = sorted([r for r in acc_df["industry"].dropna().unique() if r])
        default_stages = [s for s in STAGE_ORDER if s not in ("New leads", "Lost")]

        header_cols = st.columns([6, 1])
        with header_cols[0]:
            st.markdown("#### Browse accounts")
        with header_cols[1]:
            # Count active filters for the button badge
            active = 0
            if st.session_state.get("crm_stages", default_stages) != default_stages: active += 1
            if st.session_state.get("crm_reps", rep_opts) != rep_opts: active += 1
            if st.session_state.get("crm_industry", []): active += 1
            if st.session_state.get("crm_min_score", 0) > 0: active += 1
            btn_label = f"Filters ({active})" if active else "Filters"
            with st.popover(btn_label, use_container_width=True):
                stage_filter = st.multiselect(
                    "Stage", STAGE_ORDER,
                    default=st.session_state.get("crm_stages", default_stages),
                    key="crm_stages_input",
                )
                rep_filter = st.multiselect(
                    "Owner rep", rep_opts,
                    default=st.session_state.get("crm_reps", rep_opts),
                    key="crm_reps_input",
                )
                industry_filter = st.multiselect(
                    "Industry", industry_opts,
                    default=st.session_state.get("crm_industry", []),
                    key="crm_industry_input",
                ) if industry_opts else []
                min_score = st.slider(
                    "Minimum score", 0, 100,
                    value=st.session_state.get("crm_min_score", 0), step=5,
                    key="crm_minscore_input",
                )
                st.session_state["crm_stages"] = stage_filter
                st.session_state["crm_reps"] = rep_filter
                st.session_state["crm_industry"] = industry_filter
                st.session_state["crm_min_score"] = min_score

        # Restore values from state (in case popover was never opened this run)
        stage_filter = st.session_state.get("crm_stages", default_stages)
        rep_filter = st.session_state.get("crm_reps", rep_opts)
        industry_filter = st.session_state.get("crm_industry", [])
        min_score = st.session_state.get("crm_min_score", 0)

        view = acc_df.copy()
        # Apply quick filter first
        if quick_filter == "New leads only":
            view = view[
                (view["owner_rep"].notna() & (view["owner_rep"] != "")) &
                (view["total_calls"].fillna(0) == 0)
            ]
        elif quick_filter == "My pipeline" and my_rep:
            view = view[view["owner_rep"] == my_rep]
        if stage_filter:
            view = view[view["stage"].isin(stage_filter)]
        if rep_filter:
            view = view[view["owner_rep"].isin(rep_filter)]
        if industry_filter:
            view = view[view["industry"].isin(industry_filter)]
        view = view[view["score"].fillna(0) >= min_score]
        view = view.sort_values(["score", "last_touch_at"], ascending=[False, False])

        st.caption(f"Showing {len(view)} of {total_acc} accounts")

        def _s(v):
            """Safe string: handle NaN/None/float gracefully."""
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ""
            return str(v).strip()

        # ────────────────────────────────────────────────────────────────
        # KANBAN VIEW (single row, horizontal scroll)
        # ────────────────────────────────────────────────────────────────
        if view_mode == "Kanban":
            st.caption("Cards sorted by score within each stage. Top 15 per column. Click **Open** on any card to see full account details and call history. Use **Move →** to change stage.")

            actions_df = crm.get("actions", pd.DataFrame())
            pending_by_acc = {}
            if not actions_df.empty:
                pnd = actions_df[actions_df["status"] == "pending"]
                for aid, grp in pnd.groupby("account_id"):
                    pending_by_acc[aid] = len(grp)

            # Short labels so column headers don't wrap to 2 lines
            STAGE_SHORT = {
                "New leads": "LEADS",
                "Attempted": "ATTEMPT",
                "Gatekeeper": "GATEKEEPER",
                "DM Reached": "DM",
                "Email to Send": "EMAIL",
                "Meeting": "MEETING",
                "Proposal": "PROPOSAL",
                "Won": "WON",
                "Nurture": "NURTURE",
                "Lost": "LOST",
            }

            cols = st.columns(len(KANBAN_STAGES), gap="small")
            for col, stage in zip(cols, KANBAN_STAGES):
                with col:
                    stage_accs = view[view["stage"] == stage]
                    count = len(stage_accs)
                    color = STAGE_COLORS.get(stage, "#6b7280")
                    short = STAGE_SHORT.get(stage, stage.upper())
                    st.markdown(
                        f"<div style='background:{color}; color:white; padding:6px 8px; "
                        f"border-radius:5px; font-size:10.5px; font-weight:700; "
                        f"text-align:center; margin-bottom:6px; letter-spacing:0.5px; "
                        f"white-space:nowrap; overflow:hidden; text-overflow:ellipsis; height:26px; box-sizing:border-box;'>"
                        f"{short} · {count}"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    for _, acc in stage_accs.head(15).iterrows():
                        aid = acc["account_id"]
                        contact = _s(acc.get("contact_name"))
                        company = _s(acc.get("company_name"))
                        phone = _s(acc.get("primary_phone")) or "—"
                        owner = _s(acc.get("owner_rep")) or "—"
                        owner_short = owner[:22]

                        # Line 1: contact name if we have one, else company, else phone
                        if contact:
                            line1 = contact[:30]
                        elif company:
                            line1 = company[:30]
                        else:
                            line1 = phone
                        # 3 lines with markdown-style line breaks (two spaces + newline)
                        btn_label = f"{line1}  \n{phone}  \n{owner_short}"

                        st.markdown('<div class="kb-card">', unsafe_allow_html=True)
                        if st.button(btn_label, key=f"card_{aid}_{stage}",
                                     use_container_width=True):
                            st.session_state["crm_selected_account"] = aid
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                        move_options = ["Move →"] + [s for s in KANBAN_STAGES if s != stage]
                        new_stage = st.selectbox(
                            "move", move_options, key=f"kb_{aid}_{stage}",
                            label_visibility="collapsed",
                        )
                        if new_stage != "Move →":
                            _set_stage(aid, new_stage, owner)
                            st.toast(f"{company} moved to {new_stage}")
                            st.rerun()
                    if count > 15:
                        st.caption(f"+{count - 15} more")

            # ─── Account details dialog ──────────────────────────────────
            selected_aid = st.session_state.get("crm_selected_account")
            if selected_aid:
                sel_acc = acc_df[acc_df["account_id"] == selected_aid]
                if not sel_acc.empty:
                    sel = sel_acc.iloc[0]

                    @st.dialog(f"{_s(sel.get('company_name')) or _s(sel.get('primary_phone'))}", width="large")
                    def _account_detail():
                        # Stage / Score / Owner — each on its own line
                        stg = _s(sel.get("stage")) or "New leads"
                        scr = int(sel.get("score") or 0) if pd.notna(sel.get("score")) else 0
                        own = _s(sel.get("owner_rep")) or "—"
                        st.markdown(f"**Stage:** `{stg}`")
                        st.markdown(f"**Score:** {scr}")
                        st.markdown(f"**Owner rep:** {own}")

                        st.divider()

                        # ─── Editable company / contact fields ────────────────
                        st.markdown("#### Company & contact")
                        st.caption("Fill in anything that's missing. Changes are saved to the CRM.")

                        e_company = st.text_input("Company name",
                                                  value=_s(sel.get("company_name")),
                                                  key=f"fld_company_{selected_aid}")
                        e_industry = st.text_input("Industry / Segment",
                                                    value=_s(sel.get("industry")),
                                                    key=f"fld_industry_{selected_aid}")
                        e_city = st.text_input("City",
                                               value=_s(sel.get("city")),
                                               key=f"fld_city_{selected_aid}")
                        e_state = st.text_input("State",
                                                value=_s(sel.get("state")),
                                                key=f"fld_state_{selected_aid}")
                        e_website = st.text_input("Website",
                                                   value=_s(sel.get("website")),
                                                   key=f"fld_website_{selected_aid}")
                        e_phone = st.text_input("Phone",
                                                 value=_s(sel.get("primary_phone")),
                                                 key=f"fld_phone_{selected_aid}")
                        e_contact = st.text_input("Contact / Owner name",
                                                   value=_s(sel.get("contact_name")),
                                                   key=f"fld_contact_{selected_aid}")
                        e_title = st.text_input("Contact title",
                                                 value=_s(sel.get("contact_title")),
                                                 key=f"fld_title_{selected_aid}")
                        e_email = st.text_input("Owner email",
                                                 value=_s(sel.get("contact_email")),
                                                 key=f"fld_email_{selected_aid}")
                        e_email_gen = st.text_input("General email",
                                                     value=_s(sel.get("contact_email_general")),
                                                     key=f"fld_email_gen_{selected_aid}")

                        if st.button("Save company info", key="save_fields", type="primary", use_container_width=True):
                            _save_fields(selected_aid, {
                                "company_name": e_company,
                                "industry": e_industry,
                                "city": e_city,
                                "state": e_state,
                                "website": e_website,
                                "primary_phone": e_phone,
                                "contact_name": e_contact,
                                "contact_title": e_title,
                                "contact_email": e_email,
                                "contact_email_general": e_email_gen,
                            }, by_rep=own)
                            st.toast("Saved.")
                            st.rerun()

                        st.divider()

                        # ─── Next steps (quick action creation) ────────────────
                        st.markdown("#### Add next step")
                        st.caption("Click to create a new pending action. It will appear in Hot Accounts and this card.")

                        ns_cols = st.columns(4)
                        with ns_cols[0]:
                            with st.popover("Follow-up call", use_container_width=True):
                                fc_days = st.number_input("In how many days?", min_value=1, max_value=180, value=2, key=f"fc_days_{selected_aid}")
                                fc_desc = st.text_input("Details", value="Follow-up call",
                                                          key=f"fc_desc_{selected_aid}")
                                if st.button("Create follow-up", key=f"fc_btn_{selected_aid}", type="primary"):
                                    due = (datetime.now(timezone.utc) + timedelta(days=int(fc_days))).date().isoformat()
                                    _create_action(selected_aid, own, "follow_up_call", fc_desc, due)
                                    st.toast(f"Follow-up call scheduled for {due}")
                                    st.rerun()
                        with ns_cols[1]:
                            with st.popover("Send email", use_container_width=True):
                                em_desc = st.text_input("What to send?",
                                                         value="Send intro email with catalog",
                                                         key=f"em_desc_{selected_aid}")
                                em_due_days = st.number_input("Due in days", min_value=0, max_value=30, value=0, key=f"em_days_{selected_aid}")
                                if st.button("Create email task", key=f"em_btn_{selected_aid}", type="primary"):
                                    due = (datetime.now(timezone.utc) + timedelta(days=int(em_due_days))).date().isoformat() if em_due_days else None
                                    _create_action(selected_aid, own, "send_email", em_desc, due)
                                    st.toast("Email task created")
                                    st.rerun()
                        with ns_cols[2]:
                            with st.popover("Send catalog", use_container_width=True):
                                cat_desc = st.text_input("Details",
                                                          value="Mail physical catalog + samples",
                                                          key=f"cat_desc_{selected_aid}")
                                cat_due_days = st.number_input("Due in days", min_value=0, max_value=30, value=3, key=f"cat_days_{selected_aid}")
                                if st.button("Create catalog task", key=f"cat_btn_{selected_aid}", type="primary"):
                                    due = (datetime.now(timezone.utc) + timedelta(days=int(cat_due_days))).date().isoformat()
                                    _create_action(selected_aid, own, "send_sample", cat_desc, due)
                                    st.toast("Catalog task created")
                                    st.rerun()
                        with ns_cols[3]:
                            with st.popover("Custom action", use_container_width=True):
                                cu_type = st.selectbox("Type", ["follow_up_call", "send_email", "send_sample", "send_quote", "callback", "other"],
                                                         key=f"cu_type_{selected_aid}")
                                cu_desc = st.text_input("Description",
                                                          key=f"cu_desc_{selected_aid}")
                                cu_due_days = st.number_input("Due in days", min_value=0, max_value=365, value=0, key=f"cu_days_{selected_aid}")
                                if st.button("Create", key=f"cu_btn_{selected_aid}", type="primary"):
                                    if cu_desc.strip():
                                        due = (datetime.now(timezone.utc) + timedelta(days=int(cu_due_days))).date().isoformat() if cu_due_days else None
                                        _create_action(selected_aid, own, cu_type, cu_desc, due)
                                        st.toast(f"{cu_type} task created")
                                        st.rerun()

                        # ─── Existing pending actions for this account ─────────
                        actions_df_d = crm.get("actions", pd.DataFrame())
                        if not actions_df_d.empty:
                            acct_pending = actions_df_d[
                                (actions_df_d["account_id"] == selected_aid) &
                                (actions_df_d["status"] == "pending")
                            ].sort_values("created_at")
                            if not acct_pending.empty:
                                st.markdown("**Pending actions on this account:**")
                                for _, actr in acct_pending.iterrows():
                                    aid_a = int(actr["id"])
                                    atype = _s(actr.get("action_type"))
                                    desc = _s(actr.get("description"))
                                    due_a = _s(actr.get("due_date"))
                                    due_txt = f" · due {due_a}" if due_a else ""
                                    cb_col, info_col = st.columns([0.12, 0.88])
                                    with cb_col:
                                        if st.checkbox("", key=f"detail_done_{aid_a}",
                                                       label_visibility="collapsed"):
                                            _complete_action(aid_a, own)
                                            st.toast("Action marked done")
                                            st.rerun()
                                    with info_col:
                                        st.markdown(f"`{atype}` — {desc}{due_txt}")

                        st.divider()

                        # ─── Rep notes + revisit date ──────────────────────────
                        st.markdown("#### Rep notes & revisit")
                        e_notes = st.text_area(
                            "Notes (private to the team, e.g. '6 workers, contract until Oct, Mike is DM')",
                            value=_s(sel.get("manual_notes")), height=100,
                            key=f"fld_notes_{selected_aid}",
                        )
                        existing_date = sel.get("manual_followup_date")
                        try:
                            default_dt = pd.to_datetime(existing_date).date() if pd.notna(existing_date) and existing_date else None
                        except Exception:
                            default_dt = None
                        e_revisit = st.date_input(
                            "Revisit this account on",
                            value=default_dt,
                            key=f"fld_revisit_{selected_aid}",
                            format="YYYY-MM-DD",
                        )
                        if st.button("Save notes & revisit date", key="save_notes", use_container_width=True):
                            _save_manual(selected_aid, notes=e_notes,
                                           followup_date=e_revisit.isoformat() if e_revisit else "",
                                           by_rep=own)
                            st.toast("Notes saved")
                            st.rerun()

                        st.divider()

                        # ─── Call history ──────────────────────────────────────
                        st.markdown("#### Call history")
                        acct_enr = crm["enrich"][crm["enrich"]["account_id"] == selected_aid].copy()
                        if acct_enr.empty:
                            st.caption("No enriched calls yet for this account.")
                        else:
                            # Need transcripts from the calls table
                            acct_enr = acct_enr.sort_values("enriched_at", ascending=False)
                            call_ids = acct_enr["call_id"].tolist()
                            transcripts = df[df["id"].isin(call_ids)][["id", "start_time", "duration", "transcript"]]
                            transcripts = transcripts.set_index("id")

                            for _, ec in acct_enr.head(20).iterrows():
                                cid = ec["call_id"]
                                who = _s(ec.get("reached_whom")) or "?"
                                qs = int(ec.get("quality_score") or 0) if pd.notna(ec.get("quality_score")) else 0
                                rep = _s(ec.get("rep_name")) or "?"
                                summary = _s(ec.get("summary"))
                                dt_str = ""
                                dur_str = ""
                                transcript = ""
                                if cid in transcripts.index:
                                    trow = transcripts.loc[cid]
                                    if pd.notna(trow["start_time"]):
                                        dt_str = trow["start_time"].strftime("%b %d, %Y · %H:%M")
                                    if pd.notna(trow["duration"]):
                                        dur_str = f"{int(trow['duration'])}s"
                                    transcript = _s(trow.get("transcript"))

                                with st.expander(f"{dt_str} · rep **{rep}** · reached `{who}` · score **{qs}** · {dur_str}"):
                                    if summary:
                                        st.markdown(f"**Summary:** {summary}")
                                    # Coaching bullets
                                    try:
                                        bullets = json.loads(ec.get("coaching_feedback") or "[]")
                                    except Exception:
                                        bullets = []
                                    if bullets:
                                        st.markdown("**Coaching feedback:**")
                                        for b in bullets:
                                            st.markdown(f"- {b}")
                                    # Meeting info
                                    mb = ec.get("meeting_booked")
                                    ma = ec.get("meeting_asked")
                                    if pd.notna(ma) and int(ma) == 1:
                                        mphrase = _s(ec.get("meeting_ask_phrase"))
                                        mresp = _s(ec.get("meeting_prospect_response"))
                                        result = "BOOKED" if (pd.notna(mb) and int(mb) == 1) else "rejected"
                                        st.markdown(f"**Meeting ask ({result}):** *\"{mphrase}\"* → *\"{mresp}\"*")
                                    # Transcript
                                    if transcript:
                                        with st.expander("Full transcript"):
                                            st.text(transcript)

                        if st.button("Close", key="close_detail", type="primary"):
                            st.session_state["crm_selected_account"] = None
                            st.rerun()

                    _account_detail()

            st.stop()  # Skip the list rendering below when in Kanban

        # ────────────────────────────────────────────────────────────────
        # LIST VIEW (existing)
        # ────────────────────────────────────────────────────────────────
        # Account list with expander for detail
        for _, acc in view.head(50).iterrows():
            stage = _s(acc.get("stage")) or "New leads"
            color = STAGE_COLORS.get(stage, "#6b7280")
            score = int(acc.get("score") or 0) if pd.notna(acc.get("score")) else 0
            company = _s(acc.get("company_name")) or _s(acc.get("primary_phone")) or "Unknown"
            city_state = " · ".join([v for v in [_s(acc.get("city")), _s(acc.get("state"))] if v])
            industry = _s(acc.get("industry"))
            contact = _s(acc.get("contact_name"))
            owner = _s(acc.get("owner_rep")) or "—"
            total = int(acc.get("total_calls") or 0) if pd.notna(acc.get("total_calls")) else 0

            header = (f"**{company}** · `{stage}` · Score **{score}** · "
                      f"{total} calls · Owner: {owner}")
            with st.expander(header):
                # ─── Stage change + notes + revisit date ──────────────────
                action_cols = st.columns([2, 2, 3])
                with action_cols[0]:
                    new_stage = st.selectbox(
                        "Change stage",
                        KANBAN_STAGES,
                        index=KANBAN_STAGES.index(stage) if stage in KANBAN_STAGES else 0,
                        key=f"list_stage_{acc['account_id']}",
                    )
                    if new_stage != stage:
                        if st.button("Move", key=f"list_save_{acc['account_id']}", type="primary", use_container_width=True):
                            _set_stage(acc["account_id"], new_stage, owner)
                            st.toast(f"{company} moved to {new_stage}")
                            st.rerun()
                with action_cols[1]:
                    existing_date = acc.get("manual_followup_date")
                    try:
                        default_date = pd.to_datetime(existing_date).date() if pd.notna(existing_date) and existing_date else None
                    except Exception:
                        default_date = None
                    revisit = st.date_input(
                        "Revisit on",
                        value=default_date,
                        key=f"list_revisit_{acc['account_id']}",
                        format="YYYY-MM-DD",
                    )
                    if st.button("Save date", key=f"list_revisit_save_{acc['account_id']}", use_container_width=True):
                        _save_manual(acc["account_id"],
                                     followup_date=revisit.isoformat() if revisit else "",
                                     by_rep=owner)
                        st.toast(f"Revisit date saved for {company}")
                        st.rerun()
                with action_cols[2]:
                    existing_notes = _s(acc.get("manual_notes"))
                    notes_input = st.text_area(
                        "Notes (rep-written, e.g. '6 workers, contract until Oct')",
                        value=existing_notes,
                        height=80,
                        key=f"list_notes_{acc['account_id']}",
                    )
                    if st.button("Save notes", key=f"list_notes_save_{acc['account_id']}", use_container_width=True):
                        _save_manual(acc["account_id"], notes=notes_input, by_rep=owner)
                        st.toast(f"Notes saved for {company}")
                        st.rerun()

                st.divider()

                dc1, dc2, dc3 = st.columns(3)
                with dc1:
                    st.markdown(f"**Phone:** {_s(acc.get('primary_phone'))}")
                    if city_state:
                        st.markdown(f"**Location:** {city_state}")
                    if industry:
                        st.markdown(f"**Industry:** {industry}")
                with dc2:
                    if contact:
                        st.markdown(f"**Contact:** {contact}")
                    title = _s(acc.get("contact_title"))
                    if title:
                        st.markdown(f"**Title:** {title}")
                    email = _s(acc.get("contact_email")) or _s(acc.get("contact_email_general"))
                    if email:
                        st.markdown(f"**Email:** {email}")
                with dc3:
                    calls_dm = int(acc.get("calls_dm") or 0) if pd.notna(acc.get("calls_dm")) else 0
                    calls_gk = int(acc.get("calls_gatekeeper") or 0) if pd.notna(acc.get("calls_gatekeeper")) else 0
                    calls_vm = int(acc.get("calls_voicemail") or 0) if pd.notna(acc.get("calls_voicemail")) else 0
                    st.markdown(f"**Calls:** DM={calls_dm} · GK={calls_gk} · VM={calls_vm}")
                    last_touch = acc.get("last_touch_at")
                    if pd.notna(last_touch):
                        st.markdown(f"**Last touch:** {last_touch.strftime('%Y-%m-%d') if hasattr(last_touch, 'strftime') else last_touch}")

                # ── Pending actions for this account (checkbox to mark done)
                actions_df = crm.get("actions", pd.DataFrame())
                acct_actions = actions_df[actions_df["account_id"] == acc["account_id"]] if not actions_df.empty else pd.DataFrame()
                pending_acts = acct_actions[acct_actions["status"] == "pending"] if not acct_actions.empty else pd.DataFrame()
                done_acts = acct_actions[acct_actions["status"] == "done"] if not acct_actions.empty else pd.DataFrame()

                if not pending_acts.empty:
                    st.markdown("**Pending actions**")
                    st.caption("Check the box when the action is actually completed. Email actions auto-schedule a follow-up call in 48h.")
                    for _, act in pending_acts.sort_values("created_at").iterrows():
                        aid = int(act["id"])
                        atype = _s(act.get("action_type"))
                        desc = _s(act.get("description"))
                        created = pd.to_datetime(act["created_at"], utc=True, errors="coerce")
                        created_s = created.strftime("%b %d") if pd.notna(created) else "—"
                        due = pd.to_datetime(act.get("due_date"), utc=True, errors="coerce")
                        due_s = due.strftime("%b %d") if pd.notna(due) else ""
                        overdue = pd.notna(due) and due < pd.Timestamp.now(tz="UTC")
                        source_tag = " · auto follow-up" if _s(act.get("source")) == "auto_follow_up" else ""
                        badge = "  **OVERDUE**" if overdue else ""

                        ck_col, info_col = st.columns([0.06, 0.94])
                        with ck_col:
                            checked = st.checkbox("Done", key=f"crmact_{aid}", value=False, label_visibility="collapsed")
                            if checked:
                                _complete_action(aid, _s(act.get("rep_name")))
                                st.rerun()
                        with info_col:
                            st.markdown(
                                f"`{atype}` — {desc}  \n"
                                f"<span style='font-size:11px; color:#94a3b8;'>created {created_s}"
                                f"{' · due ' + due_s if due_s else ''}{badge}{source_tag}</span>",
                                unsafe_allow_html=True,
                            )
                elif _s(acc.get("next_action")):
                    # Fallback for accounts with next_action but no action row (edge case)
                    st.markdown(f"**Next step (not tracked yet):** {_s(acc.get('next_action'))}")

                if not done_acts.empty:
                    with st.expander(f"Completed actions ({len(done_acts)})", expanded=False):
                        for _, act in done_acts.sort_values("completed_at", ascending=False).iterrows():
                            atype = _s(act.get("action_type"))
                            desc = _s(act.get("description"))
                            completed = pd.to_datetime(act.get("completed_at"), utc=True, errors="coerce")
                            completed_s = completed.strftime("%b %d") if pd.notna(completed) else "—"
                            st.caption(f"`{atype}` — {desc} · done {completed_s}")

                notes = _s(acc.get("notes_aggregated"))
                if notes:
                    st.markdown(f"**Recent summaries:** {notes}")

                # Call history for this account
                acct_calls = crm["enrich"][crm["enrich"]["account_id"] == acc["account_id"]].copy()
                if not acct_calls.empty:
                    st.markdown("**Call timeline:**")
                    acct_calls = acct_calls.sort_values("enriched_at", ascending=False)
                    for _, ec in acct_calls.head(10).iterrows():
                        who = _s(ec.get("reached_whom")) or "?"
                        qs = int(ec.get("quality_score") or 0) if pd.notna(ec.get("quality_score")) else 0
                        rep = _s(ec.get("rep_name")) or "?"
                        summary = _s(ec.get("summary"))
                        st.markdown(f"- `{who}` · rep={rep} · score={qs} · {summary}")


# ═══════════════════════════════════════════════════════════════════════════
# TAB · HOT ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════

if page == "Hot Accounts":
    st.markdown("### Today's to-do list")
    st.markdown(
        "This is where each rep sees **what to do today**. Every time a call ends, the AI reads the transcript and "
        "creates a task here (e.g. *'send catalog to joe@acme.com'*). When the rep actually sends the email or "
        "makes the call, they **click the green button** to mark it done.\n\n"
        "**What happens when a rep clicks 'Mark email as sent':**\n"
        "- The task disappears from this page.\n"
        "- A **follow-up call task is automatically created 48h later** to check if the prospect got the email.\n"
        "- In the CRM tab, the account card shows **1 less 'todo'** next to that account.\n"
        "- If the call history already moved the stage to *Email to Send*, it stays there until the rep manually moves it (e.g. to *Meeting* after booking one)."
    )

    acc_df = crm["accounts"]
    actions_df = crm.get("actions", pd.DataFrame())

    if acc_df.empty:
        st.info("Run `python jtcalls.py enrich && python jtcalls.py aggregate` first.")
    else:
        if actions_df.empty:
            st.info("No actions yet. Actions are auto-created from call next-steps.")
        else:
            pending = actions_df[actions_df["status"] == "pending"].copy()
            if pending.empty:
                st.success("All caught up — no pending actions.")
            else:
                # Join account info
                pending = pending.merge(
                    acc_df[["account_id", "company_name", "stage", "score",
                            "contact_name", "contact_email", "contact_email_general",
                            "city", "state", "primary_phone"]],
                    on="account_id", how="left"
                )
                now_utc = pd.Timestamp.now(tz="UTC")
                pending["created_at"] = pd.to_datetime(pending["created_at"], utc=True, errors="coerce")
                pending["due_date_dt"] = pd.to_datetime(pending["due_date"], utc=True, errors="coerce")
                pending["days_open"] = (now_utc - pending["created_at"]).dt.days
                pending["is_overdue"] = pending["due_date_dt"].notna() & (pending["due_date_dt"] < now_utc)

                # Rep filter + summary KPIs
                rep_list = ["All reps"] + sorted([r for r in pending["rep_name"].dropna().unique() if r])
                rep_filter = st.selectbox("Rep", rep_list, key="pa_rep", label_visibility="collapsed")
                p = pending if rep_filter == "All reps" else pending[pending["rep_name"] == rep_filter]

                emails = p[p["action_type"] == "send_email"]
                calls = p[p["action_type"] == "follow_up_call"]
                others = p[~p["action_type"].isin(["send_email", "follow_up_call"])]

                kc1, kc2, kc3, kc4 = st.columns(4)
                kc1.metric("Total pending", f"{len(p):,}")
                kc2.metric("Emails to send", f"{len(emails):,}")
                kc3.metric("Follow-up calls", f"{len(calls):,}")
                kc4.metric("Overdue", f"{int(p['is_overdue'].sum()):,}")

                st.divider()

                def _safe(v, fallback=""):
                    """Return fallback when value is NaN/None/empty."""
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        return fallback
                    s = str(v).strip()
                    return s if s and s.lower() != "nan" else fallback

                def _render_action(act, prefix):
                    """Render one action row with a 'Mark done' button."""
                    aid = int(act["id"])
                    company = _safe(act.get("company_name")) or _safe(act.get("primary_phone")) or "Unknown"
                    rep = _safe(act.get("rep_name"), "—")
                    atype = _safe(act.get("action_type"), "other")
                    desc = _safe(act.get("description"))
                    created = act["created_at"].strftime("%b %d") if pd.notna(act["created_at"]) else "—"
                    due = act["due_date_dt"].strftime("%b %d") if pd.notna(act["due_date_dt"]) else None
                    overdue = bool(act["is_overdue"]) if pd.notna(act["is_overdue"]) else False
                    source_tag = " · auto follow-up from email sent" if _safe(act.get("source")) == "auto_follow_up" else ""

                    with st.container(border=True):
                        top = st.columns([3, 1.5])
                        with top[0]:
                            st.markdown(f"**{company}**  ·  rep: **{rep}**")
                            if desc:
                                st.markdown(desc)
                            # Email address prominently if send_email
                            if atype == "send_email":
                                email = _safe(act.get("contact_email")) or _safe(act.get("contact_email_general"))
                                contact = _safe(act.get("contact_name"))
                                if email:
                                    st.code(email, language=None)
                                    if contact:
                                        st.caption(f"Contact: {contact}")
                                else:
                                    st.caption(":red[No email on file — rep needs to find one.]")
                            meta = f"created {created}"
                            if due:
                                meta += f" · due {due}"
                            if overdue:
                                meta += "  :red[OVERDUE]"
                            meta += source_tag
                            st.caption(meta)
                        with top[1]:
                            btn_label = "Mark email as sent" if atype == "send_email" else "Mark as done"
                            if st.button(btn_label, key=f"{prefix}_{aid}", use_container_width=True, type="primary"):
                                was_email = _complete_action(aid, rep)
                                if was_email:
                                    st.toast(f"Email marked sent. Follow-up call scheduled in 48h for {company}.", icon="✉")
                                else:
                                    st.toast(f"Action completed for {company}.", icon="✓")
                                st.rerun()

                # Emails to send — dedicated section, most actionable
                if not emails.empty:
                    st.markdown("#### Emails to send today")
                    st.caption("Rep sent the email? Click the button. A follow-up call is auto-scheduled 48h later to verify receipt.")
                    emails_sorted = emails.sort_values(["is_overdue", "created_at"], ascending=[False, True])
                    for _, act in emails_sorted.head(20).iterrows():
                        _render_action(act, "email")
                    if len(emails) > 20:
                        st.caption(f"Showing 20 of {len(emails)} — use rep filter to narrow.")
                    st.divider()

                # Follow-up calls
                if not calls.empty:
                    st.markdown("#### Follow-up calls")
                    calls_sorted = calls.sort_values(["is_overdue", "due_date_dt"], ascending=[False, True])
                    for _, act in calls_sorted.head(15).iterrows():
                        _render_action(act, "call")
                    st.divider()

                # Other actions
                if not others.empty:
                    st.markdown("#### Other pending actions")
                    others_sorted = others.sort_values("created_at")
                    for _, act in others_sorted.head(15).iterrows():
                        _render_action(act, "other")
                    st.divider()

        # ─── NURTURE LEADS READY TO REVISIT ─────────────────────────────────
        if "manual_followup_date" in acc_df.columns:
            nurture_df = acc_df.copy()
            nurture_df["revisit_dt"] = pd.to_datetime(nurture_df["manual_followup_date"], errors="coerce")
            cutoff_dt = pd.Timestamp.now().normalize() + pd.Timedelta(days=7)
            due_nurture = nurture_df[
                nurture_df["revisit_dt"].notna() &
                (nurture_df["revisit_dt"] <= cutoff_dt)
            ].sort_values("revisit_dt")
            if not due_nurture.empty:
                due_nurture["revisit_dt"] = due_nurture["revisit_dt"].dt.strftime("%Y-%m-%d")

            if not due_nurture.empty:
                st.markdown("#### Nurture leads ready to revisit")
                st.caption("Accounts the team parked for later with a revisit date — these are due within 7 days.")
                show = due_nurture[[
                    "company_name", "stage", "owner_rep", "revisit_dt",
                    "manual_notes", "contact_name", "contact_email", "primary_phone"
                ]].rename(columns={
                    "company_name": "Company", "stage": "Stage", "owner_rep": "Owner rep",
                    "revisit_dt": "Revisit date", "manual_notes": "Notes",
                    "contact_name": "Contact", "contact_email": "Email", "primary_phone": "Phone",
                })
                st.dataframe(show.head(50), hide_index=True, use_container_width=True)
                st.divider()

        # ─── UNTOUCHED MASTER LEADS ─────────────────────────────────────────
        if "lead_source" in acc_df.columns:
            untouched = acc_df[
                (acc_df["lead_source"] == "master_spreadsheet") &
                (acc_df["total_calls"].fillna(0) == 0)
            ]
            if not untouched.empty:
                st.warning(f"**{len(untouched)} leads from master sheet never dialed.** Top priority if the team is running out of new prospects.")
                with st.expander(f"View the {len(untouched)} untouched leads"):
                    avail = [c for c in ["company_name","city","state","industry","contact_name","contact_title","contact_email","primary_phone"] if c in untouched.columns]
                    st.dataframe(untouched[avail].head(100), hide_index=True, use_container_width=True)

        st.divider()

        # ─── RISK OF COLD ───────────────────────────────────────────────────
        hot = acc_df.copy()
        hot = hot[~hot["stage"].isin(["New leads", "Lost"])]
        if "last_touch_at" in hot.columns:
            now_utc = pd.Timestamp.now(tz="UTC")
            hot["days_silent"] = (now_utc - pd.to_datetime(hot["last_touch_at"], utc=True)).dt.days

        st.markdown("#### Risk of going cold (30+ days silent, still open)")
        risk = hot[(hot["days_silent"].fillna(0) >= 30) &
                   (~hot["stage"].isin(["Won", "Lost"]))]
        if risk.empty:
            st.success("No accounts at risk.")
        else:
            avail = [c for c in ["company_name","stage","owner_rep","days_silent","next_action"] if c in risk.columns]
            st.dataframe(risk[avail].head(20), hide_index=True, use_container_width=True)

        st.divider()

        # ─── GATEKEEPER → DM OPPORTUNITIES ──────────────────────────────────
        st.markdown("#### Gatekeeper → DM opportunities")
        gk = hot[(hot["stage"] == "Gatekeeper") & (hot["score"].fillna(0) >= 40)]
        st.caption(f"{len(gk)} accounts where gatekeeper was reached and rep had decent call quality — ripe for another attempt with a better angle.")
        if not gk.empty:
            avail = [c for c in ["company_name","score","owner_rep","calls_gatekeeper","next_action","contact_name"] if c in gk.columns]
            st.dataframe(gk[avail].head(20), hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB · MEETING CONVERSION (the key metric)
# ═══════════════════════════════════════════════════════════════════════════

if page == "Meeting Conversion":
    st.markdown("### Meeting Conversion")
    st.caption("This is the metric that matters. The team talks to people but doesn't book. Here's why.")

    enr = crm["enrich"]
    if enr.empty or "meeting_asked" not in enr.columns:
        st.info("Run enrich to generate meeting signals: `python jtcalls.py enrich --reenrich`.")
    else:
        # Base: only calls that reached a live human prospect (DM or gatekeeper)
        live = enr[enr["reached_whom"].isin(["decision_maker", "gatekeeper"])].copy()
        dm_calls = enr[enr["reached_whom"] == "decision_maker"].copy()

        # KPIs
        kc1, kc2, kc3, kc4, kc5 = st.columns(5)
        kc1.metric("Calls with a live human", f"{len(live):,}",
                   help="DM or gatekeeper reached")
        kc2.metric("Calls with DM", f"{len(dm_calls):,}")

        asked_dm = int(dm_calls["meeting_asked"].fillna(0).sum())
        booked_dm = int(dm_calls["meeting_booked"].fillna(0).sum())
        ask_rate = (asked_dm / max(len(dm_calls), 1)) * 100
        book_rate = (booked_dm / max(asked_dm, 1)) * 100
        funnel_rate = (booked_dm / max(len(dm_calls), 1)) * 100

        kc3.metric("% asked for meeting", f"{ask_rate:.0f}%",
                   help="Of DM calls, how many times the rep actually asked")
        kc4.metric("% accepted when asked", f"{book_rate:.0f}%",
                   help="Of asks, how many became yes with day/time")
        kc5.metric("Overall DM→meeting", f"{funnel_rate:.1f}%",
                   help="Percentage of DM calls that became a booked meeting")

        st.divider()

        # Funnel by rep — include ALL reps (0 DMs still shows the effort)
        st.markdown("#### Conversion funnel · by rep")
        st.caption("Every rep with enriched calls appears, even if they haven't reached a DM yet. A rep with 0 DMs tells us the gatekeeper/IVR is the bottleneck for them.")
        if not enr.empty:
            rep_funnel = enr.groupby("rep_name").agg(
                total_calls=("call_id", "count"),
                dm_reached=("reached_whom", lambda s: (s == "decision_maker").sum()),
                gk_reached=("reached_whom", lambda s: (s == "gatekeeper").sum()),
                asked=("meeting_asked", lambda s: s.fillna(0).sum()),
                booked=("meeting_booked", lambda s: s.fillna(0).sum()),
            ).reset_index()
            rep_funnel["DM %"] = (rep_funnel["dm_reached"] / rep_funnel["total_calls"].clip(lower=1) * 100).round(1)
            rep_funnel["Ask → Book %"] = rep_funnel.apply(
                lambda r: round(r["booked"] / r["asked"] * 100, 1) if r["asked"] > 0 else 0, axis=1
            )
            rep_funnel["DM → Book %"] = rep_funnel.apply(
                lambda r: round(r["booked"] / r["dm_reached"] * 100, 1) if r["dm_reached"] > 0 else 0, axis=1
            )
            rep_funnel = rep_funnel[[
                "rep_name", "total_calls", "dm_reached", "gk_reached",
                "asked", "booked", "DM %", "Ask → Book %", "DM → Book %"
            ]]
            rep_funnel.columns = [
                "Rep", "Calls", "DM reached", "GK reached",
                "Asked for meeting", "Booked meeting",
                "DM %", "Ask → Book %", "DM → Book %"
            ]
            # Sort by DM reached first (productivity), then by booked
            rep_funnel = rep_funnel.sort_values(["DM reached", "Booked meeting"], ascending=[False, False])
            st.dataframe(rep_funnel, hide_index=True, use_container_width=True,
                column_config={
                    "Calls":              st.column_config.NumberColumn("Calls", help="Total enriched calls"),
                    "DM reached":         st.column_config.NumberColumn("DM reached", help="Calls where rep reached the decision-maker"),
                    "GK reached":         st.column_config.NumberColumn("GK reached", help="Calls blocked at gatekeeper"),
                    "Asked for meeting":  st.column_config.NumberColumn("Asked", help="DM calls where rep explicitly asked for a meeting"),
                    "Booked meeting":     st.column_config.NumberColumn("Booked", help="DM calls where a meeting was actually scheduled"),
                    "DM %":               st.column_config.ProgressColumn("DM %", help="DM reached ÷ total calls — the productivity number", format="%.1f%%", min_value=0, max_value=30),
                    "Ask → Book %":       st.column_config.ProgressColumn("Ask → Book %", help="Of asks, what % turned into a booking", format="%.1f%%", min_value=0, max_value=50),
                    "DM → Book %":        st.column_config.ProgressColumn("DM → Book %", help="Of DM calls, what % turned into a booking — the real close rate", format="%.1f%%", min_value=0, max_value=30),
                }
            )

        st.divider()

        # Why didn't convert
        st.markdown("#### Why the meeting DIDN'T happen")
        st.caption("Breakdown of DM calls that didn't become meetings.")

        WHY_LABELS = {
            "not_asked": "Rep didn't ask",
            "blocked_by_gk": "Blocked by gatekeeper",
            "not_decision_maker": "Not the decision-maker",
            "rejected_no_interest": "Prospect said: not interested",
            "rejected_bad_timing": "Prospect said: bad timing",
            "rejected_current_vendor": "Prospect said: already have a vendor",
            "deferred_send_info": "Prospect said: send me info",
            "booked": "Booked!",
        }
        no_book = dm_calls[dm_calls["meeting_booked"].fillna(0) == 0]
        why_counts = no_book["meeting_why_not"].value_counts()
        if not why_counts.empty:
            why_df = pd.DataFrame({
                "Reason": [WHY_LABELS.get(c, c) for c in why_counts.index],
                "Calls": why_counts.values,
                "% of DM no-book": (why_counts.values / max(len(no_book), 1) * 100).round(1),
            })
            st.dataframe(why_df, hide_index=True, use_container_width=True)

        st.divider()

        # Winning ask phrases
        st.markdown("#### Phrases that WON the meeting")
        st.caption("Calls where the rep asked and the prospect accepted. Use these in the script.")
        won = dm_calls[(dm_calls["meeting_booked"].fillna(0) == 1)]
        if won.empty:
            st.warning("No meetings booked in enriched DM calls yet. This is the gap — total focus on flipping this number.")
        else:
            for _, r in won.head(10).iterrows():
                st.markdown(f"**{r['rep_name']}** asked: *\"{r.get('meeting_ask_phrase', '')}\"*")
                resp = r.get("meeting_prospect_response", "")
                if resp:
                    st.markdown(f"→ Prospect: *\"{resp}\"*")
                if r.get("summary"):
                    st.caption(r["summary"])
                st.divider()

        # Losing ask phrases
        st.markdown("#### Phrases that LOST (rep asked and was rejected)")
        st.caption("Avoid these formulations.")
        asked_but_not_booked = dm_calls[
            (dm_calls["meeting_asked"].fillna(0) == 1) &
            (dm_calls["meeting_booked"].fillna(0) == 0)
        ]
        if asked_but_not_booked.empty:
            st.caption("No rejected asks on file.")
        else:
            for _, r in asked_but_not_booked.head(10).iterrows():
                st.markdown(f"**{r['rep_name']}** asked: *\"{r.get('meeting_ask_phrase', '')}\"*")
                resp = r.get("meeting_prospect_response", "")
                if resp:
                    st.markdown(f"→ Prospect: *\"{resp}\"*")
                why = WHY_LABELS.get(r.get("meeting_why_not", ""), "")
                if why:
                    st.caption(f"Reason: {why}")
                st.divider()

        st.divider()

        # Calls where rep REACHED DM but DIDN'T ask — biggest missed opportunity
        st.markdown("#### DM reached but rep did NOT ask for meeting")
        st.caption("Biggest funnel waste. The conversation happened but the close wasn't attempted.")
        missed = dm_calls[(dm_calls["meeting_asked"].fillna(0) == 0)]
        if missed.empty:
            st.success("Every rep who reached a DM asked for the meeting. Nice.")
        else:
            st.markdown(f"**{len(missed)} of {len(dm_calls)} DM calls** ({len(missed)/max(len(dm_calls),1)*100:.0f}%) ended without a meeting ask.")
            missed_by_rep = missed.groupby("rep_name").size().reset_index(name="calls_without_ask")
            missed_by_rep = missed_by_rep.sort_values("calls_without_ask", ascending=False)
            st.dataframe(missed_by_rep, hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB · COACHING (merged: Objections + per-rep coaching)
# ═══════════════════════════════════════════════════════════════════════════

if page == "Coaching":
    st.markdown("### Coaching")
    st.caption("Two lenses on the same data. **Objections** = patterns across the whole team. **Rep drill-down** = specific feedback per call.")

    sub_tab_obj, sub_tab_rep = st.tabs(["Objections", "Rep drill-down"])

    # ── OBJECTIONS SECTION ────────────────────────────────────────────────
    with sub_tab_obj:
        obj_df = crm["objections"]
        if obj_df.empty:
            st.info("No objections enriched yet. Run `python jtcalls.py enrich`.")
        else:
            total_obj = len(obj_df)
            cat_counts = obj_df["category"].value_counts()

            kc1, kc2, kc3 = st.columns(3)
            kc1.metric("Total objections logged", f"{total_obj:,}")
            handled_rate = (obj_df["outcome"].isin(["continued", "converted"])).mean() * 100
            kc2.metric("Handling rate", f"{handled_rate:.0f}%",
                       help="% of objections where the call continued after")
            top_cat = cat_counts.index[0] if len(cat_counts) else "-"
            top_pct = (cat_counts.iloc[0] / total_obj * 100) if len(cat_counts) else 0
            kc3.metric("Top objection", OBJECTION_LABELS.get(top_cat, top_cat),
                       f"{top_pct:.0f}% of objections")

            st.divider()

            st.markdown("#### Top objections (volume)")
            cat_df = pd.DataFrame({
                "Objection": [OBJECTION_LABELS.get(c, c) for c in cat_counts.index],
                "Count": cat_counts.values,
            })
            st.bar_chart(cat_df.set_index("Objection")["Count"], height=280)

            st.markdown("#### Handling rate by category")
            handling = obj_df.groupby("category").apply(
                lambda g: pd.Series({
                    "Total": len(g),
                    "Continued": (g["outcome"] == "continued").sum(),
                    "Killed": (g["outcome"] == "killed_call").sum(),
                    "Handling %": round((g["outcome"].isin(["continued", "converted"])).mean() * 100, 1),
                }), include_groups=False
            ).reset_index()
            handling["Objection"] = handling["category"].map(OBJECTION_LABELS)
            handling = handling[["Objection", "Total", "Continued", "Killed", "Handling %"]].sort_values("Total", ascending=False)
            st.dataframe(handling, hide_index=True, use_container_width=True,
                column_config={"Handling %": st.column_config.ProgressColumn(
                    "Handling %", format="%.1f%%", min_value=0, max_value=100
                )}
            )

            st.divider()

            st.markdown("#### Objections by rep")
            rep_obj = obj_df.groupby(["rep_name", "category"]).size().unstack(fill_value=0)
            if not rep_obj.empty:
                rep_obj.columns = [OBJECTION_LABELS.get(c, c) for c in rep_obj.columns]
                st.dataframe(rep_obj, use_container_width=True)

            st.divider()

            st.markdown("#### Winning vs losing responses")
            selected_cat = st.selectbox(
                "Pick an objection to see the responses",
                list(OBJECTION_LABELS.keys()),
                format_func=lambda c: OBJECTION_LABELS.get(c, c),
                key="obj_drilldown",
            )
            cat_obj = obj_df[obj_df["category"] == selected_cat]
            wc1, wc2 = st.columns(2)
            with wc1:
                st.markdown("##### Responses that kept the call alive")
                winners = cat_obj[cat_obj["outcome"].isin(["continued", "converted"])]
                if winners.empty:
                    st.caption("None — this objection is killing every call that hits it.")
                else:
                    for _, w in winners.head(5).iterrows():
                        st.markdown(f"**Prospect:** *\"{w['verbatim']}\"*")
                        if w.get("rep_response"):
                            st.markdown(f"**{w['rep_name']}:** *\"{w['rep_response']}\"*")
                        st.caption(f"→ {w['outcome']}")
                        st.divider()
            with wc2:
                st.markdown("##### Responses that killed the call")
                losers = cat_obj[cat_obj["outcome"] == "killed_call"]
                if losers.empty:
                    st.caption("None logged.")
                else:
                    for _, l in losers.head(5).iterrows():
                        st.markdown(f"**Prospect:** *\"{l['verbatim']}\"*")
                        if l.get("rep_response"):
                            st.markdown(f"**{l['rep_name']}:** *\"{l['rep_response']}\"*")
                        st.caption(f"→ {l['outcome']}")
                        st.divider()

    # ── REP DRILL-DOWN SECTION ────────────────────────────────────────────
    with sub_tab_rep:
        st.caption("Every call is analyzed by the LLM and gets 3 specific feedback bullets. Open this once a week.")
        enr = crm["enrich"]
        if enr.empty:
            st.info("No enriched calls yet.")
        else:
            reps = sorted([r for r in enr["rep_name"].dropna().unique() if r])
            if not reps:
                st.info("No reps identified in the enrichment.")
            else:
                st.markdown("#### Average score by rep (last 30 days)")
                st.caption("Score = average of quality_score per call. Quality_score (0-100) is set by the LLM per call, judging: opener quality, qualification effort, pitch clarity, objection handling, meeting ask. 0-30 weak · 30-60 average · 60+ strong.")
                recent = enr.copy()
                recent["enriched_at"] = pd.to_datetime(recent["enriched_at"], utc=True, errors="coerce")
                cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
                recent_30 = recent[recent["enriched_at"] >= cutoff]
                if not recent_30.empty:
                    rep_stats = recent_30.groupby("rep_name").agg(
                        calls=("call_id", "count"),
                        avg_score=("quality_score", "mean"),
                        dm_rate=("reached_whom", lambda s: (s == "decision_maker").mean() * 100),
                        productive_rate=("was_productive", lambda s: s.mean() * 100),
                    ).round(1).reset_index()
                    rep_stats.columns = ["Rep", "Calls", "Avg score", "% DM reached", "% productive"]
                    st.dataframe(rep_stats, hide_index=True, use_container_width=True)

                st.divider()

                sel_rep = st.selectbox("Rep to drill into", reps, key="coach_rep_sel")

                rep_enr = enr[enr["rep_name"] == sel_rep].copy()
                rep_enr["enriched_at_dt"] = pd.to_datetime(rep_enr["enriched_at"], utc=True, errors="coerce")
                rep_enr = rep_enr.sort_values("enriched_at_dt", ascending=False)

                st.markdown(f"#### Last 10 calls · {sel_rep}")
                for _, r in rep_enr.head(10).iterrows():
                    call_row = df[df["id"] == r["call_id"]]
                    if not call_row.empty:
                        c = call_row.iloc[0]
                        callee = c.get("to_name") or c.get("to_number") or ""
                        dt_str = c["start_time"].strftime("%b %d %H:%M") if pd.notna(c["start_time"]) else ""
                    else:
                        callee, dt_str = "", ""

                    qs = int(r.get("quality_score") or 0)
                    who = r.get("reached_whom") or "?"
                    stage_hint = r.get("stage_hint") or "?"

                    with st.expander(f"{dt_str} · {callee} · `{who}` · score **{qs}** · → {stage_hint}"):
                        summary = r.get("summary") or ""
                        if summary:
                            st.markdown(f"**Summary:** {summary}")

                        try:
                            bullets = json.loads(r.get("coaching_feedback") or "[]")
                        except Exception:
                            bullets = []
                        if bullets:
                            st.markdown("**Feedback:**")
                            for b in bullets:
                                st.markdown(f"- {b}")

                        try:
                            km = json.loads(r.get("key_moments") or "{}")
                        except Exception:
                            km = {}
                        if km.get("best_line"):
                            st.markdown(f"**Best moment:** *\"{km['best_line']}\"*")
                        if km.get("worst_line"):
                            st.markdown(f"**Worst moment:** *\"{km['worst_line']}\"*")
                        if km.get("pivot_moment"):
                            st.markdown(f"**Turning point:** *\"{km['pivot_moment']}\"*")

                        ns = r.get("next_step_extracted") or ""
                        if ns:
                            st.markdown(f"**Next step:** {ns}")


# ═══════════════════════════════════════════════════════════════════════════
# TAB · SCORECARD
# ═══════════════════════════════════════════════════════════════════════════

if page == "Scorecard" and is_manager:
    st.markdown("### Team Scorecard")
    st.caption("Funnel progression metrics. How the team is converting from one stage to the next.")

    enr = crm["enrich"]
    acc_df = crm["accounts"]

    if enr.empty or acc_df.empty:
        st.info("Run the full pipeline first.")
    else:
        # Funnel: overall
        st.markdown("#### Pipeline funnel (account count)")
        funnel_counts = {s: int((acc_df["stage"] == s).sum()) for s in STAGE_ORDER}
        funnel_df = pd.DataFrame([
            {"Stage": s, "Accounts": funnel_counts[s]} for s in STAGE_ORDER
            if s not in ("Lost",)  # show Lost separately
        ])
        st.bar_chart(funnel_df.set_index("Stage")["Accounts"], height=300)

        st.divider()

        # Rep progression rates
        st.markdown("#### Progression rate by rep (last 30 days)")
        enr_copy = enr.copy()
        enr_copy["enriched_at_dt"] = pd.to_datetime(enr_copy["enriched_at"], utc=True, errors="coerce")
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
        recent = enr_copy[enr_copy["enriched_at_dt"] >= cutoff]

        if not recent.empty:
            rep_funnel = recent.groupby("rep_name").agg(
                calls=("call_id", "count"),
                gatekeeper=("reached_whom", lambda s: (s == "gatekeeper").sum()),
                dm=("reached_whom", lambda s: (s == "decision_maker").sum()),
                productive=("was_productive", "sum"),
                avg_score=("quality_score", "mean"),
            ).reset_index()
            rep_funnel["% → DM"] = (rep_funnel["dm"] / rep_funnel["calls"].clip(lower=1) * 100).round(1)
            rep_funnel["% → GK"] = (rep_funnel["gatekeeper"] / rep_funnel["calls"].clip(lower=1) * 100).round(1)
            rep_funnel["avg_score"] = rep_funnel["avg_score"].round(1)
            rep_funnel = rep_funnel.sort_values("% → DM", ascending=False)
            rep_funnel.columns = ["Rep", "Calls", "Gatekeeper", "DM Reached", "Productive", "Avg Score", "% → DM", "% → GK"]
            st.dataframe(rep_funnel, hide_index=True, use_container_width=True)

        st.divider()

        # BANT presence rate
        st.markdown("#### BANT signals extracted from conversations")
        st.caption("Of DM-reached calls, what % surfaced Budget, Authority, Need, Timeline signals.")

        dm_only = enr[enr["reached_whom"] == "decision_maker"].copy()
        if not dm_only.empty:
            bant_counts = {"Budget": 0, "Authority": 0, "Need": 0, "Timeline": 0}
            for _, r in dm_only.iterrows():
                try:
                    ds = json.loads(r.get("deal_signals") or "{}")
                    for k, label in [("budget", "Budget"), ("authority", "Authority"),
                                     ("need", "Need"), ("timeline", "Timeline")]:
                        if (ds.get(k) or {}).get("present"):
                            bant_counts[label] += 1
                except Exception:
                    pass
            n = len(dm_only)
            bant_df = pd.DataFrame([
                {"Signal": k, "% of DM calls": round(v / n * 100, 1), "Count": v}
                for k, v in bant_counts.items()
            ])
            st.dataframe(bant_df, hide_index=True, use_container_width=True)
            st.caption(f"Base: {n} calls that reached the DM.")

        st.divider()

        # Industry / city cross (only if master spreadsheet was imported)
        if "industry" in acc_df.columns and acc_df["industry"].notna().any():
            st.markdown("#### Performance by industry")
            ind_stats = acc_df.groupby("industry").agg(
                accounts=("account_id", "count"),
                dm_reached=("calls_dm", "sum"),
                gk=("calls_gatekeeper", "sum"),
                avg_score=("score", "mean"),
            ).reset_index()
            ind_stats["avg_score"] = ind_stats["avg_score"].round(1)
            ind_stats = ind_stats.sort_values("dm_reached", ascending=False)
            st.dataframe(ind_stats, hide_index=True, use_container_width=True)
        else:
            st.info("Import the leads sheet to get industry breakdown: `python jtcalls.py import-leads --file /path/to/sheet.xlsx`")


# ─── Footer ──────────────────────────────────────────────────────────────

st.divider()
st.caption("J T-Shirts Call Analytics · Dashboard auto-refreshes every 5 min · Update data: `python jtcalls.py daily`")
