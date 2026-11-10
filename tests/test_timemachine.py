import copy
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
# TEST DATA: TSLA's 60 days of five-minute bars (Yahoo v8 chart, range=60d interval=5m, fetched 2026-10-19),
# re-timed to the NSE session (75 bars, 09:15 to 15:30 IST) and labelled SBIN.NS. Prices are not NSE prices.
SBIN = os.path.join(DIR, "SBIN.NS-60d5m.json.gz")
with gzip.open(SBIN, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-19 10:35"
NOW_UTC = tm.parse_now(NOW)

# the plan as trader-exit writes it (CONTRACT.md): stop and target on the 0.05 tick
PLAN = {"created_by": "trader-exit", "symbol": "SBIN.NS", "side": "buy", "date": "2026-10-19", "setup": "orb",
        "entry": 364.18, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
        "opening_range": {"high": 362.18, "low": 354.47, "minutes": 15},
        "stop": 358.30, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 370.05, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 5.88, "reward_risk": 1.0, "flat_by": "15:15"}

# regression fingerprints of this implementation on the re-timed fixture (seed 19). The reel's sim.py
# fingerprints no longer apply: the NSE session has 75 bars, not 78, so the draws and the pool differ.
SIM_BANDS = "b6b4c63ab96aee9f"
SIM_PATHS = "d824b5abd720fc19"
SIM_TRADES = "00f3f1dee2eba2dd"
SIM_HIST = {"lo": 352.61, "hi": 376.82,
            "bins": [121, 119, 164, 209, 280, 401, 479, 594, 624, 699, 761, 776, 774, 734, 696, 573, 496, 387,
                     289, 226, 176, 112, 95, 58]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_reel_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:30"))
        self.assertEqual(m["pool_sessions"], 59)
        self.assertEqual(m["bars_left"], 66)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-07-27", "2026-10-16"))
        self.assertEqual(m["p_target"], 0.2793)
        self.assertEqual(m["p_stop"], 0.2334)
        self.assertEqual(m["p_neither"], 0.4873)
        self.assertEqual(m["p_green"], 0.5186)
        self.assertEqual(m["median_close"], 364.33)
        self.assertEqual(m["p5_close"], 355.85)
        self.assertEqual(m["p95_close"], 373.02)
        self.assertEqual(m["avg_R"], 0.039)
        self.assertEqual(m["r_at_target"], 0.9983)       # 370.05 and 358.30 sit on the tick: 5.87 / 5.88
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)

    def test_fingerprints_of_the_replay(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [355.93, 360.8, 364.33, 367.7, 372.82])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (59, 33))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (12, 16, 5, 15))
        self.assertEqual(h["avg_R"], -0.12)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-07-28", 307.97, 304.85, 311.09, "target"))

    def test_the_verdict_says_coin_flip(self):
        self.assertEqual(self.doc["verdict"], "Close to a coin flip: target first 27.9 % vs stop first 23.3 %.")
        # 1R breaks even at 50 % of the replays that hit one side; this target got 54.5 %
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), round(even, 3)), (0.545, 0.5))
        self.assertEqual(self.doc["verdict_notes"],
                         ["48.7 % of the replays hit neither by 3:30 PM; the price at 3:30 PM decides those."])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [364.18] * 5)
        self.assertEqual(len(m["bands"]), 67)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("10:00", "15:30", 67))
        self.assertTrue(all(p[0] == 364.18 and len(p) == 67 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("SBIN.NS", 364.18, 358.30, 370.05, 8, "10:00 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (5.88, 1.0, "15:15"))
        self.assertEqual(self.doc["warnings"], [])

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("SBIN.NS buy, Monday 2026-10-19: entry 364.18 at 10:00 AM (bar 8), stop 358.30, target 370.05",
                     "10,000 replays", "66 five-minute bars", "seed 19", "59 full sessions",
                     "target first   27.9 %", "stop first     23.3 %", "neither        48.7 %   still open at 3:30 PM",
                     "green          51.9 %", "median 364.33, 90 % of replays between 355.85 and 373.02",
                     "Average R: +0.039", "fired 33 times: 12 target, 16 stop, 5 neither. Average R -0.120",
                     "Close to a coin flip: target first 27.9 % vs stop first 23.3 %.\n48.7 % of the replays "
                     "hit neither by 3:30 PM; the price at 3:30 PM decides those.", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2793, 0.2334, 364.33))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the reel numbers above
        # are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "SBIN.NS")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 0, "off_grid": 1, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-19", "10:25", 14))
        # with the real clock (any time later) the data's own time still marks 10:30 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "10:25")
        entry = [b for b in bars if b["date"] == "2026-10-19" and b["slot"] == 8][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:55", 364.1759948730469))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-19 09:57"))[1]
        today = [b for b in bars if b["date"] == "2026-10-19"]
        self.assertEqual(len(today), 8)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-19 09:57"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:50 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-19 10:00"), replays=200)["mc"]["pool_sessions"], 59)

    def test_ist_has_no_daylight_saving(self):
        def utc_ts(y, mo, d, h, mi):
            return int((tm.datetime.datetime(y, mo, d, h, mi) - tm.EPOCH).total_seconds())
        stamps = [utc_ts(2026, 10, 30, 3, 45),   # 09:15 IST
                  utc_ts(2026, 11, 2, 3, 45),    # 09:15 IST, the Monday after the US clocks change
                  utc_ts(2026, 11, 2, 3, 0),     # 08:30 IST, before the open
                  utc_ts(2026, 11, 2, 9, 30),    # 15:00 IST
                  utc_ts(2026, 11, 2, 10, 0)]    # 15:30 IST, the close: not a bar
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:00", 69)])
        self.assertEqual(dropped["outside_hours"], 2)
        for month in (1, 7, 11):
            self.assertEqual(tm.to_ist(tm.datetime.datetime(2026, month, 2, 3, 45)),
                             tm.datetime.datetime(2026, month, 2, 9, 15))
        self.assertEqual(tm.parse_now("2026-11-02 09:15"), tm.datetime.datetime(2026, 11, 2, 3, 45))

    def test_a_short_session_is_left_out(self):
        def cut(res):  # 2026-09-04 ends at 12:45 IST, like a special session: 42 bars
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04"
                                           and ist(ts).hour * 60 + ist(ts).minute >= 12 * 60 + 45))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 42}])
        self.assertEqual(doc["history"]["sessions"], 58)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-16")
        self.assertNotIn("2026-10-19", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 1)
        self.assertEqual(doc["mc"]["pool_sessions"], 58)   # 2026-07-27 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "SBIN.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-22")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-21")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_replay_cannot_run_past_the_close(self):
        # the NSE has no early closes: 15:30 is the latest end, whatever the date
        doc = machine(plan(date="2026-10-19"), replays=100, until="15:30")
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"]), ("15:30", 66))
        with self.assertRaises(tm.TMError) as cm:
            machine(replays=10, until="15:35")
        self.assertIn("no later than 3:30 PM", str(cm.exception))

    def test_the_data_must_be_the_plans_ticker(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="ICICIBANK.NS"))
        self.assertIn("data is for SBIN.NS", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 10:35 has not happened in this data yet
            machine(plan(entry_time="10:40 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:02 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="4:00 PM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=365.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "ticker"),
            (dict(symbol="TSLA"), "NSE ticker"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 19"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:00 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00am", entry_bar=None)["entry_bar"], 8)

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:15", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("15:15", 63, "15:15"))
        self.assertIn("still open at 3:15 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:15 PM", replays=500)["mc"], m)
        for bad, words in (("10:00 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("15:17", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=368.0, stop=362.0, target=374.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry(self):
        self.assertEqual(tm.tick_away(358.325, True), 358.30)       # to the 0.05 tick
        self.assertEqual(tm.tick_away(358.325, False), 358.35)
        self.assertEqual(tm.tick_away(354.4700012207031, True), 354.45)
        self.assertEqual(tm.tick_away(354.45, True), 354.45)        # already on the tick
        self.assertEqual(tm.tick_away(354.44999999999993, True), 354.45)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=354.45, target=373.91), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=360.0, target=372.54), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 1.1478, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 59)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=370.05, target=358.30, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0017)         # 5.88 / 5.87 on the tick
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:15"}),
                         "No replay reached the target or the stop by 3:15 PM: the price at 3:15 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # a 2R run (entry 370.58, stop 367.06, target 377.62): it needs 33 %, got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_near_break_even(self):
        # stop 362.00, 2R target 368.54: 33.4 % vs 61.0 % looks lopsided, but 2R only needs a third
        doc = machine(plan(stop=362.0, target=368.54, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R"]), (2.0, 0.334, 0.6095, 0.086))
        self.assertEqual(doc["verdict"], "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 35 % (target first 33.4 % vs stop "
                                         "first 61.0 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_2r_on_the_fixture_a_clear_lean(self):
        # the same entry and stop with a 2R target 375.95: the target first only 3.1 % vs stop first 23.5 %
        doc = machine(plan(target=375.95))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0017, 0.0312, 0.2348, 0.734, 0.033))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 12 % (target first 3.1 % vs stop "
                                         "first 23.5 %).")
        self.assertEqual(doc["verdict_notes"], [
            "73.4 % of the replays hit neither by 3:30 PM; the price at 3:30 PM decides those.",
            "Average R is +0.033 all the same: the replays that hit neither pull it the other way."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0] + "\n" + doc["verdict_notes"][1], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("sbin.ns"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/SBIN.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m.ns", "query1"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("bajaj-auto.ns", "query1"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; day-trader-skill/2.0; "
                                "+https://github.com/Jakeschincariol/day-trader-skill)")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("SBIN.NS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("SBIN.NS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("SBIN.NS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("SBIN.NS")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ.NS")
        self.assertIn("check the ticker", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", SBIN, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   27.9 %", text)
        self.assertIn("Close to a coin flip: target first 27.9 % vs stop first 23.3 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2793, 0.039))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (33, -0.12))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", SBIN, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.2334)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:20], "Close to a coin flip")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "sbin.ns", "--date", "2026-10-19", "--entry", "364.18",
                               "--stop", "358.30", "--target", "370.05", "--entry-time", "10:00 AM",
                               "--offline", SBIN, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2793, 0.5186))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (12, 16, -0.12))
        code, _, err = run(["run", "--symbol", "SBIN.NS", "--entry", "364.18", "--offline", SBIN])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(SBIN, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", SBIN, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", SBIN, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 10:00 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("   370.05 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("   358.30 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("   364.18 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("10:00", text)
        self.assertIn("3:30", text)
        self.assertEqual(lines[-2:], ["Close to a coin flip: target first 27.9 % vs stop first 23.3 %.",
                                      "48.7 % of the replays hit neither by 3:30 PM; the price at 3:30 PM decides those."])
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", SBIN, "--now", NOW])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
