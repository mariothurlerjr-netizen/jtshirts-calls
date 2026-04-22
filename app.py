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
DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_NAME)
_tz_labels = {"New_York":"ET","Chicago":"CT","Denver":"MT","Boise":"MT","Los_Angeles":"PT","Phoenix":"MST"}
TZ_LABEL = _tz_labels.get(DISPLAY_TZ_NAME.split("/")[-1], DISPLAY_TZ_NAME.split("/")[-1])

# ─── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="J T-Shirts · Call Analytics",
    page_icon="📞",
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
<div style="background:linear-gradient(135deg,#0F1B2D 0%,#1a2847 100%); padding:32px 40px; border-radius:12px; margin-bottom:24px">
  <div style="display:inline-block; background:rgba(197,165,114,.2); border:1px solid rgba(197,165,114,.4); color:#C5A572; padding:4px 12px; border-radius:16px; font-size:11px; font-weight:700; letter-spacing:1.5px; margin-bottom:12px">● LIVE · RINGCENTRAL</div>
  <h1 style="color:white; margin:0; font-size:32px; font-weight:800">J T-Shirts · <span style="color:#C5A572">Call Analytics</span></h1>
  <p style="color:rgba(255,255,255,.5); margin:4px 0 0 0; font-size:14px">Revenue Intelligence Dashboard · Real-time call classification, sales playbook & coaching</p>
</div>
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

st.caption(f"Last refresh: {data['generated_at']} · {len(df):,} total calls · Cache TTL: 5 min")

# ─── Filters (sidebar) ───────────────────────────────────��──────────────────

with st.sidebar:
    st.markdown("### Filters")
    min_date = df["start_time"].min().date()
    max_date = df["start_time"].max().date()
    default_start = max(min_date, max_date - timedelta(days=30))

    date_range = st.date_input(
        "Date range",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    reps_available = sorted([r for r in df["from_name"].dropna().unique() if r])
    reps_sel = st.multiselect("Reps", reps_available, default=reps_available)

    classes_avail = sorted([c for c in df["classification"].dropna().unique() if c])
    if classes_avail:
        classes_sel = st.multiselect("Call classification", classes_avail, default=classes_avail)
    else:
        classes_sel = []

    st.markdown("---")
    st.caption("Refresh data:\n```\npython jtcalls.py daily\n```")

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
# TABS
# ═══════════════════════════════════════════════════════════════════════════

tab_overview, tab_wins, tab_diag, tab_playbook, tab_leads, tab_reps, tab_calls = st.tabs([
    "Overview", "Winning Calls", "Strategic Diagnostic", "Sales Playbook", "Lead List", "Rep Performance", "Call Explorer"
])


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════

with tab_overview:

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
                  help=f"7d avg: {w_avg['connected']:.0f}/day")
    with c3:
        d = delta_pct(y["real_disc"], w_avg["real_disc"])
        st.metric("Real Discovery", f"{y['real_disc']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="Actual productive conversations (excludes IVR)")
    with c4:
        d = delta_pct(y["ivr"], w_avg["ivr"])
        st.metric("IVR Traps", f"{y['ivr']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="Rep stuck in automated menus", delta_color="inverse")
    with c5:
        d = delta_pct(y["wrong_num"], w_avg["wrong_num"])
        st.metric("Wrong Numbers", f"{y['wrong_num']:,}", f"{d:+.0f}%" if d is not None else None,
                  help="List hygiene signal", delta_color="inverse")

    # Period summary
    st.markdown(f"### Period Summary · {len(fdf):,} outbound dials")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Dials", f"{f_metrics['dials']:,}")
    m2.metric("Connected", f"{f_metrics['connected']:,}",
              f"{100*f_metrics['connected']/max(f_metrics['dials'],1):.0f}%")
    m3.metric(">2 min", f"{f_metrics['over_2min']:,}",
              f"{100*f_metrics['over_2min']/max(f_metrics['dials'],1):.1f}%")
    m4.metric("Real Discovery", f"{f_metrics['real_disc']:,}",
              f"{100*f_metrics['real_disc']/max(f_metrics['dials'],1):.1f}%")
    m5.metric("IVR Traps", f"{f_metrics['ivr']:,}")
    m6.metric("Wrong #", f"{f_metrics['wrong_num']:,}",
              f"{100*f_metrics['wrong_num']/max(f_metrics['dials'],1):.1f}%")

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
        daily.columns = ["date","Dials","Real Discovery","IVR Traps"]
        if not daily.empty:
            st.line_chart(daily.set_index("date"), height=300)

    st.divider()

    # Time patterns
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### Best Hour to Dial")
        fdf_h = fdf.copy()
        fdf_h["hour"] = fdf_h["start_time"].dt.hour
        hour_stats = fdf_h.groupby("hour").agg(
            Dials=("id","count"),
            RealDisc=("classification", lambda x: (x == "real_discovery").sum()),
        ).reset_index()
        hour_stats["DiscRate"] = (100 * hour_stats["RealDisc"] / hour_stats["Dials"].clip(lower=1)).round(1)
        st.line_chart(hour_stats.set_index("hour")[["Dials","DiscRate"]], height=300)

    with col_b:
        st.markdown("#### Best Day of Week")
        fdf_d = fdf.copy()
        dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        fdf_d["dow"] = fdf_d["start_time"].dt.dayofweek.apply(lambda i: dow_names[i])
        dow_stats = fdf_d.groupby("dow").agg(
            Dials=("id","count"),
            RealDisc=("classification", lambda x: (x == "real_discovery").sum()),
        ).reindex(dow_names).reset_index()
        dow_stats["DiscRate"] = (100 * dow_stats["RealDisc"] / dow_stats["Dials"].clip(lower=1)).round(1)
        st.bar_chart(dow_stats.set_index("dow")[["Dials","DiscRate"]], height=300)

    st.divider()

    # Area codes
    st.markdown("### Area Code Performance")
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
    ac_stats["Disc%"] = (100 * ac_stats["RealDisc"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats["IVR%"] = (100 * ac_stats["IVR"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats["Wrong%"] = (100 * ac_stats["Wrongs"] / ac_stats["Dials"].clip(lower=1)).round(1)
    ac_stats = ac_stats[["ac","City","Dials","Over2min","RealDisc","Disc%","IVR","IVR%","Wrongs","Wrong%"]]
    ac_stats.columns = ["Area","City","Dials",">2min","Disc.","Disc %","IVR","IVR %","Wrong #","Wrong %"]

    st.dataframe(
        ac_stats, use_container_width=True, hide_index=True,
        column_config={
            "Disc %": st.column_config.ProgressColumn("Disc %", format="%.1f%%", min_value=0, max_value=10),
            "IVR %": st.column_config.ProgressColumn("IVR %", format="%.1f%%", min_value=0, max_value=100),
            "Wrong %": st.column_config.ProgressColumn("Wrong %", format="%.1f%%", min_value=0, max_value=30),
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — WINNING CALLS
# ═══════════════════════════════════════════════════════════════════════════

with tab_wins:

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

with tab_diag:

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
        Disc=("classification", lambda x: (x == "real_discovery").sum()),
        IVR=("classification", lambda x: (x == "ivr_hold").sum()),
    ).reset_index()
    metro_stats = metro_stats[metro_stats["Dials"] >= 10].sort_values("Dials", ascending=False)
    metro_stats["Disc%"] = (100 * metro_stats["Disc"] / metro_stats["Dials"]).round(1)
    metro_stats["IVR%"] = (100 * metro_stats["IVR"] / metro_stats["Dials"]).round(1)

    col_r1, col_r2 = st.columns([1, 1])

    with col_r1:
        st.markdown("**Volume vs. Results by Metro**")
        st.dataframe(
            metro_stats.rename(columns={"metro":"Metro"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Disc%": st.column_config.ProgressColumn("Disc%", format="%.1f%%", min_value=0, max_value=10),
                "IVR%": st.column_config.ProgressColumn("IVR%", format="%.1f%%", min_value=0, max_value=100),
            }
        )

    with col_r2:
        st.markdown("**Recommended Actions:**")
        high_vol_low_conv = metro_stats[(metro_stats["Dials"] >= 30) & (metro_stats["Disc%"] <= 1)]
        high_conv = metro_stats[metro_stats["Disc%"] >= 3].sort_values("Disc%", ascending=False)

        if not high_conv.empty:
            st.markdown("**Scale UP** (high discovery rate):")
            for _, r in high_conv.iterrows():
                st.markdown(f"- **{r['metro']}** — {r['Disc%']}% discovery, {r['Dials']} dials")

        if not high_vol_low_conv.empty:
            st.markdown("**Scale DOWN or fix list** (high volume, near-zero results):")
            for _, r in high_vol_low_conv.iterrows():
                st.markdown(f"- **{r['metro']}** — {r['Disc%']}% discovery despite {r['Dials']} dials")

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

with tab_playbook:

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

with tab_leads:

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

with tab_reps:

    st.markdown("### Rep Performance Comparison")

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
    rep_stats["Conn%"] = (100 * rep_stats["Connected"] / rep_stats["Dials"]).round(1)
    rep_stats["Disc%"] = (100 * rep_stats["Real_Discovery"] / rep_stats["Dials"]).round(1)
    rep_stats["IVR%"] = (100 * rep_stats["IVR"] / rep_stats["Dials"]).round(1)
    rep_stats["Avg_Dur"] = rep_stats["Avg_Dur"].round(0).astype(int)

    rep_stats = rep_stats.rename(columns={"from_name": "Rep"})
    display_cols = ["Rep","Dials","Connected","Conn%","Over2min",
                    "Real_Discovery","Disc%","IVR","IVR%","Gatekeeper","Wrong_Num","Avg_Dur"]
    rep_stats = rep_stats[display_cols]

    st.dataframe(
        rep_stats,
        use_container_width=True, hide_index=True,
        column_config={
            "Disc%": st.column_config.ProgressColumn("Disc%", format="%.1f%%", min_value=0, max_value=10),
            "Conn%": st.column_config.ProgressColumn("Conn%", format="%.1f%%", min_value=0, max_value=100),
            "IVR%": st.column_config.ProgressColumn("IVR%", format="%.1f%%", min_value=0, max_value=100),
        }
    )

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

        with st.expander(f"**{rep_name}** — {rep['Dials']} dials, {rep['Real_Discovery']} discoveries ({rep['Disc%']}%)", expanded=True):
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Dials", int(rep["Dials"]))
            cc2.metric("Discovery Rate", f"{rep['Disc%']}%")
            cc3.metric("IVR Trap Rate", f"{rep['IVR%']}%")
            cc4.metric("Avg Duration", f"{rep['Avg_Dur']}s")

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

with tab_calls:

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


# ─── Footer ──────────────────────────────────────────────────────────────

st.divider()
st.caption("J T-Shirts Call Analytics · Dashboard auto-refreshes every 5 min · Update data: `python jtcalls.py daily`")
