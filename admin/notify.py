#!/usr/bin/env python3
"""Alert the owner's phone (a push notification through ntfy) when MacServer has an issue.

Runs as root from macserver-notify.timer (every 30 s):

    notify.py [check]   look at /run/macserver/status.json and text when something is wrong
    notify.py test      send a test text now
    notify.py report    send the hourly status report now
    notify.py status    what is on, what is open, what was sent

"Something is wrong" is exactly what the Mac's screen and the admin page call an issue: it is
tui.incidents(), minus the notices the screen itself mutes (heat is never one). Only the level
warn and bad count; "bad" is the critical kind.

The alert is sent through the `alert-push` GitHub workflow (the same route as the doomsday email),
which pushes it to the owner's private ntfy topic; the topic name lives in a GitHub secret, never on
the Mac. (Carrier email-to-text gateways were the first route; Verizon shut its gateway down.) The
Mac needs one thing: the GitHub token of `sudo macserver doomsday setup-gh`.

To keep it to a few useful texts instead of a stream:
  - an issue must last a while before it is texted (a minute if critical, three if not);
  - one text lists everything that is open, worst first, with the admin page link;
  - a new critical issue, or one that turned critical, is texted at once; a critical one that stays
    open is texted again every 4 hours; a lasting non-critical one is texted once, and waits until
    15 minutes after the last text (one incident is one text now and one summary later);
  - at most 4 texts an hour and 12 a day; a text that failed is retried every 5 minutes.

Once an hour, at the top of the hour, it also sends a status report: one line with the state, the key
numbers and the admin link, at low priority so it arrives quietly. Reports have their own clock and
never count against the alert limits (NOTIFY_REPORT=off in macserver.conf turns them off).
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONF = Path("/etc/macserver/macserver.conf")
TOKEN = Path("/etc/macserver/doomsday-gh-token")     # shared with the doomsday alert (setup-gh)
STATUS = Path("/run/macserver/status.json")
STATE = Path("/var/lib/macserver/notify.json")
WORKFLOW = "alert-push.yml"
DEFAULT_REPO = "corund207/MacServer"

SMS_LIMIT = 160           # one line that a phone's lock screen shows whole
CONFIRM_S = {"bad": 60, "warn": 180}      # how long an issue must last before it is texted
GONE_S = 120              # an issue absent this long is over; a shorter gap does not restart its clock
REMIND_S = 4 * 3600       # a critical issue that stays open is texted again after this
WARN_GAP_S = 900          # a non-critical issue waits this long after the last text (critical ones never wait)
RETRY_S = 300             # after a failed send
MAX_PER_HOUR, MAX_PER_DAY = 4, 12
LEVEL_WORD = {"bad": "CRITICAL", "warn": "Issue"}
REPORT_S = 3600           # the status report: once an hour, at the top of the hour
REPORT_LIMIT = 300        # a report may run longer than an alert: it is read in the ntfy app


# --- what is wrong ---------------------------------------------------------------------------------

def load_tui(directory=None):
    """The dashboard module: tui.py in the repo, installed as `dashboard` (units.py sits beside it)."""
    here = Path(directory or Path(__file__).resolve().parent)
    for name in ("tui.py", "dashboard"):
        path = here / name
        if path.is_file():
            if str(here) not in sys.path:
                sys.path.insert(0, str(here))
            loader = importlib.machinery.SourceFileLoader("macserver_tui", str(path))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            return module
    raise ImportError(f"no dashboard module in {here}")


class Snapshot:
    """The parts of the screen's sampler that tui.incidents() reads, built from status.json alone."""

    def __init__(self, status):
        host = status.get("host") or {}
        sensors = host.get("sensors") or {}
        disk, memory = host.get("disk") or {}, host.get("memory") or {}
        self.status = status or None
        self.now = {"temp": sensors.get("cpu_temp_c"), "fans": sensors.get("fans_rpm") or [],
                    "mem": memory or None, "disk": (disk.get("used", 0), disk.get("total", 0))}
        self.iface = "up"            # a Mac without a network cannot text; the Internet check still counts
        self.firstboot = ""
        self.counter_changed = {}    # the heat-throttle and out-of-memory counters only matter live
        self.procs = {}
        self.fans = None


def issue_key(title):
    """The same issue with a different number in it ("3 request(s)" vs "5") is the same issue."""
    return re.sub(r"\d+", "#", title)


