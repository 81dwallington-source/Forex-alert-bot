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

def swing_level_is_fresh(closes, pivot_idx, sweep_idx, level, direction):
    """A swing level is stale if an intervening candle closed through it."""
    intervening = closes.iloc[pivot_idx+3:sweep_idx]
    if direction == "SELL":
        return not (intervening > level).any()
    if direction == "BUY":
        return not (intervening < level).any()
    return False

def detect_sweep(m5):
    """
    Strict sequence: confirmed swing sweep/rejection -> later M5 structure
    close-break -> separate EMA21 retest candle -> separate confirmation
    close through the retest candle. The final candle is the entry trigger.
    """
    if len(m5) < 50:
        return None
    atr = atr_value(m5.iloc[:-1])
    if not atr or atr <= 0:
        return None

    highs = m5["High"].astype(float)
    lows = m5["Low"].astype(float)
    opens = m5["Open"].astype(float)
    closes = m5["Close"].astype(float)
    ema21 = closes.ewm(span=21, adjust=False).mean()

    retest_idx = len(m5) - 2
    confirm_idx = len(m5) - 1
    retest = m5.iloc[retest_idx]
    confirm = m5.iloc[confirm_idx]
    ro, rh, rl, rc = map(float, (retest["Open"], retest["High"], retest["Low"], retest["Close"]))
    co, ch, cl, cc = map(float, (confirm["Open"], confirm["High"], confirm["Low"], confirm["Close"]))
    retest_ema = float(ema21.iloc[retest_idx])
    confirm_ema = float(ema21.iloc[confirm_idx])

    tolerance = 0.10 * atr
    penetration = max(0.10 * atr, float(closes.iloc[-1]) * 0.00002)
    min_wick = 0.30 * atr
    min_body = 0.10 * atr

    # Reserve the final two completed candles for retest and confirmation.
    first_sweep = max(4, len(m5) - 14)
    last_sweep = len(m5) - 5

    for sweep_idx in range(last_sweep, first_sweep - 1, -1):
        pivots = []
        for i in range(max(2, sweep_idx - 30), sweep_idx - 2):
            pivot_high = float(highs.iloc[i])
            pivot_low = float(lows.iloc[i])
            if (pivot_high > max(float(x) for x in highs.iloc[i-2:i])
                    and pivot_high >= max(float(x) for x in highs.iloc[i+1:i+3])):
                pivots.append(("HIGH", i, pivot_high))
            if (pivot_low < min(float(x) for x in lows.iloc[i-2:i])
                    and pivot_low <= min(float(x) for x in lows.iloc[i+1:i+3])):
                pivots.append(("LOW", i, pivot_low))

        so, sh, sl, sc = map(float, (
            m5.iloc[sweep_idx]["Open"], m5.iloc[sweep_idx]["High"],
            m5.iloc[sweep_idx]["Low"], m5.iloc[sweep_idx]["Close"]
        ))
        candle_range = sh - sl
        if candle_range <= 0:
            continue

        bearish_levels = [
            p for p in pivots if p[0] == "HIGH"
            and sh >= p[2] + penetration
            and sc <= p[2] - 0.05 * atr
            and swing_level_is_fresh(closes, p[1], sweep_idx, p[2], "SELL")
        ]
        bullish_levels = [
            p for p in pivots if p[0] == "LOW"
            and sl <= p[2] - penetration
            and sc >= p[2] + 0.05 * atr
            and swing_level_is_fresh(closes, p[1], sweep_idx, p[2], "BUY")
        ]

        bearish_rejection = (
            bool(bearish_levels) and sc < so
            and sh - max(so, sc) >= min_wick
            and (sc - sl) / candle_range <= 0.55
            and abs(sc - so) >= min_body
        )
        bullish_rejection = (
            bool(bullish_levels) and sc > so
            and min(so, sc) - sl >= min_wick
            and (sc - sl) / candle_range >= 0.45
            and abs(sc - so) >= min_body
        )
        if not bearish_rejection and not bullish_rejection:
            continue

        direction = "SELL" if bearish_rejection else "BUY"
        chosen_levels = bearish_levels if direction == "SELL" else bullish_levels
        chosen = max(chosen_levels, key=lambda p: p[1])
        swept_level = chosen[2]

        # Find the latest confirmed opposing pivot that existed before sweep.
        structure_pivots = []
        for k in range(max(2, sweep_idx - 24), sweep_idx - 2):
            if direction == "SELL":
                is_pivot = (
                    float(lows.iloc[k]) < min(float(x) for x in lows.iloc[k-2:k])
                    and float(lows.iloc[k]) <= min(float(x) for x in lows.iloc[k+1:k+3])
                )
                if is_pivot:
                    structure_pivots.append((k, float(lows.iloc[k])))
            else:
                is_pivot = (
                    float(highs.iloc[k]) > max(float(x) for x in highs.iloc[k-2:k])
                    and float(highs.iloc[k]) >= max(float(x) for x in highs.iloc[k+1:k+3])
                )
                if is_pivot:
                    structure_pivots.append((k, float(highs.iloc[k])))

        if not structure_pivots:
            continue
        break_level = structure_pivots[-1][1]

        # A candle CLOSE must break structure after the sweep and before retest.
        break_idx = None
        for j in range(sweep_idx + 1, retest_idx):
            bj = m5.iloc[j]
            bo, bc = float(bj["Open"]), float(bj["Close"])
            if direction == "SELL" and bc < break_level and bc < bo:
                break_idx = j
                break
            if direction == "BUY" and bc > break_level and bc > bo:
                break_idx = j
                break
        if break_idx is None:
            continue

        # Retest is a separate candle, tested against its own EMA21 value.
        if direction == "SELL":
            retest_ok = (
                rh >= retest_ema - tolerance and rc < retest_ema
                and rc < ro and rc < break_level
                and retest_ema < float(ema21.iloc[max(0, retest_idx - 3)])
            )
            confirmation_ok = (
                cc < co and cc < rl and cc < confirm_ema and cc < break_level
            )
        else:
            retest_ok = (
                rl <= retest_ema + tolerance and rc > retest_ema
                and rc > ro and rc > break_level
                and retest_ema > float(ema21.iloc[max(0, retest_idx - 3)])
            )
            confirmation_ok = (
                cc > co and cc > rh and cc > confirm_ema and cc > break_level
            )

        if not (retest_ok and confirmation_ok):
            continue

        sweep_extreme = sh if direction == "SELL" else sl
        return {
            "direction": direction,
            "sweep_extreme": sweep_extreme,
            "entry": cc,
            "atr": atr,
            "candle_time": m5.index[confirm_idx].isoformat(),
            "swept_level": swept_level,
            "break_level": break_level,
            "break_candle_time": m5.index[break_idx].isoformat(),
            "retest_level": retest_ema,
            "retest_candle_time": m5.index[retest_idx].isoformat(),
            "confirmation_candle_high": ch,
            "confirmation_candle_low": cl,
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
                 f"Setup: CONFIRMED SWING SWEEP + REJECTION + STRUCTURE CLOSE-BREAK + EMA21 RETEST + NEXT-CANDLE BREAK\n"
                 f"Entry reference: {fmt(signal['entry'])}\n"
                 f"Stop-loss: {fmt(signal['sl'])}\n"
                 f"Take-profit: {fmt(signal['tp'])}\n"
                 f"Planned risk/reward: 1:1\n"
                 f"Swept level: {fmt(signal['swept_level'])}\n"
                 f"Structure break: {fmt(signal['break_level'])} at {signal['break_candle_time']}\n"
                 f"EMA21 retest: {fmt(signal['retest_level'])} at {signal['retest_candle_time']}\n"
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
