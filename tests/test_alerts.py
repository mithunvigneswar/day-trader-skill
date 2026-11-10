import datetime
import gzip
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

# The quote fixtures are US Yahoo quotes reshaped to NSE (IST, INR, ".NS" added to each name), so the
# numbers are real but the tickers are not NSE listings.
QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-tsla.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"

_saved = {}


def setUpModule():
    """No test touches the network or a saved holiday list unless it sets that up itself."""
    _saved.update(urlopen=alerts._urlopen, holidays=alerts.HOLIDAYS_FILE)
    alerts.HOLIDAYS_FILE = os.path.join(tempfile.gettempdir(), "no-such-dir-trader", "nse_holidays.json")

    def blocked(*a, **k):
        raise AssertionError("a test reached the network")
    alerts._urlopen = blocked


def tearDownModule():
    alerts._urlopen, alerts.HOLIDAYS_FILE = _saved["urlopen"], _saved["holidays"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_the_builtin_holiday_list(self):
        self.assertEqual(len(alerts.NSE_HOLIDAYS), 20)                  # NSE holiday-master, equity segment
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-10-20"], "Dussehra")
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-11-10"], "Diwali-Balipratipada")
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-11-08"], "Diwali Laxmi Pujan")
        self.assertEqual(sorted({d[:4] for d in alerts.NSE_HOLIDAYS}), ["2026"])
        self.assertFalse(hasattr(alerts, "NYSE_HOLIDAYS"))

    def test_ist_has_no_daylight_saving(self):
        self.assertEqual(alerts.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(alerts.to_ist(DT(2026, 3, 8, 7, 0)), DT(2026, 3, 8, 12, 30))
        self.assertEqual(alerts.to_ist(DT(2026, 11, 1, 6, 0)), DT(2026, 11, 1, 11, 30))

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-13T09:15"), DT(2026, 10, 13, 3, 45))
        self.assertEqual(ist("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ist("2026-12-01T09:15"), DT(2026, 12, 1, 3, 45))
        self.assertEqual(ist("2026-10-13T20:00:01Z"), DT(2026, 10, 13, 20, 0, 1))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["now_ist"]), ("09:15", "15:30", "2026-10-13 10:00"))
        self.assertFalse(alerts.market_status(ist("2026-10-13T09:14"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T09:15"))["open"])
        self.assertEqual(alerts.market_status(ist("2026-10-13T09:14"))["next_open"], "2026-10-13 09:15")
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:29"))["open"])
        after = alerts.market_status(ist("2026-10-13T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["reason"], "after the close")
        self.assertEqual(after["next_open"], "2026-10-14 09:15")
        dussehra = alerts.market_status(ist("2026-10-20T11:00"))
        self.assertFalse(dussehra["open"])
        self.assertEqual(dussehra["reason"], "NSE holiday: Dussehra")
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        diwali = alerts.market_status(ist("2026-11-10T11:00"))
        self.assertEqual(diwali["reason"], "NSE holiday: Diwali-Balipratipada")
        self.assertEqual(diwali["next_open"], "2026-11-11 09:15")
        xmas = alerts.market_status(ist("2026-12-25T10:00"))
        self.assertFalse(xmas["open"])
        self.assertEqual(xmas["next_open"], "2026-12-28 09:15")             # the weekend in between
        self.assertEqual(alerts.market_status(ist("2026-10-17T10:00"))["reason"], "weekend")
        self.assertFalse(any("early_close" in alerts.market_status(ist(d)) for d in ("2026-11-27T10:00",)))

    def test_no_early_closes(self):
        self.assertTrue(alerts.market_status(ist("2026-11-27T13:00"))["open"])      # a US half day
        self.assertTrue(alerts.market_status(ist("2026-12-24T14:00"))["open"])
        self.assertEqual(alerts.session(datetime.date(2026, 12, 24)), (alerts.OPEN, alerts.CLOSE))

    def test_calendar_years_and_the_saved_list(self):
        self.assertTrue(alerts.market_status(ist("2026-06-01T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2027-06-01T10:00"))["calendar_known"])
        d = tempfile.mkdtemp()
        old = alerts.HOLIDAYS_FILE
        try:
            alerts.HOLIDAYS_FILE = os.path.join(d, "nse_holidays.json")
            with open(alerts.HOLIDAYS_FILE, "w") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            self.assertEqual(alerts.calendar_years(), [2026, 2027])
            st = alerts.market_status(ist("2027-01-26T10:00"))
            self.assertTrue(st["calendar_known"])
            self.assertEqual(st["reason"], "NSE holiday: Republic Day")
            self.assertEqual(alerts.load_holidays()["2026-10-20"], "Dussehra")        # built-in kept
            with open(alerts.HOLIDAYS_FILE, "w") as fh:
                fh.write("not json")
            self.assertEqual(alerts.load_holidays(), alerts.NSE_HOLIDAYS)             # junk is ignored
        finally:
            alerts.HOLIDAYS_FILE = old
            shutil.rmtree(d)


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-AAPL.NS.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "AAPL.NS")
        self.assertEqual(q["price"], 341.27)
        self.assertEqual(q["prev_close"], 340.97)
        self.assertEqual(q["time"], "2026-10-13T10:15:01Z")
        self.assertEqual(q["currency"], "INR")
        with open(os.path.join(QDIR, "yahoo-TSLA.NS.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "TSLA.NS")
        self.assertEqual((q["price"], q["prev_close"], round(q["open"], 2)), (366.475, 354.72, 357.48))
        self.assertEqual(q["time"], "2026-10-19T05:07:13Z")

    def test_symbols_keep_their_dot(self):
        self.assertEqual(alerts._sym(" reliance.ns "), "RELIANCE.NS")
        self.assertEqual(alerts._sym("sbin"), "SBIN.NS")                 # a bare name gets .NS
        self.assertEqual(alerts._sym("M&M.NS"), "M&M.NS")
        self.assertEqual(alerts._sym("^nsei"), "^NSEI")                  # an index stays as typed
        self.assertEqual(alerts._sym(""), "")

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("reliance.ns"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")
        self.assertEqual(alerts.yahoo_url("m&m.ns"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")
        self.assertEqual(alerts.yahoo_url("BAJAJ-AUTO.NS"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=1d")
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TSLA.NS", alerts.YAHOO_HOSTS[1]))

    def test_there_is_no_stooq(self):
        for gone in ("stooq_url", "parse_stooq"):
            self.assertFalse(hasattr(alerts, gone), gone)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                alerts.main(["quote", "SBIN.NS", "--source", "stooq"])

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["AAPL.NS", "ko", "ZZZZ.NS", "NOPE.NS"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["AAPL.NS", "KO.NS"])
        self.assertEqual(q["KO.NS"]["price"], 90.11)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("ZZZZ.NS", errors[0])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-TSLA.NS.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-TSLA.NS.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["TSLA.NS"], "yahoo", d)
            self.assertEqual((q["TSLA.NS"]["price"], errors), (366.475, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-TSLA.NS.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent"), req.full_url))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["TSLA.NS"])
        self.assertEqual((q["TSLA.NS"]["price"], errors), (366.475, []))
        self.assertEqual([c[:2] for c in f.calls], [("query2.finance.yahoo.com", UA)])
        self.assertEqual(f.calls[0][2], "https://query2.finance.yahoo.com/v8/finance/chart/TSLA.NS?range=1d&interval=1d")

    def test_a_bare_name_is_asked_for_as_ns(self):
        f = self.net([200])
        alerts.get_quotes(["tsla"])
        self.assertIn("/chart/TSLA.NS?", f.calls[0][2])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["TSLA.NS"])[0]["TSLA.NS"]["price"], 366.475)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("TSLA.NS", alerts.get_quotes(["TSLA.NS"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("TSLA.NS", alerts.get_quotes(["TSLA.NS"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["TSLA.NS"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/TSLA.NS answered HTTP 502", errors[0])
        self.assertEqual(set(self.hosts(f)), {"query2", "query1"})          # nothing else is ever tried

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE.NS"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["TSLA.NS"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_tickers(self):
        f = self.net([200, 200])
        alerts.get_quotes(["TSLA.NS", "AAPL.NS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["AAPL.NS", "BAC.NS", "KO.NS", "TSLA.NS"], "yahoo", QDIR)
        self.open = alerts.market_status(ist("2026-10-13T10:00"))
        self.now = ist("2026-10-13T10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "aapl-340", "symbol": "AAPL.NS", "type": "above", "price": 340}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("AAPL.NS is ₹341.27: at or above your alert level ₹340", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_a_bare_symbol_in_a_rule_means_ns(self):
        rules = [{"id": "aapl-340", "symbol": "aapl", "type": "above", "price": 340}]
        rings, _ = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual([r["symbol"] for r in rings], ["AAPL.NS"])

    def test_stop_and_rearm(self):
        rules = [{"id": "bac-stop", "symbol": "BAC.NS", "type": "stop", "price": 54, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop ₹54", rings[0]["message"])
        self.q["BAC.NS"]["price"] = 55.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("bac-stop", st["rung"])
        self.q["BAC.NS"]["price"] = 53.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "bac-3", "symbol": "BAC.NS", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "bac-1", "symbol": "BAC.NS", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-1.14%", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "bac-up", "symbol": "BAC.NS", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        tsla = [{"id": "tsla-3", "symbol": "TSLA.NS", "type": "pct_move", "pct": 3}]
        rings, _ = self.at("2026-10-19 10:52", tsla)
        self.assertIn("TSLA.NS moved +3.31% to ₹366.48 (from ₹354.72)", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-13T08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-13T08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-10-13 10:00 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-13T11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-14T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-14T09:16"))[0]), 1)
        holiday = alerts.market_status(ist("2026-10-20T10:00"))                  # Dussehra: no bell
        self.assertEqual(alerts.evaluate(rules, {}, holiday, {}, ist("2026-10-20T10:00"))[0], [])

    def test_the_plan_gives_a_stop_a_target_and_a_square_off_time(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("tsla.ns-plan-stop", "stop"), ("tsla.ns-plan-target", "target"),
                          ("tsla.ns-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (358.32, 370.04))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:15", "square off MIS"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-10-19" for r in rules))
        rings, st = self.at("2026-10-19 10:52", rules)          # the 366.475 quote, between stop and target
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["TSLA.NS"]["price"] = 358.10
        rings, st = self.at("2026-10-19 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["tsla.ns-plan-stop"])
        self.assertIn("TSLA.NS is ₹358.10: at or below your stop ₹358.32 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["TSLA.NS"]["price"] = 370.04
        self.assertEqual([r["id"] for r in self.at("2026-10-19 12:00", rules, st, q)[0]], ["tsla.ns-plan-target"])

    def test_the_plan_default_square_off_is_1500(self):
        rules = alerts.rules_from_plan({"symbol": "sbin", "stop": 987.8, "target": 997.5})
        self.assertEqual(rules[0]["id"], "sbin.ns-plan-stop")
        self.assertEqual(rules[2]["at"], "15:00")
        self.assertEqual(alerts.rules_from_plan({"symbol": "SBIN.NS", "stop": 1, "target": 2}, "14:45")[2]["at"],
                         "14:45")

    def test_square_off_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-19 15:14", rules)[0], [])
        rings, st = self.at("2026-10-19 15:15", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Square off MIS: it is 15:15 IST (set for 15:15), the market closes at 15:30", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol TSLA.NS", rings[0]["message"])
        self.assertEqual(self.at("2026-10-19 15:20", rules, st)[0], [])
        rings, _ = self.at("2026-10-19 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-21 15:16", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_is_the_same_every_session(self):
        rule = [{"id": "flat", "type": "time", "at": "15:15", "label": "square off"}]
        self.assertEqual(self.at("2026-11-27 15:14", rule)[0], [])         # a US half day is a full NSE day
        rings, _ = self.at("2026-11-27 15:15", rule)
        self.assertIn("set for 15:15), the market closes at 15:30", rings[0]["message"])
        self.assertEqual(self.at("2026-10-17 15:16", rule)[0], [])         # Saturday: no session
        self.assertEqual(self.at("2026-11-10 15:16", rule)[0], [])         # Diwali-Balipratipada
        rings, st = self.at("2026-12-01 15:15", rule)                      # no daylight saving to shift it
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:45:00Z")

    def test_the_example_rules_are_what_from_plan_writes(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        plan = {"symbol": "SBIN.NS", "date": "2026-10-19", "stop": 987.8, "target": 997.5,
                "stop_label": "opening-range midpoint", "target_label": "1R (one times the risk)"}
        self.assertEqual([r for r in rules if "-plan-" in r["id"] or r["id"].endswith("-flat-by")],
                         alerts.rules_from_plan(plan))

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "AAPL.NS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.evaluate([{"id": "x", "type": "time", "at": "4pm"}], {}, self.open, {}, self.now)
        self.assertIn("IST time", str(cm.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "TSLA.NS", "stop": 358.32})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_a_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added tsla.ns-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-10-19 10:52")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        code, text, _ = self.check("2026-10-19 10:53")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, TSLA.NS ₹366.48.", text)
        self.assertIn("NSE OPEN (2026-10-19 10:53 IST, regular session).", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TSLA.NS": 366.475})
        self.assertEqual(doc["detail"]["TSLA.NS"]["time"], "2026-10-19T05:07:13Z")
        self.assertEqual(doc["detail"]["TSLA.NS"]["fetched"], "2026-10-19T05:23:00Z")
        self.assertEqual(doc["source"], "yahoo")

        code, text, _ = self.check("2026-10-19 15:15")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Square off MIS", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-19 15:17")[0], 1)
        code, text, _ = self.check("2026-10-20 10:00")                  # Dussehra: the NSE is shut, no bell
        self.assertEqual(code, 1)
        self.assertIn("NSE closed", text)
        self.assertNotIn("Opening bell", text)
        code, text, _ = self.check("2026-10-21 10:00")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-21 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "tsla.ns-flat-by", "tsla.ns-plan-stop", "tsla.ns-plan-target"])
        with open(self.state) as fh:
            self.assertNotIn("tsla.ns-flat-by", json.load(fh)["rung"])

    def test_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "AAPL.NS", "--above", "350"], ["--symbol", "bac", "--stop", "50"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "aapl", "--above", "350"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-13T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"AAPL.NS": 341.27, "BAC.NS": 53.96})

        code, text, _ = self.check("2026-10-14T09:31")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-14T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:15", "--label", "square off MIS",
                             "--symbol", "sbin"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "TSLA.NS", "--target", "370.04",
                             "--date", "2026-10-19"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1515", "type": "time", "at": "15:15", "label": "square off MIS",
                                    "symbol": "SBIN.NS"})
        self.assertEqual(rules[1], {"id": "tsla.ns-target-370.04", "symbol": "TSLA.NS", "type": "target",
                                    "price": 370.04, "date": "2026-10-19"})
        code, _, err = run(["add", "--rules", self.rules, "--at", "4pm"])
        self.assertEqual(code, 1)
        self.assertIn("IST time", err)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "AAPL.NS", "ko", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-13T20:00:01Z"])
        self.assertEqual(code, 0)
        self.assertRegex(text, r"AAPL\.NS +₹341\.27")
        self.assertIn("delayed", text)
        run(["quote", "BAC.NS", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["AAPL.NS", "BAC.NS", "KO.NS"])
        self.assertEqual(doc["detail"]["KO.NS"]["prev_close"], 89.79)
        self.assertEqual(doc["detail"]["KO.NS"]["fetched"], "2026-10-13T20:00:01Z")

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "KO", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["KO.NS", "TSLA.NS"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", text)
        self.assertIn("Today's session: 09:15 to 15:30.", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2027-03-01T10:00"])
        self.assertIn("Holiday list covers 2026 only", text)
        self.assertIn("scan.py calendar", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


class Egress(unittest.TestCase):
    """The only hosts this script may talk to: Yahoo."""

    def test_hosts_in_the_source(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.py"), encoding="utf-8") as fh:
            src = fh.read()
        hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", src)) | set(alerts.YAHOO_HOSTS)
        self.assertEqual(hosts, {"github.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com",
                                 "www.nseindia.com"})                   # nseindia.com: only in the comment above the holiday table
        self.assertEqual(alerts.YAHOO_HOSTS, ("query2.finance.yahoo.com", "query1.finance.yahoo.com"))
        for gone in ("stooq", "nasdaqtrader", "sec.gov"):
            self.assertNotIn(gone, src)


if __name__ == "__main__":
    unittest.main()
