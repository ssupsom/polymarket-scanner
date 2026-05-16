"""
Velocity Strategy
หาตลาดที่ราคา move เร็วเป็นพิเศษ — อาจเกิดก่อน market makers ตามทัน

Logic:
  - เทียบราคาตอนนี้ vs ราคา 1 ชั่วโมงก่อน
  - ถ้า |Δ| > 5% = velocity high
  - Higher velocity = higher chance of temporary mispricing

⚠️ Caveat:
  - Velocity ≠ mispricing — ราคาอาจ move ถูกต้องตาม news
  - ใช้เป็น signal เสริม ไม่ใช่ standalone arb
"""

from typing import List
from datetime import timedelta
import pandas as pd

from .base import BaseStrategy, Opportunity


class Velocity(BaseStrategy):

    LOOKBACK_HOURS = 1
    DISCOVERY_THRESHOLD_PCT = 5.0  # ราคา move > 5% ใน 1 ชั่วโมง

    @property
    def name(self) -> str:
        return "Velocity"

    @property
    def description(self) -> str:
        return f"ราคา move > {self.DISCOVERY_THRESHOLD_PCT}% ใน {self.LOOKBACK_HOURS} ชั่วโมง — อาจมี mispricing ชั่วคราว"

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty or len(snapshots) < 2:
            return []

        now = snapshots["scanned_at"].max()
        lookback = now - timedelta(hours=self.LOOKBACK_HOURS)

        # snapshot ล่าสุดของแต่ละ market+outcome
        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
            .rename(columns={"price": "price_now", "scanned_at": "time_now"})
        )

        # snapshot ที่ใกล้ lookback time (เก่ากว่า lookback แต่ไม่เก่าเกินไป)
        old_window = snapshots[
            (snapshots["scanned_at"] < lookback)
            & (snapshots["scanned_at"] > lookback - timedelta(minutes=30))
        ]
        if old_window.empty:
            return []

        old = (
            old_window.sort_values("scanned_at", ascending=False)
            .groupby(["market_id", "outcome"])
            .head(1)
            .rename(columns={"price": "price_old", "scanned_at": "time_old"})
        )

        merged = latest.merge(
            old[["market_id", "outcome", "price_old", "time_old"]],
            on=["market_id", "outcome"],
            how="inner",
        )
        if merged.empty:
            return []

        # คำนวณ velocity (% change)
        merged["delta_pct"] = (
            (merged["price_now"] - merged["price_old"])
            / merged["price_old"].clip(lower=0.001)  # ป้องกัน div by 0
            * 100
        )
        merged["abs_delta"] = merged["delta_pct"].abs()

        # filter ที่ velocity สูง
        fast = merged[merged["abs_delta"] > self.DISCOVERY_THRESHOLD_PCT]

        # merge question
        result = fast.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="left",
        )

        opportunities = []
        for _, row in result.iterrows():
            direction = "↑" if row["delta_pct"] > 0 else "↓"
            question = row.get("question", "") or ""
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[row["market_id"]],
                    description=(
                        f"{direction} {row['delta_pct']:+.1f}% ({row['outcome']}) — "
                        + question[:60]
                        + ("..." if len(question) > 60 else "")
                    ),
                    edge_pct=row["abs_delta"],
                    is_profitable=False,  # velocity = signal เท่านั้น ไม่กำไรโดยตรง
                    metadata={
                        "outcome": row["outcome"],
                        "price_now": round(row["price_now"], 4),
                        "price_old": round(row["price_old"], 4),
                        "delta_pct": round(row["delta_pct"], 2),
                    },
                )
            )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities[:20]  # คืนแค่ 20 อันดับแรก
