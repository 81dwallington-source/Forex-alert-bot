"""Bot 3: independent M5 pullback-continuation alert engine. Signal-only."""
from __future__ import annotations
import os, json, csv, time, logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd

BOT_NAME = "Forex Bot 3"
DATA_SOURCE = "BiQuote public FX OHLC endpoint (third-party; not broker-verified)"
PAIRS = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD",
"EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF",
"GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","NZDJPY",
"NZDCHF","NZDCAD","CADJPY","CHFJPY"]
UK = ZoneInfo("Europe/London")
SESSION_START_HOUR, SESSION_END_HOUR = 5, 23
MAX_ALERTS_PER_UK_DAY, MAX_ONE_ALERT_PER_PAIR_PER_UK_DAY = 15, True
MIN_RR, MIN_STOP_ATR, MAX_STOP_ATR, MAX_ENTRY_DRIFT_ATR = 1.0, 0.45, 3.5, 0.20
MAX_M5_AGE_MINUTES, RISK_ADVISORY_PERCENT = 8, 0.25
STATE_FILE, LOG_FILE = Path("bot3_state.json"), Path("bot3_signal_log.csv")
TOKEN, CHAT_ID = os.getenv("TELEGRAM_BOT_TOKEN","").strip(), os.getenv("TELEGRAM_CHAT_ID","").strip()
SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"ForexBot3/1.0"})
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(BOT_NAME)

def in_session(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(UK)
    return SESSION_START_HOUR <= now.hour < SESSION_END_HOUR

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("State unreadable; refusing to risk duplicate alerts")
        raise

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def fetch_candles(pair, interval, limit=300):
    response = SESSION.get(f"https://biquote.io/api/{pair}/ohlc",
        params={"interval":interval,"limit":limit}, timeout=18)
    response.raise_for_status()
    rows = []
    for bar in (response.json().get("bars") or []):
        if bar.get("isOpen") is True: continue
        try:
            stamp = pd.to_datetime(bar["openTime"], utc=True)
            op, hi, lo, cl = [float(bar[k]) for k in ("open","high","low","close")]
            if min(op,hi,lo,cl) <= 0 or hi < max(op,cl,lo) or lo > min(op,cl,hi): continue
            rows.append((stamp,op,hi,lo,cl))
        except (KeyError,TypeError,ValueError): continue
    if len(rows) < 60: raise ValueError(f"{pair} {interval}: only {len(rows)} valid completed candles")
    df = pd.DataFrame(rows,columns=["time","Open","High","Low","Close"])
    df = df.drop_duplicates("time",keep="last").set_index("time").sort_index()
    if df.index[-1] > pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=1):
        raise ValueError(f"{pair} {interval}: future-dated candle")
    df.attrs["source"] = DATA_SOURCE
    return df

