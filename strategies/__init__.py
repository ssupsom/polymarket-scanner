"""
Strategy registry — auto-load strategies ทั้งหมด
เพิ่ม strategy ใหม่ = สร้างไฟล์ใหม่ + import ใน list ข้างล่าง
"""

from .base import BaseStrategy, Opportunity
from .two_outcome_arb import TwoOutcomeArb
from .multi_outcome import MultiOutcomeArb
from .ladder import LadderViolation
from .velocity import Velocity
from .cross_market import CrossMarket
from .time_decay import TimeDecay
from .stale_price import StalePrice


ALL_STRATEGIES = [
    # Arb strategies — มี profitable threshold
    TwoOutcomeArb(),
    MultiOutcomeArb(),
    LadderViolation(),
    CrossMarket(),
    # Signal strategies — ใช้ดู watchlist
    Velocity(),
    TimeDecay(),
    StalePrice(),
]


def run_all_strategies(snapshots, markets):
    """รันทุก strategy แล้ว return list of summaries"""
    results = []
    for strategy in ALL_STRATEGIES:
        try:
            opportunities = strategy.detect(snapshots, markets)
            results.append(strategy.summary(opportunities))
        except Exception as e:
            results.append({
                "name": strategy.name,
                "description": strategy.description,
                "error": str(e),
                "total_discoveries": 0,
                "profitable_count": 0,
                "max_edge_pct": 0,
                "has_profitable": False,
                "opportunities": [],
            })
    return results


__all__ = [
    "BaseStrategy",
    "Opportunity",
    "ALL_STRATEGIES",
    "run_all_strategies",
]
