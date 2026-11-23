"""Bot 7: trend-aligned EMA pullback continuation. PAPER signals only."""
from __future__ import annotations
import csv, json, os
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE = "https://biquote.io/api/{symbol}/ohlc"
PAIRS = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD",
"EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF",
"GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","NZDJPY",
"NZDCHF","NZDCAD","CADJPY","CHFJPY"]
MAX_ALERTS_DAY = 3
MAX_CANDLE_AGE_MIN = 5
MAX_ENTRY_DRIFT_PIPS = 2.0
RISK_FRACTION = 0.0025
RR = 1.5
SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"ForexBot7-Paper/1.0"})
STATE = HERE/"state.json"
LOG = HERE/"signals.csv"

def now():
    return pd.Timestamp.now(tz="UTC")

def pip(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001

def fetch(pair, interval, limit=500):
    r=SESSION.get(BASE.format(symbol=pair),params={"interval":interval,"limit":limit},timeout=20)
    r.raise_for_status()
    rows=[]
    for b in (r.json().get("bars") or []):
        if b.get("isOpen") is True: continue
        try:
            rows.append({"time":pd.to_datetime(b["openTime"],utc=True),
              "open":float(b["open"]),"high":float(b["high"]),
              "low":float(b["low"]),"close":float(b["close"])})
        except (KeyError,TypeError,ValueError): pass
    f=pd.DataFrame(rows)
    if f.empty: raise ValueError("No completed candles")
    f=f.set_index("time").sort_index()
    return f[~f.index.duplicated(keep="last")]

def freshness(f, mins, max_age):
    age=(now()-(f.index[-1]+pd.Timedelta(minutes=mins))).total_seconds()/60
    if age < -1 or age > max_age: raise ValueError(f"Stale candles ({age:.1f}m)")

def enrich(f):
    x=f.copy()
    for p in (9,21,50,200): x[f"ema{p}"]=x.close.ewm(span=p,adjust=False).mean()
    pc=x.close.shift()
    tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    x["atr"]=tr.rolling(14).mean()
    delta=x.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=gain/loss.replace(0,float("nan"))
    x["rsi"]=100-(100/(1+rs))
    # Wilder-style ADX approximation using smoothed directional movement.
    up=x.high.diff(); down=-x.low.diff()
    plus=up.where((up>down)&(up>0),0.0).ewm(alpha=1/14,adjust=False).mean()
    minus=down.where((down>up)&(down>0),0.0).ewm(alpha=1/14,adjust=False).mean()
    atr=tr.ewm(alpha=1/14,adjust=False).mean().replace(0,float("nan"))
    pdi=100*plus/atr; mdi=100*minus/atr
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,float("nan"))
    x["adx"]=dx.ewm(alpha=1/14,adjust=False).mean()
    return x

def swing_stop(m5, side, entry, buffer):
    w=m5.iloc[-35:-2]
    found=[]
    for i in range(2,len(w)-2):
        q=w.iloc[i]
        if side=="BUY" and q.low < min(w.iloc[i-2].low,w.iloc[i-1].low,w.iloc[i+1].low,w.iloc[i+2].low) and q.low<entry:
            found.append(float(q.low))
        if side=="SELL" and q.high > max(w.iloc[i-2].high,w.iloc[i-1].high,w.iloc[i+1].high,w.iloc[i+2].high) and q.high>entry:
            found.append(float(q.high))
    if not found: return None
    pivot=found[-1]
    return (pivot-buffer if side=="BUY" else pivot+buffer),pivot

