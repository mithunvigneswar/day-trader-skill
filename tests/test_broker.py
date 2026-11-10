import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-sbin.json")
PLAN_PATH = os.path.join(FIX, "trader-budget", "plan-sbin.json")
NOW = "2026-10-19 10:15"
TODAY = "2026-10-19"
RELIANCE = {"symbol": "RELIANCE.NS", "side": "buy", "notional": "1250", "type": "market", "time_in_force": "day"}
BATCH = ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "ITC.NS", "SBIN.NS"]
SBIN_CALL = {"symbol": "SBIN.NS", "side": "buy", "qty": "75", "type": "market", "time_in_force": "day",
             "order_class": "bracket", "take_profit_limit_price": "997.50", "stop_loss_stop_price": "987.80"}
SBIN_KITE = {"exchange": "NSE", "tradingsymbol": "SBIN", "transaction_type": "BUY", "quantity": "75",
             "product": "MIS", "order_type": "MARKET", "stop_loss_sl_m_trigger": "987.80",
             "target_1_limit": "997.50"}
SBIN_PLAIN = ("BUY 75 SBIN.NS, market, good for today / stop-loss 987.80 / target 997.50 / max loss about "
              "₹352.50 if the stop fills at 987.80 (it can fill worse in a fast market)")


def sbin(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def cfg():
    with open(CFG_PATH) as fh:
        return json.load(fh)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=RELIANCE, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes RELIANCE", "yes RELIANCE.NS",
                  "yes ₹1,250 of RELIANCE", "yes 1250"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes HDFCBANK", "yes HDFCBANK.NS", "yes 1000", "yes but make it 1000", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 340"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "ITC.NS", "side": "sell", "qty": "3", "type": "market", "time_in_force": "day"}
        self.assertTrue(self.yes("yes sell 3 ITC", sell))
        self.assertFalse(self.yes("yes sell 4", sell))

    def test_long_symbols_are_matched_whole(self):
        # RELIANCE.NS is longer than a US ticker: naming another long symbol must still be caught
        o = {"symbol": "TATASTEEL.NS", "side": "buy", "qty": "5", "type": "market", "time_in_force": "day"}
        batch = ["TATASTEEL.NS", "BHARTIARTL.NS", "ICICIBANK.NS"]
        self.assertTrue(self.yes("yes TATASTEEL", o, batch))
        self.assertFalse(self.yes("yes BHARTIARTL", o, batch))
        self.assertFalse(self.yes("yes ICICIBANK.NS", o, batch))

    def test_a_bracket_yes(self):
        o = sbin()
        for r in ("yes", "yes 75 SBIN", "yes 987.80", "yes max loss 352.50", "yes target 997.50"):
            self.assertTrue(self.yes(r, o, ["SBIN.NS"]), r)
        for r in ("yes 76", "yes 100 SBIN", "yes but stop at 985", "yes, stop 987.80", "yes RELIANCE", "sure"):
            self.assertFalse(self.yes(r, o, ["SBIN.NS", "RELIANCE.NS"]), r)


class TicketShape(unittest.TestCase):
    def test_kite_fields_for_the_bracket(self):
        self.assertEqual(ticket.kite_fields(sbin()), SBIN_KITE)
        self.assertEqual(ticket.fingerprint(sbin()), ticket.fingerprint(SBIN_CALL))

    def test_kite_fields_for_other_order_types(self):
        lim = sbin(type="limit", limit_price="992.00")
        f = ticket.kite_fields(lim)
        self.assertEqual((f["order_type"], f["price"], f["product"], f["exchange"]), ("LIMIT", "992.00", "MIS", "NSE"))
        slm = {"symbol": "M&M.NS", "side": "sell", "qty": "10", "type": "stop", "stop_price": "3100",
               "time_in_force": "day"}
        self.assertEqual(ticket.kite_fields(slm), {"exchange": "NSE", "tradingsymbol": "M&M",
                                                   "transaction_type": "SELL", "quantity": "10", "product": "MIS",
                                                   "order_type": "SL-M", "trigger_price": "3100.00"})
        sl = dict(slm, type="stop_limit", limit_price="3095")
        f = ticket.kite_fields(sl)
        self.assertEqual((f["order_type"], f["trigger_price"], f["price"]), ("SL", "3100.00", "3095.00"))

    def test_the_fields_leave_out_why_and_plan(self):
        f = ticket.kite_fields(sbin())
        for k in ("why", "plan", "symbol", "qty", "order_class"):
            self.assertNotIn(k, f)

    def test_plain_english(self):
        self.assertEqual(ticket.plain(RELIANCE), "BUY ₹1,250 of RELIANCE.NS, market order, good for today")
        self.assertEqual(ticket.plain({"symbol": "ITC.NS", "side": "sell", "qty": "1", "type": "limit",
                                       "limit_price": "95", "time_in_force": "gtc"}),
                         "SELL 1 share of ITC.NS, limit order at ₹95, good until canceled")
        self.assertEqual(ticket.plain(sbin()), SBIN_PLAIN)
        lim = sbin(type="limit", limit_price="992.00")
        self.assertEqual(ticket.plain(lim).split(" / ")[0], "BUY 75 SBIN.NS, limit 992.00, good for today")
        self.assertEqual(ticket.max_loss(lim), (315.0, 987.8))
        no_plan = dict(SBIN_CALL)
        self.assertEqual(ticket.max_loss(no_plan), (None, 987.8))

    def test_bare_symbol(self):
        self.assertEqual(ticket.bare("sbin.ns"), "SBIN")
        self.assertEqual(ticket.bare("M&M.NS"), "M&M")
        self.assertEqual(ticket.bare("TCS"), "TCS")


class NoBroker(unittest.TestCase):
    def test_nothing_connects_to_a_broker_or_a_network(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")))
        with open(os.path.join(ROOT, "trader-broker", "ticket.py"), encoding="utf-8") as fh:
            src = fh.read()
        for word in ("urllib", "socket", "http", "requests", "subprocess", "place_stock_order", "ALPACA", "alpaca"):
            self.assertNotIn(word, src, word)
        self.assertFalse(hasattr(ticket, "tool_input"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_yes_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, NSE intraday (MIS), you enter it in your broker app", text)
        for part in SBIN_PLAIN.split(" / ")[:1] + ["stop-loss 987.80", "target 997.50"]:
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn('"tradingsymbol": "SBIN"', text)
        self.assertIn('"product": "MIS"', text)
        self.assertIn('"stop_loss_sl_m_trigger": "987.80"', text)
        self.assertIn('"target_1_limit": "997.50"', text)
        self.assertIn("SELL SL-M", text)
        self.assertIn("Nothing is sent", text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        self.assertNotIn("Allow once", text)
        self.assertNotIn("place_stock_order", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("enter it yourself in your broker app", text)
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1]), SBIN_KITE)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)
        self.assertIn("broker app", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "2610190001",
                                      "--filled-qty", "75", "--filled-price", "992.60", "--portfolio", self.pf,
                                      now="2026-10-19 10:16"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 352.5, TODAY))
        self.assertEqual(appr["at"], "2026-10-19T10:15:00+05:30")
        self.assertEqual(appr["market"], "NSE")
        self.assertNotIn("paper", appr)
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"]), ("2610190001", 352.5, "entry"))
        with open(self.pf) as fh:
            pf = json.load(fh)
        # the portfolio is kept at full value: the 5x MIS margin is trader-risk's business, not the log's
        self.assertEqual(pf["cash"], -24445.0)
        self.assertEqual(pf["positions"][0]["shares"], 75)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss ₹352.50", text)

        # trader-risk now counts it: a second SBIN buy today is a duplicate, and Rs 352.50 is used
        res = check.check([sbin()], cfg(), state=st, now=check.parse_now("2026-10-19 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 352.5))
        # and the leveraged position does not use up the cash for the next trade: only its margin is held
        other = sbin(symbol="TCS.NS")
        res = check.check([other], cfg(), portfolio=pf, state=st, now=check.parse_now("2026-10-19 10:30"))
        self.assertEqual(res["results"][0]["verdict"], "PASS", res["results"][0]["reasons"])
        self.assertEqual(res["cash_after"], 20223.5)   # 35,111.00 free minus 14,887.50 margin

    def test_flatten_by_the_square_off(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc",
                      "--filled-qty", "75", "--filled-price", "992.60", "--portfolio", self.pf))
        late = "2026-10-19 15:10"
        code, text, _ = run(["flatten", "--symbol", "SBIN.NS", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 75 shares of SBIN.NS, market order, good for today (flat by 15:15 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("cancel it first", text)
        self.assertIn("broker app", text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["market"], "NSE")
        self.assertNotIn("paper", doc)
        self.assertEqual(ticket.kite_fields(doc["orders"][0]),
                         {"exchange": "NSE", "tradingsymbol": "SBIN", "transaction_type": "SELL", "quantity": "75",
                          "product": "MIS", "order_type": "MARKET"})
        self.assertEqual(self.run_risk(now=late), 0)       # after the 15:00 entry cutoff: an exit still passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 75 SBIN", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "75",
                                   "--filled-price", "995.10", "--portfolio", self.pf, now="2026-10-19 15:11"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (50187.5, []))     # 50,000 + 75 x (995.10 - 992.60)
        st = self.state_doc()
        self.assertEqual([(r["status"], r["side"]) for r in st["orders"]],
                         [("approved", "buy"), ("placed", "buy"), ("filled", "buy"),
                          ("approved", "sell"), ("placed", "sell"), ("filled", "sell")])
        code, _, err = run(["flatten", "--symbol", "SBIN.NS", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "sbin.ns", "--qty", "75", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Check it against your broker app", text)
        self.assertIn("flat by 15:00 IST", text)
        self.assertEqual(run(["flatten", "--symbol", "SBIN.NS", "--qty", "0", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = "76"
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-16 10:15")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-19 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-19 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-19 09:20"))
        self.assertEqual(code, 1)

    def test_an_nse_holiday_blocks_the_ticket(self):
        self.assertEqual(self.run_risk(now="2026-11-10 10:15"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-11-10 10:15"))
        self.assertIn("NSE holiday: Diwali-Balipratipada", text)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_a_leftover_broker_setting_changes_nothing(self):
        c = cfg()
        c["broker"] = "ibkr"
        c["paper"] = False
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertNotIn("Interactive Brokers", text)
        self.assertNotIn("LIVE", text)
        self.assertNotIn("place_stock_order", text)
        self.assertIn("broker app", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC.NS", "shares": 3, "lots": [
            {"id": "ITC.NS-1", "date": "2025-01-02", "shares": 2, "cost": 120.0},
            {"id": "ITC.NS-2", "date": "2026-01-02", "shares": 1, "cost": 70.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC.NS", "--side", "sell", "--qty", "2.5", "--price", "90",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("₹225", text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 225.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC.NS-2"])
        self.assertEqual(lots[0]["shares"], 0.5)
        self.assertEqual(lots[0]["cost"], 35.0)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC.NS", "--side", "sell", "--qty", "5", "--price", "90",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
