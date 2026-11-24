from pathlib import Path
import pandas as pd
import pytest
from backtest import load_bars, stats


def test_stats_reports_correct_win_rate_and_expectancy():
    rows = [{"result": "WIN", "r": 1.5}, {"result": "LOSS", "r": -1.0}]
    result = stats(rows)
    assert result["trades"] == 2
    assert result["win_rate_pct"] == 50.0
    assert result["expectancy_R"] == 0.25


def test_stats_empty_is_inconclusive_shape():
    assert stats([])["trades"] == 0


def test_loader_rejects_invalid_ohlc(tmp_path: Path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=500, freq="5min", tz="UTC"),
        "open": [1.0] * 500, "high": [0.9] * 500,
        "low": [0.8] * 500, "close": [0.85] * 500,
    }).to_csv(path, index=False)
    with pytest.raises(ValueError, match="invalid OHLC"):
        load_bars(path, 5)
