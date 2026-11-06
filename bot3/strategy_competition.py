"""First-pass Bot 3 strategy competition: predeclared filters, chronological OOS, no live alerts."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from bot3 import bot3
from bot3.backtest import load_m5, backtest_pair

# These are hypotheses, not claimed optimised settings. All candidates share
# the same causal signal engine and execution model; only predeclared filters
# and reward:risk are varied in this first competition.
CANDIDATES = {
    "baseline": lambda s: True,
    "momentum_confirmed": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_momentum_6_atr", 0) >= 0.25 and
        s.get("directional_bar_fraction_6", 0) >= 0.50
    ),
    "strong_htf_trend": lambda s: (
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10
    ),
    "clean_confirmation": lambda s: (
        s.get("confirmation_body_ratio", 0) >= 0.65 and
        s.get("confirmation_close_location", 0) >= 0.80 if s.get("direction") == "BUY"
        else s.get("confirmation_body_ratio", 0) >= 0.65 and
        s.get("confirmation_close_location", 1) <= 0.20
    ),
    "room_to_target": lambda s: s.get("opposing_distance_24_atr", 0) >= 0.80,
    "fresh_pullback": lambda s: (
        1 <= s.get("pullback_age_bars", 99) <= 3 and
        0.05 <= s.get("pullback_depth_atr", -1) <= 0.45
    ),
    "combined_quality": lambda s: (
        s.get("directional_momentum_3_atr", 0) >= 0.15 and
        s.get("directional_bar_fraction_6", 0) >= 0.50 and
        s.get("m15_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("h1_directional_trend_gap_atr", 0) >= 0.10 and
        s.get("opposing_distance_24_atr", 0) >= 0.80 and
        s.get("confirmation_body_ratio", 0) >= 0.60
    ),
}

def metrics(rows):
    closed=[r for r in rows if r.get("outcome") in ("WIN","LOSS") and r.get("result_R") is not None]
    rs=[float(r["result_R"]) for r in closed]
    wins=sum(r["outcome"]=="WIN" for r in closed)
    gross_win=sum(x for x in rs if x>0)
    gross_loss=-sum(x for x in rs if x<0)
    eq=peak=dd=0.0
    for x in rs:
        eq+=x; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {"trades":len(closed),"wins":wins,"losses":len(closed)-wins,
      "win_rate_pct":round(100*wins/len(closed),2) if closed else None,
      "net_R":round(sum(rs),2),"profit_factor":round(gross_win/gross_loss,2) if gross_loss else None,
      "max_drawdown_R":round(dd,2),"ambiguous":sum(r.get("outcome")=="AMBIGUOUS" for r in rows)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir",type=Path,default=Path("research-data"))
    ap.add_argument("--out-dir",type=Path,default=Path("research-results/competition"))
    ap.add_argument("--pairs",default="EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD")
    args=ap.parse_args(); args.out_dir.mkdir(parents=True,exist_ok=True)
    original=bot3.build_signal
    report=[]
    for pair in args.pairs.split(","):
        pair=pair.strip().upper()
        path=args.data_dir/f"{pair}_M5.csv"
        if not path.exists():
            print(f"SKIP {pair}: missing {path}"); continue
        data=load_m5(path)
        split=int(len(data)*0.70)
        split_time=data.index[split]
        # OOS includes a 1,000-bar indicator warm-up; only trades signalled
        # at/after the fixed chronological boundary are scored.
        periods={"IS":data.iloc[:split],"OOS":data.iloc[max(0,split-1000):]}
        spread=1.2 if pair=="EURUSD" else 1.5 if pair=="GBPUSD" else 1.3 if pair=="USDJPY" else 1.5
        if pair not in ("EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD"):
            spread=2.0
        for period,frame in periods.items():
            for name,rule in CANDIDATES.items():
                for rr in (1.0,1.5):
                    def filtered(*a,_rule=rule,**kw):
                        s=original(*a,**kw)
                        return s if s and _rule(s) else None
                    bot3.build_signal=filtered
                    bot3.MIN_RR=rr
                    rows=backtest_pair(pair,frame.copy(),spread,0.2)
                    if period=="OOS":
                        rows=[r for r in rows if pd.Timestamp(r["signal_time_utc"])>=split_time]
                    m=metrics(rows)
                    report.append({"pair":pair,"period":period,"candidate":name,"rr":rr,
                      "spread_pips":spread,"slippage_pips_per_fill":0.2,
                      "period_start":str(frame.index[0]),"period_end":str(frame.index[-1]),
                      "split_time":str(split_time),**m})
                    print(pair,period,name,"RR",rr,m)
    bot3.build_signal=original
    bot3.MIN_RR=1.0
    out=pd.DataFrame(report)
    out.to_csv(args.out_dir/"candidate_results.csv",index=False)
    if not out.empty:
        grouped=out.groupby(["period","candidate","rr"],dropna=False).agg(
          pairs=("pair","nunique"),trades=("trades","sum"),wins=("wins","sum"),
          losses=("losses","sum"),net_R=("net_R","sum")).reset_index()
        grouped["win_rate_pct"]=100*grouped.wins/(grouped.wins+grouped.losses).replace(0,pd.NA)
        grouped.to_csv(args.out_dir/"candidate_summary.csv",index=False)
    print("Competition complete. OOS is chronological holdout; candidates with tiny samples are not eligible.")
if __name__=="__main__": main()
