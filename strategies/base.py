"""
Base class สำหรับ strategies ทั้งหมด
ทุก strategy ต้อง inherit จาก BaseStrategy และ implement methods ต่อไปนี้
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List
import pandas as pd


@dataclass
class Opportunity:
    """1 opportunity ที่พบจาก strategy"""
    strategy_name: str
    market_ids: List[str]      # ตลาดที่เกี่ยวข้อง (อาจมี 1+ ตลาด)
    description: str           # บอกว่าเกิดอะไรขึ้น
    edge_pct: float            # edge เป็น % (positive)
    is_profitable: bool        # กำไรหลังหัก fee ไหม
    metadata: dict = None      # ข้อมูลเพิ่มเติม (Σ prices, prices ฯลฯ)


class BaseStrategy(ABC):
    """
    Base class — ทุก strategy ต้อง override:
    - name: ชื่อ
    - description: คำอธิบาย
    - detect(): หา opportunities

    fee + buffer ใช้ shared logic — ไม่ต้อง override
    """

    POLYMARKET_FEE_PCT = 2.0
    PROFIT_BUFFER_PCT = 1.0

    @property
    @abstractmethod
    def name(self) -> str:
        """ชื่อ strategy เช่น 'Two-Outcome Arb'"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """คำอธิบาย 1-2 บรรทัด"""
        pass

    @property
    def edge_threshold_pct(self) -> float:
        """Edge threshold ที่ถือว่ากำไรจริง"""
        return self.POLYMARKET_FEE_PCT + self.PROFIT_BUFFER_PCT

    @abstractmethod
    def detect(
        self,
        snapshots: pd.DataFrame,
        markets: pd.DataFrame,
    ) -> List[Opportunity]:
        """
        หา opportunities จาก data

        Args:
            snapshots: DataFrame มี columns [market_id, outcome, price, scanned_at]
            markets: DataFrame มี columns [id, question, end_date]

        Returns:
            List[Opportunity]
        """
        pass

    def summary(self, opportunities: List[Opportunity]) -> dict:
        """สรุปผลของ strategy นี้"""
        profitable = [o for o in opportunities if o.is_profitable]
        max_edge = max((o.edge_pct for o in opportunities), default=0)

        return {
            "name": self.name,
            "description": self.description,
            "total_discoveries": len(opportunities),
            "profitable_count": len(profitable),
            "max_edge_pct": max_edge,
            "edge_threshold_pct": self.edge_threshold_pct,
            "has_profitable": len(profitable) > 0,
            "opportunities": opportunities,
        }
