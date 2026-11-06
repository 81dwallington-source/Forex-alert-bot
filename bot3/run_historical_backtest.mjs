import { getHistoricalRates } from "dukascopy-node";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import path from "node:path";

const pairs = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD","EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","NZDJPY","NZDCHF","NZDCAD","CADJPY","CHFJPY"];
const spreads = {
  EURUSD:1.2, GBPUSD:1.5, USDJPY:1.3, USDCHF:1.5, USDCAD:1.6, AUDUSD:1.4, NZDUSD:1.8,
  EURGBP:1.5, EURJPY:2.0, EURCHF:2.0, EURAUD:2.5, EURCAD:2.5, EURNZD:3.0,
  GBPJPY:2.5, GBPCHF:2.5, GBPAUD:3.0, GBPCAD:3.0, GBPNZD:3.5,
  AUDJPY:2.0, AUDCHF:2.0, AUDCAD:2.0, AUDNZD:2.5, NZDJPY:2.5, NZDCHF:2.5,
  NZDCAD:2.5, CADJPY:2.0, CHFJPY:2.5
};
const to = new Date().toISOString().slice(0,10);
const fromDate = new Date(Date.now() - 183*24*60*60*1000);
const from = fromDate.toISOString().slice(0,10);
await mkdir("research-data", { recursive:true });
await mkdir("research-results", { recursive:true });
const summary = [];
function csvEscape(x) { return String(x ?? "").replaceAll('"','""'); }

for (const pair of pairs) {
  console.log(`Downloading ${pair} M5 BID candles ${from} to ${to}`);
  const candles = await getHistoricalRates({
    instrument: pair.toLowerCase(),
    dates: { from, to },
    timeframe: "m5",
    priceType: "bid",
    format: "json",
    volumes: false,
    batchSize: 10,
    pauseBetweenBatchesMs: 1000,
    requestIntervalMs: 0,
    retryCount: 3,
    rateLimitRetryCount: 3,
    pauseBetweenRetriesMs: 1000,
    retryOnEmpty: true,
    failAfterRetryCount: false,
    useCache: true,
    cacheFolderPath: ".dukascopy-cache"
  });
  if (!Array.isArray(candles) || candles.length < 800) {
    throw new Error(`${pair}: insufficient historical candles (${candles?.length ?? 0})`);
  }
  const csv = ["time,Open,High,Low,Close", ...candles.map(c =>
    [new Date(c.timestamp).toISOString(), c.open, c.high, c.low, c.close].join(",")
  )].join("\n") + "\n";
  const dataPath = path.join("research-data", `${pair}_M5.csv`);
  await writeFile(dataPath, csv);
  const base = spreads[pair];
  for (const [scenario, spread] of [["base",base],["stress",base*1.5]]) {
    const outPath = path.join("research-results", `${pair}_${scenario}.csv`);
    console.log(`Backtesting ${pair} ${scenario} spread=${spread.toFixed(2)} pips`);
    execFileSync("python", ["-m","bot3.backtest","--pair",pair,"--csv",dataPath,
      "--spread-pips",String(spread),"--slippage-pips","0.2","--out",outPath],
      {stdio:"inherit"});
    const raw = await readFile(outPath,"utf8");
    const lines = raw.trim().split(/\r?\n/);
    const headers = lines.shift().split(",");
    const rows = lines.filter(Boolean).map(line => {
      const vals = line.split(",");
      return Object.fromEntries(headers.map((h,i)=>[h,vals[i]]));
    });
    const closed = rows.filter(r => r.outcome==="WIN" || r.outcome==="LOSS");
    const wins = closed.filter(r=>r.outcome==="WIN").length;
    const losses = closed.length-wins;
    const rs = closed.map(r=>Number(r.result_R)).filter(Number.isFinite);
    const grossWin = rs.filter(r=>r>0).reduce((a,b)=>a+b,0);
    const grossLoss = -rs.filter(r=>r<0).reduce((a,b)=>a+b,0);
    let equity=0, peak=0, maxDD=0;
    for (const r of rs) { equity+=r; peak=Math.max(peak,equity); maxDD=Math.max(maxDD,peak-equity); }
    summary.push({pair,scenario,from,to,bars:candles.length,signals:rows.length,
      wins,losses,ambiguous:rows.filter(r=>r.outcome==="AMBIGUOUS").length,
      open:rows.filter(r=>r.outcome==="OPEN").length,
      win_rate_pct:closed.length ? +(100*wins/closed.length).toFixed(2) : "",
      net_R:+rs.reduce((a,b)=>a+b,0).toFixed(2),
      profit_factor:grossLoss ? +(grossWin/grossLoss).toFixed(2) : (grossWin ? "INF" : ""),
      max_drawdown_R:+maxDD.toFixed(2),assumed_spread_pips:+spread.toFixed(2),
      slippage_pips_per_fill:0.2});
  }
}
const cols=Object.keys(summary[0]);
await writeFile("research-results/summary.csv",
  [cols.join(","),...summary.map(r=>cols.map(c=>csvEscape(r[c])).join(","))].join("\n")+"\n");
console.log(`Research complete: ${summary.length} pair/scenario runs across ${pairs.length} pairs; summary at research-results/summary.csv`);
