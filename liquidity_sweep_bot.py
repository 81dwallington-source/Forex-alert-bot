"""Independent M5 liquidity-sweep reversal alert bot. No automated trading."""
import os, json, csv
from pathlib import Path
from datetime import datetime, timezone
import requests
import pandas as pd

BOT_NAME = "Forex Liquidity Sweep Bot"
DATA_SOURCE = "BiQuote public FX OHLC endpoint; third-party feed, not broker-verified"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PAIRS = [
    "EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","USDCAD=X","AUDUSD=X","NZDUSD=X",
    "EURGBP=X","EURJPY=X","EURCHF=X","EURAUD=X","EURCAD=X","EURNZD=X",
    "GBPJPY=X","GBPCHF=X","GBPAUD=X","GBPCAD=X","GBPNZD=X",
    "AUDJPY=X","AUDCHF=X","AUDCAD=X","AUDNZD=X","NZDJPY=X","NZDCHF=X",
    "NZDCAD=X","CADJPY=X","CHFJPY=X"
]
STATE = Path("liquidity_sweep_state.json")
LOG = Path("liquidity_sweep_log.csv")
LOOKBACK = 12
MIN_RR = 1.0
MAX_SIGNAL_AGE_MIN = 10
MAX_ENTRY_DRIFT_ATR = 0.20
MAX_STOP_ATR = 4.5
MAX_ALERTS_PER_DAY = 10
MAX_ONE_PER_PAIR_DAY = True

