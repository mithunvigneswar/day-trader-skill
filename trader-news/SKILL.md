---
name: trader-news
description: >-
  News Monitor. Tracks breaking news on every NSE stock you watch and flags
  what moves it: Yahoo Finance headlines per ticker (RELIANCE.NS, SBIN.NS...)
  and how far each stock is from yesterday's close. Shows only what is new
  since the last check, newest first, in IST, tags each item (earnings,
  analyst, deal, halt, regulatory, macro...) and never repeats one. Yahoo is
  the only source: there are no filings feeds. Use when the user says "any
  news on SBIN", "why is this moving", "what's the news on my watchlist",
  "morning briefing", "pre-market news", "monitor the news", "did anything
  break", or after /trader-scanner finds setups.
argument-hint: "[tickers]"
---

# trader-news

A chart can look perfect and still be about to gap on an earnings date, a
halt or a lawsuit. Before a setup becomes a trade, this skill reads the
headline feed for every stock on the watchlist and tells you only what is new. It also prints how far each stock has moved today, so a
headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers SBIN.NS RELIANCE.NS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-moves --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=SBIN.NS&region=IN&lang=en-IN` |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Exchange announcements beat headlines.** There is no filings feed here.
  If a headline claims results, a deal or a board change, ask the user to
  check the company's announcement on the NSE or BSE site before acting.
- **Say what it touches.** Every item lists the tickers it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: anything tagged earnings, halt, legal,
   regulatory or risk, and every MOVING ticker first.
3. Write a short brief, per ticker: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "earnings
   are this week: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: an earnings date inside the trade, a halt, an offering.
   Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the open.

## Example

On a trading day at 10:15 IST, SBIN.NS has broken its opening range. The check
lists the headlines published since the last check, newest first, each with its
tags and link, and a price line such as
`SBIN.NS 992.50 +3.31% vs yesterday's close 960.70 at 10:15 IST  MOVING`.
The brief: "Results are due this week, so the stock can gap; today's trade is
in and out before the broker's ~15:12 square-off." When a ticker has no news, say so
rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per ticker, with a pause
  between calls.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
