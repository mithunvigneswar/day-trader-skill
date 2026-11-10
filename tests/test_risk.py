import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
SBIN_PATH = os.path.join(FIX, "trader-risk", "orders-sbin.json")
V1_ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-weights.json")
NOW = "2026-10-19 10:15"
TODAY = "2026-10-19"
V1_TODAY = "2026-10-13"
PRICES = {"RELIANCE.NS": 341.27, "HDFCBANK.NS": 309.84, "INFY.NS": 353.78, "ITC.NS": 90.11, "SBIN.NS": 53.96}
QUOTE_TIME = datetime.datetime(2026, 10, 19, 5, 22, 13)   # a Yahoo SBIN.NS quote 994.475, 10:52:13 IST
# plain rupee rules with every day-trade check switched off: the long-term checks still hold
V1 = {"budget": 5000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 2000, "min_order_inr": 5,
      "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None, "no_entries_after": None,
      "max_trades_per_day": 0, "max_open_trades": 0, "max_daily_loss_inr": 0, "max_loss_per_trade_inr": 0,
      "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def v1(**over):
    c = dict(V1)
    c.update(over)
    return c


def sbin(**over):
    with open(SBIN_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop,
            "plan": {"entry": entry, "stop": stop, "target": target}}


def v1_orders():
    with open(V1_ORDERS_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, inr):
    return {"symbol": sym, "side": "buy", "notional": str(inr), "type": "market", "time_in_force": "day"}


def placed(sym, side="buy", day=TODAY, max_loss=352.5, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:20:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheExample(unittest.TestCase):
    def test_the_sbin_bracket_passes_every_check(self):
        res = check.check([sbin()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 992.5, 987.8, 997.5))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (4.7, 352.5, 1.06))
        self.assertEqual(r["value_inr"], 74437.5)
        self.assertEqual(res["cash_after"], 35112.5)       # only the margin (value / 5) leaves the cash
        self.assertEqual(res["date"], TODAY)
        self.assertEqual(res["now_ist"], "2026-10-19 10:15")
        self.assertNotIn("paper", res)

    def test_one_more_share_is_blocked_by_the_max_order_only(self):
        res = check.check([sbin(qty="76")], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["size: ₹15,086 of margin is over your ₹15,000 max order"])

    def test_without_leverage_the_full_value_is_the_capital(self):
        res = check.check([sbin(qty="15")], cfg(leverage_multiplier_mis=1.0), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["cash_after"], 35112.5)
        res = check.check([sbin()], cfg(leverage_multiplier_mis=1.0), now=at(NOW))
        self.assertIn("size: ₹74,437.50 is over your ₹15,000 max order", reasons(res))

    def test_the_loss_check_uses_the_full_position(self):
        res = check.check([sbin()], cfg(max_loss_per_trade_inr=300), now=at(NOW))
        self.assertIn("loss: 75 shares x 4.70 risk a share = ₹352.50 at the stop, over your ₹300 max loss per trade",
                      reasons(res))

    def test_fingerprint_is_shared_with_the_broker(self):
        o = sbin()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty="76")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss_stop_price="987.00")), check.fingerprint(o))
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", SBIN_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy SBIN.NS 75 shares, entry", text)
            self.assertIn("entry 992.50 (the plan's entry), stop 987.80, target 997.50: risk 4.70 a share", text)
            self.assertIn("max loss ₹352.50 (limit ₹500), reward:risk 1.06 (min 1)", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 10:15 IST", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(sbin()))

    def test_money_is_rupees(self):
        self.assertEqual(check.money(1500), "₹1,500")
        self.assertEqual(check.money(352.5), "₹352.50")


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = sbin()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 992.5}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = sbin()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 352.5)

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 992.50", reasons(self.one(sbin(stop_loss_stop_price="995.00"))))
        res = self.one(sbin(take_profit_limit_price="990.00"))
        self.assertIn("target: the target 990.00 must be above the entry 992.50", reasons(res))

    def test_reward_risk(self):
        res = self.one(sbin(take_profit_limit_price="995.00"))
        self.assertIn("reward:risk: 0.53", reasons(res))
        self.assertEqual(verdicts(self.one(sbin(take_profit_limit_price="995.00"), min_reward_risk=0.5)), ["PASS"])

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-10-19 10:55")
        res = check.check([sbin()], cfg(), prices={"SBIN.NS": 994.475}, now=later,
                          price_times={"SBIN.NS": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (994.48, "price", 6.68))
        self.assertIn("loss: 75 shares x 6.68 risk a share = ₹501 at the stop", reasons(res))

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-10-19 11:30", "2026-10-19 10:15"):
            res = check.check([sbin()], cfg(), prices={"SBIN.NS": 994.475}, now=at(now),
                              price_times={"SBIN.NS": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 992.5)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([sbin()], cfg(), prices={"SBIN.NS": 985.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 992.5)

    def test_a_limit_order_uses_its_limit_price(self):
        o = sbin(type="limit", limit_price="992.00")
        res = check.check([o], cfg(), prices={"SBIN.NS": 994.475}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (992.0, 315.0, "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("TCS.NS"), placed("INFY.NS")]}
        res = check.check([sbin()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, ₹705 of ₹1,500 daily loss",
                      res["results"][0]["info"][-1])
        three = {"orders": [placed("TCS.NS", max_loss=500), placed("INFY.NS", max_loss=500),
                            placed("ITC.NS", max_loss=500)]}
        res = check.check([sbin()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹1,500 already at risk today plus ₹352.50 for this one = "
                      "₹1,852.50, over your ₹1,500", why)
        yesterday = {"orders": [placed(s, day="2026-10-16") for s in ("TCS.NS", "INFY.NS", "ITC.NS")]}
        self.assertEqual(verdicts(check.check([sbin()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("TCS.NS"), placed("TCS.NS", side="sell", kind="exit", max_loss=None),
                         placed("INFY.NS", status="declined"), placed("ITC.NS", status="failed")]}
        res = check.check([sbin()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [bracket(s, 1, 500.0, 100.0, 900.0) for s in ("AAA.NS", "BBB.NS", "CCC.NS", "DDD.NS")]
        res = check.check(orders, cfg(max_open_trades=0), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: ₹1,200 already at risk today plus ₹400 for this one = ₹1,600", why)

    def test_max_open_trades_counts_the_batch(self):
        orders = [bracket(s, 1, 500.0, 100.0, 900.0) for s in ("AAA.NS", "BBB.NS", "CCC.NS")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("open trades: 2 open (AAA.NS, BBB.NS), your limit is 2 at a time", reasons(res, 2))

    def test_max_open_trades_counts_what_you_hold(self):
        pf = {"cash": 40000, "positions": [
            {"ticker": "TCS.NS", "shares": 10, "lots": [{"date": TODAY, "shares": 10, "cost": 3000}]},
            {"ticker": "INFY.NS", "shares": 5, "lots": [{"date": TODAY, "shares": 5, "cost": 2000}]}]}
        res = check.check([sbin()], cfg(), portfolio=pf, now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["open trades: 2 open (INFY.NS, TCS.NS), your limit is 2 at a time"])
        # adding to a symbol you already hold is not a new open trade
        pf["positions"][0]["ticker"] = "SBIN.NS"
        pf["positions"][0]["shares"] = 1
        res = check.check([sbin()], cfg(max_open_trades=2), portfolio=pf, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"], res["results"][0]["reasons"])
        self.assertEqual(verdicts(check.check([sbin()], cfg(max_open_trades=0), portfolio=pf, now=at(NOW))),
                         ["PASS"])

    def test_entry_window_ist(self):
        cases = [("2026-10-19 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-19 11:31", "no new entries after 11:30 IST (it is 11:31)"),
                 ("2026-10-19 09:00", "the market is closed (Mon 2026-10-19 09:00"),
                 ("2026-10-19 09:14", "the market is closed"),
                 ("2026-10-19 15:30", "the market is closed"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-10-20 10:15", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-10-19T03:55:00Z", "no new entries before 09:30 IST (it is 09:25)")]
        for now, want in cases:
            res = self.one(sbin(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-19 09:30", "2026-10-19 11:30", "2026-10-19T04:00:00Z", "2026-10-19T06:00:00Z",
                    "2026-11-09 10:15"):
            self.assertEqual(verdicts(self.one(sbin(), now=now)), ["PASS"], now)
        res = self.one(sbin(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError) as e:
            self.one(sbin(), no_entries_before="9.45am")
        self.assertIn("IST time", str(e.exception))

    def test_the_session_is_09_15_to_15_30(self):
        self.assertEqual((check.OPEN, check.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertFalse(hasattr(check, "EARLY"))
        self.assertFalse(hasattr(check, "NYSE_EARLY_CLOSE"))

    def test_the_window_uses_ist_not_the_machine(self):
        self.assertEqual(check.to_ist(at("2026-10-19 10:15")), datetime.datetime(2026, 10, 19, 10, 15))
        self.assertEqual(at("2026-10-19 10:15"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-12-01 10:15"), datetime.datetime(2026, 12, 1, 4, 45))   # no daylight saving

    def test_the_holiday_list_is_the_nse_one(self):
        self.assertEqual(check.NSE_HOLIDAYS["2026-11-10"], "Diwali-Balipratipada")
        self.assertEqual(check.NSE_HOLIDAYS["2026-01-26"], "Republic Day")
        self.assertEqual(len(check.NSE_HOLIDAYS), 20)
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 9)), (True, ""))
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 7)), (False, "weekend"))

    def test_the_holiday_cache_is_merged_and_a_broken_one_is_ignored(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "trader", "state"))
            cache = os.path.join(d, "trader", "state", "nse_holidays.json")
            try:
                os.chdir(d)
                self.assertEqual(check.load_holidays(), check.NSE_HOLIDAYS)           # no cache file
                with open(cache, "w") as fh:
                    json.dump({"source": "https://www.nseindia.com/api/holiday-master?type=trading",
                               "segment": "CM", "fetched_at": "2027-01-04T08:30:00+05:30",
                               "holidays": {"2027-01-26": "Republic Day", "2026-11-10": "Diwali Balipratipada (NSE)"}}, fh)
                merged = check.load_holidays()
                self.assertEqual(merged["2027-01-26"], "Republic Day")
                self.assertEqual(merged["2026-11-10"], "Diwali Balipratipada (NSE)")  # the cache wins
                self.assertEqual(merged["2026-01-26"], "Republic Day")                 # the built-in stays
                self.assertEqual(check.calendar_years(merged), [2026, 2027])
                res = check.check([sbin()], cfg(), now=at("2027-01-26 10:15"))
                self.assertIn("NSE holiday: Republic Day", reasons(res))
                self.assertEqual(res["warnings"], [])
                with open(cache, "w") as fh:
                    fh.write("{not json")
                self.assertEqual(check.load_holidays(), check.NSE_HOLIDAYS)
                with open(cache, "w") as fh:
                    json.dump({"holidays": "nope"}, fh)
                self.assertEqual(check.load_holidays(), check.NSE_HOLIDAYS)
            finally:
                os.chdir(old)

    def test_a_date_outside_the_holiday_list_warns(self):
        res = check.check([sbin()], cfg(), now=at("2028-01-03 10:15"))
        self.assertIn("the holiday list covers 2026 only", res["warnings"][0])
        self.assertIn("scan.py calendar", res["warnings"][0])

    def test_a_short_entry_when_allowed(self):
        short = {"symbol": "SBIN.NS", "side": "sell", "qty": "75", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "987.50", "stop_loss_stop_price": "997.50",
                 "plan": {"entry": 992.5}}
        res = self.one(short)
        self.assertIn("short: selling more SBIN.NS than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 375.0, 1.0))
        res = self.one(dict(short, stop_loss_stop_price="990.00"), allow_short=True)
        self.assertIn("must be above the entry 992.50 for a sell", reasons(res))

    def test_bracket_format(self):
        self.assertIn("whole-share qty", reasons(self.one(dict(sbin(qty=None), notional="2900"))))
        self.assertIn("whole shares only", reasons(self.one(sbin(qty="75.5"))))
        self.assertIn("time_in_force day or gtc", reasons(self.one(sbin(time_in_force="ioc"))))
        self.assertEqual(verdicts(self.one(sbin(time_in_force="gtc"))), ["PASS"])
        o = sbin()
        o.pop("take_profit_limit_price")
        self.assertIn("needs both take_profit_limit_price and stop_loss_stop_price", reasons(self.one(o)))
        self.assertIn("needs a stop_loss_stop_price", reasons(self.one(dict(sbin(), stop_loss_stop_price=None,
                                                                            stop_loss_limit_price="987.00"))))
        self.assertIn("as a string", warnings(self.one(sbin(stop_loss_stop_price=987.8))))


class Exits(unittest.TestCase):
    PF = {"cash": 20600.0, "positions": [{"ticker": "SBIN.NS", "shares": 80,
                                           "lots": [{"date": TODAY, "shares": 80, "cost": 79400.0}]}]}
    SELL = {"symbol": "SBIN.NS", "side": "sell", "qty": "80", "type": "market", "time_in_force": "day"}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("SBIN.NS"), placed("TCS.NS"), placed("INFY.NS")] +
                          [placed("T%d.NS" % i, max_loss=1) for i in range(6)] +
                          [placed("SBIN.NS", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["SBIN.NS"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-17 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-11-10 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])          # even on an NSE holiday: getting out is never blocked

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, qty="40")
        res = check.check([half, half], cfg(), portfolio=self.PF, prices={"SBIN.NS": 992.5}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"SBIN.NS": 992.5}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(self.SELL, qty="81")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more SBIN.NS than you hold (80 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([sbin(), dict(self.SELL, qty="75")], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class V1Checks(unittest.TestCase):
    """The long-term checks still hold (day-trade checks off)."""

    def one(self, order, **over):
        return check.check([order], v1(**over), prices=PRICES, today=V1_TODAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_5000_account(self):
        res = check.check(v1_orders(), v1(), today=V1_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 500)
        self.assertEqual(res["results"][0]["value_inr"], 1250)
        self.assertEqual(res["results"][0]["amount"], "₹1,250")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(v1_orders() + [buy("RELIANCE.NS", 100)], v1(), today=V1_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_example_config_blocks_buys_without_a_stop(self):
        res = check.check(v1_orders(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        self.assertIn("over your ₹2,000 max order",
                      " ".join(self.one(buy("ITC.NS", 2500), max_position_pct=100)["reasons"]))
        self.assertIn("under your ₹5 minimum", " ".join(self.one(buy("ITC.NS", 2))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("ITC.NS", 100), blocked=["ITC.NS"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_only_nse_tickers_pass(self):
        for sym in ("BE270617C00100000", "AAPL", "BRK-B", "BTC/USD", "ITC", "ITC-NS", "itc.bo"):
            o = {"symbol": sym, "side": "buy", "qty": "1", "type": "limit", "limit_price": "12.50",
                 "time_in_force": "day"}
            r = self.one(o)
            self.assertEqual(r["verdict"], "BLOCK", sym)
            self.assertIn("does not look like an NSE ticker", " ".join(r["reasons"]), sym)
        for sym in ("M&M.NS", "BAJAJ-AUTO.NS", "SBIN.NS", "TATASTEEL.NS"):
            o = {"symbol": sym, "side": "buy", "qty": "1", "type": "limit", "limit_price": "12.50",
                 "time_in_force": "day"}
            self.assertNotIn("NSE ticker", " ".join(self.one(o)["reasons"]), sym)

    def test_format_rules(self):
        both = dict(buy("ITC.NS", 100), qty="1")
        self.assertIn("exactly one of qty or notional", " ".join(self.one(both)["reasons"]))
        lim = dict(buy("ITC.NS", 100), type="limit", limit_price="90")
        self.assertIn("market order with time_in_force day", " ".join(self.one(lim)["reasons"]))
        nolimit = {"symbol": "ITC.NS", "side": "buy", "qty": "1", "type": "limit", "time_in_force": "day"}
        self.assertIn("needs a limit_price", " ".join(self.one(nolimit)["reasons"]))
        bad = {"symbol": "ITC.NS", "side": "hold", "notional": "100"}
        self.assertIn("side must be buy or sell", " ".join(self.one(bad)["reasons"]))

    def test_numbers_should_be_strings(self):
        r = self.one({"symbol": "ITC.NS", "side": "buy", "notional": 100, "type": "market", "time_in_force": "day"})
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("as a string", r["warnings"][0])

    def test_share_orders_need_a_price(self):
        q = {"symbol": "ZZZZ.NS", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day"}
        self.assertIn("no price for ZZZZ.NS", " ".join(self.one(q)["reasons"]))
        q["symbol"] = "ITC.NS"
        r = self.one(q)
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_inr"], 180.22)

    def test_more_than_your_cash_is_blocked(self):
        r = check.check([buy("ITC.NS", 1500)], v1(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, today=V1_TODAY)["results"][0]
        self.assertIn("cash: needs ₹1,500 but only ₹1,000 cash", " ".join(r["reasons"]))

    def test_mis_leverage_turns_cash_into_buying_power(self):
        c = v1(cash_buffer_pct=0, max_position_pct=100, max_order_inr=0, leverage_multiplier_mis=5)
        res = check.check([buy("ITC.NS", 4000)], c, portfolio={"cash": 1000, "positions": []}, today=V1_TODAY)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["cash_after"], 200)           # Rs 800 of margin is held, not Rs 4,000
        res = check.check([buy("ITC.NS", 6000)], c, portfolio={"cash": 1000, "positions": []}, today=V1_TODAY)
        self.assertIn("cash: needs ₹1,200 of margin (₹6,000 at 5x MIS leverage) but only ₹1,000 cash",
                      reasons(res))

    PF = {"cash": 500.0, "positions": [{"ticker": "ITC.NS", "shares": 8,
                                         "lots": [{"date": "2026-10-13", "shares": 8, "cost": 720.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "ITC.NS", "side": "sell", "qty": "10", "type": "market", "time_in_force": "day"}
        r = check.check([sell], v1(), portfolio=self.PF, prices=PRICES, today=V1_TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = "3"
        r = check.check([sell], v1(), portfolio=self.PF, prices=PRICES, today=V1_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "ITC.NS", "side": "sell", "qty": "5", "type": "market", "time_in_force": "day"},
                  buy("SBIN.NS", 450)]
        res = check.check(orders, v1(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=V1_TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("SBIN.NS", 450)], v1(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=V1_TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d.NS" % i, "side": "buy", "status": "placed", "at": V1_TODAY + "T10:00:00"}
                         for i in range(10)]}
        res = check.check([buy("ITC.NS", 100)], v1(), state=st, today=V1_TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC.NS", 100)], v1(), state=st, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        ko = {"orders": [{"symbol": "ITC.NS", "side": "buy", "status": "placed", "at": V1_TODAY + "T10:00:00"}]}
        res = check.check([buy("ITC.NS", 100)], v1(), state=ko, today=V1_TODAY)
        self.assertIn("already placed today", reasons(res))
        declined = {"orders": [{"symbol": "ITC.NS", "side": "buy", "status": "declined", "at": V1_TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC.NS", 100)], v1(), state=declined, today=V1_TODAY)), ["PASS"])

    def test_there_is_no_paper_or_live_account(self):
        res = check.check([buy("ITC.NS", 100)], v1(paper=False), today=V1_TODAY)
        self.assertEqual(res["warnings"], [])
        self.assertNotIn("paper", res)


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-19T05:25:00Z", "prices": {"SBIN.NS": 994.475},
                           "detail": {"SBIN.NS": {"time": "2026-10-19T05:22:13Z", "fetched": "2026-10-19T05:25:00Z"}}},
                          fh)
            none = os.path.join(d, "none")
            base = ["--orders", SBIN_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:55"]), 1)
            self.assertIn("75 shares x 6.68 risk a share = ₹501 at the stop", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 11:30"]), 0)

    def test_weights_orders_with_a_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(v1(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", V1_ORDERS_PATH, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", V1_TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], V1_TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": v1_orders() + [buy("RELIANCE.NS", 100)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", V1_TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", SBIN_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertIn("IST", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", SBIN_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()
