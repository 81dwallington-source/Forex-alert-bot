"""Bot 1: H1 impulse pullback + M5 mini break of structure. Signal-only."""
from __future__ import annotations
import csv, json, os
from pathlib import Path
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE = "https://biquote.io/api/{symbol}/ohlc"
PAIRS = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD",
"EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF",
"GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD",
"NZDJPY","NZDCHF","NZDCAD","CADJPY","CADCHF","CHFJPY"]
RR = 1.5
SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"ForexBot1-H1Pullback-M5BOS/1.0"})
STATE_FILE, LOG_FILE = HERE/"state.json", HERE/"signals.csv"

def utc_now():
    return pd.Timestamp.now(tz="UTC")

def pip_size(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001

def fetch_bars(pair, interval, limit=300):
    r = SESSION.get(BASE.format(symbol=pair), params={"interval":interval,"limit":limit}, timeout=20)
    r.raise_for_status()
    rows = []
    for b in r.json().get("bars") or []:
        if b.get("isOpen") is True:
            continue
        try:
            rows.append({"time":pd.to_datetime(b["openTime"],utc=True),"open":float(b["open"]),
                         "high":float(b["high"]),"low":float(b["low"]),"close":float(b["close"])})
        except (KeyError,TypeError,ValueError):
            continue
    if not rows:
        raise ValueError(f"No completed {interval} candles returned")
    f = pd.DataFrame(rows).set_index("time").sort_index()
    return f[~f.index.duplicated(keep="last")]

def check_freshness(f, minutes, max_age):
    age = (utc_now() - (f.index[-1] + pd.Timedelta(minutes=minutes))).total_seconds()/60
    if age < -1 or age > max_age:
        raise ValueError(f"Stale {minutes}m candles ({age:.1f}m old)")

def atr_series(f, period=14):
    pc = f.close.shift()
    tr = pd.concat([f.high-f.low,(f.high-pc).abs(),(f.low-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(period).mean()

def find_h1_pullback(h1):
    """Latest strong H1 candle breaks 5-bar structure; later H1 candle pulls back 38.2-70.5%."""
    if len(h1) < 30:
        return None
    atr = atr_series(h1)
    last = len(h1)-1
    for i in range(last-1, max(4,last-10), -1):
        row, a = h1.iloc[i], atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        prior = h1.iloc[i-5:i]
        span, body = float(row.high-row.low), abs(float(row.close-row.open))
        if span <= 0:
            continue
        bull = row.close>row.open and body>=a and (row.close-row.low)/span>=0.75 and row.close>prior.high.max()
        bear = row.close<row.open and body>=a and (row.high-row.close)/span>=0.75 and row.close<prior.low.min()
        if not bull and not bear:
            continue
        later = h1.iloc[i+1:last+1]
        if later.empty or len(later)>6:
            return None
        move = float(row.high-row.low)
        if bull:
            zl, zh = float(row.high-0.705*move), float(row.high-0.382*move)
            touched = ((later.low<=zh)&(later.high>=zl)).any()
            invalid = (later.close<row.low).any()
            if touched and not invalid:
                return {"side":"BUY","impulse_time":h1.index[i].isoformat(),
                        "pullback_time":h1.index[-1].isoformat(),"zone_low":zl,"zone_high":zh}
            return None
        zl, zh = float(row.low+0.382*move), float(row.low+0.705*move)
        touched = ((later.high>=zl)&(later.low<=zh)).any()
        invalid = (later.close>row.high).any()
        if touched and not invalid:
            return {"side":"SELL","impulse_time":h1.index[i].isoformat(),
                    "pullback_time":h1.index[-1].isoformat(),"zone_low":zl,"zone_high":zh}
        return None
    return None

def confirmed_pivots(f, side, lookback=36):
    found=[]
    for i in range(max(2,len(f)-lookback),len(f)-2):
        q,left,right=f.iloc[i],f.iloc[i-2:i],f.iloc[i+1:i+3]
        if side=="HIGH" and q.high>left.high.max() and q.high>right.high.max():
            found.append(float(q.high))
        if side=="LOW" and q.low<left.low.min() and q.low<right.low.min():
            found.append(float(q.low))
    return list(reversed(found))

def find_m5_bos(m5, side):
    """Latest completed M5 candle must close through a confirmed local swing."""
    if len(m5)<20:
        return None
    cur,prev=m5.iloc[-1],m5.iloc[-2]
    if side=="BUY":
        for level in confirmed_pivots(m5.iloc[:-1],"HIGH"):
            if prev.close<=level and cur.close>level and cur.close>cur.open:
                return {"level":level,"close":float(cur.close),"candle_time":m5.index[-1].isoformat()}
    else:
        for level in confirmed_pivots(m5.iloc[:-1],"LOW"):
            if prev.close>=level and cur.close<level and cur.close<cur.open:
                return {"level":level,"close":float(cur.close),"candle_time":m5.index[-1].isoformat()}
    return None

def structural_stop(m5, side, entry, pair, atr):
    pivots=confirmed_pivots(m5.iloc[:-1],"LOW" if side=="BUY" else "HIGH")
    candidates=[p for p in pivots if (p<entry if side=="BUY" else p>entry)]
    w=m5.iloc[-13:-1]
    pivot=candidates[0] if candidates else float(w.low.min() if side=="BUY" else w.high.max())
    buffer=max(0.15*atr,2*pip_size(pair))
    stop=pivot-buffer if side=="BUY" else pivot+buffer
    risk=entry-stop if side=="BUY" else stop-entry
    if risk<=0 or risk<0.25*atr or risk>4.0*atr:
        return None
    target=entry+1.5*risk if side=="BUY" else entry-1.5*risk
    return stop,target,risk,pivot

def build_signal(pair,m5,h1):
    setup=find_h1_pullback(h1)
    if not setup:
        return None
    pullback_close=pd.Timestamp(setup["pullback_time"])+pd.Timedelta(hours=1)
    if m5.index[-1]<pullback_close:
        return None
    bos=find_m5_bos(m5,setup["side"])
    if not bos:
        return None
    atr=float(atr_series(m5).iloc[-1])
    if pd.isna(atr) or atr<=0 or abs(bos["close"]-bos["level"])>atr:
        return None
    stopdata=structural_stop(m5,setup["side"],bos["close"],pair,atr)
    if not stopdata:
        return None
    sl,tp,risk,pivot=stopdata
    return {"pair":pair,"side":setup["side"],"entry":bos["close"],"sl":sl,"tp":tp,
            "risk_pips":risk/pip_size(pair),"bos_level":bos["level"],"pivot":pivot,
            "impulse_time":setup["impulse_time"],"pullback_time":setup["pullback_time"],
            "candle_time":bos["candle_time"]}

def live_quote(pair):
    r=SESSION.get(f"https://biquote.io/api/{pair}",params={"allowStale":"false"},timeout=15)
    r.raise_for_status()
    q=r.json(); mid=float(q.get("mid") or 0); age=float(q.get("quoteAgeSeconds",999999))
    stamp=pd.to_datetime(q.get("timestamp"),utc=True,errors="coerce")
    if mid<=0 or pd.isna(stamp) or age>120 or q.get("stale") is True:
        raise ValueError("Live quote missing or stale")
    return mid,stamp,age

def load_state():
    if not STATE_FILE.exists():
        return {"last_signal_by_pair":{},"last_scan_utc":None}
    d=json.loads(STATE_FILE.read_text()); d.setdefault("last_signal_by_pair",{})
    return d

def save_state(d):
    t=STATE_FILE.with_suffix(".tmp"); t.write_text(json.dumps(d,indent=2)); t.replace(STATE_FILE)

def append_signal(s):
    fields=["signal_time_utc","pair","direction","entry","stop_loss","take_profit","risk_pips",
            "rr","h1_impulse_time","h1_pullback_time","m5_bos_level","source"]
    with LOG_FILE.open("a",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if f.tell()==0: w.writeheader()
        w.writerow({"signal_time_utc":s["candle_time"],"pair":s["pair"],"direction":s["side"],
          "entry":round(s["entry"],6),"stop_loss":round(s["sl"],6),"take_profit":round(s["tp"],6),
          "risk_pips":round(s["risk_pips"],1),"rr":1.5,"h1_impulse_time":s["impulse_time"],
          "h1_pullback_time":s["pullback_time"],"m5_bos_level":round(s["bos_level"],6),
          "source":"BiQuote public OHLC/live quote; not broker verified"})

def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID Actions secret")
    r=SESSION.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
       json={"chat_id":CHAT_ID,"text":message,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"):
        raise RuntimeError("Telegram did not confirm delivery")

def scan():
    if (HERE/"STOP").exists():
        print("BOT 1 paused: STOP file exists"); return
    state=load_state(); sent=errors=0
    for pair in PAIRS:
        try:
            m5,h1=fetch_bars(pair,"5m",300),fetch_bars(pair,"1h",200)
            check_freshness(m5,5,8); check_freshness(h1,60,75)
            s=build_signal(pair,m5,h1)
            if not s or state["last_signal_by_pair"].get(pair)==s["candle_time"]:
                continue
            mid,qt,age=live_quote(pair)
            close_time=pd.Timestamp(s["candle_time"])+pd.Timedelta(minutes=5)
            if qt<close_time:
                raise ValueError("Live quote predates M5 signal close")
            atr=float(atr_series(m5).iloc[-1])
            if abs(mid-s["entry"])>0.20*atr:
                raise ValueError("Price moved too far from confirmed M5 close")
            msg=(f"🚨 BOT 1 — {s['side']} {pair}\nEntry: {s['entry']:.6f}\n"
                 f"Stop loss: {s['sl']:.6f}\nTake profit: {s['tp']:.6f}\n"
                 f"Risk: {s['risk_pips']:.1f} pips | R:R 1:1.5\n"
                 f"H1 impulse + pullback confirmed\nM5 mini BOS close: {s['bos_level']:.6f}\n"
                 f"Signal candle: {s['candle_time']}\nLive quote: {mid:.6f} ({age:.0f}s old)\n"
                 "Signal only. BiQuote prices are public-feed estimates, not broker-verified.")
            send_telegram(msg); append_signal(s)
            state["last_signal_by_pair"][pair]=s["candle_time"]; sent+=1
            print(f"{pair}: {s['side']} alert delivered",flush=True)
        except Exception as e:
            errors+=1; print(f"{pair}: {type(e).__name__}: {e}",flush=True)
    state["last_scan_utc"]=utc_now().isoformat(); save_state(state)
    print(f"Bot 1 scan complete: pairs={len(PAIRS)}, alerts={sent}, errors={errors}",flush=True)

if __name__=="__main__":
    scan()
