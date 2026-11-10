---
name: trader-broker
description: >-
  Order Ticket Desk. Turns an approved NSE order into a ticket you enter in
  your own broker app (Zerodha Kite, Groww, Angel One), never without your
  yes. Shows each order as a plain ticket (a bracket shows its stop-loss,
  target and the max loss at the stop) plus the exact fields to type: NSE,
  product MIS, order type, trigger and limit price, in rupees. Records your
  yes word for word, then you place it yourself and tell Claude what
  happened. Builds the sell ticket that closes a day trade by your flat-by
  time (15:00 IST by default, ahead of the broker's ~15:12 auto square-off), records fills, and logs each trade's
  max loss so trader-risk can hold the daily limit. Nothing is sent to any
  broker: Indian brokers need a daily 2FA login, so Claude works as a
  research desk and you press the button. Use when the user says "place the
  order", "buy it", "execute", "give me the ticket", "close my position",
  "flatten", "get me out", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Ticket Desk is the last step before real money, so it is the
strictest. A ticket counts as approved only after two gates, every time, for
every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.

Then the user enters the order in their own broker app. This skill never
connects to a broker, never holds a password, TOTP code or API key, and never
places anything. One PASS from the rules, one yes from the user. No yes, no ticket.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 75 --filled-price 992.50
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol SBIN.NS --side sell --qty 75 --price 997.50
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol SBIN.NS --qty 75
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker or any network. It renders tickets, checks
that a reply is a real yes for that order, builds the flatten ticket, and
keeps the log in `trader/state/broker.json`. `--now "2026-10-19 10:15"` pins
the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## How the ticket maps to the broker app

The ticket prints these fields, in rupees, for the user to type in. The
symbol is shown without `.NS`: `SBIN.NS` is `SBIN` on the NSE.

| ticket field | in Zerodha Kite |
| --- | --- |
| `exchange`: NSE, `tradingsymbol` | search the symbol, choose NSE |
| `transaction_type` | BUY or SELL |
| `product`: MIS | Intraday (MIS), squared off by the broker (Zerodha) from around 15:12 |
| `order_type`: MARKET, LIMIT, SL, SL-M | the order type; `price` is the limit, `trigger_price` the trigger |
| `quantity` | whole shares |
| `stop_loss_sl_m_trigger` | a SELL SL-M order at this trigger, placed right after the entry fills |
| `target_1_limit` | a SELL LIMIT order at this price |

Kite has no bracket order for this, so after the entry fills the user places
the stop-loss and the target as two sell orders, and cancels the other by hand
when one fills. Say so every time. A stop is an order, not a promise: in a fast
market it can fill below the trigger. Do not trade the 09:15 to 09:30 open;
the example config blocks entries before 09:30.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Approve this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Only after "Recorded your yes", show the fields the command printed. The
   user enters the order in the broker app themselves.
5. Ask what happened, then record it: `ticket.py result --index N --status placed
   --broker-id <id> --filled-qty Q --filled-price P` (from the broker app's order
   book), or `--status denied` if they chose not to, or `--status failed` with
   `--note`. A failed order is not retried without a new check and a new yes.
   The log keeps the entry's max loss for trader-risk's daily limit.

## Steps: while it is open, and out by 15:00

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`).
- When the stop-loss or the target fills in the broker app, record it:
  `ticket.py fill --symbol SBIN.NS --side sell --qty 75 --price 997.50`.
- By `flat_by` (15:00 IST), anything still open gets closed:
  1. Ask the user how many shares the broker app shows as open.
  2. `ticket.py flatten --symbol SBIN.NS --qty 75` writes a market sell for
     those shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. If the stop-loss or target order is still open in the broker app, the user
     cancels it first (it holds those shares), then enters this sell and
     records the `result`.

## Example

```
ORDER 1 of 1, NSE intraday (MIS), you enter it in your broker app
  BUY 75 SBIN.NS, market, good for today
  stop-loss 987.80
  target 997.50
  max loss about ₹352.50 if the stop fills at 987.80 (it can fill worse in a fast market)
  why: opening-range breakout on SBIN.NS: 75 shares, bound by your max order (75 x 4.70 = ₹352.50 at the stop)
  READY: trader-risk PASS, waiting for your yes
  Enter exactly this in your broker app (Zerodha Kite: BUY or SELL ticket, product MIS):
    {"exchange": "NSE", "tradingsymbol": "SBIN", "transaction_type": "BUY", "quantity": "75",
     "product": "MIS", "order_type": "MARKET", "stop_loss_sl_m_trigger": "987.80", "target_1_limit": "997.50"}
```

The user replies "yes". Claude records it and shows the fields again. The user
types them into Kite, then tells Claude it is placed and Claude records the
result. The `why` and the plan stay in the file.

## What it never does

- Never marks a ticket approved without both gates, and never two orders on one yes.
- Never connects to a broker, and never runs from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats broker passwords, TOTP codes or API keys.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. A ticket counts as approved only when trader-risk passed that
  exact order today AND the user typed an explicit yes for that one order in the chat. The user then
  enters it in their own broker app.
- Intraday MIS leverage magnifies losses as well as gains.
- No options or short selling unless the config allows it and the user confirms.
- Never ask for broker passwords, TOTP codes or API keys. They stay in the user's broker app.