def open_issues(status, tui):
    """[{key, level, title, why}], critical first: what the dashboards flag as warn or bad right now."""
    snap = Snapshot(status)
    found = []
    for item in tui.incidents(snap):
        if item["level"] in LEVEL_WORD and not tui.muted_notice(snap, item["level"], item["title"]):
            found.append({"key": issue_key(item["title"]), "level": item["level"],
                          "title": item["title"], "why": item.get("why", "")})
    return found


# --- when to text ----------------------------------------------------------------------------------

def track(state, issues, now):
    """Note which issues are open and since when."""
    seen = state.setdefault("seen", {})
    for issue in issues:
        entry = seen.setdefault(issue["key"], {"first": now, "sent": None, "sent_level": None})
        entry["last"], entry["level"] = now, issue["level"]
    for key in [k for k, e in seen.items() if now - e.get("last", 0) > GONE_S]:
        del seen[key]
    state["texts"] = [t for t in state.get("texts", []) if now - t < 86400]


def confirmed(state, issue, now):
    return now - state["seen"][issue["key"]]["first"] >= CONFIRM_S[issue["level"]]


def should_text(state, issues, now):
    """The confirmed issues to put in a text now, or [] when there is nothing new to say."""
    ready = [i for i in issues if confirmed(state, i, now)]
    news = []                    # the levels of what has not been texted yet
    for issue in ready:
        entry = state["seen"][issue["key"]]
        if entry["sent"] is None:
            news.append(issue["level"])
        elif issue["level"] == "bad" and (entry["sent_level"] != "bad" or now - entry["sent"] >= REMIND_S):
            news.append("bad")
    if not news:
        return []
    if "bad" not in news and now - state.get("last_text", {}).get("at", 0) < WARN_GAP_S:
        return []                # a lesser issue that followed a text joins the next one instead of making its own
    texts = state.get("texts", [])
    if sum(now - t < 3600 for t in texts) >= MAX_PER_HOUR or len(texts) >= MAX_PER_DAY:
        return []
    if now - state.get("attempt", 0) < RETRY_S:
        return []
    return ready


def ascii_line(text):
    return " ".join(str(text).split()).encode("ascii", "replace").decode()


def compose(issues, url, limit=SMS_LIMIT):
    """One text: what is wrong (critical first), then the admin page link, in at most `limit` characters."""
    tail = " " + url
    if len(issues) == 1:
        one = issues[0]
        body = f"{LEVEL_WORD[one['level']]}: {one['title']}" + (f" - {one['why']}" if one["why"] else "")
    else:
        critical = sum(i["level"] == "bad" for i in issues)
        body = f"{len(issues)} issues" + (f" ({critical} critical)" if critical else "") \
            + ": " + "; ".join(i["title"] for i in issues)
    body = ascii_line("MacServer " + body)
    room = limit - len(tail)
    if len(body) > room:
        body = body[:room - 3].rstrip() + "..."
    return body + tail


def check(state, status, now, send, url, tui):
    """One look. Returns (text, sent, detail) when it tried to text, else None. `state` is updated."""
    issues = open_issues(status, tui)
    track(state, issues, now)
    ready = should_text(state, issues, now)
    if not ready:
        return None
    text = compose(ready, url)
    level = "critical" if any(i["level"] == "bad" for i in ready) else "issue"
    sent, detail = send(text, level)
    if sent:
        for issue in ready:
            entry = state["seen"][issue["key"]]
            entry["sent"], entry["sent_level"] = now, issue["level"]
        state.setdefault("texts", []).append(now)
        state["attempt"] = 0
        state["last_text"] = {"at": now, "text": text}
    else:
        state["attempt"] = now
    return text, sent, detail


# --- the hourly report -----------------------------------------------------------------------------

def ago(seconds):
    s = max(int(seconds), 0)
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else f"{s // 60}m"


