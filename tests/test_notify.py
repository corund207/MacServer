import copy
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import tui_fixture   # points admin/tui.py at a fake machine; must come before importing notify

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notify", ROOT / "admin/notify.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)
tui = tui_fixture.tui

URL = "https://macserver-1.tail9e3c7b.ts.net/"


def sample(scenario="healthy"):
    """A status.json for a scenario of the dashboard fixture (healthy | warning | trouble)."""
    tui_fixture.machine()
    tui_fixture.status(scenario)
    return json.loads((tui_fixture.BASE / "status.json").read_text())


def broken(service=False, reboot=False, public=False):
    """A healthy Mac with exactly the faults asked for: a stopped service (critical), a pending
    restart (not critical), the public API not answering (critical)."""
    status = sample("healthy")
    if service:
        status["containers"][7]["state"], status["containers"][7]["status"] = "exited", "Exited (1) 4 minutes ago"
    status["host"]["reboot_required"] = reboot
    status["public_ok"] = not public
    return status


class Phone:
    """A stand-in for the GitHub workflow call: records the texts, fails on demand."""

    def __init__(self):
        self.texts, self.ok = [], True

    def __call__(self, text, level):
        self.texts.append((text, level))
        return self.ok, "HTTP 204" if self.ok else "HTTP 404"


def titles(issues):
    return [i["title"] for i in issues]


class WhatIsWrongTests(unittest.TestCase):
    def test_a_healthy_mac_has_no_issue(self):
        self.assertEqual(notify.open_issues(sample("healthy"), tui), [])

    def test_a_stopped_service_is_critical_and_comes_first(self):
        status = sample("trouble")
        status["host"]["reboot_required"] = True
        found = notify.open_issues(status, tui)
        self.assertEqual(found[0]["level"], "bad")
        self.assertEqual(found[0]["title"], "service edge-functions is exited")
        self.assertIn("restart needed for updates", titles(found))
        self.assertEqual([i["level"] for i in found], sorted((i["level"] for i in found), key=lambda l: l != "bad"))

    def test_heat_is_never_an_issue_but_a_stopped_fan_is(self):
        """This Mac sits at 90-100 C with the fan flat out. Only a warm CPU with a fan that is not turning counts."""
        status = sample("healthy")
        status["host"]["sensors"] = {"cpu_temp_c": 100.0, "fans_rpm": [8071]}
        self.assertEqual(notify.open_issues(status, tui), [])
        status["host"]["sensors"] = {"cpu_temp_c": 100.0, "fans_rpm": []}      # no fan reading is not a fault
        self.assertEqual(notify.open_issues(status, tui), [])
        status["host"]["sensors"] = {"cpu_temp_c": 92.0, "fans_rpm": [0]}
        self.assertEqual(titles(notify.open_issues(status, tui)), ["the fan is not turning (92°C)"])

    def test_the_notices_the_screen_mutes_stay_quiet_here_too(self):
        status = sample("healthy")
        status["debug"]["system"]["failed_units"] = ["macserver-claude.service"]
        status["claude"] = {"problem": "login"}
        self.assertEqual(notify.open_issues(status, tui), [])

    def test_old_or_missing_health_data_is_an_issue(self):
        status = sample("healthy")
        status["age_s"] = 400
        self.assertEqual(titles(notify.open_issues(status, tui)), ["health data is old"])
        self.assertEqual(titles(notify.open_issues({}, tui)), ["no health data yet"])

    def test_numbers_do_not_make_a_new_issue(self):
        self.assertEqual(notify.issue_key("the API answered 3 request(s) with a server error"),
                         notify.issue_key("the API answered 12 request(s) with a server error"))


class WhenToTextTests(unittest.TestCase):
    def run_timeline(self, status, steps, phone=None):
        """steps: seconds at which to look. Returns {second: text} for the looks that texted."""
        state, phone, texted = {}, phone or Phone(), {}
        for t in steps:
            done = notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
            if done:
                texted[t] = done
        return state, phone, texted

    def test_a_critical_issue_is_texted_once_after_a_minute(self):
        state, phone, texted = self.run_timeline(broken(service=True), range(0, 600, 30))
        self.assertEqual(list(texted), [60])
        text, level = phone.texts[0]
        self.assertEqual(level, "critical")
        self.assertTrue(text.startswith("MacServer CRITICAL: service edge-functions is exited"))
        self.assertTrue(text.endswith(URL))

    def test_a_critical_issue_that_stays_open_is_texted_again_every_four_hours(self):
        _, phone, texted = self.run_timeline(broken(service=True), [0, 60, 3600, 4 * 3600 + 30, 4 * 3600 + 60, 8 * 3600 + 120])
        self.assertEqual(list(texted), [60, 4 * 3600 + 60, 8 * 3600 + 120])

    def test_a_warning_waits_three_minutes_and_is_never_repeated(self):
        _, phone, texted = self.run_timeline(broken(reboot=True), [0, 60, 150, 180, 600, 5 * 3600, 12 * 3600])
        self.assertEqual(list(texted), [180])
        self.assertEqual(phone.texts[0][1], "issue")
        self.assertIn("restart needed for updates", phone.texts[0][0])

    def test_one_text_lists_everything_and_a_new_critical_issue_gets_its_own_text(self):
        state, phone = {}, Phone()
        for t in range(0, 200, 30):
            notify.check(state, copy.deepcopy(broken(reboot=True)), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), 1)
        for t in range(200, 400, 30):
            notify.check(state, copy.deepcopy(broken(service=True, reboot=True)), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), 2)
        self.assertTrue(phone.texts[1][0].startswith("MacServer 2 issues (1 critical): service edge-functions is exited; restart"))

    def test_a_lesser_issue_that_follows_a_text_waits_for_the_next_one(self):
        """One incident (a stopped service, then the API errors it causes) is one text now and one summary later."""
        _, phone, texted = self.run_timeline(sample("trouble"), range(0, 1200, 30))
        self.assertEqual(list(texted), [60, 960])            # 15 minutes after the first text
        self.assertTrue(phone.texts[0][0].startswith("MacServer 2 issues (2 critical):"))
        self.assertTrue(phone.texts[1][0].startswith("MacServer 4 issues (2 critical):"))

    def test_a_warning_that_turns_critical_is_texted_at_once(self):
        status = broken(reboot=True)
        state, phone = {}, Phone()
        for t in range(0, 240, 30):
            notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), 1)
        state["seen"]["restart needed for updates"]["level"] = "bad"           # same issue, now critical
        issue = {"key": "restart needed for updates", "level": "bad", "title": "restart needed for updates", "why": ""}
        self.assertEqual(notify.should_text(state, [issue], 1_000_000 + 270), [issue])

    def test_a_blip_is_not_texted_and_a_short_gap_does_not_restart_the_clock(self):
        trouble, healthy = broken(service=True), broken()
        state, phone = {}, Phone()
        for t, status in ((0, trouble), (30, trouble), (45, healthy), (60, healthy)):     # over before a minute: nothing
            notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(phone.texts, [])
        for t, status in ((100, trouble), (130, healthy), (160, trouble)):                # back after a 30 s gap: same clock
            notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), 1)

    def test_an_issue_that_was_gone_for_good_is_new_when_it_returns(self):
        trouble, healthy = broken(service=True), broken()
        state, phone = {}, Phone()
        steps = [(t, trouble) for t in range(0, 90, 30)] + [(t, healthy) for t in range(90, 400, 30)] \
            + [(t, trouble) for t in range(400, 520, 30)]
        for t, status in steps:
            notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), 2)

    def test_a_failed_send_is_retried_after_five_minutes_and_not_counted(self):
        phone = Phone()
        phone.ok = False
        state, status = {}, broken(service=True)
        results = {}
        for t in range(0, 900, 30):
            done = notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
            if done:
                results[t] = done[1]
            if t == 390:
                phone.ok = True
        self.assertEqual(results, {60: False, 360: False, 660: True})
        self.assertEqual(len(state["texts"]), 1)

    def test_no_more_than_four_texts_an_hour(self):
        state, phone = {}, Phone()
        for n in range(8):                                  # eight different issues, one after another
            status = broken()
            status["containers"][n]["state"] = "exited"
            status["containers"][n]["status"] = "Exited (1) 1 minute ago"
            for t in range(n * 400, n * 400 + 150, 30):
                notify.check(state, copy.deepcopy(status), 1_000_000 + t, phone, URL, tui)
        self.assertEqual(len(phone.texts), notify.MAX_PER_HOUR)


class MessageTests(unittest.TestCase):
    def test_a_text_fits_one_sms_and_ends_with_the_link(self):
        issues = [{"key": "k", "level": "bad", "title": "service " + "x" * 300 + " is exited", "why": "w" * 300}]
        text = notify.compose(issues, URL)
        self.assertLessEqual(len(text), 160)
        self.assertTrue(text.endswith(" " + URL))
        self.assertIn("...", text)
        many = [{"key": str(n), "level": "warn", "title": f"problem number {n} needs a look", "why": ""} for n in range(9)]
        text = notify.compose(many, URL)
        self.assertLessEqual(len(text), 160)
        self.assertTrue(text.startswith("MacServer 9 issues: problem number 0"))

    def test_a_text_is_plain_ascii_on_one_line(self):
        text = notify.compose([{"key": "k", "level": "warn", "title": "fan\nnot turning (97°C)", "why": "café"}], URL)
        text.encode("ascii")
        self.assertNotIn("\n", text)
        self.assertEqual(text, f"MacServer Issue: fan not turning (97?C) - caf? {URL}")

    def test_the_link_is_the_live_tailscale_name(self):
        self.assertEqual(notify.admin_url({"tailscale": {"name": "macserver-1.tail9e3c7b.ts.net"}}, {"TAILNET_NAME": "old.ts.net"}), URL)
        self.assertEqual(notify.admin_url({}, {"TAILNET_NAME": "macserver.tail9e3c7b.ts.net"}), "https://macserver.tail9e3c7b.ts.net/")
        self.assertNotIn("http", notify.admin_url({"tailscale": {"name": "evil.com/x y"}}, {}))
        self.assertLessEqual(len(notify.compose_test(URL)), 160)


class DispatchTests(unittest.TestCase):
    def call(self, outcome):
        seen = {}

        def fake(request, timeout=0):
            seen["request"] = request
            if isinstance(outcome, Exception):
                raise outcome
            return mock.MagicMock(__enter__=lambda s: mock.MagicMock(status=outcome), __exit__=lambda *a: False)

        with mock.patch.object(notify.urllib.request, "urlopen", fake):
            result = notify.dispatch("corund207/MacServer", "ghp_secret", "hello " + URL, "critical")
        return result, seen.get("request")

    def test_it_asks_the_alert_workflow_to_send_the_text(self):
        (ok, detail), request = self.call(204)
        self.assertEqual((ok, detail), (True, "HTTP 204"))
        self.assertEqual(request.full_url, "https://api.github.com/repos/corund207/MacServer/actions/workflows/alert-sms.yml/dispatches")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"ref": "main", "inputs": {"message": "hello " + URL, "level": "critical"}})
        self.assertEqual(request.get_header("Authorization"), "Bearer ghp_secret")

    def test_failures_say_what_to_do(self):
        for code, words in ((401, "setup-gh"), (403, "Actions"), (404, "alert-sms.yml")):
            error = urllib.error.HTTPError("u", code, "x", {}, None)
            (ok, detail), _ = self.call(error)
            self.assertFalse(ok)
            self.assertIn(words, detail)
        (ok, detail), _ = self.call(urllib.error.URLError("down"))
        self.assertEqual((ok, detail), (False, "no connection to GitHub (URLError)"))
        with mock.patch.object(notify.urllib.request, "urlopen", side_effect=AssertionError("must not be called")):
            self.assertFalse(notify.dispatch("not a repo", "t", "m", "issue")[0])
            self.assertFalse(notify.dispatch("a/b/../c", "t", "m", "issue")[0])


class FilesTests(unittest.TestCase):
    def test_the_workflow_keeps_the_text_and_the_phone_number_out_of_the_public_log(self):
        text = (ROOT / ".github/workflows/alert-sms.yml").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\$\{\{\s*(github\.event\.)?inputs", "an expression would print the text in the run log")
        self.assertIn("GITHUB_EVENT_PATH", text)
        self.assertIn("secrets.SMS_GATEWAY_ADDRESS", text)
        self.assertNotRegex(text, r"\d{3}[-. ]?\d{3}[-. ]?\d{4}", "a phone number must live in a secret, not in the repository")
        self.assertNotRegex(text, r"@(vtext|tmomail|txt\.att|messaging)\.", "the gateway address belongs in the secret")

    def test_it_is_installed_and_runs_every_half_minute(self):
        installer = (ROOT / "installer/steps/40-access.sh").read_text(encoding="utf-8")
        self.assertIn("admin/notify.py", installer)
        for unit in ("macserver-notify.service", "macserver-notify.timer"):
            self.assertIn(unit, installer)
            self.assertTrue((ROOT / "admin" / unit).is_file())
        self.assertIn("OnUnitActiveSec=30s", (ROOT / "admin/macserver-notify.timer").read_text())
        self.assertIn("notify.py check", (ROOT / "admin/macserver-notify.service").read_text())

    def test_it_loads_the_dashboard_the_way_the_installer_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(ROOT / "admin/tui.py", Path(tmp) / "dashboard")       # install_tools: tui.py -> dashboard
            shutil.copy(ROOT / "admin/units.py", Path(tmp) / "units.py")
            module = notify.load_tui(tmp)
            self.assertTrue(callable(module.incidents) and callable(module.muted_notice))
            with self.assertRaises(ImportError):
                notify.load_tui(Path(tmp) / "nothing-here")

    def test_conf_and_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "macserver.conf"
            conf.write_text("# NOTIFY_SMS=off\nNOTIFY_SMS=on\nDOOMSDAY_GH_REPO=a/b\nbroken line\n")
            self.assertEqual(notify.read_conf(conf), {"NOTIFY_SMS": "on", "DOOMSDAY_GH_REPO": "a/b"})
            self.assertEqual(notify.read_conf(Path(tmp) / "missing"), {})
            state = Path(tmp) / "sub" / "notify.json"
            notify.write_state({"texts": [1]}, state)
            self.assertEqual(notify.read_json(state, {}), {"texts": [1]})
            state.write_text("garbage")
            self.assertEqual(notify.read_json(state, {}), {})


if __name__ == "__main__":
    unittest.main()
