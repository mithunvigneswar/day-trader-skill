import argparse
import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

scan = load("trader-scanner", "scan")

# The chart fixtures are US Yahoo charts reshaped to an NSE session (09:15 IST first bar, IST
# time zone, INR) with ".NS" added to each name, so the numbers are real but the tickers are not
# NSE listings. Oct 20 2026 is an NSE holiday (Dussehra): the live 5-minute answers sit on Oct 21.
SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-19.json.gz")       # 234 5-min charts, Mon Oct 19 2026
DAILY = os.path.join(SDIR, "daily-spark-2026-10-19.json")    # 19 daily charts as one spark answer
LIVE = os.path.join(SDIR, "spark-3mo-live-2026-10-20.json")  # a 20-symbol spark answer
SPARK5 = os.path.join(SDIR, "spark-5m-live-2026-10-21-0946.json")  # fetched 09:46:47 AM IST Oct 21
TSLA5 = os.path.join(SDIR, "TSLA-5m-live-2026-10-21-0946.json")    # v8, fetched with it
NSE_HOLIDAYS = os.path.join(SDIR, "nse-holiday-master.json")  # NSE holiday-master, trimmed
DT = datetime.datetime
D = datetime.date

_saved = {}


def setUpModule():
    """No test touches the network or a saved holiday list unless it sets that up itself."""
    _saved.update(http_get=scan.http_get, nse_get=scan.nse_get, holidays=scan.HOLIDAYS_FILE)
    scan.HOLIDAYS_FILE = os.path.join(tempfile.gettempdir(), "no-such-dir-trader", "nse_holidays.json")

    def blocked(*a, **k):
        raise AssertionError("a test reached the network")
    scan.http_get = blocked
    scan.nse_get = blocked


