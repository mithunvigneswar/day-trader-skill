---
name: trader-budget
description: >-
  Budget Manager. Sizes every NSE position to your budget (in rupees) and your
  max loss per trade. Reads the trade plan from trader-exit (entry, stop,
  target), finds the whole shares that fit inside four limits (max loss per
  trade, position cap, max order, cash after your buffer, with intraday MIS
  leverage applied to the capital limits), says which limit decided it, and
  writes ONE bracket order (entry, stop-loss, target) to trader/orders.json.
  A stop too far away for your max loss is a plain refusal, not a smaller
  order. Nothing is placed. Use when the user says "how many shares", "size
  this trade", "how much should I buy", "I have Rs 50,000", "I only want to
  risk Rs 500 a trade", "set my rules", "keep 10% in cash", or after
  /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy SBIN.NS at 992.50, stop 987.80") into an
exact order ("buy 75 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. trader-risk checks it and trader-broker prints a ticket the
user enters in their broker app after a yes. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 992.50 --json  # a limit order at 992.50
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights SBIN.NS=40,ITC.NS=60 --config split.json   # weights mode, below
```

`--now "2026-10-19 10:15"` pins the clock (IST). A plan for
another day is refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (example 500).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (Rs 15,000 of capital) and the smallest order (Rs 5).
- **leverage_multiplier_mis**: the intraday (MIS) leverage your broker gives (example 5.0). The
  position cap, max order and cash limits are capital limits: they are multiplied by it to get
  buying power. Use 1.0 for no leverage (delivery).
- The day limits trader-risk enforces: **max_daily_loss_inr** (1500), **max_trades_per_day** (3),
  **max_open_trades** (2), **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** /
  **no_entries_after** ("09:30" / "14:30") and **flat_by** ("15:00", ahead of the broker's ~15:12 MIS square-off), all IST and configurable.
- **blocked**: tickers they never want to trade. **universe**: the `.NS` tickers the scanner reads.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: the NSE trades whole shares. The size is the smallest of
four limits (L is `leverage_multiplier_mis`):

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` x L / entry |
| max order | `max_order_inr` x L / entry |
| cash | (budget minus the cash buffer) x L / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees,
the margin (capital) it uses and the max loss at the stop. Say plainly that a stop is an order, not a
promise: in a fast market it can fill below the stop. Then hand over to
`/trader-risk`.

## Example

SBIN.NS, an opening-range breakout: entry 992.50, stop 987.80, target 997.50.
Risk 4.70 a share. The example config: Rs 50,000 budget, 10% cash buffer,
Rs 500 max loss, Rs 15,000 max order, 5x MIS leverage.

| limit | the math | shares |
| --- | --- | --- |
| max loss | Rs 500 / 4.70 | 106 |
| position cap | 75% of Rs 50,000 = Rs 37,500 x 5 / 992.50 | 188 |
| max order | Rs 15,000 x 5 / 992.50 | **75 (decides it)** |
| cash | Rs 50,000 minus Rs 5,000 = Rs 45,000 x 5 / 992.50 | 226 |

75 shares = Rs 74,437.50 of position, Rs 14,887.50 of margin at 5x. Max loss
at the stop: 75 x 4.70 = Rs 352.50. It writes:

```json
{"symbol": "SBIN.NS", "side": "buy", "qty": "75", "type": "market", "time_in_force": "day",
 "order_class": "bracket", "take_profit_limit_price": "997.50", "stop_loss_stop_price": "987.80",
 "why": "opening-range breakout on SBIN.NS: 75 shares, bound by your max order (75 x 4.70 = Rs 352.50 at the stop)",
 "plan": {"entry": 992.5, "stop": 987.8, "target": 997.5, "risk_per_share": 4.7, "max_loss": 352.5,
          "setup": "orb", "entry_time": "10:15 AM", "flat_by": "15:00"}}
```

`why` and `plan` are notes for trader-risk and trader-broker. With the stop at
900.00 instead, one share would risk 92.50, so the Rs 500 max loss allows
only 5 shares (Rs 462.50 at the stop), not 75: the max loss limit decides it.

## Weights mode (kept from v1)

`--weights SBIN.NS=22.04,ITC.NS=10.86` or `--targets FILE` (`targets.example.json`
here) splits a budget across a list by weight: the cash buffer comes off the
top, weights are renormalised to 100, any stock over `max_position_pct` is
pinned at the cap and its excess shared out, slices under the minimum order
are dropped, and amounts round to whole rupees so nothing is left idle by
accident. It always buys whole shares and needs prices
(`--prices trader/prices.json`). These are plain buys with no stop: trader-risk blocks them while
`require_stop` is true and counts each one against `max_trades_per_day`. Use
a separate config (`--config`) for a long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.
- Amounts are rupees. Never show a dollar sign.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes options or
short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. trader-broker prints a ticket, and only when both hold
  does it count as approved: trader-risk passed that exact order today, and the user typed an
  explicit yes for that one order in the chat. The user then enters it in their own broker app.
- Intraday MIS leverage magnifies losses as well as gains: the max loss limit is what protects the account.
- No options or short selling unless the config allows it and the user confirms.
- Never ask for broker passwords, TOTP codes or API keys. They stay in the user's broker app.