def add_indicators(frame):
    # Backtests pass precomputed indicator frames to avoid recalculating the
    # full history at every M5 decision point. Live scans still calculate once.
    required = {"EMA21", "EMA20", "EMA50", "ATR"}
    if required.issubset(frame.columns):
        return frame
    df = frame.copy()
    df["EMA21"] = df.Close.ewm(span=21,adjust=False).mean()
    df["EMA20"] = df.Close.ewm(span=20,adjust=False).mean()
    df["EMA50"] = df.Close.ewm(span=50,adjust=False).mean()
    prev = df.Close.shift(1)
    tr = pd.concat([(df.High-df.Low),(df.High-prev).abs(),(df.Low-prev).abs()],axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    return df

def trend_direction(frame):
    df = add_indicators(frame)
    last = df.iloc[-1]
    if pd.isna(last.EMA50): return None
    if last.Close > last.EMA20 > last.EMA50: return "BUY"
    if last.Close < last.EMA20 < last.EMA50: return "SELL"
    return None

def confirmed_pivots(frame, side, lookback=30, left=2, right=2):
    start, end = max(left,len(frame)-lookback), len(frame)-right
    values = frame.Low.to_numpy() if side == "low" else frame.High.to_numpy()
    found = []
    for i in range(start,end):
        window, value = values[i-left:i+right+1], values[i]
        if side == "low" and value == window.min() and (window==value).sum()==1:
            found.append((i,float(value)))
        elif side == "high" and value == window.max() and (window==value).sum()==1:
            found.append((i,float(value)))
    return found

def detect_approved_pattern(df, direction):
    """Return only one of the three user-approved M5 structures, or None."""
    if direction not in ("BUY", "SELL") or len(df) < 60:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    atr = float(last.ATR)
    if not pd.notna(atr) or atr <= 0:
        return None
    candle_range = float(last.High - last.Low)
    body = abs(float(last.Close - last.Open))
    if candle_range <= 0 or body / candle_range < 0.55:
        return None
    close_location = (float(last.Close) - float(last.Low)) / candle_range
    directional_candle = (
        float(last.Close) > float(last.Open) and close_location >= 0.70
        if direction == "BUY" else
        float(last.Close) < float(last.Open) and close_location <= 0.30
    )
    if not directional_candle:
        return None

    # 1) Liquidity sweep in either direction: take a confirmed swing level,
    # close back through it, then confirm by breaking the prior candle.
    pivots = confirmed_pivots(df.iloc[:-1], "low" if direction == "BUY" else "high",
                              lookback=30, left=2, right=2)
    for pivot_i, level in reversed(pivots):
        if len(df) - 1 - pivot_i > 20:
            break
        swept = (float(last.Low) < level and float(last.Close) > level
                 if direction == "BUY" else
                 float(last.High) > level and float(last.Close) < level)
        broke = (float(last.Close) > float(prev.High) if direction == "BUY"
                 else float(last.Close) < float(prev.Low))
        if swept and broke:
            return {"setup_type": "TWO-SIDED LIQUIDITY SWEEP",
                    "evidence": f"Confirmed {direction.lower()}-side swing sweep, rejection back through {level:.6f}, and M5 close-break"}

    # 2) Impulse + origin retest: find a strong displacement candle and the
    # last opposing candle immediately before it; require a retest and fresh
    # directional close-break now (not entry on the impulse itself).
    for impulse_i in range(len(df) - 3, max(2, len(df) - 11), -1):
        impulse = df.iloc[impulse_i]
        impulse_atr = float(impulse.ATR)
        if not pd.notna(impulse_atr) or impulse_atr <= 0:
            continue
        impulse_range = float(impulse.High - impulse.Low)
        impulse_body = abs(float(impulse.Close - impulse.Open))
        impulse_dir = (float(impulse.Close) > float(impulse.Open) if direction == "BUY"
                       else float(impulse.Close) < float(impulse.Open))
        impulse_location = ((float(impulse.Close)-float(impulse.Low))/impulse_range
                            if impulse_range > 0 else 0.5)
        strong_impulse = (impulse_dir and impulse_body >= 1.20*impulse_atr
                          and (impulse_location >= 0.75 if direction == "BUY"
                               else impulse_location <= 0.25))
        if not strong_impulse:
            continue
        origin_i = impulse_i - 1
        while origin_i > max(0, impulse_i - 4):
            origin = df.iloc[origin_i]
            opposing = (float(origin.Close) < float(origin.Open) if direction == "BUY"
                        else float(origin.Close) > float(origin.Open))
            if opposing:
                break
            origin_i -= 1
        if origin_i <= 0:
            continue
        origin = df.iloc[origin_i]
        touched_origin = (float(last.Low) <= float(origin.High) + 0.15*atr
                          and float(last.Low) >= float(origin.Low) - 0.25*atr
                          if direction == "BUY" else
                          float(last.High) >= float(origin.Low) - 0.15*atr
                          and float(last.High) <= float(origin.High) + 0.25*atr)
        close_break = (float(last.Close) > float(prev.High) and float(last.Close) > float(origin.High)
                       if direction == "BUY" else
                       float(last.Close) < float(prev.Low) and float(last.Close) < float(origin.Low))
        if touched_origin and close_break:
            return {"setup_type": "IMPULSE + ORIGIN RETEST",
                    "evidence": f"Strong M5 displacement at {df.index[impulse_i]}, retest of origin candle {df.index[origin_i]}, then confirmed close-break"}

    # 3) Consolidation breakout + retest: a compact base, a completed close
    # outside it, then a later retest of the broken boundary and confirmation.
    for breakout_i in range(len(df) - 3, max(5, len(df) - 9), -1):
        base = df.iloc[max(0, breakout_i-8):breakout_i]
        if len(base) < 5:
            continue
        ceiling, floor = float(base.High.max()), float(base.Low.min())
        if ceiling - floor > 3.0*atr:
            continue
        breakout = df.iloc[breakout_i]
        broke_range = (float(breakout.Close) > ceiling if direction == "BUY"
                       else float(breakout.Close) < floor)
        if not broke_range:
            continue
        retested = (float(last.Low) <= ceiling + 0.20*atr and float(last.Low) >= ceiling - 0.35*atr
                    if direction == "BUY" else
                    float(last.High) >= floor - 0.20*atr and float(last.High) <= floor + 0.35*atr)
        confirmed = (float(last.Close) > float(prev.High) and float(last.Close) > ceiling
                     if direction == "BUY" else
                     float(last.Close) < float(prev.Low) and float(last.Close) < floor)
        if retested and confirmed:
            return {"setup_type": "CONSOLIDATION BREAKOUT + RETEST",
                    "evidence": f"Compact M5 base ({floor:.6f}-{ceiling:.6f}), completed breakout, boundary retest, and confirmation close-break"}
    return None


def validate_m15_structure(m15, direction, expected_setup):
    """Require M15 to independently confirm the same approved structure as M5."""
    if len(m15) < 60:
        return None
    m15_pattern = detect_approved_pattern(add_indicators(m15), direction)
    if not m15_pattern or m15_pattern["setup_type"] != expected_setup:
        return None
    return m15_pattern


def build_signal(pair,m5,m15,h1,live_price=None,live_stamp=None,live_bid=None,live_ask=None,now=None):
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)
    now_utc = now_ts.tz_convert("UTC") if now_ts.tzinfo else now_ts.tz_localize("UTC")
    age = (now_utc-(m5.index[-1]+pd.Timedelta(minutes=5))).total_seconds()/60
    m15_age = (now_utc-(m15.index[-1]+pd.Timedelta(minutes=15))).total_seconds()/60
    h1_age = (now_utc-(h1.index[-1]+pd.Timedelta(hours=1))).total_seconds()/60
    if age < -1 or age > MAX_M5_AGE_MINUTES: return None
    if m15_age < -1 or m15_age > 20 or h1_age < -1 or h1_age > 75: return None

    # H1 is the mandatory directional gate. M15 must agree; neither can be
    # overridden by an M5 pattern.
    direction = trend_direction(h1)
    if not direction or trend_direction(m15) != direction: return None
    df = add_indicators(m5)
    if len(df)<60 or pd.isna(df.iloc[-1].ATR): return None
    last = df.iloc[-1]
    atr = float(last.ATR)
    if atr<=0 or float(last.High-last.Low)>2.0*atr: return None
    pattern = detect_approved_pattern(df, direction)
    if not pattern: return None

    # M15 must independently show the same approved structure. M5 remains
    # responsible for the actual retest/confirmation candle and entry timing.
    m15_pattern = validate_m15_structure(m15, direction, pattern["setup_type"])
    if not m15_pattern: return None

    # Independent stop placement: latest confirmed M5 swing plus a volatility
    # buffer. The strategy never copies manually drawn chart levels.
    pivots = confirmed_pivots(df.iloc[:-1], "low" if direction=="BUY" else "high", 36, 2, 2)
    if not pivots: return None
    swing = pivots[-1][1]
    entry = float(last.Close)
    stop = swing - 0.25*atr if direction=="BUY" else swing + 0.25*atr
    risk = entry-stop if direction=="BUY" else stop-entry
    if risk < MIN_STOP_ATR*atr or risk > MAX_STOP_ATR*atr: return None
    target = entry + risk*MIN_RR if direction=="BUY" else entry-risk*MIN_RR

    if live_price is None or live_stamp is None or live_bid is None or live_ask is None: return None
    live_time=pd.Timestamp(live_stamp)
    if live_time.tzinfo is None: live_time=live_time.tz_localize("UTC")
    live_age_seconds=(now_utc-live_time.tz_convert("UTC")).total_seconds()
    if live_age_seconds < -60 or live_age_seconds > 120: return None
    bid,ask,mid=float(live_bid),float(live_ask),float(live_price)
    if bid<=0 or ask<=bid or mid<=0 or not (bid<=mid<=ask): return None
    spread=ask-bid
    if spread > 0.15*atr or abs(mid-entry)>MAX_ENTRY_DRIFT_ATR*atr: return None

    m15_last = add_indicators(m15).iloc[-1]
    h1_last = add_indicators(h1).iloc[-1]
    direction_sign = 1.0 if direction == "BUY" else -1.0
    prior6, prior12, prior24 = df.iloc[-7:-1], df.iloc[-13:-1], df.iloc[-25:-1]
    prior_atr_median = float(df.ATR.iloc[-60:].median())
    features = {
        "m5_ema21_slope_atr": float((last.EMA21-df.iloc[-4].EMA21)/atr),
        "m5_directional_ema21_slope_atr": float((last.EMA21-df.iloc[-4].EMA21)/atr*direction_sign),
        "m15_ema20_50_gap_atr": float(abs(m15_last.EMA20-m15_last.EMA50)/m15_last.ATR),
        "h1_ema20_50_gap_atr": float(abs(h1_last.EMA20-h1_last.EMA50)/h1_last.ATR),
        "m15_directional_trend_gap_atr": float((m15_last.EMA20-m15_last.EMA50)/m15_last.ATR*direction_sign),
        "h1_directional_trend_gap_atr": float((h1_last.EMA20-h1_last.EMA50)/h1_last.ATR*direction_sign),
        "confirmation_body_ratio": float(abs(last.Close-last.Open)/(last.High-last.Low)),
        "confirmation_close_location": float((last.Close-last.Low)/(last.High-last.Low)),
        "confirmation_range_atr": float((last.High-last.Low)/atr),
        "directional_momentum_3_atr": float((last.Close-df.iloc[-4].Close)/atr*direction_sign),
        "directional_momentum_6_atr": float((last.Close-df.iloc[-7].Close)/atr*direction_sign),
        "directional_bar_fraction_6": float(((df.Close.iloc[-6:].to_numpy()-df.Open.iloc[-6:].to_numpy())*direction_sign>0).mean()),
        "prior_range_6_atr": float((prior6.High.max()-prior6.Low.min())/atr),
        "breakout_margin_12_atr": float(((last.Close-prior12.High.max()) if direction=="BUY" else (prior12.Low.min()-last.Close))/atr),
        "opposing_distance_24_atr": float(max(0.0,((prior24.High.max()-last.Close) if direction=="BUY" else (last.Close-prior24.Low.min()))/atr)),
        "pullback_distance_atr": 0.0, "pullback_depth_atr": 0.0, "pullback_age_bars": 0,
        "atr_regime_60": float(atr/prior_atr_median) if prior_atr_median>0 else 0.0,
    }
    return {"pair":pair,"direction":direction,"setup_type":pattern["setup_type"],
        "pattern_evidence":pattern["evidence"],"m15_pattern_evidence":m15_pattern["evidence"],
        "entry":entry,"stop":stop,"target":target,
        "rr":MIN_RR,"atr":atr,"swing":swing,"candle_time":m5.index[-1].isoformat(),
        "live_price":mid,"live_bid":bid,"live_ask":ask,"spread":spread,
        "quote_time":live_time.isoformat(),"data_source":DATA_SOURCE, **features}

