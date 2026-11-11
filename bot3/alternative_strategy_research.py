"""Independent M5 trend-pullback breakout hypothesis. Research only; never sends alerts."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from bot3 import bot3
from bot3.backtest import load_m5, backtest_pair, prepare_backtest_context

SPREADS = {
    "EURUSD":1.2,"GBPUSD":1.5,"USDJPY":1.3,"USDCHF":1.5,"USDCAD":1.6,
    "AUDUSD":1.4,"NZDUSD":1.8,"EURGBP":1.5,"EURJPY":2.0,"EURCHF":2.0,
    "EURAUD":2.5,"EURCAD":2.5,"EURNZD":3.0,"GBPJPY":2.5,"GBPCHF":2.5,
    "GBPAUD":3.0,"GBPCAD":3.0,"GBPNZD":3.5,"AUDJPY":2.0,"AUDCHF":2.0,
    "AUDCAD":2.0,"AUDNZD":2.5,"NZDJPY":2.5,"NZDCHF":2.5,"NZDCAD":2.5,
    "CADJPY":2.0,"CHFJPY":2.5,
}
RR_VALUES = (1.0, 1.25)

def build_signal(pair,m5,m15,h1,live_price=None,live_stamp=None,live_bid=None,live_ask=None,now=None):
    """Breakout-after-pullback entry, with mandatory M15/H1 agreement."""
    if len(m5)<60 or len(m15)<60 or len(h1)<60:
        return None
    direction=bot3.trend_direction(h1)
    if not direction or bot3.trend_direction(m15)!=direction:
        return None
    d=bot3.add_indicators(m5)
    m15d=bot3.add_indicators(m15); h1d=bot3.add_indicators(h1)
    x=d.iloc[-1]; prev=d.iloc[-2]
    atr=float(x.ATR)
    if pd.isna(atr) or atr<=0 or float(x.High-x.Low)>2.0*atr:
        return None
    # Require the M5 fast/slow trend to agree with both higher timeframes.
    if direction=="BUY" and not (x.Close>x.EMA21>x.EMA50): return None
    if direction=="SELL" and not (x.Close<x.EMA21<x.EMA50): return None
    # A recent, shallow EMA21 retest must precede the trigger candle.
    pull=None
    for j in range(len(d)-2,max(1,len(d)-6),-1):
        c=d.iloc[j]; a=float(c.ATR) if pd.notna(c.ATR) else 0
        if a<=0: continue
        if direction=="BUY":
            touched=c.Low<=c.EMA21+0.10*a and c.Low>=c.EMA21-0.35*a and c.Close>=c.EMA50
        else:
            touched=c.High>=c.EMA21-0.10*a and c.High<=c.EMA21+0.35*a and c.Close<=c.EMA50
        if touched: pull=j; break
    if pull is None: return None
    # Break the preceding three-bar micro-range after the pullback.
    prior=d.iloc[-4:-1]
    rng=float(x.High-x.Low); body=abs(float(x.Close-x.Open))
    if rng<=0 or body/rng<0.55: return None
    loc=(float(x.Close)-float(x.Low))/rng
    if direction=="BUY":
        if not (x.Close>x.Open and x.Close>prior.High.max() and loc>=0.72): return None
        piv=bot3.confirmed_pivots(d.iloc[:-1],"low",30)
        if not piv: return None
        swing=piv[-1][1]; entry=float(x.Close); stop=swing-0.15*atr
        risk=entry-stop
    else:
        if not (x.Close<x.Open and x.Close<prior.Low.min() and loc<=0.28): return None
        piv=bot3.confirmed_pivots(d.iloc[:-1],"high",30)
        if not piv: return None
        swing=piv[-1][1]; entry=float(x.Close); stop=swing+0.15*atr
        risk=stop-entry
    # Reject implausibly tight and excessively wide structural stops.
    if risk<0.45*atr or risk>2.75*atr: return None
    if live_price is None or live_stamp is None or live_bid is None or live_ask is None: return None
    bid,ask,mid=float(live_bid),float(live_ask),float(live_price)
    if bid<=0 or ask<=bid or not bid<=mid<=ask: return None
    spread=ask-bid
    if spread>0.15*atr or abs(mid-entry)>0.20*atr: return None
    stamp=pd.Timestamp(live_stamp)
    if stamp.tzinfo is None: stamp=stamp.tz_localize("UTC")
    now_ts=pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    if now_ts.tzinfo is None: now_ts=now_ts.tz_localize("UTC")
    age=(now_ts.tz_convert("UTC")-stamp.tz_convert("UTC")).total_seconds()
    if age < -60 or age>120: return None
    rr=float(bot3.MIN_RR)
    target=entry+risk*rr if direction=="BUY" else entry-risk*rr
    m15x=m15d.iloc[-1]; h1x=h1d.iloc[-1]
    return {"pair":pair,"direction":direction,"entry":entry,"stop":stop,"target":target,
      "rr":rr,"atr":atr,"swing":swing,"candle_time":m5.index[-1].isoformat(),
      "live_price":mid,"live_bid":bid,"live_ask":ask,"spread":spread,
      "quote_time":stamp.isoformat(),"data_source":"Dukascopy historical BID OHLC",
      "m5_ema21_slope_atr":float((x.EMA21-d.iloc[-4].EMA21)/atr),
      "m5_directional_ema21_slope_atr":float((x.EMA21-d.iloc[-4].EMA21)/atr*(1 if direction=="BUY" else -1)),
      "m15_ema20_50_gap_atr":float(abs(m15x.EMA20-m15x.EMA50)/m15x.ATR),
      "h1_ema20_50_gap_atr":float(abs(h1x.EMA20-h1x.EMA50)/h1x.ATR),
      "m15_directional_trend_gap_atr":float((m15x.EMA20-m15x.EMA50)/m15x.ATR*(1 if direction=="BUY" else -1)),
      "h1_directional_trend_gap_atr":float((h1x.EMA20-h1x.EMA50)/h1x.ATR*(1 if direction=="BUY" else -1)),
      "confirmation_body_ratio":body/rng,"confirmation_close_location":loc,
      "directional_momentum_3_atr":float((x.Close-d.iloc[-4].Close)/atr*(1 if direction=="BUY" else -1)),
      "directional_momentum_6_atr":float((x.Close-d.iloc[-7].Close)/atr*(1 if direction=="BUY" else -1)),
      "directional_bar_fraction_6":float((((d.Close.iloc[-6:].to_numpy()-d.Open.iloc[-6:].to_numpy())*(1 if direction=="BUY" else -1))>0).mean()),
      "opposing_distance_24_atr":float(max(0,((d.High.iloc[-25:-1].max()-x.Close) if direction=="BUY" else (x.Close-d.Low.iloc[-25:-1].min()))/atr)),
      "pullback_depth_atr":float(((d.iloc[pull].EMA21-d.iloc[pull].Low) if direction=="BUY" else (d.iloc[pull].High-d.iloc[pull].EMA21))/d.iloc[pull].ATR),
      "pullback_age_bars":int(len(d)-1-pull)}

def score(rows):
    closed=[r for r in rows if r["outcome"] in ("WIN","LOSS") and r["result_R"] is not None]
    wins=sum(r["outcome"]=="WIN" for r in closed)
    rs=[float(r["result_R"]) for r in closed]
    gross_win=sum(x for x in rs if x>0); gross_loss=-sum(x for x in rs if x<0)
    return {"trades":len(closed),"wins":wins,"losses":len(closed)-wins,
      "win_rate_pct":round(100*wins/len(closed),2) if closed else 0.0,
      "net_R":round(sum(rs),2),"profit_factor":round(gross_win/gross_loss,3) if gross_loss else None}

def run(pair,data_dir,out_dir):
    data=load_m5(data_dir/f"{pair}_M5.csv")
    if len(data)<30000: raise ValueError(f"{pair}: insufficient history ({len(data)})")
    n=len(data); cuts={"TRAIN":(0,int(n*.60)),"VALIDATION":(int(n*.60),int(n*.80)),"FINAL_TEST":(int(n*.80),n)}
    records=[]
    original=bot3.build_signal; original_rr=bot3.MIN_RR
    try:
      for period,(start,end) in cuts.items():
        frame=data.iloc[max(0,start-1000):end].copy()
        context=prepare_backtest_context(frame); period_start=data.index[start]
        for rr in RR_VALUES:
          bot3.build_signal=build_signal; bot3.MIN_RR=rr
          rows=backtest_pair(pair,frame,SPREADS[pair],0.2,prepared=context)
          rows=[r for r in rows if pd.Timestamp(r["signal_time_utc"])>=period_start]
          records.append({"pair":pair,"period":period,"rr":rr,"spread_pips":SPREADS[pair],
             "slippage_pips_per_fill":0.2,**score(rows)})
          print(pair,period,"RR",rr,score(rows),flush=True)
    finally:
      bot3.build_signal=original; bot3.MIN_RR=original_rr
    return records

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("research-data"))
    p.add_argument("--out-dir",type=Path,default=Path("research-results/alternative"))
    p.add_argument("--pairs",default=",".join(SPREADS))
    a=p.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    from concurrent.futures import ProcessPoolExecutor
    pairs=[x.strip().upper() for x in a.pairs.split(",") if x.strip()]
    with ProcessPoolExecutor(max_workers=2) as pool:
      results=[]
      jobs=[pool.submit(run,pair,a.data_dir,a.out_dir) for pair in pairs]
      for job in jobs: results.extend(job.result())
    df=pd.DataFrame(results); df.to_csv(a.out_dir/"alternative_results.csv",index=False)
    val=df[df.period=="VALIDATION"].groupby("rr").agg(trades=("trades","sum"),wins=("wins","sum"),losses=("losses","sum"),net_R=("net_R","sum"),profitable_pairs=("net_R",lambda s:int((s>0).sum())),pairs=("pair","nunique")).reset_index()
    val["win_rate_pct"]=100*val.wins/(val.wins+val.losses).replace(0,pd.NA)
    val.to_csv(a.out_dir/"validation_summary.csv",index=False)
    eligible=val[(val.trades>=50)&(val.win_rate_pct>=60)&(val.net_R>0)&(val.profitable_pairs>=((val.pairs*.60).apply(lambda x:int(x+0.999))))]
    if eligible.empty: raise SystemExit("ALTERNATIVE STRATEGY FAILED VALIDATION; no live deployment")
    winner=eligible.sort_values(["net_R","win_rate_pct"],ascending=False).iloc[0]
    final=df[(df.period=="FINAL_TEST")&(df.rr==float(winner.rr))]
    total=int(final.trades.sum()); wins=int(final.wins.sum()); net=float(final.net_R.sum())
    winrate=100*wins/total if total else 0
    if total<30 or winrate<60 or net<=0 or int((final.net_R>0).sum())<max(1,int(len(final)*.60+0.999)):
      raise SystemExit(f"LOCKED FINAL TEST FAILED: {total} trades, {winrate:.2f}% wins, {net:.2f}R")
    print(f"PASS RESEARCH ONLY: RR {winner.rr}; final {total} trades, {winrate:.2f}% wins, {net:.2f}R",flush=True)

if __name__=="__main__": main()
