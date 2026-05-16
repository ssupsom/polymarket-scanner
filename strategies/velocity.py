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
    MIN_PRICE_DELTA = 0.05       # ราคาขยับ >= 5 cents (absolute)
    NOT_EXTREME_LOW = 0.10        # กรองตลาดราคา < 10% (resolve)
    NOT_EXTREME_HIGH = 0.90       # กรองตลาดราคา > 90% (resolve)

    @property
    def name(self) -> str:
        return "Velocity"

    @property
    def description(self) -> str:
        return f"ราคาขยับ ≥ {self.MIN_PRICE_DELTA:.2f} ใน {self.LOOKBACK_HOURS}h (ราคาไม่ extreme) — อาจมี mispricing"

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

        # คำนวณ delta (absolute change)
        merged["abs_delta"] = (merged["price_now"] - merged["price_old"]).abs()
        merged["delta_pct"] = (
            (merged["price_now"] - merged["price_old"])
            / merged["price_old"].clip(lower=0.001)
            * 100
        )

        # filter:
        # 1. ราคาทั้งคู่ต้องไม่ extreme (กัน resolved markets)
        # 2. absolute change ต้องใหญ่พอ
        # 3. เก็บแค่ Yes outcome (Yes/No เป็น mirror ของกันและกัน — ไม่ต้องนับ 2 ครั้ง)
        fast = merged[
            (merged["abs_delta"] >= self.MIN_PRICE_DELTA)
            & (merged["price_now"] >= self.NOT_EXTREME_LOW)
            & (merged["price_now"] <= self.NOT_EXTREME_HIGH)
            & (merged["price_old"] >= self.NOT_EXTREME_LOW)
            & (merged["price_old"] <= self.NOT_EXTREME_HIGH)
            & (merged["outcome"].isin(["Yes", "yes", "YES"]))
        ]

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
                        f"{direction} {row['price_old']:.2f}→{row['price_now']:.2f} "
                        f"(Δ{row['delta_pct']:+.1f}%, {row['outcome']}) — "
                        + question[:50]
                        + ("..." if len(question) > 50 else "")
                    ),
                    edge_pct=row["abs_delta"] * 100,  # show as % points moved
                    is_profitable=False,
                    metadata={
                        "outcome": row["outcome"],
                        "price_now": round(row["price_now"], 4),
                        "price_old": round(row["price_old"], 4),
                        "abs_delta": round(row["abs_delta"], 4),
                        "delta_pct": round(row["delta_pct"], 2),
                    },
                )
            )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities[:20]