def fetch_live_quote(pair, now=None):
    # Use the dedicated tick endpoint, never the close of an unfinished candle as a quote.
    r=SESSION.get(f"https://biquote.io/api/{pair}",params={"allowStale":"false"},timeout=15)
    r.raise_for_status()
    tick=r.json()
    if tick.get("marketState") != "open" or tick.get("stale") is not False:
        raise ValueError(f"{pair}: market is closed or quote is marked stale")
    bid,ask,mid=float(tick["bid"]),float(tick["ask"]),float(tick["mid"])
    stamp=pd.to_datetime(tick["timestamp"],utc=True)
    now_ts=pd.Timestamp(now or datetime.now(timezone.utc))
    if now_ts.tzinfo is None: now_ts=now_ts.tz_localize("UTC")
    age=(now_ts.tz_convert("UTC")-stamp).total_seconds()
    reported_age=float(tick.get("quoteAgeSeconds",age))
    if age < -60 or age > 120 or reported_age < 0 or reported_age > 120:
        raise ValueError(f"{pair}: quote is too old or timestamp is invalid ({age:.0f}s)")
    if bid<=0 or ask<=bid or mid<=0 or not (bid<=mid<=ask):
        raise ValueError(f"{pair}: invalid bid/ask/mid quote")
    return mid,stamp,bid,ask

