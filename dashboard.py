"""
Polymarket Scanner - Decision Dashboard
ตอบคำถามหลัก: "ตลาดนี้เล่นได้ไหม?"
"""

import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ======================================================
# เกณฑ์ตัดสินใจ (ปรับค่าได้)
# ======================================================
HEALTH_MAX_MINUTES = 25           # Scanner ต้องรันใน X นาทีล่าสุด
MIN_DATA_HOURS = 24               # ต้องมีข้อมูลอย่างน้อย X ชั่วโมง
MIN_OPPORTUNITIES = 1             # ต้องเจอ violation ≥ X อันใน 24h
EDGE_THRESHOLD_PCT = 3.0          # Edge ต้อง > X% (Polymarket fee = 2%)
VIOLATION_THRESHOLD = 0.02        # ตลาดที่ |Σprice - 1| > X = violation


# ======================================================
# Setup
# ======================================================
st.set_page_config(page_title="Polymarket Scanner", page_icon="📊", layout="wide")


def get_secret(key):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        val = os.getenv(key)
        if not val:
            st.error(f"ไม่พบ {key}")
            st.stop()
        return val


@st.cache_resource
def get_supabase():
    return create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY"))


@st.cache_data(ttl=60)
def load_data():
    sb = get_supabase()
    markets = pd.DataFrame(sb.table("markets").select("*").execute().data)
    snapshots = pd.DataFrame(
        sb.table("price_snapshots")
        .select("*")
        .order("scanned_at", desc=True)
        .limit(20000)
        .execute()
        .data
    )
    if not snapshots.empty:
        snapshots["scanned_at"] = pd.to_datetime(snapshots["scanned_at"], utc=True)
        snapshots["price"] = pd.to_numeric(snapshots["price"])
    return markets, snapshots


markets_df, snapshots_df = load_data()


# ======================================================
# คำนวณผลการทดสอบ
# ======================================================
now = datetime.now(timezone.utc)

if snapshots_df.empty:
    st.title("📊 Polymarket Scanner")
    st.error("## 🔴 NO DATA\nยังไม่มีข้อมูลใน Supabase — รอ scanner รันครั้งแรก")
    st.stop()


# ---- Test 1: Scanner ทำงานหรือไม่ ----
minutes_since_last = (now - snapshots_df["scanned_at"].max()).total_seconds() / 60
test1_pass = minutes_since_last < HEALTH_MAX_MINUTES


# ---- Test 1.5: Scan reliability (scale ตามอายุข้อมูลจริง) ----
SCAN_INTERVAL_MIN = 15  # cron interval
RELIABILITY_THRESHOLD = 0.85  # 85% ของ scans ต้องสำเร็จ

cutoff_24h = now - timedelta(hours=24)
snapshots_recent = snapshots_df[snapshots_df["scanned_at"] > cutoff_24h]

# คำนวณ expected ตามช่วงเวลาที่มี data จริง (สูงสุด 24 ชั่วโมง)
if not snapshots_recent.empty:
    oldest_recent = snapshots_recent["scanned_at"].min()
    actual_window_hours = min((now - oldest_recent).total_seconds() / 3600, 24)
else:
    actual_window_hours = 0

EXPECTED_SCANS = max(1, int(actual_window_hours * 60 / SCAN_INTERVAL_MIN))
scan_batches = snapshots_recent["scanned_at"].dt.floor("5min").nunique()
reliability_pct = (scan_batches / EXPECTED_SCANS) * 100 if EXPECTED_SCANS else 0
reliability_pass = reliability_pct >= RELIABILITY_THRESHOLD * 100


# ---- Test 2: ข้อมูลพอตัดสินใจหรือไม่ ----
data_age_hours = (now - snapshots_df["scanned_at"].min()).total_seconds() / 3600
test2_pass = data_age_hours >= MIN_DATA_HOURS


# ---- Test 3 & 4: Opportunities + Edge size ----
latest = (
    snapshots_df.sort_values("scanned_at")
    .groupby(["market_id", "outcome"])
    .tail(1)
)

sum_per_market = (
    latest.groupby("market_id")
    .agg(sum_price=("price", "sum"), n_outcomes=("outcome", "count"))
    .reset_index()
)
two_outcome = sum_per_market[sum_per_market["n_outcomes"] == 2].copy()
two_outcome["deviation"] = (two_outcome["sum_price"] - 1.0).abs()

violations = two_outcome[two_outcome["deviation"] > VIOLATION_THRESHOLD]
test3_pass = len(violations) >= MIN_OPPORTUNITIES

