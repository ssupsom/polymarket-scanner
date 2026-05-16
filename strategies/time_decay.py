"""
Time Decay Strategy
หาตลาดที่ใกล้ resolve แต่ราคายังไม่ extreme (ไม่ใกล้ 0 หรือ 1)

Theory:
  - ตลาดใกล้ resolve → uncertainty ลดลง → ราคาควรใกล้ 0 หรือ 1
  - ถ้าใกล้ resolve แล้วยังราคา 0.4-0.6 = market ไม่มั่นใจ
  - อาจเป็นโอกาส: ถ้าเรามี edge ในการ predict ราคาควรเป็นอะไร

Logic:
  - filter ตลาดที่ end_date < 24 ชั่วโมง
  - filter ราคาอยู่ในช่วง 0.05-0.95 (ไม่ extreme)
  - rank by closest to 0.5 (uncertainty สูงสุด)

⚠️ Caveat:
  - ไม่ใช่ arb — เป็น opportunity ต้องมี view เพิ่ม
  - ใช้ดู watchlist สำหรับ directional trades
"""

from typing import List
from datetime import timedelta
import pandas as pd

from .base import BaseStrategy, Opportunity


class TimeDecay(BaseStrategy):

    MAX_HOURS_TO_RESOLVE = 24
    NOT_EXTREME_LOW = 0.05
    NOT_EXTREME_HIGH = 0.95

    @property
    def name(self) -> str:
        return "Time Decay"

    @property
    def description(self) -> str:
        return f"ตลาดใกล้ resolve (< {self.MAX_HOURS_TO_RESOLVE}h) แต่ราคายังไม่ extreme — directional watchlist"

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty or markets.empty:
            return []

        # parse end_date
        markets = markets.copy()
        markets["end_date_parsed"] = pd.to_datetime(
            markets["end_date"], errors="coerce", utc=True
        )

        now = snapshots["scanned_at"].max()
        if now.tzinfo is None:
            now = now.tz_localize("UTC")

        cutoff = now + timedelta(hours=self.MAX_HOURS_TO_RESOLVE)

        # ตลาดที่ใกล้ resolve
        closing_soon = markets[
            markets["end_date_parsed"].notna()
            & (markets["end_date_parsed"] > now)
            & (markets["end_date_parsed"] < cutoff)
        ]
        if closing_soon.empty:
            return []

        # snapshot ล่าสุดของ Yes
        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
        )
        yes_only = latest[latest["outcome"].isin(["Yes", "yes", "YES"])]

        merged = yes_only.merge(
            closing_soon[["id", "question", "end_date_parsed"]],
            left_on="market_id",
            right_on="id",
            how="inner",
        )

        # ราคาไม่ extreme
        not_extreme = merged[
            (merged["price"] >= self.NOT_EXTREME_LOW)
            & (merged["price"] <= self.NOT_EXTREME_HIGH)
        ].copy()
        if not_extreme.empty:
            return []

        # คะแนน: ใกล้ 0.5 = uncertainty สูง
        not_extreme["uncertainty"] = 0.5 - (not_extreme["price"] - 0.5).abs()
        not_extreme["hours_left"] = (
            (not_extreme["end_date_parsed"] - now).dt.total_seconds() / 3600
        )

        opportunities = []
        for _, row in not_extreme.iterrows():
            question = row.get("question", "") or ""
            hours = row["hours_left"]
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[row["market_id"]],
                    description=(
                        f"⏰ {hours:.1f}h เหลือ — ราคา {row['price']:.2f} — "
                        + question[:50]
                        + ("..." if len(question) > 50 else "")
                    ),
                    edge_pct=row["uncertainty"] * 100,  # uncertainty as edge
                    is_profitable=False,  # ไม่ใช่ direct arb
                    metadata={
                        "price": round(row["price"], 4),
                        "hours_left": round(hours, 2),
                        "end_date": str(row["end_date_parsed"]),
                    },
                )
            )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities[:20]
