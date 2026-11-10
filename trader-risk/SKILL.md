---
name: trader-risk
description: >-
  Risk Manager. Checks every NSE trade against your limits and stops anything
  over them, before you enter it in your broker app. For a day trade: a stop
  is required, the loss at the stop must fit your max loss per trade
  (measured from the higher of the plan's entry and a fresh price),
  reward:risk must meet your minimum, the day's total risk must fit your daily
  loss limit, no more than your trades per day and open trades, and no new
  entries outside your entry window or when the market is closed (weekend or
  NSE holiday). It also keeps the basics: blocked tickers, order size (with
  MIS leverage), position cap, cash buffer and margin, short sales, malformed
  or bad bracket orders, duplicates and a daily order limit. Getting out is
  never blocked by a day limit. Prints PASS or BLOCK with the reason and
  records the verdicts so trader-broker only shows an order that passed. Use
  when the user says "check my order", "is this safe", "risk check", "does
  this break my rules", "can I take this trade", "how much have I risked
  today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker. Most people who day trade lose money. These limits
keep each loss and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 10:15"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, both or neither of qty and notional, a notional order that is not market + day, a limit order with no limit price; a bracket that is not whole shares, not `day` or `gtc`, or misses its stop-loss or target |
| asset | anything that is not an NSE ticker such as `SBIN.NS` (no options, no crypto) |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the ticker is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` (as margin: the order value divided by `leverage_multiplier_mis`) |
| position | (entries) the stock would be over `max_position_pct` of the account (as margin) |
| cash | (entries) the buy would leave less than the cash buffer, or need more margin than your cash |
| stop | (entries) no stop while `require_stop` is true (a bracket `stop_loss_stop_price`, or the order's plan stop); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| open trades | (entries) `max_open_trades` positions already open and this is a new symbol |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the market is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 10:15 cannot
  understate the risk at 10:55 after the price ran up. An older price is not
  used, and it says so.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size trades use up a Rs 1,500 day.
- **The window** is IST (a fixed +5:30, no daylight saving). The session is
  09:15 to 15:30. Weekends and NSE holidays are closed. The holiday list is
  built in and updated from `trader/state/nse_holidays.json`, which the
  scanner's `calendar` command refreshes from the NSE website each morning;
  the check warns when the date is outside the years the list covers. Set
  both `no_entries_*` to null to switch the window off.
- **Leverage**: with `leverage_multiplier_mis` above 1, the size, position and
  cash checks use the margin an order needs (order value divided by the
  multiplier), because that is the capital it ties up. The loss check always
  uses the full position, because the stop loses on the full position.

Warnings do not block, but say them to the user: a number sent as a number
instead of a string, a stop that lives only in the plan and not in the broker
app, a price too old to use, a date outside the holiday list.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote SBIN.NS --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.

## Example

The SBIN.NS bracket from trader-budget at 10:15 IST on Monday 2026-10-19, on a
fresh Rs 50,000 account with the example config (5x MIS leverage):

```
PASS  #1 buy SBIN.NS 75 shares, entry
          entry 992.50 (the plan's entry), stop 987.80, target 997.50: risk 4.70 a share
          max loss ₹352.50 (limit ₹500), reward:risk 1.06 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss

1 passed, 0 blocked at 2026-10-19 10:15 IST. Cash after the passed orders: ₹35,112.50.
```

One more share and it is blocked:

```
BLOCK #1 buy SBIN.NS 76 shares, entry
          entry 992.50 (the plan's entry), stop 987.80, target 997.50: risk 4.70 a share
          max loss ₹357.20 (limit ₹500), reward:risk 1.06 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss
        - size: ₹15,086 of margin is over your ₹15,000 max order
```

The same 75 shares checked at 09:20 are blocked by the window (no new entries
before 09:30), at 14:45 by the entry cutoff (no new entries after 14:30), and on
Tuesday 2026-11-10 because the NSE is closed (Diwali-Balipratipada). A sell
that closes the position at 15:00 passes even though it is after the 14:30
entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one that fills, and a
  stop can fill worse than its price in a fast market.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. trader-broker prints a ticket, and only when both hold
  does it count as approved: trader-risk passed that exact order today, and the user typed an
  explicit yes for that one order in the chat. The user then enters it in their own broker app.
- Intraday MIS leverage magnifies losses as well as gains.
- No options or short selling unless the config allows it and the user confirms.
- Never ask for broker passwords, TOTP codes or API keys. They stay in the user's broker app.