def signal(m5,m15,h1,pair):
    a,b,c=enrich(m5),enrich(m15),enrich(h1)
    if len(a)<220 or len(b)<220 or len(c)<220: return None
    x,prev=a.iloc[-1],a.iloc[-2]; mt=b.iloc[-1]; ht=c.iloc[-1]
    vals=[x.atr,x.rsi,mt.adx]
    if any(pd.isna(v) for v in vals) or x.atr<=0: return None
    up=(ht.close>ht.ema200 and ht.ema50>ht.ema200 and ht.ema21>ht.ema50 and
        mt.close>mt.ema200 and mt.ema50>mt.ema200 and mt.ema21>mt.ema50 and mt.adx>=20)
    dn=(ht.close<ht.ema200 and ht.ema50<ht.ema200 and ht.ema21<ht.ema50 and
        mt.close<mt.ema200 and mt.ema50<mt.ema200 and mt.ema21<mt.ema50 and mt.adx>=20)
    side="BUY" if up else "SELL" if dn else None
    if not side: return None
    atr=float(x.atr)
    # Pullback must have touched the EMA21 band recently, then reclaim it.
    recent=a.iloc[-4:-1]
    band=0.20*atr
    if side=="BUY":
        touched=(recent.low <= recent.ema21+band).any()
        reclaimed=x.close>x.ema21 and x.close>prev.high and x.close>x.open
        momentum=50<=x.rsi<=68 and x.close-x.open>=0.45*atr
    else:
        touched=(recent.high >= recent.ema21-band).any()
        reclaimed=x.close<x.ema21 and x.close<prev.low and x.close<x.open
        momentum=32<=x.rsi<=50 and x.open-x.close>=0.45*atr
    # Reject stretched entries: signal close must remain near EMA21.
    if not touched or not reclaimed or not momentum or abs(x.close-x.ema21)>0.85*atr: return None
    entry=float(x.close)
    stopdata=swing_stop(a,side,entry,max(0.18*atr,2*pip(pair)))
    if not stopdata: return None
    stop,pivot=stopdata
    risk=(entry-stop) if side=="BUY" else (stop-entry)
    if risk < 0.75*atr or risk > 2.2*atr: return None
    target=entry+(RR*risk if side=="BUY" else -RR*risk)
    return {"side":side,"entry":entry,"sl":stop,"tp":target,"risk_pips":risk/pip(pair),
      "atr":atr,"pivot":pivot,"rsi":float(x.rsi),"adx_m15":float(mt.adx),
      "candle_time":a.index[-1].isoformat()}

def quote(pair):
    r=SESSION.get(f"https://biquote.io/api/{pair}",params={"allowStale":"false"},timeout=15)
    r.raise_for_status(); q=r.json()
    mid=float(q.get("mid") or 0); age=float(q.get("quoteAgeSeconds",999999))
    stamp=pd.to_datetime(q.get("timestamp"),utc=True,errors="coerce")
    if mid<=0 or pd.isna(stamp) or age>120 or q.get("stale") is True:
        raise ValueError("Live quote missing or stale")
    return mid,stamp,age

def load():
    if not STATE.exists(): return {"pending":[],"completed":[],"sent_today":{}}
    d=json.loads(STATE.read_text()); d.setdefault("pending",[]); d.setdefault("completed",[]); d.setdefault("sent_today",{})
    return d

def save(d):
    t=STATE.with_suffix(".tmp"); t.write_text(json.dumps(d,indent=2)); t.replace(STATE)

def log_trade(t):
    fields=["signal_time_utc","pair","direction","entry","stop_loss","take_profit","risk_pips","rr","outcome","outcome_note","source"]
    rows=[]
    if LOG.exists():
        with LOG.open(newline="") as f: rows=list(csv.DictReader(f))
    key=(t["signal_time_utc"],t["pair"])
    row={k:t.get(k,"") for k in fields}
    for i,z in enumerate(rows):
        if (z.get("signal_time_utc"),z.get("pair"))==key: rows[i]=row; break
    else: rows.append(row)
    tmp=LOG.with_suffix(".tmp")
    with tmp.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    tmp.replace(LOG)

