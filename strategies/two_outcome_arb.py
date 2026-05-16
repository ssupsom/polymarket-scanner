"""
Two-Outcome Arbitrage Strategy
ตรวจตลาด 2-outcome (Yes/No, Over/Under) ที่ Σ(prices) ≠ 1

Logic:
  - สำหรับตลาด 2-outcome: ราคา outcome1 + ราคา outcome2 ควร = 1
  - ถ้า Σ < 1 → ซื้อทั้ง 2 ข้าง ราคาถูก
  - ถ้า Σ > 1 → short ทั้ง 2 ข้าง ราคาแพง
  - Edge = |Σ - 1|
"""

from typing import List
import pandas as pd

from .base import BaseStrategy, Opportunity


class TwoOutcomeArb(BaseStrategy):

    DISCOVERY_THRESHOLD = 0.01  # ≥ 1% deviation = discovery

    @property
    def name(self) -> str:
        return "Two-Outcome Arb"

    @property
    def description(self) -> str:
        return "ตลาด 2-outcome ที่ Σ(prices) ≠ 1 — กลยุทธ์ classic arb"

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty:
            return []

        # snapshot ล่าสุดของแต่ละ market+outcome
        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
        )

        # group by market — เลือกเฉพาะตลาดที่มี 2 outcomes
        per_market = (
            latest.groupby("market_id")
            .agg(sum_price=("price", "sum"), n_outcomes=("outcome", "count"))
            .reset_index()
        )
        two_outcome = per_market[per_market["n_outcomes"] == 2].copy()
        two_outcome["deviation"] = (two_outcome["sum_price"] - 1.0).abs()

        # filter เฉพาะที่เกิน threshold
        discoveries = two_outcome[
            two_outcome["deviation"] > self.DISCOVERY_THRESHOLD
        ]

        # merge ชื่อตลาด
        merged = discoveries.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="left",
        )

        opportunities = []
        for _, row in merged.iterrows():
            edge_pct = row["deviation"] * 100
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[row["market_id"]],
                    description=row.get("question", "")[:80] + (
                        "..." if len(row.get("question", "")) > 80 else ""
                    ),
                    edge_pct=edge_pct,
                    is_profitable=edge_pct > self.edge_threshold_pct,
                    metadata={
                        "sum_price": round(row["sum_price"], 4),
                        "deviation": round(row["deviation"], 4),
                    },
                )
            )

        # sort by edge desc
        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities
