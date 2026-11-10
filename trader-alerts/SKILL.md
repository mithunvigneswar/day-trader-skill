---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price, a stop or the opening bell hits. Turns
  the trade plan into three alerts in one go (the stop, the target and the
  square-off time, 15:00 IST by default, ahead of the broker's ~15:12 auto square-off), plus above / below / stop /
  target levels, percent moves from the previous close, any time of day, and
  the NSE opening bell, each ringing once so you are not spammed. Knows NSE
  hours (9:15 AM to 3:30 PM IST) and trading holidays (built in, refreshed
  from nseindia.com by trader-scanner), works out IST itself, fetches free
  delayed quotes (Yahoo chart JSON, .NS tickers) one ticker at a time, and
  writes trader/prices.json for sizing and the risk check. Use when the user
  says "alert me when", "tell me if SBIN.NS hits", "watch my stop", "watch my
  trade", "remind me to square off by 3 PM", "is the market open", "when does
  the market open", "get me a price", or right after /trader-exit writes a
  plan.
argument-hint: "[ticker] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. The user places the stop and the target in
their broker app (a Zerodha Kite SL-M for the stop, a limit sell for the
target, or a GTT OCO for a delivery trade); the alerts tell the user what
happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Tickers are Yahoo NSE tickers with `.NS` (`SBIN.NS`); a bare name gets `.NS`
added. Times are IST, prices are rupees (₹).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, square-off from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SBIN.NS --stop 987.80
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SBIN.NS --target 997.50
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "square off MIS"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol KOTAKBANK.NS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote SBIN.NS --write          # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset sbin.ns-plan-stop
```

`--now "2026-10-19 15:15"` pins the clock (IST); `--offline DIR`
reads saved quote files (`yahoo-<TICKER>.json`) instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the tickers held, for `quote --holdings` |
| `trader/state/nse_holidays.json` | the NSE holiday list saved by `trader-scanner`'s `scan.py calendar`; read only, no network |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:00") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan`
rules carry the plan's date, so yesterday's stop never rings today. The NSE
has no early closes, so a `time` rule rings at the same clock time every
session.

## Quotes

- Yahoo (the only source): `query2.finance.yahoo.com/v8/finance/chart/<TICKER>.NS`,
  an unofficial free JSON endpoint, one request per ticker, half a second
  apart. On a 429 it waits and tries once more; on a 429 or a 5xx after that it
  tries `query1`. It identifies itself as `day-trader-skill/2.0`.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. Zerodha Kite's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from the broker app and write it into
  `trader/prices.json` as `{"as_of": "...Z", "prices": {"SBIN.NS": 992.5}}`.

## The NSE clock

Regular session 9:15 AM to 3:30 PM IST, closed on weekends and on the NSE's
trading holidays. The tool carries the equity holiday list from NSE's
holiday-master as read on 2026-11-10 (20 days in 2026, among them Dussehra on
Oct 20, Diwali Laxmi Pujan on Nov 8 and Diwali-Balipratipada on Nov 10) and
adds the saved list from `scan.py calendar` on top. IST has no daylight
saving, so it needs no time zone files and ignores the computer's own zone.
NSE publishes the current calendar year only: for any year the list does not
cover, the clock says the holiday list is unknown, and `scan.py calendar`
refreshes it. Special sessions (a Muhurat evening session, a Saturday session)
are not modelled: check the NSE circular on those days.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the 15:00
   square-off time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means the
   order in Kite has probably filled: the user checks Positions and records
   the fill with trader-broker. "Square off MIS" means close anything still
   open before the broker's auto square-off (~15:12): trader-broker `flatten`
   builds the sell ticket, then trader-risk, then the user's yes. Never act
   for them.

## Example

SBIN.NS on a Monday (illustrative numbers): entry 992.50 at 10:00 AM, stop
987.80, target 997.50, square off 15:00. `add --from-plan` writes `sbin.ns-plan-stop`,
`sbin.ns-plan-target` and `sbin.ns-flat-by` for that day. A `check` between
the stop and the target prints:

```
NSE OPEN (2026-10-19 10:53 IST, regular session).
Nothing hit. 3 rule(s) watched today, SBIN.NS ₹992.50.
```

At 3:00 PM:

```
ALERT Square off MIS: it is 15:00 IST (set for 15:00), the market closes at 15:30. If SBIN.NS is still
open, close it: trader-broker flatten --symbol SBIN.NS builds the sell ticket, then trader-risk and your yes.
```

It rings once. The next morning the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