def outcomes(d):
    keep=[]
    for t in d["pending"]:
        try: f=fetch(t["pair"],"5m",500)
        except Exception: keep.append(t); continue
        future=f.loc[f.index>pd.Timestamp(t["signal_time_utc"])]
        done=False
        for stamp,k in future.iloc[:288].iterrows():
            sl=(k.low<=t["stop_loss"]) if t["direction"]=="BUY" else (k.high>=t["stop_loss"])
            tp=(k.high>=t["take_profit"]) if t["direction"]=="BUY" else (k.low<=t["take_profit"])
            if sl and tp: result="AMBIGUOUS"
            elif sl: result="LOSS"
            elif tp: result="WIN"
            else: continue
            t.update(outcome=result,outcome_note=f"BiQuote completed M5 candle at {stamp.isoformat()}; same-candle double touch marked ambiguous")
            d["completed"].append(t); log_trade(t); done=True; break
        if not done and len(future)>=288:
            t.update(outcome="EXPIRED",outcome_note="No target/stop within 24h")
            d["completed"].append(t); log_trade(t); done=True
        if not done: keep.append(t)
    d["pending"]=keep

def telegram(msg):
    if not TOKEN or not CHAT_ID: raise RuntimeError("Telegram secrets missing")
    r=SESSION.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
      json={"chat_id":CHAT_ID,"text":msg,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"): raise RuntimeError("Telegram delivery not confirmed")

def scan():
    if (HERE/"STOP").exists():
        print("BOT 7 paused by STOP file"); return
    d=load(); outcomes(d); today=now().strftime("%Y-%m-%d")
    d["sent_today"]={k:v for k,v in d["sent_today"].items() if k==today}
    sent=0
    for pair in PAIRS:
        if d["sent_today"].get(today,0)+sent>=MAX_ALERTS_DAY: break
        if any(t["pair"]==pair for t in d["pending"]): continue
        try:
            m5,m15,h1=fetch(pair,"5m"),fetch(pair,"15m"),fetch(pair,"1h")
            freshness(m5,5,MAX_CANDLE_AGE_MIN); freshness(m15,15,25); freshness(h1,60,75)
            s=signal(m5,m15,h1,pair)
            if not s: continue
            mid,qt,age=quote(pair)
            close_time=pd.Timestamp(s["candle_time"])+pd.Timedelta(minutes=5)
            if qt<close_time: continue
            maxgap=min(MAX_ENTRY_DRIFT_PIPS*pip(pair),0.15*s["atr"])
            if abs(mid-s["entry"])>maxgap: continue
            t={"signal_time_utc":s["candle_time"],"pair":pair,"direction":s["side"],
               "entry":round(s["entry"],6),"stop_loss":round(s["sl"],6),"take_profit":round(s["tp"],6),
               "risk_pips":round(s["risk_pips"],1),"rr":RR,"outcome":"PENDING",
               "outcome_note":"","source":"BiQuote public OHLC/live tick; not broker verified"}
            msg=(f"🧪 BOT 7 PAPER TEST — {s['side']} {pair}\n"
              f"Entry: {t['entry']}\nSL: {t['stop_loss']}\nTP: {t['take_profit']}\n"
              f"Risk: {t['risk_pips']} pips | R:R 1:{RR}\n"
              f"M5 EMA21 pullback + confirmation | M15/H1 trend aligned\n"
              f"M15 ADX: {s['adx_m15']:.1f} | RSI: {s['rsi']:.1f}\n"
              f"Live quote: {mid} ({age:.0f}s old)\n"
              "PAPER ONLY — do not place a real trade. Risk cap after validation: 0.25% max per trade. BiQuote feed is not broker-verified.")
            telegram(msg); d["pending"].append(t); log_trade(t); sent+=1
        except Exception as e: print(f"{pair}: {type(e).__name__}: {e}",flush=True)
    d["sent_today"][today]=d["sent_today"].get(today,0)+sent
    d["last_scan_utc"]=now().isoformat(); save(d)
    print(f"Bot 7 paper scan complete; sent={sent}, pending={len(d['pending'])}, completed={len(d['completed'])}",flush=True)

if __name__=="__main__": scan()
