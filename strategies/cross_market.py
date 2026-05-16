"""
Cross-Market Consistency Strategy
หาตลาดที่เกี่ยวข้องกันแต่ราคาไม่ consistent

Example:
  Market A: "Will Trump win 2028?" → P = 0.55
  Market B: "Will Democrats win 2028?" → P = 0.50
  → Σ = 1.05 → arb แม้คนละตลาด

Logic:
  - หาตลาดที่ตั้งคำถามเกี่ยวข้องกัน (keyword matching)
  - คำนวณ Σ ของ probability ทุกตลาดในกลุ่ม
  - ถ้า Σ ≠ 1 ในกลุ่ม mutually exclusive → arb

⚠️ Caveat:
  - Heuristic keyword matching อาจ false positive
  - ต้อง verify ด้วยมือก่อนเทรด
"""

import re
from collections import defaultdict
from typing import List
import pandas as pd

from .base import BaseStrategy, Opportunity


class CrossMarket(BaseStrategy):

    DISCOVERY_THRESHOLD = 0.02
    SANITY_MAX_EDGE_PCT = 25.0   # edge > นี้ = false positive
    MIN_GROUP_SIZE = 2
    MAX_GROUP_SIZE = 4           # 5+ markets มัก match ผิด
    MIN_KEYWORD_OVERLAP = 3      # อย่างน้อย 3 keywords ต้องตรงกัน
    STOPWORDS = {
        "will", "the", "a", "an", "be", "in", "on", "at", "by", "to", "of",
        "is", "are", "was", "were", "and", "or", "for", "with", "this", "that",
        "before", "after", "win", "lose", "have", "has", "than", "more", "less",
    }

    @property
    def name(self) -> str:
        return "Cross-Market"

    @property
    def description(self) -> str:
        return "ตลาดที่ keyword คล้ายกัน ราคาขัดกัน — multi-market arb"

    def _keywords(self, question: str) -> frozenset:
        """ดึง keyword หลักจากคำถาม"""
        if not question:
            return frozenset()
        words = re.findall(r"[A-Za-z0-9]+", question.lower())
        # เก็บคำที่ยาว > 3 chars และไม่ใช่ stopword
        keywords = [w for w in words if len(w) > 3 and w not in self.STOPWORDS]
        return frozenset(keywords[:5])  # เก็บ 5 keyword หลัก

    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        if snapshots.empty or markets.empty:
            return []

        # snapshot ล่าสุดของ Yes outcome ต่อ market
        latest = (
            snapshots.sort_values("scanned_at")
            .groupby(["market_id", "outcome"])
            .tail(1)
        )
        yes_only = latest[latest["outcome"].isin(["Yes", "yes", "YES"])]

        merged = yes_only.merge(
            markets[["id", "question"]],
            left_on="market_id",
            right_on="id",
            how="inner",
        )

        # group by keyword set
        groups = defaultdict(list)
        for _, row in merged.iterrows():
            kws = self._keywords(row.get("question", ""))
            if len(kws) < self.MIN_KEYWORD_OVERLAP:
                continue
            # ใช้ top-3 keywords เป็น key (ลด combinatorial)
            key = frozenset(sorted(kws)[:3])
            groups[key].append({
                "market_id": row["market_id"],
                "question": row["question"],
                "price": row["price"],
            })

        # หา groups ที่มี 2+ markets และ Σ ผิด
        opportunities = []
        for key, entries in groups.items():
            if not (self.MIN_GROUP_SIZE <= len(entries) <= self.MAX_GROUP_SIZE):
                continue
            sum_p = sum(e["price"] for e in entries)
            deviation = abs(sum_p - 1.0)
            if deviation < self.DISCOVERY_THRESHOLD:
                continue

            edge_pct = deviation * 100

            # sanity check
            if edge_pct > self.SANITY_MAX_EDGE_PCT:
                continue

            descriptions = [
                f"{e['question'][:40]}.. ({e['price']:.2f})"
                for e in entries
            ]
            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    market_ids=[e["market_id"] for e in entries],
                    description=(
                        f"Σ={sum_p:.3f} ({len(entries)} markets): "
                        + " | ".join(descriptions[:2])
                    ),
                    edge_pct=edge_pct,
                    is_profitable=edge_pct > self.edge_threshold_pct,
                    metadata={
                        "sum_price": round(sum_p, 4),
                        "keywords": list(key),
                        "n_markets": len(entries),
                        "entries": entries[:5],
                    },
                )
            )

        opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
        return opportunities[:15]
