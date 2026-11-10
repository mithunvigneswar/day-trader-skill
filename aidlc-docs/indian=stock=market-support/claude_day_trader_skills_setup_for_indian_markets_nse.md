# Claude Day Trader Skill: Complete Indian Market (NSE) Setup Guide

This operational manual documents the step-by-step setup of the open-source **Claude Day Trader Skill** (by Jake Schincariol) tailored specifically for the **Indian Stock Market (NSE)**, as outlined in the video guide.

It covers:
1. **Way 1: Claude Web / Desktop App (Free Plan)**
2. **Way 2: Claude Code CLI (Windows & macOS)**
3. **The "India Fix" Adaptation (NSE symbols, IST market hours, ₹ currency, SEBI compliance)**
4. **Configuration (`config.json`) & Universe Definition (Nifty 50 / F&O Focus)**
5. **Execution Bridge with Indian Brokers (Zerodha Kite / Groww)**

---

## 1. System Architecture & Indian Market Realities

The Day Trader Skill framework operates as an **AI-powered trading research and validation desk**. 

```
                                [ MARKET DATA FEED ]
                             Yahoo Finance (yfinance)
                          NSE Tickers (e.g. RELIANCE.NS)
                                       │
                                       ▼
     ┌──────────────────────────────────────────────────────────────────┐
     │                     11-SKILL AI TRADING PIPELINE                 │
     │                                                                  │
     │  [1. trader-scanner]     Detects ORB, multi-timeframe bounces    │
     │  [2. trader-news]        Catalysts, sentiment, earnings context   │
     │  [3. trader-timer]       Candle confirmation & entry triggers    │
     │  [4. trader-exit]        Structural stops & two-leg targets      │
     │  [5. trader-timemachine] 10,000x Monte Carlo odds validation     │
     │  [6. trader-budget]      Rupee position sizing ($Qty = Loss/\Delta P$)│
     │  [7. trader-risk]        Drawdown limits & trade frequency caps   │
     └──────────────────────────────────┬───────────────────────────────┘
                                        │
                                        ▼
                            [ ORDER RECOMMENDATION TICKET ]
                      "BUY 15 SBIN @ ₹992.50 | SL: ₹987.80 | T1: ₹997.50"
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
         [ Claude Web / Free Tier ]               [ Claude Code / CLI ]
       Manual Execution on Broker App         Webhook / Copy-paste into Kite
       (Zerodha Kite / Groww / Angel)          (MIS / CNC GTT OCO Bracket)
```

### Critical Indian Market Invariants
* **Research Desk Model:** The original skill connects natively to Alpaca or Interactive Brokers (US markets only). Indian brokers (Zerodha, Groww, Angel One) require SEBI-mandated daily 2FA/TOTP handshakes. Therefore, in the Indian setup, Claude functions as an **autonomous research and trade-structuring desk** that outputs validated order tickets for you to execute in your broker app.
* **Symbol Format:** All NSE symbols must retain the `.NS` suffix without sanitization alterations (`RELIANCE.NS`, `SBIN.NS`, `TATAMOTORS.NS`). Never replace the dot with a hyphen (do not use `RELIANCE-NS`).
* **Market Hours:** 
  * Discovery & Hands-Off Open: `09:15 – 09:30 IST`
  * Prime Impulse Window: `09:30 – 11:30 IST`
  * Midday Liquidity Review: `11:30 – 13:00 IST`
  * Intraday MIS Square-Off Cutoff: `15:15 IST` (Exchange closes at `15:30 IST`)
* **Currency:** All financial units, budgets, risks, and targets must be displayed in **Indian Rupees (₹)**.

---

## 2. Setup Route A: Claude Web / App (Free Tier)

This method requires no local Python environment or terminal setup.

### Step 1: Enable Execution Capabilities
1. Open Claude (`claude.ai`) and navigate to **Settings** $\rightarrow$ **Capabilities**.
2. Turn ON **"Create and edit files"** (or **"Code execution and file creation"** in earlier UI versions).
3. Ensure network access/fetching capabilities are toggled on.

### Step 2: Install the Day Trader Skills
In a new chat, paste the repository installation command:
```text
Download and install the trading skills from:
https://github.com/JakeSchincariol/claude-trading-skills
Install every trader-* skill into this workspace.
```

### Step 3: Verify Installation
1. Go to **Customize** $\rightarrow$ **Skills**.
2. Confirm the presence of the core skills:
   * `trader-scanner`
   * `trader-timer`
   * `trader-exit`
   * `trader-timemachine`
   * `trader-budget`
   * `trader-risk`
   * `trader-news`

---

