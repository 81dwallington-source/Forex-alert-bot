import { getHistoricalRates } from "dukascopy-node";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const pairs = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD","EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","NZDJPY","NZDCHF","NZDCAD","CADJPY","CHFJPY"];
const to = new Date().toISOString().slice(0,10);
// Use 18 months so the chronological validation block has enough observations.
const fromDate = new Date(Date.now() - 548*24*60*60*1000);
const from = fromDate.toISOString().slice(0,10);
await mkdir("research-data", { recursive:true });
await mkdir("research-results", { recursive:true });
const summary = [];
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
  // Empty weekend/holiday periods are normal, but missing weekday data can
  // indicate an upstream block or failed download. Do not silently backtest it.
  const coveredDays = new Set(candles.map(c => new Date(c.timestamp).toISOString().slice(0,10)));
  const expectedWeekdays = [];
  for (let d = new Date(fromDate); d <= new Date(to + "T00:00:00Z"); d.setUTCDate(d.getUTCDate()+1)) {
    const day = d.getUTCDay();
    if (day !== 0 && day !== 6) expectedWeekdays.push(d.toISOString().slice(0,10));
  }
  const missingWeekdays = expectedWeekdays.filter(day => !coveredDays.has(day));
  console.log(`${pair} coverage: ${candles.length} bars; ${missingWeekdays.length} missing weekdays`);
  if (missingWeekdays.length > 4) {
    throw new Error(`${pair}: refusing incomplete history; missing weekdays: ${missingWeekdays.slice(0,12).join(", ")}`);
  }
  const csv = ["time,Open,High,Low,Close", ...candles.map(c =>
    [new Date(c.timestamp).toISOString(), c.open, c.high, c.low, c.close].join(",")
  )].join("\n") + "\n";
  const dataPath = path.join("research-data", `${pair}_M5.csv`);
  await writeFile(dataPath, csv);
  // Candidate competition performs all trading simulations once on this
  // shared, validated history. Do not run 54 redundant baseline simulations here.
  summary.push({pair,from,to,bars:candles.length,missing_weekdays:missingWeekdays.length,
    data_file:dataPath});

}
const cols=Object.keys(summary[0]);
await writeFile("research-results/summary.csv",
  [cols.join(","),...summary.map(r=>cols.map(c=>csvEscape(r[c])).join(","))].join("\n")+"\n");
console.log(`Historical data ready: ${summary.length} pairs; candidate competition will run the simulations.`);
