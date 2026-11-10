import datetime
import io
import json
import os
import re
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-10-19T06:00:00+00:00"     # 11:30 IST on Monday 2026-10-19
# yahoo-SBIN.NS.xml, yahoo-RELIANCE.NS.xml and chart-SBIN.NS.json are hand-written fixtures in Yahoo's shapes;
# yahoo-AAPL.xml is a real feed, kept to prove the parser does not care which market a feed is from
SBIN_TITLES = ["SBI Q2 results: net profit rises 12% on lower provisions",
               "RBI keeps repo rate unchanged; bank stocks edge higher (NSE: SBIN)",
               "Nifty Bank opens flat as SBIN, HDFCBANK trade mixed",
               "SBI declares interim dividend, record date next week"]


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss_for_an_nse_ticker(self):
        items = news.parse_rss(raw("yahoo-SBIN.NS.xml"), "SBIN.NS")
        self.assertEqual([i["title"] for i in items], SBIN_TITLES)
        first = items[0]
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 19, 3, 50, tzinfo=datetime.timezone.utc))
        self.assertEqual(news.to_ist(first["time"]).strftime("%Y-%m-%d %H:%M"), "2026-10-19 09:20")
        self.assertTrue(first["link"].startswith("https://finance.yahoo.com/"))
        self.assertTrue(first["id"].startswith("rss:"))
        self.assertEqual((first["source"], first["kind"], first["ticker"]), ("Yahoo Finance", "headline", "SBIN.NS"))

    def test_yahoo_rss_real_feed_shape(self):
        items = news.parse_rss(raw("yahoo-AAPL.xml"), "AAPL")
        self.assertEqual(len(items), 6)
        self.assertEqual(items[0]["title"], "Apple may have a way to dominate the smart home space")
        self.assertEqual(items[0]["time"], datetime.datetime(2026, 10, 13, 19, 46, 52,
                                                             tzinfo=datetime.timezone.utc))

    def test_times(self):
        self.assertEqual(news.parse_time("Tue, 13 Oct 2026 20:02:03 +0000").hour, 20)
        self.assertEqual(news.parse_time("2026-09-01T16:30:35-04:00").hour, 20)
        self.assertEqual(news.parse_time("2026-10-19T10:15:00+05:30").hour, 4)
        self.assertIsNone(news.parse_time("soon"))

    def test_yahoo_symbols_keep_the_dot_and_the_suffix(self):
        self.assertEqual(news.yahoo_symbol("sbin.ns"), "SBIN.NS")
        self.assertEqual(news.yahoo_symbol(" RELIANCE.NS "), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.yahoo_symbol("M&M.NS"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("BRK.B"), "BRK.B")      # the dot is never turned into a dash

    def test_no_sec_code_is_left(self):
        for name in ("parse_sec_atom", "sec_user_agent", "cik_map", "SEC_FEED", "TICKERS_URL", "ITEMS_8K",
                     "IMPORTANT_ITEMS", "PLACEHOLDER"):
            self.assertFalse(hasattr(news, name), name)
        for flag in ("--no-sec", "--forms", "--user-agent"):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as e:
                    news.main(["--tickers", "SBIN.NS", flag] + (["x"] if flag != "--no-sec" else []))
            self.assertEqual(e.exception.code, 2, flag)


class Egress(unittest.TestCase):
    """Yahoo Finance is the only place this tool talks to, and it sends tickers only."""

    class Recorder(object):
        offline = False

        def __init__(self):
            self.urls = []

        def get(self, url, name):
            self.urls.append(url)
            path = os.path.join(DIR, name)
            if not os.path.isfile(path):
                raise news.NewsError("no fixture %s" % name)
            with open(path, "rb") as fh:
                return fh.read()

    def test_headline_and_chart_urls(self):
        rec = self.Recorder()
        watch = {"SBIN.NS": None, "M&M.NS": None}
        news.collect(rec, watch, errors=[])
        news.collect_moves(rec, watch, errors=[])
        self.assertEqual(rec.urls, [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=M%26M.NS&region=IN&lang=en-IN",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SBIN.NS&region=IN&lang=en-IN",
            "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m",
            "https://query2.finance.yahoo.com/v8/finance/chart/SBIN.NS?range=1d&interval=5m"])
        hosts = {urllib.parse.urlparse(u).hostname for u in rec.urls}
        self.assertEqual(hosts, {"feeds.finance.yahoo.com", "query2.finance.yahoo.com"})

    def test_the_real_fetcher_sends_no_credentials_or_personal_data(self):
        seen = []

        class Resp(object):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<rss/>"

        def fake_urlopen(req, timeout=30):
            seen.append((req.full_url, dict((k.lower(), v) for k, v in req.header_items())))
            return Resp()

        old = news.urllib.request.urlopen
        news.urllib.request.urlopen = fake_urlopen
        try:
            news.Fetcher(pause=0).get(news.YAHOO % news.yahoo_symbol("SBIN.NS"), "x")
        finally:
            news.urllib.request.urlopen = old
        (url, headers), = seen
        self.assertIn("region=IN", url)
        self.assertEqual(headers["user-agent"], news.BROWSER_UA)
        self.assertNotIn("@", headers["user-agent"])
        for h in ("authorization", "cookie", "x-api-key"):
            self.assertNotIn(h, headers)


class Tagging(unittest.TestCase):
    WATCH = {"SBIN.NS": "State Bank of India", "RELIANCE.NS": "Reliance Industries"}

    def feeds(self):
        return (news.parse_rss(raw("yahoo-SBIN.NS.xml"), "SBIN.NS") +
                news.parse_rss(raw("yahoo-RELIANCE.NS.xml"), "RELIANCE.NS"))

    def test_touches_and_tags(self):
        items = news.tag(self.feeds(), dict(self.WATCH))
        by = {i["title"]: i for i in items}
        deal = by["Reliance Industries in talks to acquire stake in solar maker"]
        self.assertEqual(deal["touches"], ["RELIANCE.NS", "SBIN.NS"])      # the summary names State Bank of India
        self.assertIn("deal", deal["tags"])
        rbi = by[SBIN_TITLES[1]]
        self.assertEqual(rbi["touches"], ["SBIN.NS"])                      # "(NSE: SBIN)" is the bare symbol
        self.assertIn("macro", rbi["tags"])                                # RBI, repo rate
        self.assertIn("earnings", by[SBIN_TITLES[0]]["tags"])
        self.assertIn("capital", by[SBIN_TITLES[3]]["tags"])               # dividend
        sensex = by["Sensex, Nifty edge up; Reliance leads gainers (RELIANCE)"]
        self.assertEqual(sensex["touches"], ["RELIANCE.NS"])
        self.assertIn("macro", sensex["tags"])

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-SBIN.NS.xml"), "SBIN.NS")
        b = news.parse_rss(raw("yahoo-SBIN.NS.xml"), "RELIANCE.NS")
        items = news.dedupe(news.tag(a + b, {"SBIN.NS": None, "RELIANCE.NS": None}))
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["touches"], ["RELIANCE.NS", "SBIN.NS"])

    def test_day_trading_tags_for_india(self):
        items = [{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "SBIN.NS",
                  "title": "Trading halted in XYZ after SEBI order; RBI repo rate ahead; Nifty futures rise",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"SBIN.NS": None})[0]["tags"]
        for t in ("halt", "legal", "macro"):
            self.assertIn(t, tags)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "SBIN.NS", "RELIANCE.NS", "--hours", "24", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 7)  # 4 SBIN.NS + 3 RELIANCE.NS headlines
        self.assertEqual(data["items"][0]["title"], SBIN_TITLES[3])
        code, out, _ = run(["--tickers", "SBIN.NS", "RELIANCE.NS", "--hours", "24"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_times_print_in_ist(self):
        code, out, err = run(["--tickers", "SBIN.NS", "--hours", "24"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("  2026-10-19 09:20 IST  SBIN.NS", out)
        self.assertIn("Checked 2026-10-19 11:30 IST", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(" ET", out)
        self.assertNotIn(chr(0x2014), out)

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "RELIANCE.NS", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "SBIN.NS"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["RELIANCE.NS", "SBIN.NS"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "24"] + args)
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"RELIANCE.NS"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_the_sbin_morning(self):
        # Monday 2026-10-19, checked at 10:15 IST (04:45 UTC): the 10:40 IST dividend headline is not out yet
        code, out, err = run(["--tickers", "SBIN.NS", "--hours", "6", "--no-moves",
                              "--now", "2026-10-19T10:15:00+05:30"] + self.base[:4])
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-19 09:20 IST  SBIN.NS", out)
        self.assertNotIn("interim dividend", out)  # published after the pinned 10:15 clock
        lines = out.splitlines()
        results = [l for l in lines if "Q2 results" in l]
        self.assertIn("earnings", lines[lines.index(results[0]) + 1])
        repo = [l for l in lines if "repo rate" in l]
        self.assertIn("macro", lines[lines.index(repo[0]) + 1])
        self.assertIn("Checked 2026-10-19 10:15 IST", out)
        self.assertEqual(len([l for l in lines if l.startswith("  2026-10-19")]), 3)
        # the price line, from the chart fetched at 10:15 IST
        code, out, err = run(["--tickers", "SBIN.NS", "--hours", "6", "--dry-run",
                              "--now", "2026-10-19T10:15:00+05:30"] + self.base[:4])
        self.assertRegex(out, r"SBIN\.NS 1,011\.00 \+3\.15% vs yesterday's close 980\.10 at 10:15 IST  MOVING")

    def test_moves(self):
        m = news.parse_chart(raw("chart-SBIN.NS.json"), "SBIN.NS")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1011.0, 980.1, 3.15))
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "10:15")
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet])[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0)[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ.NS")
        # no chart file offline: no move line, no error note
        code, out, _ = run(["--tickers", "RELIANCE.NS", "--hours", "24", "--dry-run"] + self.base)
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("note:", out)

    def test_ist_is_a_fixed_offset(self):
        utc = datetime.datetime(2026, 1, 15, 15, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(news.to_ist(utc).strftime("%H:%M"), "20:30")
        utc = datetime.datetime(2026, 10, 19, 4, 45, tzinfo=datetime.timezone.utc)
        self.assertEqual(news.to_ist(utc).strftime("%H:%M"), "10:15")      # same offset in summer
        utc = datetime.datetime(2026, 11, 1, 6, 30)                          # a naive UTC time works too
        self.assertEqual(news.to_ist(utc).strftime("%H:%M"), "12:00")
        self.assertFalse(hasattr(news, "to_eastern"))

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


if __name__ == "__main__":
    unittest.main()