def send_telegram(message):
    if not TOKEN or not CHAT_ID: raise RuntimeError("Telegram secrets missing")
    r=SESSION.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id":CHAT_ID,"text":message,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"): raise RuntimeError("Telegram rejected Bot 3 alert")

OUTCOME_FIELDS = ["timestamp_utc","uk_date","pair","direction","setup_type","pattern_evidence","m15_pattern_evidence","entry","stop","target","rr","swing",
    "candle_time","live_price","live_bid","live_ask","spread","quote_time","data_source",
    "m5_ema21_slope_atr","m5_directional_ema21_slope_atr","m15_ema20_50_gap_atr","h1_ema20_50_gap_atr",
    "m15_directional_trend_gap_atr","h1_directional_trend_gap_atr","confirmation_body_ratio",
    "confirmation_close_location","confirmation_range_atr","directional_momentum_3_atr",
    "directional_momentum_6_atr","directional_bar_fraction_6","prior_range_6_atr",
    "breakout_margin_12_atr","opposing_distance_24_atr","pullback_distance_atr",
    "pullback_depth_atr","pullback_age_bars","atr_regime_60",
    "outcome","outcome_time_utc","outcome_detail"]

def log_signal(s):
    # Explicit field selection prevents extra signal metadata breaking CSV writes.
    row = {"timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "uk_date":datetime.now(timezone.utc).astimezone(UK).date().isoformat(),
        **{k:v for k,v in s.items() if k in OUTCOME_FIELDS}, "outcome":"",
        "outcome_time_utc":"","outcome_detail":""}
    exists=LOG_FILE.exists()
    with LOG_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=OUTCOME_FIELDS,extrasaction="ignore")
        if not exists: w.writeheader()
        w.writerow(row)