## 3. Setup Route B: Claude Code CLI (Advanced / Pro)

Claude Code runs directly in your local terminal, giving Claude full access to your filesystem and local execution tools.

### Step 1: Prerequisites
* **Python 3.10+**: Ensure Python is installed. On Windows, check **"Add python.exe to PATH"** during installation. Verify via:
  ```bash
  python --version   # or python3 --version
  ```
* **Node.js 18+** (Required for Claude Code CLI engine).

### Step 2: Install Claude Code CLI
* **Windows (PowerShell as Administrator):**
  ```powershell
  irm https://claude.ai/install.ps1 | iex
  ```
* **macOS / Linux (Terminal):**
  ```bash
  curl -fsSL https://claude.ai/install.sh | bash
  ```

### Step 3: Initialize Trading Workspace
Create a dedicated folder and launch Claude Code:
```bash
mkdir claude-trading-india
cd claude-trading-india
claude
```

### Step 4: Clone & Mount Skills
Inside the Claude Code session, issue the following setup prompt:
```text
Download https://github.com/JakeSchincariol/claude-trading-skills and copy every trader-* folder into .claude/skills in this project. Also download the tests folder. Do not change any file logic yet.
```

---

## 4. The "India Fix" Adaptation

Once the skills are mounted, prompt Claude (either in Claude Code or Claude Web) to adjust the codebase for Indian market mechanics.

### The System Adaptation Prompt
Copy and paste this exact prompt into your Claude interface:

```text
Make these trader skills work strictly for the Indian stock market (NSE):

1. SYMBOLS & DATA:
   - Use Yahoo Finance symbols with the exact '.NS' suffix (e.g., RELIANCE.NS, SBIN.NS, JSWSTEEL.NS, ICICIBANK.NS).
   - Never strip or replace the dot (do NOT convert 'RELIANCE.NS' to 'RELIANCE-NS').

2. MARKET HOURS & TIMEZONE:
   - Timezone: Asia/Kolkata (IST, UTC+5:30).
   - Market Open: 09:15 IST.
   - Initial Hands-Off Discovery Window: 09:15 to 09:30 IST.
   - Prime Trading Window: 09:30 to 11:30 IST.
   - Midday Lull / Stagnant Trade Review: 11:30 IST.
   - Mandatory MIS Intraday Position Square-Off: 15:15 IST.
   - Market Close: 15:30 IST.
   - Integrate standard NSE trading holidays.

3. CURRENCY & RISK SIZING:
   - Convert all sizing, reporting, P&L, stop-loss, and capital calculations to Indian Rupees (₹).
   - Base position sizing on backward-calculated rupee risk:
     Quantity = Max Rupee Risk / (Entry Price - Stop Loss Price).

4. TESTS:
   - Run the tests in the tests/ folder and verify all tests pass with the .NS tickers and IST timezone adjustments.
```

---

## 5. Configuration & Universe Definition

Create your local operational parameters in `trader/config.json`.

```json
{
  "market": "NSE",
  "timezone": "Asia/Kolkata",
  "currency": "INR",
  "symbol_currency_symbol": "₹",
  "trading_hours": {
    "open": "09:15",
    "prime_start": "09:30",
    "midday_review": "11:30",
    "intraday_cutoff": "15:15",
    "close": "15:30"
  },
  "budget": {
    "total_account_capital": 50000.0,
    "max_capital_per_trade": 15000.0,
    "max_loss_per_trade": 500.0,
    "max_daily_drawdown": 1500.0,
    "max_open_trades": 2,
    "leverage_multiplier_mis": 5.0
  },
  "universe": [
    "RELIANCE.NS",
    "SBIN.NS",
    "ICICIBANK.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "TCS.NS",
    "JSWSTEEL.NS",
    "HINDALCO.NS",
    "BHARTIARTL.NS",
    "TATASTEEL.NS",
    "BEL.NS"
  ]
}
```

---

## 6. Daily Step-by-Step Operating Workflow

Follow this daily timeline to run the pipeline seamlessly:

```
09:15 AM IST ──> Opening Bell: Hands off execution. Absorb initial volatility.
09:30 AM IST ──> Step 1: Run Market Scanner & Filter Nifty 50 Breakouts.
09:35 AM IST ──> Step 2: Run Timing & Exit Planner for High-Conviction Setups.
09:40 AM IST ──> Step 3: Run Time Machine (Monte Carlo 10,000x Odds Validation).
09:45 AM IST ──> Step 4: Run Budget Sizing & Generate Broker Order Ticket.
09:48 AM IST ──> Step 5: Execute Order on Zerodha Kite / Indian Broker Terminal.
10:30 AM IST ──> Step 6: Leg 1 Target Hit -> Book 50% & Trail Stop to Cost Basis.
11:30 AM IST ──> Step 7: Midday Cutoff Review -> Flatten stagnant/sideways trades.
15:15 PM IST ──> Step 8: Absolute Intraday Hard Cutoff -> Ensure Net Positions = 0.
```

