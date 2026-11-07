#!/usr/bin/env python3
"""Honest historical tester for the live Bot 3 rules.

Input files: DATA_DIR/EURUSD_M5.csv, EURUSD_M15.csv, EURUSD_H1.csv, etc.
Each CSV must contain a UTC timestamp column (timestamp/time/datetime) and
Open, High, Low, Close columns. Timestamps are candle OPEN times.

The tester calls Forex_alert.analyse_pair() on rolling, completed-candle
windows. Entry is the next M5 candle open (not the signal close). It applies
an adverse half-spread at entry and exit. If SL and TP both trade in one
candle, it records a conservative SL. It never downloads data or sends Telegram.
"""
import argparse
import csv
from pathlib import Path
from collections import Counter
import pandas as pd
import Forex_alert as bot

def load_csv(path, duration):
    if not path.exists():
        raise FileNotFoundError(f"Missing historical data file: {path}")
    df = pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    time_col = next((lower[k] for k in ("timestamp", "time", "datetime", "date") if k in lower), None)
    if not time_col:
        raise ValueError(f"{path}: needs timestamp/time/datetime/date column")
    rename = {}
    for name in ("Open", "High", "Low", "Close"):
        if name.lower() not in lower:
            raise ValueError(f"{path}: missing {name} column")
        rename[lower[name.lower()]] = name
    df = df.rename(columns=rename)
    raw_time = df[time_col]
    if pd.api.types.is_numeric_dtype(raw_time):
        # Dukascopy CSV exports use Unix epoch milliseconds.
        df.index = pd.to_datetime(raw_time, unit="ms", utc=True, errors="raise")
    else:
        df.index = pd.to_datetime(raw_time, utc=True, errors="raise")
    df = df[["Open", "High", "Low", "Close"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if len(df) < 100:
        raise ValueError(f"{path}: only {len(df)} candles; at least 100 required")
    if (df[["Open","High","Low","Close"]] <= 0).any().any():
        raise ValueError(f"{path}: non-positive OHLC values")
    if ((df.High < df[["Open","Close","Low"]].max(axis=1)) |
        (df.Low > df[["Open","Close","High"]].min(axis=1))).any():
        raise ValueError(f"{path}: malformed OHLC candles")
    # Require a regular timeframe; missing candles are allowed only as market closures.
    gaps = df.index.to_series().diff().dropna()
    if (gaps < pd.Timedelta(minutes=duration)).any():
        raise ValueError(f"{path}: duplicate/overlapping candle timestamps")
    return df

def metrics(trades):
    n=len(trades)
    if not n:
        return {"trades":0,"wins":0,"losses":0,"ambiguous":0,"win_rate":0.0,"avg_r":0.0,"expectancy_r":0.0,"max_dd_r":0.0}
    wins=sum(t["result"]=="WIN" for t in trades)
    losses=sum(t["result"]=="LOSS" for t in trades)
    ambiguous=sum(t["result"]=="AMBIGUOUS_AS_LOSS" for t in trades)
    rs=[t["r"] for t in trades]
    equity=peak=dd=0.0
    for value in rs:
        equity+=value; peak=max(peak,equity); dd=max(dd,peak-equity)
    return {"trades":n,"wins":wins,"losses":losses,"ambiguous":ambiguous,
            "win_rate":round(wins/n*100,2),"avg_r":round(sum(rs)/n,3),
            "expectancy_r":round(sum(rs)/n,3),"max_dd_r":round(dd,2)}

def run_pair(pair, data_dir, spread_pips, max_bars):
    stem=pair.replace("/","").upper()
    m5=load_csv(data_dir/f"{stem}_M5.csv",5)
    m15=load_csv(data_dir/f"{stem}_M15.csv",15)
    h1=load_csv(data_dir/f"{stem}_H1.csv",60)
    if max_bars:
        m5=m5.iloc[-max_bars:]
    pip=0.01 if stem.endswith("JPY") else 0.0001
    half_spread=spread_pips*pip/2
    original_download=bot.download_data
    original_fresh=bot.candle_is_fresh
    results=[]
    last_day_by_pair=None
    next_available_index=260
    try:
        for i in range(260, len(m5)-2):
            if i < next_available_index:
                continue
            signal_open=m5.index[i]
            signal_close=signal_open+pd.Timedelta(minutes=5)
            # Only HTF bars whose full candle close was known at signal time.
            h15=m15.loc[m15.index+pd.Timedelta(minutes=15)<=signal_close]
            hh1=h1.loc[h1.index+pd.Timedelta(minutes=60)<=signal_close]
            if len(h15)<60 or len(hh1)<60:
                continue
            day=signal_close.date()
            if day==last_day_by_pair:
                continue
            windows={
                "5m":m5.iloc[max(0,i-499):i+1],
                "15m":h15.iloc[-500:],
                "1h":hh1.iloc[-500:],
            }
            def fake_download(ticker, interval, period):
                return windows[interval].copy()
            bot.download_data=fake_download
            bot.candle_is_fresh=lambda data,*args,**kwargs:(True,0.0)
            signal, reason=bot.analyse_pair(stem+"=X")
            if not signal:
                continue
            # Alert is formed at the signal close; simulated fill is next candle open.
            nxt=m5.iloc[i+1]
            raw_entry=float(nxt.Open)
            shift=raw_entry-float(signal["entry"])
            direction=signal["direction"]
            entry=raw_entry+(half_spread if direction=="BUY" else -half_spread)
            stop=float(signal["stop"])+shift
            target=float(signal["target"])+shift
            risk=(entry-(stop-half_spread)) if direction=="BUY" else ((stop+half_spread)-entry)
            if risk<=0:
                continue
            outcome=None; exit_r=None
            for j in range(i+1,len(m5)):
                bar=m5.iloc[j]
                if direction=="BUY":
                    hit_sl=float(bar.Low)<=stop+half_spread
                    hit_tp=float(bar.High)>=target+half_spread
                else:
                    hit_sl=float(bar.High)>=stop-half_spread
                    hit_tp=float(bar.Low)<=target-half_spread
                if hit_sl and hit_tp:
                    outcome="AMBIGUOUS_AS_LOSS"; exit_r=-1.0; break
                if hit_sl:
                    outcome="LOSS"; exit_r=-1.0; break
                if hit_tp:
                    outcome="WIN"
                    reward = ((target-half_spread)-entry) if direction=="BUY" else (entry-(target+half_spread))
                    exit_r=reward/risk
                    break
            if outcome is None:
                continue
            # Adverse spread is charged on both sides via entry offset and exit offset.
            # Planned SL/TP levels are shifted with next-open fill; R is price-distance based.
            results.append({"time":signal_close.isoformat(),"pair":stem,"direction":direction,
                            "setup":signal["setup_type"],"result":outcome,"r":round(exit_r,4)})
            last_day_by_pair=day
            next_available_index=j+1
    finally:
        bot.download_data=original_download
        bot.candle_is_fresh=original_fresh
    return results

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",default="historical_data")
    p.add_argument("--pairs",default="EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,AUD/USD,NZD/USD")
    p.add_argument("--spread-pips",type=float,default=1.2,help="assumed full spread in pips; replace with broker/session-specific values")
    p.add_argument("--max-bars",type=int,default=0,help="optional most-recent M5 candle limit per pair")
    args=p.parse_args()
    if args.spread_pips<0: p.error("--spread-pips cannot be negative")
    all_trades=[]
    for pair in [x.strip().upper() for x in args.pairs.split(",") if x.strip()]:
        try:
            trades=run_pair(pair,args.data_dir,args.spread_pips,args.max_bars)
            all_trades.extend(trades)
            print(f"{pair}: {len(trades)} resolved trades")
        except Exception as exc:
            print(f"{pair}: NOT TESTED — {exc}")
    all_trades.sort(key=lambda x:x["time"])
    if not all_trades:
        print("NO PERFORMANCE CLAIM: no resolved trades. Supply valid historical M5/M15/H1 CSV exports.")
        return
    split=max(1,int(len(all_trades)*0.70))
    train=all_trades[:split]; test=all_trades[split:]
    print("\nBOT 3 HISTORICAL RESULTS — NOT BROKER-VERIFIED")
    print("Assumptions: next-M5-open entry; configured constant spread; ambiguous same-bar SL/TP counted as loss; no commissions/swaps.")
    print(f"All: {metrics(all_trades)}")
    print(f"Chronological first 70% (development): {metrics(train)}")
    print(f"Final 30% (out-of-sample): {metrics(test)}")
    out=Path("backtest_trades.csv")
    with out.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=["time","pair","direction","setup","result","r"])
        writer.writeheader(); writer.writerows(all_trades)
    print(f"Trade-level results saved to {out}")

if __name__=="__main__":
    main()
