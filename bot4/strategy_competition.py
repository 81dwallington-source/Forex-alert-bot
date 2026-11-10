"""Bot 4 research runner; research only, never sends alerts."""
from __future__ import annotations
from bot3 import strategy_competition as lab

# Predeclared point-in-time filters; final-test outcomes are never used for selection.
lab.CANDIDATES.update({
    "h1_dominant_momentum": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.12 and
        s.get("directional_momentum_6_atr", 0) >= 0.20
    ),
    "h1_strong_pullback": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.40
    ),
    "breakout_with_room": lambda s: (
        s.get("breakout_margin_12_atr", 0) >= 0.03 and
        s.get("opposing_distance_24_atr", 0) >= 1.00 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
    "momentum_breakout": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_momentum_6_atr", 0) >= 0.25 and
        s.get("breakout_margin_12_atr", 0) >= 0.02
    ),
    "tight_pullback_strong_trend": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.12 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.08 and
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.30 and
        s.get("confirmation_body_ratio", 0) >= 0.60
    ),
    "balanced_confluence": lambda s: (
        s.get("h1_directional_trend_gap_atr", 0) >= 0.08 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.05 and
        s.get("directional_momentum_6_atr", 0) >= 0.15 and
        s.get("opposing_distance_24_atr", 0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.55
    ),
})

if __name__ == "__main__":
    lab.main()