### Execution Commands to Run with Claude

#### 1. Morning Discovery (At 09:30 AM IST)
```text
Run trader-scanner across our config universe:
- Scan 15-minute Opening Range Breakouts (ORB) from 09:15-09:30.
- Check price relative to daily Central Pivot Range (CPR) and rising session VWAP.
- Check trader-news for any overnight corporate earnings or macro filings.
```

#### 2. Entry & Exit Structuring (At 09:35 AM IST)
```text
For the top scanner candidate (e.g. SBIN.NS):
- Use trader-timer to identify the 5-minute candle trigger.
- Use trader-exit to determine the structural stop-loss (anchored below session VWAP / CPR) and the two-leg profit targets (T1 at 1.0x RRR, T2 at 1.5x-2.0x RRR).
```

#### 3. Validation & Sizing (At 09:40 AM IST)
```text
Validate this SBIN.NS setup:
- Run trader-timemachine with 10,000 Monte Carlo replays using 60-day 5-minute historical distributions. What is the win probability?
- If win probability > 55%, run trader-budget with max loss of ₹500. Compute exact MIS quantity with 5x leverage.
- Generate the final order ticket.
```

---

## 7. Manual Order Execution Bridge: Zerodha Kite

When Claude validates a setup, it outputs an execution specification. Here is how to route it into Zerodha Kite cleanly without API latency:

### Option A: Intraday MIS (5x Leverage — Active Morning Monitoring)
1. **Entry Leg:**
   * Search `SBIN` $\rightarrow$ Click **BUY** $\rightarrow$ Product: **MIS** $\rightarrow$ Type: **SL** (or Limit).
   * Enter Trigger and Limit prices provided by Claude.
2. **Initial Protective Stop (Immediate Post-Fill):**
   * Go to **Positions** $\rightarrow$ Click **Exit** on `SBIN MIS`.
   * Product: **MIS** $\rightarrow$ Type: **SL-M** (or SL-Limit).
   * Set Trigger to the exact structural stop price. Status: `TRIGGER PENDING`.
3. **Leg 1 Target (50% Quantity):**
   * Open a new **SELL** ticket $\rightarrow$ Product: **MIS** $\rightarrow$ Type: **LIMIT** $\rightarrow$ Price: Target 1.
4. **Risk Neutralization (When Leg 1 Fills):**
   * Modify the pending SL-M: reduce quantity to the runner lot, and move the trigger price up to **exact entry cost**.
   * Downside risk is now ₹0.00.

### Option B: Delivery CNC + GTT OCO (Hands-Off / Meeting-Safe)
If you have work meetings between 10:00 AM and 1:00 PM, avoid intraday MIS auto square-off penalties:
1. Buy shares under **CNC (Cash & Carry)**.
2. The instant it fills, tap the position $\rightarrow$ **Create GTT**.
3. Select **OCO (One Cancels the Other)**:
   * **Stop-Loss Leg:** Trigger = Structural Stop | Price = Trigger - 0.2%.
   * **Target Leg:** Trigger = Target 1 | Price = Target 1.
4. The exchange servers manage both legs automatically without screen time or 15:15 PM broker square-off risks.

---

## 8. Troubleshooting & Common Pitfalls

| Error / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| `"No data found for symbol"` | Missing `.NS` extension or wrong symbol delimiter. | Ensure tickers use standard Yahoo format: `RELIANCE.NS`, not `RELIANCE` or `RELIANCE-NS`. |
| `"Network domain blocked"` | Claude Web restricted domain environment. | In Claude Web settings, ensure network permissions allow `query1.finance.yahoo.com`. Alternatively, run via Claude Code CLI. |
| `Claude attempts to place live US order` | Skill defaulted to Alpaca connector. | Re-run the India prompt. Remind Claude that Indian execution operates in **Research / Ticket Recommendation Mode**, requiring output as a structured ticket for manual broker entry. |
| `Unrealistic position sizes` | Position sizing calculated using dollar increments or full equity value without leverage adjustment. | Ensure `trader/config.json` sets `currency: "INR"` and defines `leverage_multiplier_mis: 5.0`. |
| `Slippage during opening bell` | Placing market orders between 09:15 and 09:20 IST. | Enforce the 09:15–09:30 IST discovery rule: never trade opening wicks; enter only after the 15-minute range prints. |