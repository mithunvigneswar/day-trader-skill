import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-sbin.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
TOP5 = os.path.join(FIX, "trader-budget", "nse-top5-weights.json")
NOW = "2026-10-19 10:15"
TODAY = "2026-10-19"
REEL_V1 = {"RELIANCE.NS": 1250, "HDFCBANK.NS": 1119, "INFY.NS": 823, "ITC.NS": 708, "SBIN.NS": 600}
PRICES = {"RELIANCE.NS": 341.27, "HDFCBANK.NS": 309.84, "INFY.NS": 353.78, "ITC.NS": 90.11, "SBIN.NS": 53.96}
# the NSE example config, key for key
CONTRACT_CFG = {
    "market": "NSE", "timezone": "Asia/Kolkata", "currency": "INR",
    "budget": 50000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 15000, "min_order_inr": 5,
    "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500, "max_trades_per_day": 3, "max_open_trades": 2,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "11:30", "flat_by": "15:00",
    "leverage_multiplier_mis": 5.0, "blocked": [], "allow_short": False,
    "universe": ["RELIANCE.NS", "SBIN.NS", "ICICIBANK.NS", "HDFCBANK.NS", "INFY.NS", "TCS.NS", "JSWSTEEL.NS",
                 "HINDALCO.NS", "BHARTIARTL.NS", "TATASTEEL.NS", "BEL.NS"],
}
# plain rupee rules for the weights mode tests
V1 = {"budget": 5000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 2000, "min_order_inr": 5,
      "blocked": []}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def v1(**over):
    c = dict(V1)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["rupees"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-19T05:22:13Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"SBIN.NS": price},
            "detail": {"SBIN.NS": {"prev_close": 980.10, "time": quote_time, "source": "yahoo"}}}