max_edge_pct = violations["deviation"].max() * 100 if not violations.empty else 0
test4_pass = max_edge_pct > EDGE_THRESHOLD_PCT


# ======================================================
# Verdict Logic
# ======================================================
if not test1_pass:
    verdict_color = "error"
    verdict_title = "🔴 SYSTEM ERROR"
    verdict_msg = "Scanner ไม่ทำงาน — ตรวจ GitHub Actions ก่อนทำอะไรต่อ"
    next_action = "ไปที่ GitHub Actions ดูว่ารัน fail อะไร"

elif not test2_pass:
    hours_left = MIN_DATA_HOURS - data_age_hours
    verdict_color = "warning"
    verdict_title = "🟡 WAITING FOR DATA"
    verdict_msg = f"ยังเก็บข้อมูลไม่พอตัดสินใจ — รออีก **{hours_left:.0f} ชั่วโมง**"
    next_action = "ไม่ต้องทำอะไร — ระบบเก็บข้อมูลเอง กลับมาดูทีหลัง"

elif test3_pass and test4_pass:
    verdict_color = "success"
    verdict_title = "🟢 PROCEED"
    verdict_msg = f"พบ **{len(violations)} ตลาด** ที่มี edge > {EDGE_THRESHOLD_PCT}% — น่าลงทุนต่อ"
    next_action = "Phase 2: เริ่ม paper trade ทดสอบกลยุทธ์ก่อน deploy ทุนจริง"

elif test3_pass and not test4_pass:
    verdict_color = "warning"
    verdict_title = "🟡 MARGINAL"
    verdict_msg = f"มี {len(violations)} opportunities แต่ edge เล็กเกินไป (max {max_edge_pct:.1f}%)"
    next_action = "ขยาย scope: เพิ่ม LIMIT จาก 20 เป็น 100 ตลาด เพื่อหาตลาด niche"

else:
    verdict_color = "error"
    verdict_title = "🔴 NO EDGE"
    verdict_msg = "ตลาด top 20 efficient เกินไป — ไม่พบ opportunity"
    next_action = "ขยาย scope: เพิ่ม LIMIT จาก 20 เป็น 100+ ตลาด หรือลองตลาดอื่น"


# ======================================================
# Render UI
# ======================================================
st.title("📊 Polymarket Scanner")
st.caption("Decision Dashboard — ตอบคำถาม: ตลาดนี้เล่นได้ไหม?")

# ---- VERDICT (ใหญ่ๆบนสุด) ----
verdict_box = f"## {verdict_title}\n\n{verdict_msg}\n\n**Next:** {next_action}"
if verdict_color == "success":
    st.success(verdict_box)
elif verdict_color == "warning":
    st.warning(verdict_box)
else:
    st.error(verdict_box)


# ---- ผลทดสอบ 4 ข้อ ----
st.subheader("📋 ผลการทดสอบ 4 ข้อ")

c1, c2 = st.columns(2)


