'use strict';
// Renders /api/status. Every value goes through textContent, never innerHTML.

const $ = (id) => document.getElementById(id);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function bytes(n) {
  if (typeof n !== 'number') return '?';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function duration(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function rows(target, pairs) {
  target.replaceChildren();
  for (const [label, value, cls, copy] of pairs) {
    const dd = el('dd', value, cls);
    if (copy) {
      dd.classList.add('mono');
      const btn = el('button', 'Copy');
      btn.type = 'button';
      btn.addEventListener('click', () => navigator.clipboard.writeText(copy).then(() => { btn.textContent = 'Copied'; }));
      dd.append(btn);
    }
    target.append(el('dt', label), dd);
  }
}

function setSummary(text, cls) {
  const summary = $('summary');
  summary.textContent = text;
  summary.className = cls;
}

function render(s) {
  const h = s.host;
  const down = s.containers.filter((c) => c.state !== 'running');
  const temp = h.sensors.cpu_temp_c;
  const hot = temp !== null && temp >= 90;
  if (s.stale) setSummary('Status is out of date: the collector has stopped.', 'bad');
  else if (down.length) setSummary(`${down.length} service(s) not running.`, 'bad');
  else if (hot) setSummary('Running hot.', 'warn');
  else if (h.reboot_required) setSummary('Restart needed to finish updates.', 'warn');
  else setSummary('Everything is running.', 'good');
  $('viewer').textContent = `Signed in through Tailscale as ${s.viewer}`;

  const bat = h.battery;
  const t2 = h.t2_modules || {};
  const onOff = (v) => (v ? 'on' : 'off');
  rows($('host'), [
    ['Model', h.model],
    ['Kernel', `${h.kernel}${h.t2_kernel ? '' : ' (not the T2 kernel)'}`, h.t2_kernel ? '' : 'warn'],
    ['T2 drivers', `keyboard ${onOff(t2.keyboard)} · Wi-Fi ${onOff(t2.brcmfmac)} · SMC ${onOff(t2.applesmc)}`],
    ['Uptime', duration(h.uptime_s)],
    ['Load', `${h.load.join(' / ')} on ${h.cpus} threads`],
    ['Memory', `${bytes(h.memory.used)} of ${bytes(h.memory.total)}`],
    ['Disk', `${bytes(h.disk.used)} of ${bytes(h.disk.total)}`],
    ['CPU temp', temp === null ? 'unknown' : `${temp.toFixed(0)} °C`, hot ? 'bad' : ''],
    ['Fans', h.sensors.fans_rpm.length ? h.sensors.fans_rpm.map((r) => `${r} rpm`).join(', ') : 'unknown'],
    ['Battery', bat ? `${bat.percent ?? '?'}% · ${bat.status}${bat.limit ? ` · stops at ${bat.limit}%` : ''}` : 'none'],
    ['Updates', h.updates_pending ? `${h.updates_pending} pending (sudo macserver update)` : 'up to date', h.updates_pending ? 'warn' : ''],
    ['Tailscale', `${s.tailscale.state}${s.tailscale.name ? ` · ${s.tailscale.name}` : ''}`],
    ['MacServer', s.macserver_update ? `${s.macserver_update.message}${s.macserver_update.auto === 'off' ? ' (auto-update off)' : ''}` : 'unknown',
      s.macserver_update && ['skipped', 'refused', 'rolled-back', 'error'].includes(s.macserver_update.state) ? 'warn' : ''],
  ]);

  const keys = s.public_keys || {};
  const pairs = [
    ['API URL', s.api_url || 'unknown', '', s.api_url],
    ['Public route', s.public_domain ? `https://${s.public_domain}` : 'off (tailnet only)'],
  ];
  if (keys.SUPABASE_PUBLISHABLE_KEY) pairs.push(['Publishable key', `${keys.SUPABASE_PUBLISHABLE_KEY.slice(0, 24)}…`, '', keys.SUPABASE_PUBLISHABLE_KEY]);
  if (keys.ANON_KEY) pairs.push(['Anon key (legacy)', `${keys.ANON_KEY.slice(0, 24)}…`, '', keys.ANON_KEY]);
  pairs.push(['Secret keys', 'never shown here: sudo macserver keys']);
  rows($('connect'), pairs);

  renderClaude(s.claude, s.tailscale.name);

  const body = $('services');
  body.replaceChildren();
  for (const c of s.containers) {
    const tr = el('tr');
    tr.append(el('td', c.name, 'mono'), el('td', c.project), el('td', c.state, c.state === 'running' ? 'good' : 'bad'), el('td', c.status));
    body.append(tr);
  }
}

function renderClaude(c, host) {
  const state = $('claude-state');
  const start = $('claude-start'), open = $('claude-open'), stop = $('claude-stop');
  const ssh = `ssh <your user>@${(host || 'macserver').split('.')[0]}`;
  const running = c && (c.state === 'active' || c.state === 'activating');
  start.hidden = !c || !c.installed || running;
  stop.hidden = !running;
  open.hidden = true;
  state.className = '';
  if (!c || !c.installed) {
    state.textContent = 'Claude Code is not installed. Over SSH run: sudo /opt/macserver-src/install.sh --redo claude';
  } else if (c.problem === 'login') {
    state.textContent = `Sign in once first: ${ssh}, run claude, and follow the login link. Then start the session here.`;
    state.className = 'warn';
  } else if (c.problem === 'consent') {
    state.textContent = `Allow Remote Control once: ${ssh}, run claude remote-control, answer y, then press Ctrl+C. Then start it here.`;
    state.className = 'warn';
  } else if (running && c.url && c.url.startsWith('https://claude.ai/code')) {
    state.textContent = 'A session is running on this Mac.';
    state.className = 'good';
    open.href = c.url;
    open.hidden = false;
  } else if (running) {
    state.textContent = 'Starting… (the link appears here in a few seconds; it is also listed at claude.ai/code)';
  } else if (c.state === 'failed') {
    state.textContent = 'The last session stopped with an error. Try again, or check: journalctl -u macserver-claude';
    state.className = 'bad';
  } else {
    state.textContent = 'No session running. Start one to get help connecting apps and backends.';
  }
}

async function claudeAction(action) {
  const buttons = [$('claude-start'), $('claude-stop')];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const res = await fetch('/api/claude', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) throw new Error(String(res.status));
    $('claude-state').textContent = action === 'start' ? 'Starting…' : 'Stopping…';
    for (let i = 0; i < 12; i++) setTimeout(refresh, 3000 * (i + 1));
  } catch {
    $('claude-state').textContent = 'The server refused the request.';
    $('claude-state').className = 'bad';
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

$('claude-start').addEventListener('click', () => claudeAction('start'));
$('claude-stop').addEventListener('click', () => claudeAction('stop'));

async function refresh() {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    const data = await res.json();
    if (data.error) { setSummary(data.error, 'warn'); return; }
    render(data);
  } catch {
    setSummary('Cannot reach the server.', 'bad');
  }
}

refresh();
setInterval(refresh, 2000);