def tearDownModule():
    scan.http_get, scan.nse_get, scan.HOLIDAYS_FILE = _saved["http_get"], _saved["nse_get"], _saved["holidays"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """IST wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                      else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


class Symbols(unittest.TestCase):
    def test_the_dot_is_never_touched(self):
        self.assertEqual(scan.yahoo_symbol(" reliance.ns "), "RELIANCE.NS")
        self.assertEqual(scan.nse_symbol("sbin"), "SBIN.NS")             # a bare name gets .NS
        self.assertEqual(scan.nse_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.nse_symbol("m&m.ns"), "M&M.NS")
        self.assertEqual(scan.nse_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")
        for bad in ("BRK.B", "", "^NSEI", "RELIANCE.BO"):
            with self.assertRaises(scan.ScanError, msg=bad):
                scan.nse_symbol(bad)

    def test_urls(self):
        self.assertEqual(scan.spark_path(["RELIANCE.NS", "m&m.ns"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=RELIANCE.NS,M%26M.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("SBIN.NS", "1d", "5m"), "/v8/finance/chart/SBIN.NS?range=1d&interval=5m")
        self.assertEqual(scan.chart_path("BAJAJ-AUTO.NS", "5d", "5m"),
                         "/v8/finance/chart/BAJAJ-AUTO.NS?range=5d&interval=5m")


class Universe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, "config.json")
        self.out = os.path.join(self.d, "universe.txt")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_universe_comes_from_the_config(self):
        with open(self.cfg, "w") as fh:
            json.dump({"universe": ["RELIANCE.NS", "sbin", "M&M.NS", "SBIN.NS"]}, fh)
        code, text, _ = run(["universe", "--config", self.cfg, "--out", self.out])
        self.assertEqual(code, 0)
        self.assertIn("3 NSE symbols", text)
        with open(self.out, encoding="utf-8") as fh:
            self.assertEqual(fh.read().splitlines(), ["RELIANCE.NS|RELIANCE", "SBIN.NS|SBIN", "M&M.NS|M&M"])

    def test_no_universe_in_the_config(self):
        code, _, err = run(["universe", "--config", os.path.join(self.d, "none.json"), "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn('no "universe"', err)
        self.assertIn("trader/config.json", err)
        self.assertFalse(os.path.exists(self.out))
        with open(self.cfg, "w") as fh:
            json.dump({"universe": ["AAPL.US"]}, fh)
        code, _, err = run(["universe", "--config", self.cfg, "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE ticker", err)


class Time(unittest.TestCase):
    def test_ist_clock(self):
        self.assertEqual(scan.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(scan.ts_to_ist(1792381500), DT(2026, 10, 19, 9, 15))        # TSLA.NS's first bar
        self.assertEqual(scan.ts_to_ist(ts("2026-12-01 09:15")), DT(2026, 12, 1, 9, 15))
        self.assertEqual(scan.parse_now("2026-10-19 10:00"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.parse_now("2026-12-01 10:15"), DT(2026, 12, 1, 4, 45))   # no daylight saving
        self.assertEqual(scan.parse_now("2026-10-19T04:30:00Z"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.parse_now("2026-10-19T10:00:00+05:30"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.to_ist(DT(2026, 10, 19, 4, 30)), DT(2026, 10, 19, 10, 0))
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(datetime.time(12, 5)), "12:05 PM")
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_session_hours(self):
        self.assertEqual((scan.OPEN, scan.CLOSE, scan.SQUAREOFF),
                         (datetime.time(9, 15), datetime.time(15, 30), datetime.time(15, 12)))
        self.assertEqual(scan.session(D(2026, 10, 19)), (scan.OPEN, scan.CLOSE))
        self.assertEqual(scan.session(D(2026, 11, 27)), (scan.OPEN, scan.CLOSE))     # no early closes on NSE
        self.assertEqual((scan.CLOSE.hour * 60 + scan.CLOSE.minute - scan.OPEN.hour * 60 - scan.OPEN.minute)
                         // scan.BAR_MIN, 75)

    def test_nse_holidays(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 19)), (True, ""))
        self.assertEqual(scan.trading_day(D(2026, 10, 17)), (False, "weekend"))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(scan.trading_day(D(2026, 11, 26)), (True, ""))              # US Thanksgiving is just a Thursday
        self.assertIsNone(scan.session(D(2026, 11, 10)))
        self.assertEqual(len(scan.NSE_HOLIDAYS), 20)


class Spark(unittest.TestCase):
    def test_a_spark_answer(self):
        with open(LIVE, encoding="utf-8") as fh:
            text = fh.read()
        wanted = {scan.yahoo_symbol(s): s for s in ["AAPL.NS", "TSLA.NS", "BRK-B.NS", "ZZZZQX.NS"]}
        data = scan.parse_spark(text, wanted)
        self.assertEqual(len(data), 19)            # 20 asked; Yahoo silently drops the made-up ZZZZQX
        self.assertNotIn("ZZZZQX.NS", data)
        self.assertIn("BRK-B.NS", data)            # names come back exactly as sent: no dot swapping
        t = data["TSLA.NS"]
        self.assertEqual(len(t["t"]), 65)
        self.assertIsNone(t["h"])                  # spark is closes only
        self.assertEqual(t["c"][-1], 368.06)
        self.assertEqual(t["meta"]["regularMarketVolume"], 38651173)
        self.assertEqual(t["meta"]["currency"], "INR")
        self.assertEqual(data["AMC.NS"]["meta"]["regularMarketPrice"], 2.98)
        closes = scan.daily_closes(t, scan.parse_now("2026-10-20 09:14"))
        self.assertEqual(closes[-1], (D(2026, 10, 19), 368.06))
        # the same answer read during Monday's session: Monday's bar is still in progress
        closes = scan.daily_closes(t, scan.parse_now("2026-10-19 12:00"))
        self.assertEqual(closes[-1][0], D(2026, 10, 16))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(LIVE, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=TSLA.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/SBIN.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = [s + ".NS" for s in ["AAPL", "TSLA", "NVDA", "BRK-B", "AMC", "ZZZZQX", "MSFT", "AMZN", "META", "GOOGL",
                                    "PLTR", "AMD", "INTC", "F", "BAC", "KO", "PLUG", "SOFI", "RIVN", "LCID"]]
        syms += ["X%02d.NS" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertEqual(failed["ZZZZQX.NS"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00.NS"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-20 09:14"), 5, 1000000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    # Hand-checked from the closes (see the numbers in the comments): through Fri Oct 16 2026.
    EXPECT = {
        "BLK.NS": ["breakout_20d"],    # 1134.38 > 1120.78, the best close of the 20 before
        "CI.NS": ["breakout_20d"],     # 302.30 > 298.81
        "XOM.NS": ["breakout_20d"],    # 178.98 > 177.01
        "HD.NS": ["breakout_20d"],     # 309.63 > 308.24
        "KO.NS": ["breakout_20d"],     # 93.90 > 92.00 (a closing high; its intraday high was not taken out)
        "CCL.NS": ["bull_flag"],       # up 18.4 % over the 10 sessions, last 5 closes in a 3.2 % range
        "RCL.NS": ["bull_flag"],       # up 16.3 %, 2.6 % range
        "F.NS": ["reclaim_ma20"],      # 12.31 under 12.41, then 12.43 over 12.37
        "JPM.NS": ["reclaim_ma20"],    # 335.53 under 336.15, then 339.72 over 335.65
        "GE.NS": ["reclaim_ma20"],     # 312.00 under 312.90, then 313.83 over 312.88
        "BAC.NS": ["oversold"],        # RSI 24.0
        "PLUG.NS": ["oversold"],       # RSI 21.0
        "MS.NS": ["oversold"],         # RSI 25.5
        "RIVN.NS": ["oversold"],       # RSI 22.7
        "PNC.NS": ["oversold_bounce"],  # RSI 27.1 on Thursday, closed up Friday (214.94 -> 217.39), RSI 34.0
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 19)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 10, 16))
        self.assertEqual(counts, {"requested": 19, "fetched": 19, "illiquid": 0, "short": 0, "scanned": 19,
                                  "hits": 15})
        blk = [h for h in hits if h["symbol"] == "BLK.NS"][0]
        self.assertEqual((blk["price"], blk["rsi"], blk["close_date"]), (1134.38, 63.7, "2026-10-16"))

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:50"), 0, 0)
        self.assertEqual(before, during)
        bars = scan.daily_closes(self.data["TSLA.NS"], scan.parse_now("2026-10-19 15:29"))
        self.assertEqual(bars[-1], (D(2026, 10, 16), 354.7200012207031))
        bars = scan.daily_closes(self.data["TSLA.NS"], scan.parse_now("2026-10-19 15:30"))   # the 3:30 PM close
        self.assertEqual(bars[-1], (D(2026, 10, 19), 365.7950134277344))

    def test_bar_completes_at_the_close_every_day(self):
        s = {"t": [ts("2026-11-25 09:15"), ts("2026-11-27 09:15")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-27 15:29"))), 1)
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-27 15:30"))), 2)

    def test_liquidity_filter(self):
        # meta is Yahoo's as of the session on Monday, so the volume is the day's so far
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:50"), 5, 1000000)
        self.assertEqual(sorted(h["symbol"] for h in hits),
                         ["BAC.NS", "CCL.NS", "F.NS", "JPM.NS", "KO.NS", "RIVN.NS", "XOM.NS"])
        self.assertEqual(counts["illiquid"], 8)     # PLUG under 5; BLK, CI, HD, RCL, GE, MS, PNC under 1M so far
        self.assertEqual(counts["scanned"], 11)

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [10.0] * 10,
             "meta": {"regularMarketPrice": 10, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X.NS": s, "E.NS": {"t": [], "c": [], "meta": {}}},
                                             ["X.NS", "E.NS", "GONE.NS"], scan.parse_now("2026-10-20 09:00"), 5,
                                             1000000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_the_reel_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(15, 0))
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_scan_at_1000(self):
        hits, counts, failed = self.scan_at("2026-10-19 10:00")
        self.assertEqual(len(self.syms), 234)
        self.assertEqual(counts["scanned"], 231)          # usable charts
        self.assertEqual(len(hits), 42)
        self.assertEqual(self.errors, {"SQ.NS": "no data for SQ.NS: No data found, symbol may be delisted"})
        self.assertEqual(counts["unusable"], 2)            # WBD (no bars), PARA (missing bars)
        tsla = [h for h in hits if h["symbol"] == "TSLA.NS"][0]
        self.assertEqual(tsla, {"symbol": "TSLA.NS", "setups": ["orb"], "price": 364.18, "at": "10:00 AM",
                                "bar_start": "09:55", "bar_index": 8, "or_high": 362.18, "or_low": 354.47,
                                "pct_over": 0.55})
        self.assertEqual(hits[0]["symbol"], "AMC.NS")      # sorted by time, then symbol
        self.assertEqual([h["at"] for h in hits], sorted([h["at"] for h in hits], key=lambda x: scan.parse_hhmm(
            x[:-3]).hour % 12 * 60 + int(x[-5:-3])))
        self.assertEqual(sum(1 for h in hits if h["at"] == "10:00 AM"), 11)
        lly = [h for h in hits if h["symbol"] == "LLY.NS"][0]
        self.assertEqual((lly["price"], lly["or_high"], lly["pct_over"]), (1255.0, 1254.99, 0.0))

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:00", min_price=5)
        self.assertEqual(len(hits), 41)                    # AMC at 2.97 drops out
        self.assertEqual(counts["illiquid"], 9)
        self.assertNotIn("AMC.NS", [h["symbol"] for h in hits])

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-19 09:59", syms=["TSLA.NS"])
        self.assertEqual(hits, [])                         # the 09:55 bar is not done until 10:00
        hits, _, _ = self.scan_at("2026-10-19 10:00", syms=["TSLA.NS"])
        self.assertEqual(hits[0]["at"], "10:00 AM")

    def test_the_live_tick_is_dropped(self):
        s = self.data["TSLA.NS"]
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 10, 19, 10, 43, 7))   # off the grid
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 10, 44))
        self.assertEqual(len(bars), 17)                    # 9:15 to 10:35; 10:40 in progress, the tick dropped
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 10, 44), 3))
        hits, _, _ = self.scan_at("2026-10-19 10:45", syms=["TSLA.NS"])
        self.assertEqual(hits[0]["price"], 364.18)         # still the first close above the range

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 10, 19), DT(2026, 10, 19, 10, 0), 15,
                                             0, 0, confirm)
        self.assertEqual(len(hits), 42)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), 231)                   # stage 1 skipped most charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["TSLA.NS", "KO.NS"], D(2026, 10, 19),
                                             DT(2026, 10, 19, 10, 0), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["TSLA.NS"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-21", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        bars = scan.session_bars(s, D(2026, 10, 21), DT(2026, 10, 21, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 21), DT(2026, 10, 21, 10, 0), 3))

    def test_a_30_minute_range_and_the_close(self):
        # Fri Nov 27 2026: an ordinary NSE session, 9:15 to 3:30
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-11-27", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X.NS": s}, ["X.NS"], D(2026, 11, 27), DT(2026, 11, 27, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")          # range 9:15 to 9:45 (high 101.4), 9:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        bars = scan.session_bars(s, D(2026, 11, 27), DT(2026, 11, 27, 12, 59))
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 11, 27, 9, 15))
        self.assertEqual(len(bars), 44)                    # 9:15 to 12:50 done at 12:59; no early close
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-27 15:40"), None, 15, datetime.time(15, 0))
        self.assertIn("closed at 3:30 PM IST", str(cm.exception))

    def test_bars_at_and_after_the_close_are_dropped(self):
        closes = [100.0] * 76                              # 9:15 ... 15:30, the 76th bar starts at the close
        s = bars_series("2026-11-27", closes)
        bars = scan.session_bars(s, D(2026, 11, 27), DT(2026, 11, 27, 16, 0))
        self.assertEqual(len(bars), 75)
        self.assertEqual(bars[-1]["start"], DT(2026, 11, 27, 15, 25))

    def test_window_messages(self):
        until = datetime.time(15, 0)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-17 10:00"))
        self.assertIn("NSE holiday: Dussehra", msg("2026-10-20 10:00"))
        self.assertIn("NSE holiday: Diwali-Balipratipada", msg("2026-11-10 10:00"))
        self.assertIn("opening range runs 9:15 AM to 9:30 AM and", msg("2026-10-19 09:20"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:25"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:32"))
        self.assertIn("expired", msg("2026-10-19 15:10"))
        self.assertIn("expired", msg("2026-10-19 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-19 16:00"))
        self.assertIn("later than now", msg("2026-10-19 09:45", at=datetime.time(10, 0)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-19 17:00", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), datetime.time(10, 0), 15, until),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, until)[1],
                         DT(2026, 10, 19, 9, 35))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:00"), None, 7, until)


class LiveFiveMinute(unittest.TestCase):
    """A spark 5-minute answer and TSLA.NS's v8 chart, fetched at 09:46:47 AM IST on Wed Oct 21 2026."""

    def setUp(self):
        with open(SPARK5, encoding="utf-8") as fh:
            self.spark = scan.parse_spark(fh.read())
        with open(TSLA5, encoding="utf-8") as fh:
            self.v8 = scan.parse_chart(fh.read(), "TSLA.NS")
        self.now = scan.parse_now("2026-10-21T04:16:48Z")

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        self.assertEqual(len(self.spark), 20)
        self.assertIn("BRK-B.NS", self.spark)
        t = self.spark["TSLA.NS"]
        self.assertEqual(len(t["t"]), 8)                   # 9:15 ... 9:45 (in progress) + the 9:46:47 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 10, 21, 9, 46, 47))
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 0))
        self.assertEqual(cut, DT(2026, 10, 21, 9, 46))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars], ["09:15", "09:20", "09:25", "09:30",
                                                                         "09:35", "09:40"])
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 6)

    def test_tsla_broke_its_range_at_0945(self):
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 0))
        hits, counts, failed = scan.scan_orb(self.spark, ["TSLA.NS", "AMC.NS"], day, cut, 15, 5, 0, lambda s: self.v8)
        # range 9:15 to 9:30: high 370.49 (the 9:15 bar), low 363.64; the 9:40 bar closed 370.585
        self.assertEqual(hits, [{"symbol": "TSLA.NS", "setups": ["orb"], "price": 370.58, "at": "9:45 AM",
                                 "bar_start": "09:40", "bar_index": 5, "or_high": 370.49, "or_low": 363.64,
                                 "pct_over": 0.03}])
        self.assertEqual(counts["illiquid"], 1)            # AMC.NS under the floor

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=10)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (10, counts["candidates"] - 10))
        self.assertIn("TSLA.NS", asked)                    # the most traded by 10:00
        self.assertLessEqual(len(hits), 10)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 42)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        os.makedirs(self.t)
        cfg = os.path.join(self.t, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"universe": ["KO.NS", "TSLA.NS", "RELIANCE.NS", "SBIN.NS"]}, fh)
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--config", cfg, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("4 NSE symbols", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines, ["KO.NS|KO", "TSLA.NS|TSLA", "RELIANCE.NS|RELIANCE", "SBIN.NS|SBIN"])

        # daily over that universe with the fixture charts: only KO and TSLA are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-19 09:00",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 2 of 4 charts (2 failed or had no data)", text)
        self.assertIn("passed the liquidity filter (₹50+ and 0+ shares)", text)
        self.assertIn("IST, closes through 2026-10-16", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 4, 2, 2))
        self.assertEqual(doc["hits"], [{"symbol": "KO.NS", "setups": ["breakout_20d"], "price": 93.9,
                                        "close_date": "2026-10-16", "rsi": 75.3, "volume": 3680307}])
        self.assertEqual(doc["as_of"], "2026-10-16 close")
        self.assertIn("  KO.NS            ₹93.90  RSI  75.3  20-day closing-high breakout", text)

        # the 10:00 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--min-price", "0",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("42 of 231 charts closed above their opening-range high by 10:00 AM", text)
        self.assertIn("IST: 15-minute range (9:15 AM to 9:30 AM)", text)
        self.assertIn("10:00 AM  TSLA.NS         ₹364.18  range 354.47 to 362.18  +0.55% over", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:30:00Z", "mode": "orb",
                          "as_of": "2026-10-19 10:00 IST", "universe": 234, "scanned": 231, "source": "yahoo"})
        self.assertEqual(len(doc["hits"]), 42)
        self.assertIn({"symbol": "TSLA.NS", "setups": ["orb"], "price": 364.18, "at": "10:00 AM", "bar_start": "09:55",
                       "bar_index": 8, "or_high": 362.18, "or_low": 354.47, "pct_over": 0.55}, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:00", "--watchlist", self.wl, "--min-price", "0",
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (42, 42, 42))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Oct 19 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    r = ((bundle.get(y) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": y, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[path.split("/chart/")[1].split("?")[0]])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:00", "--min-price", "0",
                                 "--max-confirm", "10", "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("231 usable; 95 candidates, 10 confirmed", text)
        self.assertIn("PARTIAL: 85 more candidates were not checked", text)
        self.assertIn("5 of the 10 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 12)   # 234 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-17 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-20 10:00", "--out", self.wl])
        self.assertIn("NSE holiday: Dussehra", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:29", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:55", "--symbols", "TSLA.NS",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:55 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)
        code, _, err = run(["daily", "--offline", DAILY, "--symbols", "BRK.B", "--now", "2026-10-19 09:00"])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE ticker", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("TSLA.NS", "AAPL.NS", "KO.NS", "SQ.NS"):
            with open(os.path.join(folder, sym + ".json"), "w") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["AAPL.NS", "TSLA.NS"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nTSLA.NS|Tesla\nko.ns\n\nTSLA.NS\n")
        self.assertEqual(scan.symbols_from_file(txt), ["TSLA.NS", "KO.NS", "TSLA.NS"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "tsla.ns"}, {"symbol": "KO.NS"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["TSLA.NS", "KO.NS"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-19 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_symbols_flag_adds_ns_to_bare_names(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "ko, tsla.ns", "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["KO.NS"])

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 15)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")


class Calendar(unittest.TestCase):
    """The NSE holiday list: fetch, save, overlay, and the once-a-day refresh."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cache = os.path.join(self.d, "trader", "state", "nse_holidays.json")
        self.calls, self.sleeps = [], []
        self._saved = (scan.nse_get, scan.HOLIDAYS_FILE, scan.SLEEP)
        scan.HOLIDAYS_FILE = self.cache
        scan.SLEEP = self.sleeps.append
        with open(NSE_HOLIDAYS, encoding="utf-8") as fh:
            self.body = fh.read()
        self.answer(self.body)

    def tearDown(self):
        scan.nse_get, scan.HOLIDAYS_FILE, scan.SLEEP = self._saved
        shutil.rmtree(self.d)

    def answer(self, body=None, error=None):
        def fake(url, timeout=20):
            self.calls.append(url)
            if error:
                raise error
            return body
        scan.nse_get = fake

    def read_cache(self):
        with open(self.cache, encoding="utf-8") as fh:
            return json.load(fh)

    def args(self, **kw):
        base = dict(offline=None, now=None, no_write=False, json=False, quiet=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_parse_the_holiday_master(self):
        got = scan.parse_nse_holidays(self.body)
        self.assertEqual(got, {"2026-01-26": "Republic Day", "2026-10-20": "Dussehra",
                               "2026-11-08": "Diwali Laxmi Pujan",          # the trailing * is dropped
                               "2026-11-10": "Diwali-Balipratipada"})      # only the CM (equity) list
        for bad in ("<html>blocked</html>", "{}", '{"CM": []}', '{"CM": [{"tradingDate": "x"}]}'):
            with self.assertRaises(scan.ScanError, msg=bad):
                scan.parse_nse_holidays(bad)

    def test_calendar_command_saves_the_list(self):
        code, text, _ = run(["calendar", "--out", self.cache])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [scan.NSE_HOLIDAY_URL])
        self.assertIn("4 listed by NSE (2026)", text)
        self.assertIn("Next holidays:", text)
        self.assertIn("Today, ", text)
        self.assertIn("Wrote %s." % self.cache, text)
        doc = self.read_cache()
        self.assertEqual((doc["source"], doc["segment"]), (scan.NSE_HOLIDAY_URL, "CM"))
        self.assertRegex(doc["fetched_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
        self.assertEqual(doc["holidays"]["2026-10-20"], "Dussehra")
        code, text, _ = run(["calendar", "--out", self.cache + ".x", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["count"], out["years"], out["out"]), (4, ["2026"], None))
        self.assertFalse(os.path.exists(self.cache + ".x"))

    def test_calendar_failure_says_the_builtin_table_stays(self):
        self.answer(error=urllib.error.URLError("timed out"))
        code, _, err = run(["calendar", "--out", self.cache])
        self.assertEqual(code, 1)
        self.assertIn("the built-in table stays in use", err)
        self.assertEqual(len(self.calls), 2)                # one retry
        self.assertEqual(self.sleeps, [2.0])
        self.assertFalse(os.path.exists(self.cache))
        self.answer("<html>Access Denied</html>")
        code, _, err = run(["calendar", "--out", self.cache])
        self.assertEqual(code, 1)
        self.assertIn("not the shape this tool knows", err)

    def test_the_saved_list_overlays_the_builtin_table(self):
        self.assertEqual(scan.load_holidays(self.cache), scan.NSE_HOLIDAYS)          # no file: built-in only
        os.makedirs(os.path.dirname(self.cache))
        with open(self.cache, "w") as fh:
            json.dump({"holidays": {"2026-12-31": "Test Holiday", "2027-01-26": "Republic Day"}}, fh)
        table = scan.load_holidays(self.cache)
        self.assertEqual(table["2026-12-31"], "Test Holiday")
        self.assertEqual(table["2026-10-20"], "Dussehra")                              # built-in kept
        self.assertEqual(len(table), len(scan.NSE_HOLIDAYS) + 2)
        self.assertEqual(scan.trading_day(D(2026, 12, 31)), (False, "NSE holiday: Test Holiday"))
        self.assertEqual(scan.session(D(2027, 1, 26)), None)
        for junk in ("not json", "[]", '{"holidays": 5}'):
            with open(self.cache, "w") as fh:
                fh.write(junk)
            self.assertEqual(scan.load_holidays(self.cache), scan.NSE_HOLIDAYS, junk)

    def test_refresh_happens_at_most_once_per_ist_day(self):
        scan.refresh_calendar(self.args())
        self.assertEqual(len(self.calls), 1)
        today = scan.to_ist(scan.parse_now(None)).date().isoformat()
        self.assertEqual(self.read_cache()["fetched_at"][:10], today)
        scan.refresh_calendar(self.args())
        scan.refresh_calendar(self.args())
        self.assertEqual(len(self.calls), 1)                # fresh today: no more requests
        doc = self.read_cache()
        doc["fetched_at"] = "2020-01-01T09:00:00+05:30"      # a stale copy
        with open(self.cache, "w") as fh:
            json.dump(doc, fh)
        scan.refresh_calendar(self.args())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.read_cache()["fetched_at"][:10], today)

    def test_replays_and_dry_runs_stay_off_the_network(self):
        for kw in (dict(offline="x"), dict(now="2026-10-19 10:00"), dict(no_write=True)):
            scan.refresh_calendar(self.args(**kw))
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.cache))

    def test_a_failed_refresh_never_stops_the_scan(self):
        self.answer(error=OSError("no route"))
        err = io.StringIO()
        with redirect_stderr(err):
            scan.refresh_calendar(self.args(quiet=False))
        self.assertIn("NSE holiday list not refreshed", err.getvalue())
        self.assertIn("built-in table stays in use", err.getvalue())
        self.assertFalse(os.path.exists(self.cache))

    def test_daily_refreshes_the_list_first(self):
        # the networked path of `daily`: Yahoo and NSE both faked
        with open(LIVE, encoding="utf-8") as fh:
            body = fh.read()
        old_get, old_sleep = scan.yahoo_get, scan.SLEEP
        scan.yahoo_get = lambda path: body
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                scan.main(["daily", "--symbols", "TSLA.NS", "--out", os.path.join(self.d, "wl.json")])
        finally:
            scan.yahoo_get, scan.SLEEP = old_get, old_sleep
        self.assertEqual(self.calls, [scan.NSE_HOLIDAY_URL])
        self.assertTrue(os.path.exists(self.cache))
        self.assertIn("Refreshed the NSE holiday list", err.getvalue())


class Egress(unittest.TestCase):
    """The only hosts this script may talk to: Yahoo, and NSE for the holiday list."""

    def test_hosts_in_the_source(self):
        with open(os.path.join(ROOT, "trader-scanner", "scan.py"), encoding="utf-8") as fh:
            src = fh.read()
        hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)", src))
        self.assertEqual(hosts, {"github.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com",
                                 "www.nseindia.com"})
        for gone in ("nasdaqtrader", "stooq", "sec.gov", "NASDAQ_URL"):
            self.assertNotIn(gone, src)


if __name__ == "__main__":
    unittest.main()
