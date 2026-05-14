"""
Polymarket Scanner Dashboard
สรุปข้อมูลจาก Supabase ให้เข้าใจง่าย
"""

import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from supabase import create_client

# โหลด .env ถ้ารัน local (ไม่ error ถ้าไม่มี dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---- Page config ----
st.set_page_config(
    page_title="Polymarket Scanner",
    page_icon="📊",
    layout="wide",
)

# ---- Connect Supabase ----
def get_secret(key):
    """ดึงค่าจาก st.secrets (cloud) หรือ os.environ (local)"""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        val = os.getenv(key)
        if not val:
            st.error(f"ไม่พบ {key} — ตรวจ .env (local) หรือ Secrets (cloud)")
            st.stop()
        return val

@st.cache_resource
def get_supabase():
    return create_client(
        get_secret("SUPABASE_URL"),
        get_secret("SUPABASE_KEY"),
    )

supabase = get_supabase()


# ---- Load data (cache 60 วินาที) ----
@st.cache_data(ttl=60)
def load_markets():
    res = supabase.table("markets").select("*").execute()
    return pd.DataFrame(res.data)

@st.cache_data(ttl=60)
def load_snapshots(limit=10000):
    res = (
        supabase.table("price_snapshots")
        .select("*")
        .order("scanned_at", desc=True)
        .limit(limit)
        .execute()
    )
    df = pd.DataFrame(res.data)
    if not df.empty:
        df["scanned_at"] = pd.to_datetime(df["scanned_at"], utc=True)
        df["price"] = pd.to_numeric(df["price"])
    return df


markets_df = load_markets()
snapshots_df = load_snapshots()


# ---- Header ----
st.title("📊 Polymarket Scanner")
st.caption("ระบบสำรวจ edge ในตลาด prediction market — อัปเดตอัตโนมัติทุก 5 นาที")


# ---- Health check ----
now = datetime.now(timezone.utc)

if snapshots_df.empty:
    st.warning("⚠️ ยังไม่มีข้อมูล — รอ scanner รันครั้งแรก")
    st.stop()

last_scan = snapshots_df["scanned_at"].max()
minutes_ago = (now - last_scan).total_seconds() / 60

if minutes_ago < 10:
    st.success(f"🟢 Scanner ทำงานปกติ • Last scan: {int(minutes_ago)} นาทีที่แล้ว")
elif minutes_ago < 30:
    st.warning(f"🟡 Scanner ช้ากว่าปกติ • Last scan: {int(minutes_ago)} นาทีที่แล้ว")
else:
    st.error(f"🔴 Scanner อาจหยุดทำงาน • Last scan: {int(minutes_ago)} นาทีที่แล้ว")


# ---- Key metrics ----
st.subheader("ภาพรวม")

cutoff_24h = now - timedelta(hours=24)
snapshots_24h = snapshots_df[snapshots_df["scanned_at"] > cutoff_24h]
unique_markets_24h = snapshots_24h["market_id"].nunique()
scans_24h = snapshots_24h.groupby(snapshots_24h["scanned_at"].dt.floor("5min")).ngroups

c1, c2, c3, c4 = st.columns(4)
c1.metric("ตลาดที่ track", len(markets_df))
c2.metric("Snapshots ทั้งหมด", f"{len(snapshots_df):,}")
c3.metric("Snapshots ใน 24h", f"{len(snapshots_24h):,}")
c4.metric("รอบที่ scan ใน 24h", f"{scans_24h} / 288")


# ---- Activity chart ----
st.subheader("กิจกรรมของ scanner")

snapshots_df["hour"] = snapshots_df["scanned_at"].dt.floor("h")
hourly_counts = snapshots_df.groupby("hour").size().reset_index(name="snapshots")
hourly_counts = hourly_counts.sort_values("hour").tail(48)  # 48h ล่าสุด

st.line_chart(
    hourly_counts.set_index("hour")["snapshots"],
    height=250,
)
st.caption("จำนวน snapshots ที่เก็บได้ในแต่ละชั่วโมง — ควรนิ่งที่ ~40/ชั่วโมง (20 ตลาด × 2 outcomes × 12 รอบ/ชั่วโมง)")


# ---- Price distribution ----
st.subheader("การกระจายของราคา")
st.caption("ดูว่าตลาดส่วนใหญ่อยู่ในช่วงราคาไหน — ตลาดที่น่าสนใจสำหรับ arb มักอยู่ในช่วง 0.10–0.90")

latest_per_outcome = (
    snapshots_df.sort_values("scanned_at")
    .groupby(["market_id", "outcome"])
    .tail(1)
)

price_bins = pd.cut(
    latest_per_outcome["price"],
    bins=[0, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 1.00],
    labels=["0-5%", "5-10%", "10-25%", "25-50%", "50-75%", "75-90%", "90-95%", "95-100%"],
)
dist = price_bins.value_counts().sort_index()

st.bar_chart(dist, height=200)


# ---- Market efficiency check ----
st.subheader("ตรวจสอบ market efficiency")
st.caption("ตลาดที่ Σ(prices) ≠ 1 = อาจมี arb opportunity (สำหรับตลาด 2-outcome)")

# คำนวณ Σ(prices) ของแต่ละตลาดจาก snapshot ล่าสุด
sum_prices = latest_per_outcome.groupby("market_id")["price"].sum().reset_index(name="sum_price")
sum_prices = sum_prices.merge(markets_df[["id", "question"]], left_on="market_id", right_on="id", how="left")
sum_prices["deviation"] = (sum_prices["sum_price"] - 1.0).abs()
sum_prices = sum_prices.sort_values("deviation", ascending=False)

violations = sum_prices[sum_prices["deviation"] > 0.02]  # >2% deviation

if violations.empty:
    st.info("ยังไม่พบ violation ที่มีนัยสำคัญ (>2%) ในตลาด 2-outcome — ตลาดอยู่ในภาวะ efficient")
else:
    st.success(f"พบ {len(violations)} ตลาดที่ Σ(prices) ผิดปกติ (deviation > 2%)")
    st.dataframe(
        violations[["question", "sum_price", "deviation"]].head(10),
        use_container_width=True,
        hide_index=True,
    )


# ---- Markets list ----
st.subheader("ตลาดที่ track")

# Latest price per market
latest_yes = latest_per_outcome[latest_per_outcome["outcome"].isin(["Yes", "Over", "Odd"])]
markets_view = markets_df.merge(
    latest_yes[["market_id", "price", "scanned_at"]],
    left_on="id",
    right_on="market_id",
    how="left",
)

st.dataframe(
    markets_view[["question", "price", "end_date", "scanned_at"]]
    .rename(columns={
        "question": "คำถาม",
        "price": "ราคา (outcome 1)",
        "end_date": "End date",
        "scanned_at": "Last scan",
    })
    .sort_values("Last scan", ascending=False),
    use_container_width=True,
    hide_index=True,
)


# ---- Footer ----
st.markdown("---")
st.caption(f"Data refreshed every 60s • {len(snapshots_df):,} snapshots loaded • Scanner runs every 5 minutes")
