---
name: trader-scanner
description: >-
  Chart Scanner. Scans charts for setups on NSE stocks: opening-range
  breakouts, flags, oversold bounces. Takes the list of NSE tickers (Yahoo
  .NS symbols) from the "universe" in trader/config.json, runs a pre-market
  scan of daily closes (20-day closing-high breakout, bull flag, reclaimed the
  20-day average, oversold, oversold bounce) and an intraday opening-range
  breakout scan on 5-minute bars (9:15 to 9:30 AM IST range), then writes
  trader/watchlist.json for trader-news and trader-entry. Keeps the NSE
  trading-holiday list fresh from nseindia.com once a day. Free Yahoo data,
  bar by bar. Use when the user says "scan the market", "find me setups",
  "what is breaking out", "opening range breakouts", "scan for bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "what should I
  look at today", "is the NSE open today", or at the start of a trading day
  before /trader-news.
argument-hint: "[universe | calendar | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads free charts and
lists the ones that match a rule. That is all it does. A scan is a list of
charts that match a rule, not a list of stocks to buy, and a setup on a chart
is not a prediction. Most people who day trade lose money; the scanner does
not change that. It only saves you from flipping through charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Tickers are Yahoo's NSE form with the `.NS` suffix (`RELIANCE.NS`, `SBIN.NS`,
`M&M.NS`); a bare name like `SBIN` gets `.NS` added, and the dot is never
changed. Every time is IST (UTC+5:30), prices are rupees (₹).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" calendar                           # NSE holidays -> trader/state/nse_holidays.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                           # config.json universe -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                              # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols SBIN.NS,RELIANCE.NS  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                                # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                     # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (₹) and `--min-volume` (daily 1,000,000, orb 0),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:15"` (IST) to pin the clock, `--offline PATH`
to read saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0
when the scan found something, 1 when it found nothing or could not run (the
reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | read by `universe` (the `universe` list of `.NS` tickers) and by `orb` (`no_entries_after`, default 14:30) |
| `trader/universe.txt` | written by `universe`: one `SYMBOL|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/state/nse_holidays.json` | written by `calendar`, and by `daily` / `orb` once per IST day: the NSE equity holiday list, with `fetched_at` |

## The NSE holiday list

The NSE closes on weekends and on its own holidays (Diwali, Holi, Dussehra
and so on) that are not the US ones. The scripts carry a built-in list read
from NSE's holiday-master on 2026-11-10, and `scan.py calendar` replaces it
with the live list from `www.nseindia.com/api/holiday-master?type=trading`
(the equity segment). NSE publishes the current calendar year only, so the
list has to be refreshed; `daily` and `orb` do it by themselves the first
time they run each IST day. A failed refresh never stops a scan: it prints
one line and the saved or built-in list stays in use. `--offline`, `--now`
and `--no-write` runs never go to the network for it. It is the only request
this tool makes outside Yahoo, and it sends nothing about you. NSE answers a
browser-style User-Agent and hangs on a plain script one, so this one request
sends a browser-style User-Agent.

`trader-alerts`, `trader-entry`, `trader-exit` and `trader-risk` read the
saved file too, so run `scan.py calendar` first thing each day (or let the
first `daily` do it). To refresh it on a schedule, run that one command from
a Claude scheduled task or `/loop` before 9:00 AM IST.

## The universe

`universe` reads the `universe` list in `trader/config.json` (for example
`["RELIANCE.NS", "SBIN.NS", "ICICIBANK.NS"]`), checks every entry is an NSE
ticker, and writes `trader/universe.txt`. No network. It stops with one plain
line if the config has no `universe`.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so a 50-name
universe takes 3 requests. Liquidity comes from the spark meta: last price at
least `--min-price` and last session's volume at least `--min-volume`. Run it
before the open or after the close: during the session the volume is the
day's so far, and today's daily bar is left out until 3:30 PM IST.

The setups are computed from daily closes only (spark has no highs or lows),
so they are close cousins of the reel's `refs/scan.py`, which used highs and
lows:

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. The 9:15 to 9:30 AM IST window is hands-off:
the first bar that can break the range closes at 9:35. A hit is the first
completed 5-minute bar to CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped,
a bar off the 5-minute grid (the live tick, like 10:43:07) is dropped, and a
bar counts only once it is complete (start + 5 minutes). Before 9:35 AM IST, on
a weekend or an NSE holiday, after the 3:30 PM close, or after
`no_entries_after` (an expired setup) it prints one plain line and writes
nothing. The NSE has no early closes: every session runs 9:15 to 3:30, 75
five-minute bars. Intraday MIS positions are squared off by the broker (Zerodha) from
about 3:12 PM IST.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 48 of 50 charts (2 failed or
  had no data)". Never round a partial scan up to "the whole market".
- The only other request is the NSE holiday list, above.

## Steps

1. First thing each day: `scan.py calendar` (the first `daily` or `orb` of the
   day does this too). If today is an NSE holiday, say so and stop.
2. When the universe changes: edit `universe` in `trader/config.json`, then
   `scan.py universe`.
3. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
4. After 9:35 AM IST (9:45 to 10:00 is the usual): `scan.py orb`. Tell the
   user how many usable charts broke their range and when, and that a later
   hit is closer to the price now.
5. The user picks one chart. Hand it on: `/trader-news TICKER` (what is moving
   it), then `/trader-entry TICKER` (the entry bar by bar). Never pick for them.

## Example: a morning

At about 8:45 AM IST: `scan.py calendar`, then `scan.py daily --symbols SBIN.NS,RELIANCE.NS`
for the pre-market setups on the closes through yesterday. At 9:45 AM IST:
`scan.py orb` lists the charts that closed above their 9:15 to 9:30 range, each
with its range, the close that broke it and the time. The user picks `SBIN.NS`
and Claude hands it to `/trader-news SBIN.NS`, then `/trader-entry SBIN.NS`.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