def render_test(col, num, title, status, value, criteria, explain):
    icon = "✅" if status == "pass" else ("⏳" if status == "wait" else "❌")
    color = "#1D9E75" if status == "pass" else ("#BA7517" if status == "wait" else "#A32D2D")
    col.markdown(
        f"""
        <div style="border-left: 4px solid {color}; padding: 8px 16px; margin-bottom: 12px;">
        <b>{icon} Test {num}: {title}</b><br>
        ค่าที่วัดได้: <code>{value}</code><br>
        เกณฑ์ผ่าน: <code>{criteria}</code><br>
        <small style="color: #888;">{explain}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


render_test(
    c1, 1, "Scanner ทำงานต่อเนื่อง",
    "pass" if test1_pass else "fail",
    f"{minutes_since_last:.0f} นาทีตั้งแต่ scan ล่าสุด",
    f"< {HEALTH_MAX_MINUTES} นาที",
    "Scanner ควรรันทุก 15 นาที — ถ้าช้าเกินคือ GitHub Actions มีปัญหา",
)

if test2_pass:
    status2 = "pass"
elif data_age_hours > 0:
    status2 = "wait"
else:
    status2 = "fail"
render_test(
    c2, 2, "ข้อมูลพอตัดสินใจ",
    status2,
    f"{data_age_hours:.1f} ชั่วโมง",
    f"≥ {MIN_DATA_HOURS} ชั่วโมง",
    "ต้องมีข้อมูลอย่างน้อย 1 วัน เพื่อเห็น pattern ของตลาด",
)

render_test(
    c1, 3, "พบ Arb Opportunities",
    "pass" if test3_pass else ("wait" if not test2_pass else "fail"),
    f"{len(violations)} ตลาด มี Σ(prices) ผิดจาก 1.0",
    f"≥ {MIN_OPPORTUNITIES} ตลาด deviation > {VIOLATION_THRESHOLD*100:.0f}%",
    "ตลาด 2-outcome ที่ Σ(Yes + No) ≠ 1 = มี mispricing",
)

render_test(
    c2, 4, "Edge ใหญ่พอคุ้ม Fee",
    "pass" if test4_pass else ("wait" if not test3_pass else "fail"),
    f"{max_edge_pct:.2f}% (max deviation)",
    f"> {EDGE_THRESHOLD_PCT}%",
    "Polymarket fee = 2% ของกำไร — ต้องมี buffer ให้กิน fee + slippage",
)


# ---- Scan Reliability ----
st.subheader(f"⚙️ Scan Reliability ({actual_window_hours:.1f}h ล่าสุด)")

rc1, rc2, rc3 = st.columns(3)
rc1.metric(
    "Expected scans",
    EXPECTED_SCANS,
    help=f"คำนวณจาก {actual_window_hours:.1f} ชั่วโมง × 4 scans/ชั่วโมง (ทุก 15 นาที)",
)
rc2.metric("Actual scans", scan_batches)
rc3.metric(
    "Reliability",
    f"{reliability_pct:.0f}%",
    delta=f"{'PASS' if reliability_pass else 'BELOW THRESHOLD'} (เกณฑ์ ≥{RELIABILITY_THRESHOLD*100:.0f}%)",
    delta_color="normal" if reliability_pass else "inverse",
)

if reliability_pct < 50:
    st.error(
        f"⚠️ Scanner รันแค่ {reliability_pct:.0f}% ของที่ตั้งไว้ — "
        "GitHub Actions อาจ skip cron runs บ่อย พิจารณาเพิ่ม external cron service"
    )
elif reliability_pct < RELIABILITY_THRESHOLD * 100:
    st.warning(
        f"Scanner รัน {reliability_pct:.0f}% ของที่ตั้งไว้ — "
        "ต่ำกว่าเกณฑ์ 85% แต่ยังพอใช้งานได้"
    )
else:
    st.success(f"✅ Scanner ทำงานเสถียร ({reliability_pct:.0f}% ของที่ตั้งไว้)")


# ---- รายละเอียด Opportunities ----
st.subheader("🎯 ตลาดที่พบ Opportunity")

if violations.empty:
    st.info("ยังไม่พบตลาดที่มี deviation เกิน 2% — ตลาดอยู่ในภาวะ efficient")
else:
    opps = violations.merge(
        markets_df[["id", "question"]], left_on="market_id", right_on="id", how="left"
    )
    opps = opps.sort_values("deviation", ascending=False)
    opps["edge_pct"] = (opps["deviation"] * 100).round(2)
    opps["sum_price"] = opps["sum_price"].round(4)

    st.dataframe(
        opps[["question", "sum_price", "edge_pct"]].rename(
            columns={"question": "คำถาม", "sum_price": "Σ(prices)", "edge_pct": "Edge %"}
        ).head(20),
        use_container_width=True,
        hide_index=True,
    )


# ---- Progress bar ----
st.subheader("⏱️ ความคืบหน้าการเก็บข้อมูล")
progress = min(data_age_hours / MIN_DATA_HOURS, 1.0)
st.progress(progress)
st.caption(
    f"เก็บข้อมูลมาแล้ว {data_age_hours:.1f} / {MIN_DATA_HOURS} ชั่วโมง "
    f"({progress*100:.0f}%) • {len(snapshots_df):,} snapshots"
)


# ---- ข้อมูลเสริม ----
with st.expander("📈 ข้อมูลเสริม"):
    st.markdown("**กิจกรรมของ Scanner (snapshots/ชั่วโมง)**")
    snapshots_df["hour"] = snapshots_df["scanned_at"].dt.floor("h")
    hourly = snapshots_df.groupby("hour").size()
    st.line_chart(hourly, height=200)
    st.caption("ปกติ ~40 snapshots/ชั่วโมง (20 ตลาด × 2 outcomes × 12 รอบ)")

    st.markdown("**ตลาดทั้งหมดที่ track**")
    st.dataframe(
        markets_df[["id", "question", "end_date"]].rename(
            columns={"question": "คำถาม", "end_date": "End date"}
        ),
        use_container_width=True,
        hide_index=True,
    )


st.markdown("---")
st.caption(
    f"Refreshed every 60s • {len(snapshots_df):,} snapshots • "
    f"Last scan: {snapshots_df['scanned_at'].max().strftime('%H:%M')} UTC"
)