def evaluate_trade_outcome(signal, candles):
    """Return first confirmed TP/SL result from completed M5 candles after signal."""
    start = pd.to_datetime(signal["candle_time"], utc=True)
    later = candles.loc[candles.index > start]
    for stamp, candle in later.iterrows():
        if signal["direction"] == "BUY":
            stop_hit = float(candle.Low) <= float(signal["stop"])
            target_hit = float(candle.High) >= float(signal["target"])
        else:
            stop_hit = float(candle.High) >= float(signal["stop"])
            target_hit = float(candle.Low) <= float(signal["target"])
        if stop_hit and target_hit:
            return "AMBIGUOUS", stamp.isoformat(), "Both stop and target inside the same M5 candle; excluded from performance totals"
        if stop_hit:
            return "LOSS", stamp.isoformat(), "Stop-loss touched first on completed M5 candle"
        if target_hit:
            return "WIN", stamp.isoformat(), "Take-profit touched first on completed M5 candle"
    return "", "", ""

def update_outcomes():
    """Update unresolved signals using completed M5 OHLC; never guess ambiguous bars."""
    if not LOG_FILE.exists():
        return
    with LOG_FILE.open("r",newline="",encoding="utf-8") as f:
        reader=csv.DictReader(f)
        rows=list(reader)
        old_fields=reader.fieldnames or []
    if not rows:
        return
    cache={}
    changed=False
    for row in rows:
        if row.get("outcome"):
            continue
        pair=row.get("pair","")
        if not pair:
            continue
        try:
            if pair not in cache:
                cache[pair]=fetch_candles(pair,"5m")
            outcome,stamp,detail=evaluate_trade_outcome(row,cache[pair])
            if outcome:
                row["outcome"],row["outcome_time_utc"],row["outcome_detail"]=outcome,stamp,detail
                changed=True
                log.info("Outcome recorded: %s %s %s",pair,outcome,stamp)
        except Exception:
            log.exception("Could not verify outcome for %s",pair)
    if changed or old_fields != OUTCOME_FIELDS:
        temp=LOG_FILE.with_suffix(".tmp")
        with temp.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=OUTCOME_FIELDS,extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(LOG_FILE)