def fetch_bars(symbol, interval, limit=300):
    pair = symbol.replace("=X", "")
    r = requests.get(f"https://biquote.io/api/{pair}/ohlc",
        params={"interval": interval, "limit": limit},
        headers={"User-Agent":"ForexLiquiditySweepBot/1.0"}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    rows = []
    for b in payload.get("bars", []):
        if b.get("isOpen") is True:
            continue
        try:
            stamp = pd.to_datetime(b["openTime"], utc=True)
            vals = [float(b[k]) for k in ("open","high","low","close")]
            if min(vals) <= 0: continue
            o,h,l,c = vals
            if h < max(o,c,l) or l > min(o,c,h): continue
            rows.append((stamp,o,h,l,c))
        except (KeyError, TypeError, ValueError):
            continue
    if len(rows) < 60:
        raise ValueError(f"Only {len(rows)} completed {interval} candles")
    df = pd.DataFrame(rows, columns=["time","Open","High","Low","Close"]).set_index("time").sort_index()
    return df[~df.index.duplicated(keep="last")]

def live_quote(symbol):
    pair = symbol.replace("=X", "")
    r = requests.get(f"https://biquote.io/api/{pair}/ohlc",
        params={"interval":"5m","limit":10},
        headers={"User-Agent":"ForexLiquiditySweepBot/1.0"}, timeout=15)
    r.raise_for_status()
    bars = [b for b in (r.json().get("bars") or []) if b.get("isOpen") is True]
    if not bars: raise ValueError("No live/open M5 quote supplied")
    b = max(bars, key=lambda x: pd.to_datetime(x.get("openTime"), utc=True))
    stamp = pd.to_datetime(b["openTime"], utc=True)
    age = (datetime.now(timezone.utc)-stamp.to_pydatetime()).total_seconds()/60
    price = float(b["close"])
    if not 0 <= age <= 6 or price <= 0:
        raise ValueError(f"Live quote stale/invalid ({age:.1f} min)")
    return price, stamp.isoformat()

def atr_value(df, period=14):
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"]-df["Low"],(df["High"]-prev).abs(),(df["Low"]-prev).abs()],axis=1).max(axis=1)
    return float(tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean().iloc[-1])

def htf_direction(df):
    e20 = df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = df["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
    return "BUY" if e20 > e50 else "SELL" if e20 < e50 else "NONE"

def detect_sweep(m5):
    """Require M5 sweep/reclaim, displacement break, then a confirmed EMA21 pullback."""
    if len(m5) < LOOKBACK + 8:
        return None
    atr = atr_value(m5.iloc[:-1])
    if not atr or atr <= 0:
        return None
    ema21 = m5["Close"].ewm(span=21, adjust=False).mean()
    pullback = m5.iloc[-1]
    ema_now = float(ema21.iloc[-1])
    tolerance = 0.15 * atr

    # Sweep must be recent; the latest closed candle must be the pullback confirmation.
    first_sweep = max(LOOKBACK, len(m5) - 8)
    last_sweep = len(m5) - 3
    for sweep_idx in range(last_sweep, first_sweep - 1, -1):
        sweep = m5.iloc[sweep_idx]
        prior = m5.iloc[sweep_idx-LOOKBACK:sweep_idx]
        if len(prior) != LOOKBACK:
            continue
        prior_low = float(prior["Low"].min())
        prior_high = float(prior["High"].max())
        body = abs(float(sweep["Close"] - sweep["Open"]))

        bullish_sweep = (
            float(sweep["Low"]) < prior_low
            and float(sweep["Close"]) > prior_low
            and float(sweep["Close"]) > float(sweep["Open"])
            and body >= 0.20 * atr
        )
        bearish_sweep = (
            float(sweep["High"]) > prior_high
            and float(sweep["Close"]) < prior_high
            and float(sweep["Close"]) < float(sweep["Open"])
            and body >= 0.20 * atr
        )
        if not bullish_sweep and not bearish_sweep:
            continue

        break_idx = None
        for j in range(sweep_idx + 1, len(m5) - 1):
            candle = m5.iloc[j]
            if bullish_sweep and float(candle["Close"]) > float(sweep["High"]) and float(candle["Close"]) > float(candle["Open"]):
                break_idx = j
                break
            if bearish_sweep and float(candle["Close"]) < float(sweep["Low"]) and float(candle["Close"]) < float(candle["Open"]):
                break_idx = j
                break
        if break_idx is None:
            continue

        if bullish_sweep:
            touched = float(pullback["Low"]) <= ema_now + tolerance
            confirmed = (
                touched
                and float(pullback["Close"]) > ema_now
                and float(pullback["Close"]) > float(pullback["Open"])
                and float(pullback["Close"]) > prior_low
            )
            if confirmed:
                return {
                    "direction": "BUY",
                    "sweep_extreme": float(sweep["Low"]),
                    "entry": float(pullback["Close"]),
                    "atr": atr,
                    "candle_time": m5.index[-1].isoformat(),
                    "swept_level": prior_low,
                    "pullback_level": ema_now,
                }
        else:
            touched = float(pullback["High"]) >= ema_now - tolerance
            confirmed = (
                touched
                and float(pullback["Close"]) < ema_now
                and float(pullback["Close"]) < float(pullback["Open"])
                and float(pullback["Close"]) < prior_high
            )
            if confirmed:
                return {
                    "direction": "SELL",
                    "sweep_extreme": float(sweep["High"]),
                    "entry": float(pullback["Close"]),
                    "atr": atr,
                    "candle_time": m5.index[-1].isoformat(),
                    "swept_level": prior_high,
                    "pullback_level": ema_now,
                }
    return None

def build_signal(m5,m15,h1):
    setup = detect_sweep(m5)
    if not setup:
        return None, "no completed sweep + break + M5 EMA21 pullback"
    direction = setup["direction"]
    fresh_age = (datetime.now(timezone.utc)- (pd.Timestamp(m5.index[-1])+pd.Timedelta(minutes=5)).to_pydatetime()).total_seconds()/60
    if not -1 <= fresh_age <= MAX_SIGNAL_AGE_MIN:
        return None, f"M5 pullback confirmation stale ({fresh_age:.1f} min)"
    m15_dir = htf_direction(m15)
    h1_dir = htf_direction(h1)
    # Both higher timeframes are mandatory directional filters.
    # Never let M15 override a conflicting H1 trend.
    if m15_dir != direction:
        return None, f"M15 trend ({m15_dir}) does not agree with the M5 setup"
    if h1_dir != direction:
        return None, f"H1 trend ({h1_dir}) does not agree with the M5 setup"
    entry = setup["entry"]
    atr = setup["atr"]
    buffer = max(0.10*atr, entry*0.00005)
    if direction == "BUY":
        sl = setup["sweep_extreme"]-buffer
        risk = entry-sl
        tp = entry+risk*MIN_RR
    else:
        sl = setup["sweep_extreme"]+buffer
        risk = sl-entry
        tp = entry-risk*MIN_RR
    if risk <= 0 or risk > MAX_STOP_ATR*atr:
        return None, "structure stop too wide or invalid"
    return {**setup,"entry":entry,"sl":sl,"tp":tp,"risk":risk,
            "rr":MIN_RR,"m15":m15_dir,"h1":h1_dir,
            "h1_agrees":h1_dir == direction,
            "age_min":fresh_age}, "confirmed"

def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are missing")
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id":CHAT_ID,"text":message,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"): raise RuntimeError("Telegram rejected alert")

def load_state():
    try: return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except Exception: return {}

def save_signal(pair,s):
    fields=["timestamp_utc","pair","direction","entry","sl","tp","rr","swept_level","sweep_extreme","m15","h1","candle_time","data_source","outcome"]
    exists=LOG.exists()
    with LOG.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if not exists: w.writeheader()
        w.writerow({"timestamp_utc":datetime.now(timezone.utc).isoformat(),"pair":pair,
            **{k:s.get(k,"") for k in fields if k not in ("timestamp_utc","pair","data_source","outcome")},
            "data_source":DATA_SOURCE,"outcome":""})

def main():
    today=datetime.now(timezone.utc).date().isoformat()
    state=load_state()
    if state.get("date") != today: state={"date":today,"alerts":0,"pairs":[]}
    sent=0
    rejected={}
    for pair in PAIRS:
        if state["alerts"] >= MAX_ALERTS_PER_DAY: break
        if MAX_ONE_PER_PAIR_DAY and pair in state["pairs"]: continue
        try:
            m5=fetch_bars(pair,"5m")
            m15=fetch_bars(pair,"15m")
            h1=fetch_bars(pair,"1h")
            signal,reason=build_signal(m5,m15,h1)
            if not signal:
                rejected[reason]=rejected.get(reason,0)+1
                print(f"{pair}: skipped — {reason}", flush=True)
                continue
            quote,quote_time=live_quote(pair)
            if abs(quote-signal["entry"]) > MAX_ENTRY_DRIFT_ATR*signal["atr"]:
                print(f"{pair}: skipped; live price moved too far from entry")
                continue
            pip=3 if "JPY" in pair else 5
            fmt=lambda x: f"{x:.{pip}f}"
            msg=(f"FOREX LIQUIDITY SWEEP — M5 REVERSAL\n"
                 f"Pair: {pair.replace('=X','').replace('USD','/USD') if False else pair.replace('=X','')}\n"
                 f"Direction: {signal['direction']}\n"
                 f"Setup: SWEEP + REJECTION + BREAK + EMA21 PULLBACK\n"
                 f"Entry reference: {fmt(signal['entry'])}\n"
                 f"Stop-loss: {fmt(signal['sl'])}\n"
                 f"Take-profit: {fmt(signal['tp'])}\n"
                 f"Planned risk/reward: 1:1\n"
                 f"Swept level: {fmt(signal['swept_level'])}\n"
                 f"M15 trend: {signal['m15']} | H1 trend: {signal['h1']} (both aligned)\n"
                 f"Live public-feed price: {fmt(quote)}\n"
                 f"Data: {DATA_SOURCE}\n"
                 f"Alert only — check spread and execution with your broker.")
            send_telegram(msg)
            save_signal(pair,signal)
            state["alerts"]+=1
            state["pairs"].append(pair)
            sent+=1
            print(f"{pair}: alert sent ({signal['direction']})")
        except Exception as exc:
            print(f"{pair}: scan error: {exc}")
    STATE.write_text(json.dumps(state,indent=2),encoding="utf-8")
    print(f"{BOT_NAME}: scanned {len(PAIRS)} pairs; sent {sent}; daily total {state['alerts']}; source={DATA_SOURCE}")
    if rejected:
        top = sorted(rejected.items(), key=lambda item: item[1], reverse=True)[:8]
        print("Top rejection reasons: " + " | ".join(f"{reason}: {count}" for reason,count in top), flush=True)

if __name__=="__main__":
    main()
