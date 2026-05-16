"""
Multi-Outcome Arbitrage Strategy
ตรวจตลาด 3+ outcomes ที่ Σ(prices) ≠ 1

ตัวอย่าง: เลือกตั้ง 4 ผู้สมัคร
  A = 0.45, B = 0.30, C = 0.20, D = 0.10  → Σ = 1.05 → arb!

Edge: ตลาดที่มีหลาย outcome คำนวณยากกว่า → bot ตามไม่ทันบ่อย
"""

from typing import List
import pandas as pd

from .base import BaseStrategy, Opportunity


class MultiOutcomeArb(BaseStrategy):

    DISCOVERY_THRESHOLD = 0.01

    @property
    def name(self) -> str:
        return "Multi-Outcome Arb"

    @property
    def description(self) -> str:
        return "ตลาด 3+ outcomes ที่ Σ(prices) ≠ 1 — bot คำนวณยากกว่า มีโอกาส mispriced มากกว่า"

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty:
            return []

        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
        )

        per_market = (
            latest.groupby("market_id")
            .agg(sum_price=("price", "sum"), n_outcomes=("outcome", "count"))
            .reset_index()
        )

        # เฉพาะ 3+ outcomes
        multi = per_market[per_market["n_outcomes"] >= 3].copy()
        if multi.empty:
            return []

        multi["deviation"] = (multi["sum_price"] - 1.0).abs()
        discoveries = multi[multi["deviation"] > self.DISCOVERY_THRESHOLD]

        merged = discoveries.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="left",
        )

        opportunities = []
        for _, row in merged.iterrows():
            edge_pct = row["deviation"] * 100
            question = row.get("question", "") or ""
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[row["market_id"]],
                    description=question[:80] + ("..." if len(question) > 80 else ""),
                    edge_pct=edge_pct,
                    is_profitable=edge_pct > self.edge_threshold_pct,
                    metadata={
                        "sum_price": round(row["sum_price"], 4),
                        "deviation": round(row["deviation"], 4),
                        "n_outcomes": int(row["n_outcomes"]),
                    },
                )
            )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities
