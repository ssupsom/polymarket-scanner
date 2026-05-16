"""
Polymarket Scanner — Multi-Strategy Decision Dashboard
แสดงผลทุก strategy พร้อมๆกัน + verdict รวม
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

from strategies import ALL_STRATEGIES, run_all_strategies


# ======================================================
# Config
# ======================================================
st.set_page_config(
    page_title="Polymarket Scanner",
    page_icon="📊",
    layout="wide",
)

HEALTH_MAX_MINUTES = 25
MIN_DATA_HOURS = 24
SCAN_INTERVAL_MIN = 15
RELIABILITY_THRESHOLD = 0.85


# ======================================================
# Data loading
# ======================================================
@st.cache_resource
def get_supabase():
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
    return create_client(url, key)


@st.cache_data(ttl=60)
def load_data():
    sb = get_supabase()
    markets = pd.DataFrame(sb.table("markets").select("*").execute().data)
    snapshots = pd.DataFrame(
        sb.table("price_snapshots")
        .select("*")
        .order("scanned_at", desc=True)
        .limit(50000)
        .execute()
        .data
    )
    if not snapshots.empty:
        snapshots["scanned_at"] = pd.to_datetime(snapshots["scanned_at"], utc=True)
    return markets, snapshots


# ======================================================
# Header
# ======================================================
st.title("📊 Polymarket Scanner")
st.caption("Multi-Strategy Decision Dashboard — ตรวจหา edge หลายรูปแบบพร้อมกัน")

with st.spinner("กำลังโหลดข้อมูล..."):
    markets_df, snapshots_df = load_data()

if snapshots_df.empty:
    st.error("ไม่มีข้อมูลใน DB — Scanner ยังไม่รันหรือ connection ผิดพลาด")
    st.stop()


# ======================================================
# Compute system health
# ======================================================
now = pd.Timestamp.now(tz="UTC")

# Active scope = ตลาดที่มี snapshot ใน 30 นาทีล่าสุด
recent_cutoff = now - timedelta(minutes=30)
active_market_ids = set(
    snapshots_df[snapshots_df["scanned_at"] > recent_cutoff]["market_id"].unique()
)
active_snapshots = snapshots_df[snapshots_df["market_id"].isin(active_market_ids)]
active_markets = markets_df[markets_df["id"].isin(active_market_ids)] if not markets_df.empty else markets_df

# Health metrics
minutes_since_last = (now - snapshots_df["scanned_at"].max()).total_seconds() / 60
scanner_ok = minutes_since_last < HEALTH_MAX_MINUTES

# Data age (active scope)
if not active_snapshots.empty:
    first_seen_per_market = active_snapshots.groupby("market_id")["scanned_at"].min()
    scope_start = first_seen_per_market.median()
    data_age_hours = (now - scope_start).total_seconds() / 3600
else:
    data_age_hours = 0
data_ready = data_age_hours >= MIN_DATA_HOURS


# ======================================================
# Run all strategies
# ======================================================
with st.spinner("กำลังรัน strategies..."):
    strategy_results = run_all_strategies(active_snapshots, active_markets)


# ======================================================
# Overall verdict
# ======================================================
total_profitable = sum(s.get("profitable_count", 0) for s in strategy_results)
total_discoveries = sum(s.get("total_discoveries", 0) for s in strategy_results)
strategies_with_profitable = [s for s in strategy_results if s.get("has_profitable", False)]

if not scanner_ok:
    verdict = ("🔴 SYSTEM ERROR", "error", "Scanner ไม่ทำงาน — ตรวจ GitHub Actions ก่อนทำอะไรต่อ")
elif not data_ready:
    hours_left = max(0, MIN_DATA_HOURS - data_age_hours)
    verdict = (
        "🟡 WAITING FOR DATA",
        "warning",
        f"ยังเก็บข้อมูลไม่พอตัดสินใจ — รออีก {hours_left:.0f} ชั่วโมง ({len(active_market_ids)} ตลาด active)"
    )
elif strategies_with_profitable:
    names = [s["name"] for s in strategies_with_profitable]
    verdict = (
        "🟢 PROCEED",
        "success",
        f"พบ {total_profitable} opportunities ที่กำไรหลังหัก fee — strategies: {', '.join(names)}"
    )
elif total_discoveries > 0:
    verdict = (
        "🟡 MARGINAL",
        "warning",
        f"พบ {total_discoveries} discoveries แต่ไม่กำไรหลังหัก fee — ตลาดมี movement แต่ edge ไม่พอ"
    )
else:
    verdict = (
        "🔴 NO EDGE",
        "error",
        "ไม่พบ opportunity ใดในทุก strategy — Polymarket top markets efficient เกินไป"
    )

verdict_title, verdict_color, verdict_msg = verdict
if verdict_color == "success":
    st.success(f"## {verdict_title}\n\n{verdict_msg}")
elif verdict_color == "warning":
    st.warning(f"## {verdict_title}\n\n{verdict_msg}")
else:
    st.error(f"## {verdict_title}\n\n{verdict_msg}")


# ======================================================
# System metrics
# ======================================================
st.divider()
st.subheader("⚙️ System Health")

h1, h2, h3, h4 = st.columns(4)
h1.metric(
    "Scanner",
    "✅ OK" if scanner_ok else "❌ DOWN",
    f"{minutes_since_last:.0f} นาทีล่าสุด",
)
h2.metric(
    "Active Markets",
    len(active_market_ids),
)
h3.metric(
    "Data Age (active scope)",
    f"{data_age_hours:.1f}h / {MIN_DATA_HOURS}h",
    delta=f"{'PASS' if data_ready else 'WAITING'}",
    delta_color="normal" if data_ready else "off",
)

# Reliability
cutoff_24h = now - timedelta(hours=24)
recent_active = active_snapshots[active_snapshots["scanned_at"] > cutoff_24h]
if not recent_active.empty:
    window_hours = min((now - recent_active["scanned_at"].min()).total_seconds() / 3600, 24)
    expected = max(1, int(window_hours * 60 / SCAN_INTERVAL_MIN))
    actual = recent_active["scanned_at"].dt.floor("5min").nunique()
    reliability_pct = (actual / expected) * 100
else:
    reliability_pct = 0
h4.metric(
    "Reliability",
    f"{reliability_pct:.0f}%",
    delta=f"{'PASS' if reliability_pct >= RELIABILITY_THRESHOLD * 100 else 'LOW'}",
    delta_color="normal" if reliability_pct >= RELIABILITY_THRESHOLD * 100 else "inverse",
)


# ======================================================
# Strategy cards
# ======================================================
st.divider()
st.subheader("🎯 Strategy Results")

cols_per_row = 2
for i in range(0, len(strategy_results), cols_per_row):
    cols = st.columns(cols_per_row)
    for j, col in enumerate(cols):
        if i + j >= len(strategy_results):
            break
        result = strategy_results[i + j]
        with col:
            with st.container(border=True):
                if result.get("error"):
                    status_icon = "⚠️"
                elif result.get("has_profitable"):
                    status_icon = "🟢"
                elif result.get("total_discoveries", 0) > 0:
                    status_icon = "🟡"
                else:
                    status_icon = "⚪"

                st.markdown(f"### {status_icon} {result['name']}")
                st.caption(result.get("description", ""))

                if result.get("error"):
                    st.error(f"Error: {result['error']}")
                else:
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Discoveries", result.get("total_discoveries", 0))
                    m2.metric("Profitable", result.get("profitable_count", 0))
                    m3.metric("Max Edge", f"{result.get('max_edge_pct', 0):.2f}%")


# ======================================================
# Detailed opportunities
# ======================================================
st.divider()
st.subheader("📋 รายละเอียด Opportunities")

for result in strategy_results:
    opps = result.get("opportunities", [])
    if not opps:
        continue

    icon = "🟢" if result.get("has_profitable") else "🟡"
    with st.expander(
        f"{icon} {result['name']} — {len(opps)} discoveries"
        + (f" ({result['profitable_count']} กำไร)" if result.get("profitable_count") else "")
    ):
        rows = []
        for o in opps[:20]:
            rows.append({
                "Edge %": f"{o.edge_pct:.2f}%",
                "Profitable": "✅" if o.is_profitable else "❌",
                "Description": o.description,
            })
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )


# ======================================================
# Footer
# ======================================================
st.divider()
st.caption(
    f"🔄 Last scan: {snapshots_df['scanned_at'].max().strftime('%Y-%m-%d %H:%M UTC')} | "
    f"💾 Snapshots loaded: {len(snapshots_df):,} | "
    f"🎯 Strategies: {len(ALL_STRATEGIES)}"
)