def compose_report(status, issues, url, limit=REPORT_LIMIT):
    """One line: how the Mac is, then its key numbers, then the admin link. Missing numbers are left out."""
    host = status.get("host") or {}
    live = status.get("live") if isinstance(status.get("live"), dict) and not status["live"].get("error") else {}
    critical = sum(i["level"] == "bad" for i in issues)
    if not issues:
        head = "All systems normal"
    else:
        head = (f"{len(issues)} issue(s)" + (f", {critical} critical" if critical else "") + ": "
                + "; ".join(i["title"] for i in issues[:2]) + ("; ..." if len(issues) > 2 else ""))
    parts = []
    cpu = ((live or {}).get("cpu") or {}).get("total")
    if cpu is not None:
        parts.append(f"CPU {cpu:.0f}%")
    mem = host.get("memory") or {}
    if mem.get("total"):
        parts.append(f"mem {100 * mem.get('used', 0) / mem['total']:.0f}%")
    disk = host.get("disk") or {}
    if disk.get("total"):
        parts.append(f"disk {100 * disk.get('used', 0) / disk['total']:.0f}%")
    temp = (host.get("sensors") or {}).get("cpu_temp_c")
    if temp is not None:
        parts.append(f"{temp:.0f}C")
    containers = status.get("containers") or []
    if containers:
        parts.append(f"{sum(c.get('state') == 'running' for c in containers)}/{len(containers)} services")
    online = (status.get("tailscale") or {}).get("peers_online")
    if online is not None:
        parts.append(f"{online} device(s) online")
    backups = ((status.get("debug") or {}).get("backups") or {}) if isinstance(status.get("debug"), dict) else {}
    if backups.get("newest_age_s") is not None:
        parts.append(f"backup {ago(backups['newest_age_s'])} ago")
    if host.get("updates_pending"):
        parts.append(f"{host['updates_pending']} package update(s)")
    if host.get("uptime_s"):
        parts.append(f"up {ago(host['uptime_s'])}")
    tail = " " + url
    body = ascii_line(f"MacServer: {head} | " + " | ".join(parts)) if parts else ascii_line(f"MacServer: {head}")
    room = limit - len(tail)
    if len(body) > room:
        body = body[:room - 3].rstrip() + "..."
    return body + tail


def report_due(state, now):
    """Due once in each clock hour; after a failed send, again 5 minutes later (not every 30 s)."""
    if int(now // REPORT_S) == state.get("report_hour"):
        return False
    return now - state.get("report_attempt", 0) >= RETRY_S


def report(state, status, now, send, url, tui):
    """The hourly report when it is due. Returns (text, sent, detail) when it tried, else None."""
    if not report_due(state, now):
        return None
    text = compose_report(status, open_issues(status, tui), url)
    sent, detail = send(text, "report")
    if sent:
        state["report_hour"], state["last_report"] = int(now // REPORT_S), {"at": now, "text": text}
        state.pop("report_attempt", None)
    else:
        state["report_attempt"] = now
    return text, sent, detail


# --- the outside world -----------------------------------------------------------------------------

def read_conf(path=CONF):
    conf = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return conf
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            conf[key.strip()] = value.strip()
    return conf


def read_json(path, default):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def write_state(state, path=STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def admin_url(status, conf):
    name = ((status.get("tailscale") or {}).get("name") or conf.get("TAILNET_NAME") or "").strip(".")
    return f"https://{name}/" if re.fullmatch(r"[A-Za-z0-9.-]+", name) else "(admin page: open it over Tailscale)"


def dispatch(repo, token, text, level):
    """Ask the alert-push workflow to send `text`. Returns (accepted, what happened)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return False, f"bad repository name {repo!r} (DOOMSDAY_GH_REPO)"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches", method="POST",
        data=json.dumps({"ref": "main", "inputs": {"message": text, "level": level}}).encode(),
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                 "User-Agent": "macserver-notify"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            code = reply.status
    except urllib.error.HTTPError as err:
        code = err.code
    except (urllib.error.URLError, OSError) as err:
        return False, f"no connection to GitHub ({err.__class__.__name__})"
    hint = {401: " (the token was rejected: sudo macserver doomsday setup-gh)",
            403: " (the token may lack Actions: read and write)",
            404: f" ({WORKFLOW} is not on the main branch of {repo}, or the token cannot see it)",
            422: " (GitHub refused the request)"}.get(code, "")
    return code == 204, f"HTTP {code}{'' if code == 204 else hint}"


def load_status():
    status = read_json(STATUS, {})
    if status:
        status["age_s"] = time.time() - status.get("generated_at", 0)
    return status


def read_token():
    try:
        return TOKEN.read_text().strip()
    except OSError:
        return ""


def setup_problem():
    """Why texting cannot work, or '' when it can."""
    if read_token():
        return ""
    if TOKEN.exists():
        return "cannot read the GitHub token: run this with sudo"
    return "no GitHub token: run  sudo macserver doomsday setup-gh"


def run_check(now=None):
    conf = read_conf()
    if conf.get("NOTIFY_SMS", "on") != "on":
        return 0
    state = read_json(STATE, {})
    now = time.time() if now is None else now
    problem = setup_problem()
    if problem:
        if now - state.get("warned", 0) > 6 * 3600:         # say it now and then, not every 30 s
            print(f"phone alerts are on but cannot send: {problem}", flush=True)
            state["warned"] = now
            write_state(state)
        return 0
    token = read_token()
    repo = conf.get("DOOMSDAY_GH_REPO", DEFAULT_REPO)
    status = load_status()
    send = lambda text, level: dispatch(repo, token, text, level)
    tui, url = load_tui(), admin_url(status, conf)
    done = check(state, status, now, send, url, tui)
    hourly = report(state, status, now, send, url, tui) if conf.get("NOTIFY_REPORT", "on") == "on" and status else None
    write_state(state)
    failed = False
    if done:
        text, sent, detail = done
        print(f"{'texted' if sent else 'text FAILED'} ({detail}): {text}", flush=True)
        failed = not sent
    if hourly:
        text, sent, detail = hourly
        print(f"{'hourly report sent' if sent else 'hourly report FAILED'} ({detail}): {text}", flush=True)
        failed = failed or not sent
    return 1 if failed else 0


def run_test():
    conf = read_conf()
    problem = setup_problem()
    if problem:
        print(problem)
        return 1
    status = load_status()
    text = compose_test(admin_url(status, conf))
    sent, detail = dispatch(conf.get("DOOMSDAY_GH_REPO", DEFAULT_REPO), read_token(), text, "test")
    print(f"{'asked GitHub to send the alert' if sent else 'FAILED'} ({detail}): {text}")
    if sent:
        print("it arrives within about a minute in the ntfy app (GitHub starts the job, then ntfy pushes it)")
    return 0 if sent else 1


def run_report():
    conf = read_conf()
    problem = setup_problem()
    if problem:
        print(problem)
        return 1
    status = load_status()
    if not status:
        print("no status yet: is macserver-status.timer running?")
        return 1
    text = compose_report(status, open_issues(status, load_tui()), admin_url(status, conf))
    sent, detail = dispatch(conf.get("DOOMSDAY_GH_REPO", DEFAULT_REPO), read_token(), text, "report")
    print(f"{'asked GitHub to send the report' if sent else 'FAILED'} ({detail}): {text}")
    return 0 if sent else 1


def compose_test(url):
    return ascii_line("MacServer test: phone alerts work. This is what an alert looks like.") + " " + url


def run_status():
    conf = read_conf()
    state = read_json(STATE, {})
    now = time.time()
    problem = setup_problem()
    print(f"phone alerts: {conf.get('NOTIFY_SMS', 'on')}" + (f"   (cannot send: {problem})" if problem else "   (ready)"))
    last = state.get("last_text")
    print("last alert:   " + (f"{int((now - last['at']) // 60)} min ago: {last['text']}" if last else "none yet"))
    print(f"sent today:   {sum(now - t < 86400 for t in state.get('texts', []))} of {MAX_PER_DAY}")
    on = conf.get("NOTIFY_SMS", "on") == "on" and conf.get("NOTIFY_REPORT", "on") == "on"
    rep = state.get("last_report")
    print(f"hourly report: {'on' if on else 'off'}" + (f"   last {int((now - rep['at']) // 60)} min ago: {rep['text']}" if rep else ""))
    seen = state.get("seen", {})
    print("open issues:  " + ("none" if not seen else ""))
    for key, entry in sorted(seen.items(), key=lambda kv: kv[1].get("level") != "bad"):
        sent = "texted" if entry.get("sent") else "not texted yet"
        print(f"  {LEVEL_WORD.get(entry.get('level'), '?'):8} {key}  (open {int((now - entry['first']) // 60)} min, {sent})")
    return 0


def main(argv):
    command = argv[0] if argv else "check"
    if command == "check":
        return run_check()
    if command == "test":
        return run_test()
    if command == "report":
        return run_report()
    if command == "status":
        return run_status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
