"""Walk-forward backtest for the fresh strategy; no Telegram/live execution."""
from __future__ import annotations
import argparse, csv
from pathlib import Path
import pandas as pd
from strategy import make_signal


def load(path):
    d = pd.read_csv(path)
    d.columns = [str(c).lower() for c in d.columns]
    t = next((c for c in ("timestamp","time","datetime","date") if c in d), None)
    if not t: raise ValueError(f"{path}: missing timestamp")
    idx = pd.to_datetime(d[t], unit="ms", utc=True) if pd.api.types.is_numeric_dtype(d[t]) else pd.to_datetime(d[t], utc=True)
    d.index = idx
    for c in ("open","high","low","close"):
        if c not in d: raise ValueError(f"{path}: missing {c}")
        d[c] = pd.to_numeric(d[c], errors="raise")
    d = d[["open","high","low","close"]].sort_index()
    if len(d) < 500 or d.index.has_duplicates or (d.high < d[["open","close","low"]].max(axis=1)).any() or (d.low > d[["open","close","high"]].min(axis=1)).any():
        raise ValueError(f"{path}: insufficient or invalid OHLC history")
    return d


def evaluate(pair, folder, spread):
    stem=pair.replace("/","").upper()
    m5,m15,h1=[load(Path(folder)/f"{stem}_{tf}.csv") for tf in ("M5","M15","H1")]
    pip=0.01 if stem.endswith("JPY") else 0.0001
    out=[]; next_i=210
    # Iterate completed M5 candles. Higher-timeframe frames are clipped to bars
    # whose close time is <= current M5 close, preventing look-ahead.
    for i in range(210,len(m5)-2):
        if i < next_i: continue
        close_time=m5.index[i]+pd.Timedelta(minutes=5)
        x15=m15.loc[m15.index+pd.Timedelta(minutes=15)<=close_time]
        x1=h1.loc[h1.index+pd.Timedelta(hours=1)<=close_time]
        if len(x15)<210 or len(x1)<210: continue
        sig=make_signal(m5.iloc[:i+1],x15,x1,pip)
        if not sig: continue
        nxt=m5.iloc[i+1]
        entry=float(nxt.open)+(spread*pip if sig["direction"]=="BUY" else 0)
        shift=float(nxt.open)-sig["signal_close"]
        sl=sig["stop"]+shift; tp=sig["target"]+shift
        risk=(entry-sl) if sig["direction"]=="BUY" else (sl+spread*pip-entry)
        if risk<=0: continue
        result=None; r=None
        for j in range(i+1,len(m5)):
            bar=m5.iloc[j]
            if sig["direction"]=="BUY":
                hit_sl=bar.low<=sl; hit_tp=bar.high>=tp
            else:
                hit_sl=bar.high+spread*pip>=sl; hit_tp=bar.low+spread*pip<=tp
            if hit_sl and hit_tp: result,r="LOSS",-1.0; break
            if hit_sl: result,r="LOSS",-1.0; break
            if hit_tp:
                reward=(tp-entry) if sig["direction"]=="BUY" else entry-(tp-spread*pip)
                result,r="WIN",reward/risk; break
        if result:
            out.append({"time":close_time.isoformat(),"pair":stem,"side":sig["direction"],"result":result,"r":round(r,3),"stop_pips":sig["risk_pips"]})
            next_i=j+1
    return out


def stats(rows):
    if not rows:return {"trades":0,"win_rate_pct":0,"expectancy_R":0,"max_drawdown_R":0}
    eq=peak=dd=0
    for t in rows:
        eq+=t["r"]; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {"trades":len(rows),"wins":sum(t["result"]=="WIN" for t in rows),"losses":sum(t["result"]=="LOSS" for t in rows),
            "win_rate_pct":round(100*sum(t["result"]=="WIN" for t in rows)/len(rows),2),
            "expectancy_R":round(sum(t["r"] for t in rows)/len(rows),3),"max_drawdown_R":round(dd,2)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",default="historical_data")
    p.add_argument("--pairs",default="EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,AUD/USD,NZD/USD")
    p.add_argument("--spread-pips",type=float,default=1.2)
    args=p.parse_args()
    if args.spread_pips<0: raise SystemExit("Spread cannot be negative")
    trades=[]
    for pair in args.pairs.split(","):
        pair=pair.strip().upper()
        rows=evaluate(pair,args.data_dir,args.spread_pips)
        print(pair,stats(rows))
        trades.extend(rows)
    trades.sort(key=lambda x:x["time"])
    if not trades: raise SystemExit("INCONCLUSIVE: no resolved trades; no win rate claimed.")
    cut=int(len(trades)*0.70)
    dev,hold=trades[:cut],trades[cut:]
    print("DEVELOPMENT",stats(dev))
    print("UNTOUCHED OUT-OF-SAMPLE",stats(hold))
    if len(hold)<30: raise SystemExit("INCONCLUSIVE: fewer than 30 out-of-sample trades.")
    with open("best_bot_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=trades[0].keys());w.writeheader();w.writerows(trades)

if __name__=="__main__": main()