class TheExample(unittest.TestCase):
    """Rs 50,000, 10% buffer, 5x MIS, Rs 500 max loss, SBIN.NS risk 4.70 -> 75 shares bound by the max order."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_sbin_is_75_shares_bound_by_the_max_order(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 75)
        self.assertEqual(s["bound_by"], "max order")
        self.assertEqual(s["risk_per_share"], 4.7)
        self.assertEqual(s["cost"], 74437.5)
        self.assertEqual(s["margin"], 14887.5)
        self.assertEqual(s["leverage"], 5.0)
        self.assertEqual(s["max_loss"], 352.5)
        self.assertEqual(s["reward_risk"], 1.06)
        self.assertEqual(s["cash_buffer"], 5000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 106), ("position cap", 188), ("max order", 75), ("cash", 226)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["max order"])
        self.assertIsNone(s["refusal"])

    def test_no_leverage_means_capital_is_the_limit(self):
        s = size.size_trade(plan(), cfg(leverage_multiplier_mis=1.0), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (15, "max order"))
        self.assertEqual((s["cost"], s["margin"]), (14887.5, 14887.5))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 106), ("position cap", 37), ("max order", 15), ("cash", 45)])

    def test_the_bracket_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "SBIN.NS", "side": "buy", "qty": "75", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "997.50", "stop_loss_stop_price": "987.80",
            "plan": {"entry": 992.5, "stop": 987.8, "target": 997.5, "risk_per_share": 4.7,
                     "max_loss": 352.5, "setup": "orb", "entry_time": "10:15 AM", "flat_by": "15:15"}})
        self.assertIn("bound by your max order", why)
        self.assertIn("\u20b9352.50", why)
        self.assertNotIn("$", why)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(os.path.join(FIX, "trader-risk", "orders-sbin.json")) as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=992.5, today=TODAY))
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", "992.50", "75"))
        s = size.size_trade(plan(), cfg(max_order_inr=100000), limit=990.0, today=TODAY)
        self.assertEqual((s["risk_per_share"], s["qty"], s["bound_by"], s["max_loss"]),
                         (2.2, 189, "position cap", 415.8))   # max loss alone would allow 227
        self.assertEqual(size.bracket_order(s)["limit_price"], "990.00")

    def test_money_is_rupees(self):
        self.assertEqual(size.money(74437.5), "\u20b974,437.50")
        self.assertEqual(size.money(500), "\u20b9500")

    def test_ist_clock(self):
        self.assertEqual(size.parse_now("2026-10-19 10:15").isoformat(), "2026-10-19T04:45:00")
        self.assertEqual(size.to_ist(size.parse_now(NOW)).isoformat(), "2026-10-19T10:15:00")
        self.assertEqual(size.parse_now("2026-10-19T04:45:00Z").isoformat(), "2026-10-19T04:45:00")

    def test_cli_writes_one_bracket_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 75 shares of SBIN.NS = \u20b974,437.50 at 992.50", text)
            self.assertIn("75 x 4.70 = \u20b9352.50", text)
            self.assertIn("Capital used (margin) \u20b914,887.50 of \u20b950,000 at 5x MIS leverage", text)
            self.assertIn("<- binds", text)
            self.assertIn("Flat by 15:15 IST", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertEqual(doc["market"], "NSE")
            self.assertNotIn("paper", doc)
            self.assertEqual(doc["created_at"], "2026-10-19T04:45:00Z")
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 352.5)

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 75)
        self.assertEqual(out["orders"][0]["limit_price"], "992.50")


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=300.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your \u20b9500 max loss per trade", s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(plan(stop=300.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_inr=1000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (5, "max order"))
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (50, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=12000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (35, "cash", 7000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=100), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (21, "max loss"))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=994.475, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (994.48, "price", 6.68))
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"], s["reward_risk"]), (74, "max loss", 494.32, 0.45))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=994.475, today=TODAY)
        self.assertIn("reward:risk 0.45 (target 997.50, entry 994.48, stop 987.80) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        lower = size.size_trade(plan(), cfg(), price=990.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (992.5, 75))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(994.475), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-19 10:55"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 74, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("as of 10:52 IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-19 11:30"])[1])
            self.assertEqual(out["sizing"]["qty"], 75)
            self.assertIn("not used", out["notes"][0])
            self.assertIn("from 2026-10-19 10:52 IST", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 12000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (35, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=995.0), "not below the entry"),
                        (plan(target=990.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1000.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["sbin.ns"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-20")
        self.assertIn("make a new plan", str(e.exception))
        self.assertIn("in IST", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC.NS", entry=4.0, stop=3.0, target=5.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=1), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your \u20b95 minimum order", s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets: Rs 5,000, 10% cash, 25% cap.

    The allocation math is checked with whole=False (plain rupees); the NSE trades whole shares,
    so the CLI and the default always size in whole shares and need prices."""

    def check_reel(self, r):
        self.assertEqual(rupees(r), REEL_V1)
        self.assertEqual(r["cash_buffer"], 500)
        self.assertEqual(r["invested"], 4500)
        self.assertEqual(r["idle"], 0)
        self.assertEqual(r["cap"], 1250)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE.NS"])

    def test_from_the_raw_weights(self):
        self.check_reel(size.size(rows(TOP5), v1(), whole=False))

    def test_from_the_renormalised_weights_file(self):
        self.check_reel(size.size(rows(TARGETS), v1(), whole=False))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg(
            "RELIANCE.NS=22.04,HDFCBANK.NS=17.14,INFY.NS=12.62,ITC.NS=10.86,SBIN.NS=9.20"))
        self.check_reel(size.size(w, v1(), whole=False))

    def test_renormalised_weights(self):
        r = size.size(rows(TOP5), v1(), whole=False)
        shown = {p["ticker"]: round(p["weight"], 1) for p in r["positions"]}
        self.assertEqual(shown, {"RELIANCE.NS": 30.7, "HDFCBANK.NS": 23.9, "INFY.NS": 17.6, "ITC.NS": 15.1,
                                 "SBIN.NS": 12.8})

    def test_one_decimal_weights_stay_within_a_rupee(self):
        w = size.parse_weights({"RELIANCE.NS": 30.7, "HDFCBANK.NS": 23.9, "INFY.NS": 17.6, "ITC.NS": 15.1,
                                "SBIN.NS": 12.8})
        r = size.size(w, v1(), whole=False)
        self.assertEqual(r["invested"], 4500)
        self.assertEqual(rupees(r)["RELIANCE.NS"], 1250)
        for t, v in rupees(r).items():
            self.assertLessEqual(abs(v - REEL_V1[t]), 1.0, t)

    def test_rupee_orders_carry_a_notional_string(self):
        r = size.size(rows(TOP5), v1(), whole=False)
        orders = size.orders_from(r, "example weights")
        self.assertEqual(orders[0], {"symbol": "RELIANCE.NS", "side": "buy", "notional": "1250", "type": "market",
                                     "time_in_force": "day", "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        for o in orders:
            self.assertIsInstance(o["notional"], str)
            self.assertNotIn("qty", o)
        self.assertEqual([o["notional"] for o in orders], ["1250", "1119", "823", "708", "600"])

    def test_paise_mode_keeps_the_total(self):
        r = size.size(rows(TOP5), v1(), cents=True, whole=False)
        d = rupees(r)
        self.assertEqual(d["RELIANCE.NS"], 1250)
        self.assertAlmostEqual(sum(d.values()), 4500, places=2)
        self.assertAlmostEqual(d["HDFCBANK.NS"], 1118.13, places=2)
        self.assertEqual(size.orders_from(r)[1]["notional"], "1118.13")

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A.NS": 60, "B.NS": 30, "C.NS": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1},
                      whole=False)
        self.assertEqual(rupees(r), {"A.NS": 400, "B.NS": 400, "C.NS": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A.NS", "B.NS"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE.NS": 50, "ITC.NS": 50})
        r = size.size(w, v1(), whole=False)
        self.assertEqual(rupees(r), {"RELIANCE.NS": 1250, "ITC.NS": 1250})
        self.assertEqual(r["idle"], 2000)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"A.NS": 90, "B.NS": 7, "C.NS": 3})
        r = size.size(w, {"budget": 100, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 5},
                      whole=False)
        self.assertNotIn("C.NS", rupees(r))
        self.assertEqual(sum(rupees(r).values()), 90)
        self.assertTrue(all(v >= 5 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "C.NS")

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(TOP5), v1(blocked=["itc.ns"]), whole=False)
        self.assertNotIn("ITC.NS", rupees(r))
        self.assertEqual(r["invested"], 4500)
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TOP5), v1(max_position_pct=100), top=2, whole=False)
        self.assertEqual(sorted(rupees(r)), ["HDFCBANK.NS", "RELIANCE.NS"])

    def test_whole_shares_are_the_default_and_need_prices(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(TOP5), v1())
        self.assertIn("whole shares need a price", str(e.exception))
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), cfg())

    def test_whole_shares_with_prices(self):
        r = size.size(rows(TOP5), v1(), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["rupees"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["rupees"], 1250)
        self.assertAlmostEqual(r["invested"] + r["idle"], 4500, places=2)
        self.assertLess(r["idle"], min(PRICES.values()) * 2)
        orders = size.orders_from(r)
        self.assertTrue(all("qty" in o and "notional" not in o for o in orders))

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), {"cash_buffer_pct": 10}, whole=False)
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "ITC.NS", "name": "", "weight": 1}, {"ticker": "ITC.NS", "name": "", "weight": 2}],
                      v1(), whole=False)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(v1(), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            self.assertNotIn("$", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]],
                             ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "ITC.NS", "SBIN.NS"])
            self.assertTrue(all("qty" in o for o in doc["orders"]))
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--json"])
            self.assertTrue(json.loads(text)["result"]["whole_shares"])
            code, _, err = run(["--targets", TARGETS, "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("whole shares need a price", err)
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)


if __name__ == "__main__":
    unittest.main()
