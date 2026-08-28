"""Final expense product tiers, ordered best (most favourable to the client) to worst.

Final expense whole life is sold in a small number of death-benefit structures.
Carriers use different marketing names for them, but they collapse to these:

``preferred``  Immediate (day-one) full death benefit at the carrier's best rate.
``level``      Immediate (day-one) full death benefit at standard rates.
``graded``     Reduced benefit in the early years, e.g. 30% / 70% / 100%.
``modified``   Return of premium plus interest in years 1-2, then full benefit.
``gi``         Guaranteed issue. No health questions, ROP + interest waiting period.
``decline``    Not eligible for any product from this carrier.

Everything in the engine compares tiers by ``rank``: a higher rank is a worse
outcome, so combining two findings is always ``max(rank)`` -- the worst finding
wins, which is how underwriting actually works.
"""

from __future__ import annotations

from enum import Enum


class Tier(Enum):
    PREFERRED = ("preferred", 0, "Preferred / Level Plus")
    LEVEL = ("level", 1, "Level (day-one full benefit)")
    GRADED = ("graded", 2, "Graded benefit")
    MODIFIED = ("modified", 3, "Modified benefit")
    GI = ("gi", 4, "Guaranteed issue")
    DECLINE = ("decline", 5, "Decline")

    def __init__(self, key: str, rank: int, label: str) -> None:
        self.key = key
        self.rank = rank
        self.label = label

    def __lt__(self, other: "Tier") -> bool:
        return self.rank < other.rank

    @classmethod
    def parse(cls, value) -> "Tier":
        if isinstance(value, cls):
            return value
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "guaranteed_issue": "gi",
            "guaranteed": "gi",
            "immediate": "level",
            "standard": "level",
            "day_one": "level",
            "declined": "decline",
            "deny": "decline",
            "knockout": "decline",
            "uninsurable": "decline",
        }
        key = aliases.get(key, key)
        for tier in cls:
            if tier.key == key:
                return tier
        raise ValueError(f"unknown tier: {value!r}")


def worst(*tiers: Tier) -> Tier:
    """Return the least favourable of the given tiers."""
    return max(tiers, key=lambda t: t.rank)


def best(*tiers: Tier) -> Tier:
    """Return the most favourable of the given tiers."""
    return min(tiers, key=lambda t: t.rank)


#: Tiers that still pay a full benefit from day one.
IMMEDIATE_BENEFIT = (Tier.PREFERRED, Tier.LEVEL)

#: Tiers that represent an actual offer of coverage.
ISSUABLE = (Tier.PREFERRED, Tier.LEVEL, Tier.GRADED, Tier.MODIFIED, Tier.GI)