def format_alert(s):
    digits=3 if "JPY" in s["pair"] else 5
    fmt=lambda x:f"{x:.{digits}f}"
    return (f"🟢 FOREX BOT 3 — TRADE SIGNAL\nSetup: {s['setup_type']}\nPair: {s['pair']}\nDirection: {s['direction']}\n"
        f"Entry reference: {fmt(s['entry'])}\nStop-loss: {fmt(s['stop'])}\n"
        f"Take-profit: {fmt(s['target'])}\nPlanned R:R: 1:{s['rr']:.1f}\n"
        f"Confirmed M5 swing: {fmt(s['swing'])}\nM15 structure: {s['m15_pattern_evidence']}\nM5 entry confirmation: {s['pattern_evidence']}\nBid/ask: {fmt(s['live_bid'])} / {fmt(s['live_ask'])} | spread: {fmt(s['spread'])}\nQuote timestamp: {s['quote_time']}\nRisk advisory: max {RISK_ADVISORY_PERCENT:.2f}% (manual sizing required)\n"
        f"Data: {DATA_SOURCE}\nCheck broker price and spread before entry. No automatic trading.")

def scan_once():
    now=datetime.now(timezone.utc)
    if not in_session(now):
        log.info("Outside UK 05:00–23:00 session"); return
    update_outcomes()
    state=load_state(); day=now.astimezone(UK).date().isoformat()
    day_key,pair_key=f"alerts:{day}",f"pairs:{day}"
    count=int(state.get(day_key,0)); sent=set(state.get(pair_key,[]))
    attempted=data_ok=failures=no_setup=0
    for pair in PAIRS:
        if count>=MAX_ALERTS_PER_UK_DAY: break
        if MAX_ONE_ALERT_PER_PAIR_PER_UK_DAY and pair in sent: continue
        attempted += 1
        try:
            m5=fetch_candles(pair,"5m"); m15=fetch_candles(pair,"15m"); h1=fetch_candles(pair,"1h")
            price,stamp,bid,ask=fetch_live_quote(pair,now)
            data_ok += 1
            s=build_signal(pair,m5,m15,h1,price,stamp,bid,ask,now)
            if s:
                send_telegram(format_alert(s)); log_signal(s)
                count+=1; sent.add(pair); state[day_key]=count; state[pair_key]=sorted(sent); save_state(state)
                log.info("Sent %s %s",pair,s["direction"])
            else:
                no_setup += 1
        except Exception:
            failures += 1
            log.exception("Scan failed for %s",pair)
        time.sleep(0.15)
    log.info("Bot 3 scan summary: attempted=%s valid_data=%s failures=%s no_setup=%s alerts_today=%s/%s",
             attempted,data_ok,failures,no_setup,count,MAX_ALERTS_PER_UK_DAY)
    if attempted and data_ok == 0:
        raise RuntimeError("Bot 3 scan rejected: no pair returned fully validated candles and a fresh live quote")

if __name__=="__main__": scan_once()
