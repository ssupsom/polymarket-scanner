"""
Stale Price Strategy
หาตลาดที่ราคา "ค้าง" — ไม่ขยับเลยในระยะเวลานาน

Theory:
  - ตลาดเสรีต้องมี movement บ้าง (แม้แค่ 0.001)
  - ถ้าราคา constant > 6 ชั่วโมง = liquidity ต่ำ / ไม่มีคนเทรด
  - ราคาอาจ outdated / news ใหม่ยังไม่ถูก price in

Logic:
  - หาตลาดที่ราคาไม่เปลี่ยน > X ชั่วโมง
  - rank by uncertainty (ราคาที่ไม่ extreme)
  - ใช้ดู watchlist ของ tail markets

⚠️ Caveat:
  - Stale price อาจหมายถึงตลาดที่ราคาถูกต้องอยู่แล้ว (consensus)
  - ใช้ดูตลาดที่อาจ trade ได้ก่อนคนอื่น
"""

from typing import List
import pandas as pd

from .base import BaseStrategy, Opportunity


class StalePrice(BaseStrategy):

    MIN_STALE_HOURS = 6
    NOT_EXTREME_LOW = 0.10
    NOT_EXTREME_HIGH = 0.90

    @property
    def name(self) -> str:
        return "Stale Price"

    @property
    def description(self) -> str:
        return f"ตลาดที่ราคาค้าง > {self.MIN_STALE_HOURS}h — อาจ outdated หรือ low liquidity"

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty:
            return []

        now = snapshots["scanned_at"].max()

        # ใช้เฉพาะ Yes outcome
        yes_snaps = snapshots[snapshots["outcome"].isin(["Yes", "yes", "YES"])].copy()
        if yes_snaps.empty:
            return []

        # group by market: นับ unique prices + earliest scanned_at ของ price ปัจจุบัน
        results = []
        for market_id, group in yes_snaps.groupby("market_id"):
            group = group.sort_values("scanned_at")
            current_price = group["price"].iloc[-1]
            current_time = group["scanned_at"].iloc[-1]

            # ตรวจราคาไม่ extreme
            if not (self.NOT_EXTREME_LOW <= current_price <= self.NOT_EXTREME_HIGH):
                continue

            # หาเวลาที่ราคาเริ่มค้างที่ค่าปัจจุบัน
            # = scanned_at เก่าสุดที่ price = current_price ติดต่อกันถึงตอนนี้
            reversed_group = group.iloc[::-1]
            stale_since = current_time
            for _, row in reversed_group.iterrows():
                if abs(row["price"] - current_price) < 0.001:
                    stale_since = row["scanned_at"]
                else:
                    break

            stale_hours = (now - stale_since).total_seconds() / 3600
            if stale_hours < self.MIN_STALE_HOURS:
                continue

            results.append({
                "market_id": market_id,
                "price": current_price,
                "stale_hours": stale_hours,
            })

        if not results:
            return []

        df = pd.DataFrame(results)
        merged = df.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="left",
        )

        opportunities = []
        for _, row in merged.iterrows():
            uncertainty = 0.5 - abs(row["price"] - 0.5)
            question = row.get("question", "") or ""
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[row["market_id"]],
                    description=(
                        f"💤 {row['stale_hours']:.1f}h ค้างที่ {row['price']:.2f} — "
                        + question[:50]
                        + ("..." if len(question) > 50 else "")
                    ),
                    edge_pct=uncertainty * 100,
                    is_profitable=False,  # ไม่ใช่ direct arb
                    metadata={
                        "price": round(row["price"], 4),
                        "stale_hours": round(row["stale_hours"], 2),
                    },
                )
            )

        opportunities.sort(key=lambda o: o.metadata["stale_hours"], reverse=True)
        return opportunities[:20]
