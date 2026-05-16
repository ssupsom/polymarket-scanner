"""
Ladder Violation Strategy
ตรวจตลาดที่เป็น "price ladder" ราคาขัดหลักคณิตศาสตร์

ตัวอย่าง:
  Market A: "BTC > $90K by Dec 31"  → P = 0.70
  Market B: "BTC > $100K by Dec 31" → P = 0.75  ← ผิด! ต้อง ≤ 0.70
  → arb 100%: ซื้อ A, short B

Detection: หาคู่ตลาดที่มีคำถามคล้ายกัน + ตัวเลข threshold ต่างกัน
"""

import re
from typing import List, Tuple, Optional
import pandas as pd

from .base import BaseStrategy, Opportunity


class LadderViolation(BaseStrategy):

    @property
    def name(self) -> str:
        return "Ladder Violation"

    @property
    def description(self) -> str:
        return "คู่ตลาด ladder ที่ราคาขัดธรรมชาติคณิตศาสตร์ (เช่น P(>$95K) > P(>$90K))"

    def _extract_threshold(self, question: str) -> Optional[Tuple[str, float]]:
        """
        ดึง numeric threshold จากคำถาม
        Return: (template, value) หรือ None ถ้า parse ไม่ได้

        Examples:
          "Will BTC be above $100K by Dec 31?" → ("Will BTC be above $___K by Dec 31?", 100)
          "Will Tesla close above $410 on May 14?" → ("Will Tesla close above $___ on May 14?", 410)
        """
        if not question:
            return None

        # match number with optional $ K/M suffix
        # ตัวอย่าง: $100, $100K, 100K, $1.5M
        pattern = r"\$?(\d+(?:\.\d+)?)\s*([KMkm]?)"
        matches = list(re.finditer(pattern, question))

        if not matches:
            return None

        # ใช้ match แรก (heuristic)
        m = matches[0]
        value = float(m.group(1))
        unit = m.group(2).upper()
        if unit == "K":
            value *= 1000
        elif unit == "M":
            value *= 1_000_000

        # แทนตัวเลขด้วย ___ เพื่อเทียบ template
        template = (
            question[:m.start()]
            + "___"
            + question[m.end():]
        )
        return template.strip().lower(), value

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty or markets.empty:
            return []

        # snapshot ล่าสุดของ outcome "Yes" ต่อ market
        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
        )
        yes_prices = latest[latest["outcome"].isin(["Yes", "yes", "YES"])]

        # merge with markets
        merged = yes_prices.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="inner",
        )

        # group by template (คำถามที่ตัวเลขต่างกัน)
        templates = {}  # template_str → list of (value, price, market_id, question)
        for _, row in merged.iterrows():
            extracted = self._extract_threshold(row.get("question", ""))
            if not extracted:
                continue
            template, value = extracted
            templates.setdefault(template, []).append({
                "value": value,
                "price": row["price"],
                "market_id": row["market_id"],
                "question": row["question"],
            })

        # หา violation ในแต่ละ template
        opportunities = []
        for template, entries in templates.items():
            if len(entries) < 2:
                continue

            # sort by threshold value (จากน้อยไปมาก)
            entries.sort(key=lambda e: e["value"])

            # ตรวจ ladder: ถ้า value สูงขึ้น แต่ price ก็สูงขึ้น = violation
            # (P(X > 100) ต้อง ≥ P(X > 110) เสมอ)
            for i in range(len(entries) - 1):
                low = entries[i]
                high = entries[i + 1]
                if high["price"] > low["price"]:
                    # violation!
                    edge = (high["price"] - low["price"]) * 100  # arb size %
                    opportunities.append(
                        Opportunity(
                            strategy_name=self.name,
                            market_ids=[low["market_id"], high["market_id"]],
                            description=(
                                f"P(>{high['value']:.0f}) = {high['price']:.3f} > "
                                f"P(>{low['value']:.0f}) = {low['price']:.3f}"
                            ),
                            edge_pct=edge,
                            is_profitable=edge > self.edge_threshold_pct,
                            metadata={
                                "low_market": low["question"],
                                "high_market": high["question"],
                                "low_price": low["price"],
                                "high_price": high["price"],
                                "template": template,
                            },
                        )
                    )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities
