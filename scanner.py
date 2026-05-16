"""
Polymarket Scanner — Minimal Viable Version
ดึงข้อมูลตลาดจาก Polymarket แล้วเก็บลง Supabase
"""

import os
import json
import requests
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

# โหลดค่าจาก .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
POLYMARKET_API = "https://gamma-api.polymarket.com/markets"

# ตั้งค่าจำนวนตลาดที่ดึง
LIMIT = 500


def fetch_markets():
    """ดึง active markets จาก Polymarket"""
    params = {
        "limit": LIMIT,
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",  # เรียงจาก volume สูงสุด
    }
    response = requests.get(POLYMARKET_API, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def parse_market(raw):
    """แปลงข้อมูลดิบให้พร้อม insert"""
    try:
        outcomes = json.loads(raw.get("outcomes", "[]"))
        prices = json.loads(raw.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None, []

    if not outcomes or not prices or len(outcomes) != len(prices):
        return None, []

    # ดึง volume + liquidity (ใช้ None ถ้าไม่มี)
    def _num(key):
        v = raw.get(key)
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    market = {
        "id": raw["id"],
        "question": raw.get("question", ""),
        "end_date": raw.get("endDate"),
        "volume": _num("volumeNum") or _num("volume"),
        "volume_24h": _num("volume24hr"),
        "liquidity": _num("liquidityNum") or _num("liquidity"),
    }

    snapshots = []
    for outcome, price in zip(outcomes, prices):
        try:
            snapshots.append({
                "market_id": raw["id"],
                "outcome": outcome,
                "price": float(price),
            })
        except (ValueError, TypeError):
            continue

    return market, snapshots


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] เริ่ม scan...")

    # ตรวจสอบ env
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: ไม่พบ SUPABASE_URL หรือ SUPABASE_KEY ใน .env")
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 1. ดึงข้อมูล
    try:
        raw_markets = fetch_markets()
        print(f"ดึง {len(raw_markets)} ตลาดมาแล้ว")
    except Exception as e:
        print(f"ERROR ดึงข้อมูล: {e}")
        return

    # 2. Parse + เตรียม batch
    market_batch = []
    snapshot_batch = []

    for raw in raw_markets:
        market, snapshots = parse_market(raw)
        if market:
            market_batch.append(market)
            snapshot_batch.extend(snapshots)

    print(f"Parse สำเร็จ: {len(market_batch)} ตลาด, {len(snapshot_batch)} snapshots")

    if not market_batch:
        print("ไม่มีข้อมูลให้บันทึก")
        return

    # 3. Upsert markets (ใส่ใหม่หรือ update ถ้ามีแล้ว)
    try:
        supabase.table("markets").upsert(market_batch).execute()
        print(f"บันทึก markets สำเร็จ")
    except Exception as e:
        print(f"ERROR บันทึก markets: {e}")
        return

    # 4. Insert price snapshots (batch 500 rows/ครั้ง เพื่อไม่ติด Supabase limit)
    try:
        BATCH_SIZE = 500
        total = 0
        for i in range(0, len(snapshot_batch), BATCH_SIZE):
            chunk = snapshot_batch[i:i + BATCH_SIZE]
            supabase.table("price_snapshots").insert(chunk).execute()
            total += len(chunk)
        print(f"บันทึก {total} snapshots สำเร็จ")
    except Exception as e:
        print(f"ERROR บันทึก snapshots: {e}")
        return

    # 5. แสดงตัวอย่าง 3 ตลาดแรก
    print("\n--- ตัวอย่าง 3 ตลาดแรก ---")
    for m in market_batch[:3]:
        print(f"  {m['question'][:60]}...")
        market_snaps = [s for s in snapshot_batch if s["market_id"] == m["id"]]
        for s in market_snaps:
            print(f"    {s['outcome']}: {s['price']}")

    print(f"\n[{datetime.now(timezone.utc).isoformat()}] เสร็จสิ้น")


if __name__ == "__main__":
    main()
