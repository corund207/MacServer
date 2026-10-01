'use strict';
// btop-style view of /api/status. Every value goes through textContent, never innerHTML.
// The collector reports raw rates every 2 s; the graphs are the history this page keeps.

const $ = (id) => document.getElementById(id);
const HISTORY = 120;   // samples per graph: 4 minutes at 2 s
const COLORS = { good: '#3fb950', warn: '#d29922', bad: '#f85149', purple: '#bc8cff', cyan: '#39c5cf', grid: '#21262d' };

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function bytes(n) {
  if (typeof n !== 'number' || Number.isNaN(n)) return '?';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 100 ? 1 : 0)} ${units[i]}`;
}

const perSecond = (n) => `${bytes(n)}/s`;

function duration(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

const heat = (pct) => (pct >= 85 ? 'bad' : pct >= 60 ? 'warn' : 'good');

function push(series, value) {
  series.push(value);
  if (series.length > HISTORY) series.shift();
}

const hist = { cpu: [], rx: [], tx: [], req: [] };
let lastSample = 0;
let latest = null;
let sortKey = 'cpu';
let sortDir = -1;

// ---- graphs -------------------------------------------------------------------------------

function fitCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;   // its tab is hidden
  if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

// One column per sample, newest on the right. `mode` "heat" colours tall columns red, like
// btop's gradient; any other value is a fixed colour and the scale follows the data.
function graph(id, series, { max = null, mode = 'heat' } = {}) {
  const canvas = $(id);
  const area = fitCanvas(canvas);
  if (!area) return 0;
  const { ctx, w, h } = area;
  const top = max || Math.max(1024, ...series) * 1.15;
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const y = Math.round((h * i) / 4) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  if (mode === 'heat') {
    const g = ctx.createLinearGradient(0, h, 0, 0);
    g.addColorStop(0, COLORS.good); g.addColorStop(0.6, COLORS.warn); g.addColorStop(1, COLORS.bad);
    ctx.fillStyle = g;
  } else {
    ctx.fillStyle = mode;
  }
  const step = w / HISTORY;
  const gap = step > 3 ? 1 : 0;
  series.forEach((v, i) => {
    const x = w - (series.length - i) * step;
    const bar = Math.max(v > 0 ? 1 : 0, Math.min(v / top, 1) * (h - 1));
    ctx.fillRect(x, h - bar, Math.max(step - gap, 1), bar);
  });
  return top;
}

function meterRow(name, pct, value, cls) {
  const row = el('div', null, 'meter-row');
  const track = el('div', null, 'meter');
  const fill = el('div', null, 'fill');
  fill.style.width = `${Math.max(0, Math.min(pct, 100))}%`;
  track.append(fill);
  row.append(el('span', name, 'name'), track, el('span', value, `val ${cls || ''}`));
  // The gradient spans the whole track, so a full bar is red and a short one stays green.
  requestAnimationFrame(() => { fill.style.backgroundSize = `${track.clientWidth || 100}px 100%`; });
  return row;
}

// ---- tables and lists ---------------------------------------------------------------------

function fill(tbody, rows) {
  tbody.replaceChildren();
  for (const cells of rows) {
    const tr = el('tr');
    for (const [text, cls] of cells) tr.append(el('td', text, cls));
    tbody.append(tr);
  }
  return tbody;
}

function line(item) {
  if (item === null || item === undefined) return '';
  if (typeof item !== 'object') return String(item);
  return Object.values(item).filter((v) => v !== '' && v !== null && typeof v !== 'object').join(' · ');
}

function items(list, values, empty) {
  list.replaceChildren();
  const shown = (values || []).slice(0, 12);
  if (!shown.length) list.append(el('li', empty, 'dim'));
  for (const v of shown) list.append(el('li', line(v)));
}

function rows(target, pairs) {
  target.replaceChildren();
  for (const [label, value, cls, copy] of pairs) {
    const dd = el('dd', value, cls);
    if (copy) {
      const btn = el('button', 'copy');
      btn.type = 'button';
      btn.addEventListener('click', () => navigator.clipboard.writeText(copy).then(() => { btn.textContent = 'copied'; }));
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

// ---- overview -----------------------------------------------------------------------------

function renderCpu(s) {
  const live = s.live && !s.live.error ? s.live : null;
  const h = s.host;
  const cores = $('cores');
  cores.replaceChildren();
  if (live) {
    live.cpu.cores.forEach((pct, i) => {
      const mhz = live.cpu.freq_mhz[i];
      cores.append(meterRow(`c${i}`, pct, `${pct.toFixed(0)}%`, heat(pct)));
      if (mhz) cores.lastChild.title = `${mhz} MHz`;
    });
  }
  graph('g-cpu', hist.cpu, { max: 100 });
  const temp = h.sensors.cpu_temp_c;
  const mhz = live ? live.cpu.freq_mhz.filter((x) => x) : [];
  const foot = $('cpu-foot');
  foot.replaceChildren(
    el('span', `total ${live ? live.cpu.total.toFixed(0) : '?'}%`, live ? heat(live.cpu.total) : 'dim'),
    el('span', `load ${h.load.join(' ')}  (${h.cpus} threads)`),
    el('span', `temp ${temp === null ? '?' : `${temp.toFixed(0)}°C`}`, temp !== null && temp >= 90 ? 'bad' : temp !== null && temp >= 75 ? 'warn' : ''),
    ...(mhz.length ? [el('span', `${Math.round(mhz.reduce((a, b) => a + b, 0) / mhz.length)} MHz`)] : []),
    ...(live ? [el('span', `${live.tasks.total} tasks, ${live.tasks.running} running, ${live.tasks.threads} threads`, 'dim')] : []),
    el('span', `up ${duration(h.uptime_s)}`, 'dim'),
  );
}

function renderMem(s) {
  const h = s.host, live = s.live && !s.live.error ? s.live : null;
  const box = $('mem-rows');
  box.className = 'wide-meters';
  box.replaceChildren();
  const mem = h.memory;
  const memPct = mem.total ? (100 * mem.used) / mem.total : 0;
  box.append(meterRow('used', memPct, `${bytes(mem.used)} / ${bytes(mem.total)}`, heat(memPct)));
  if (live && live.mem.total) {
    const cachePct = (100 * live.mem.cached) / live.mem.total;
    box.append(meterRow('cache', cachePct, bytes(live.mem.cached)));
  }
  if (live && live.swap.total) {
    const swapPct = (100 * live.swap.used) / live.swap.total;
    box.append(meterRow('swap', swapPct, `${bytes(live.swap.used)} / ${bytes(live.swap.total)}`, heat(swapPct)));
  }
  const diskPct = h.disk.total ? (100 * h.disk.used) / h.disk.total : 0;
  box.append(meterRow('disk /', diskPct, `${bytes(h.disk.used)} / ${bytes(h.disk.total)}`, heat(diskPct)));
  if (live) {
    box.append(el('div', `disk io  read ${perSecond(live.disk_io.read)}  write ${perSecond(live.disk_io.write)}`, 'foot'));
  }
  const bat = h.battery;
  if (bat) box.append(el('div', `battery ${bat.percent ?? '?'}% · ${bat.status}${bat.limit ? ` · stops at ${bat.limit}%` : ''}`, 'foot'));
}

function renderNet(s) {
  const live = s.live && !s.live.error ? s.live : null;
  const topRx = graph('g-rx', hist.rx, { mode: COLORS.purple });
  const topTx = graph('g-tx', hist.tx, { mode: COLORS.cyan });
  $('rx-now').textContent = live ? perSecond(live.net.rx) : '?';
  $('tx-now').textContent = live ? perSecond(live.net.tx) : '?';
  const foot = $('net-foot');
  foot.replaceChildren();
  if (live) {
    for (const [name, v] of Object.entries(live.net.ifaces)) {
      if (v.rx || v.tx || !name.startsWith('br-')) foot.append(el('span', `${name} ▼${bytes(v.rx)} ▲${bytes(v.tx)}`));
    }
  }
  foot.append(el('span', `scale ${perSecond(Math.max(topRx || 0, topTx || 0))}`, 'dim'));
}

function sortProcs(list) {
  const key = sortKey;
  return [...list].sort((a, b) => {
    const x = a[key], y = b[key];
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * (key === 'name' || key === 'user' || key === 'pid' ? -sortDir : sortDir);
  });
}

function renderProcs(s) {
  const live = s.live && !s.live.error ? s.live : null;
  const body = $('procs');
  if (!live) { fill(body, []); $('proc-foot').textContent = 'process list not collected yet'; return; }
  fill(body, sortProcs(live.procs).map((p) => [
    [p.pid, 'dim'], [p.name], [p.user, 'dim'], [bytes(p.rss), 'r'], [p.cpu.toFixed(1), `r ${p.cpu >= 50 ? 'bad' : p.cpu >= 15 ? 'warn' : ''}`],
  ]));
  $('proc-foot').textContent = `${live.procs.length} of ${live.tasks.total} processes: busiest by cpu, biggest by memory`;
  for (const th of document.querySelectorAll('#proc-table th[data-sort]')) th.classList.toggle('on', th.dataset.sort === sortKey);
}

let openService = null;

function renderServices(s) {
  const body = $('services');
  body.replaceChildren();
  for (const c of s.containers) {
    const tr = el('tr', null, 'click');
    const ok = c.state === 'running' && !(c.status || '').includes('unhealthy');
    tr.append(
      el('td', c.name.replace(/^supabase-/, ''), 'mono'),
      el('td', c.state, `state ${ok ? 'good' : 'bad'}`),
      el('td', c.cpu === null || c.cpu === undefined ? '' : c.cpu.toFixed(1), 'r'),
      el('td', c.mem ? bytes(c.mem) : '', 'r'),
    );
    tr.addEventListener('click', () => { openService = openService === c.name ? null : c.name; renderServiceDetail(); });
    body.append(tr);
  }
  renderServiceDetail();
}

function renderServiceDetail() {
  const pre = $('svc-detail');
  const c = latest && latest.containers.find((x) => x.name === openService);
  pre.hidden = !c;
  if (!c) return;
  const logs = c.logs && c.logs.length ? `\n\n${c.logs.join('\n')}` : '';
  pre.textContent = `${c.name} (${c.project})\n${c.status}${logs}`;
}

function renderSystem(s) {
  const h = s.host, t2 = h.t2_modules || {};
  const onOff = (v) => (v ? 'on' : 'off');
  const idle = s.idle || {};
  const idleText = idle.mode === 'off' ? 'off'
    : idle.idle ? `low-power${idle.since ? ` since ${new Date(idle.since * 1000).toLocaleString()}` : ''}`
    : `active (low-power after ${Math.max(Math.round((idle.after_s || 900) / 60), 1)}m quiet)`;
  rows($('host'), [
    ['idle', idleText],
    ['model', h.model],
    ['kernel', `${h.kernel}${h.t2_kernel ? '' : ' (not the T2 kernel)'}`, h.t2_kernel ? '' : 'warn'],
    ['t2', `kbd ${onOff(t2.keyboard)} · wifi ${onOff(t2.brcmfmac)} · smc ${onOff(t2.applesmc)}`],
    ['fans', h.sensors.fans_rpm.length ? h.sensors.fans_rpm.map((r) => `${r} rpm`).join(', ') : 'unknown'],
    ['updates', h.updates_pending ? `${h.updates_pending} pending (sudo macserver update)` : 'up to date', h.updates_pending ? 'warn' : ''],
    ['tailscale', `${s.tailscale.state} · ${s.tailscale.peers_online ?? 0} peer(s) online`],
    ['macserver', s.macserver_update ? `${s.macserver_update.message}${s.macserver_update.auto === 'off' ? ' (auto-update off)' : ''}` : 'unknown',
      s.macserver_update && ['skipped', 'refused', 'rolled-back', 'error'].includes(s.macserver_update.state) ? 'warn' : ''],
    ['clock', s.clock_synced ? 'synced' : 'not synced', s.clock_synced ? '' : 'warn'],
  ]);
  const keys = s.public_keys || {};
  const pairs = [
    ['api url', s.api_url || 'unknown', '', s.api_url],
    ['public', s.public_domain ? `https://${s.public_domain}` : 'off (tailnet only)'],
  ];
  if (keys.SUPABASE_PUBLISHABLE_KEY) pairs.push(['publishable', `${keys.SUPABASE_PUBLISHABLE_KEY.slice(0, 16)}…`, '', keys.SUPABASE_PUBLISHABLE_KEY]);
  if (keys.ANON_KEY) pairs.push(['anon (legacy)', `${keys.ANON_KEY.slice(0, 16)}…`, '', keys.ANON_KEY]);
  pairs.push(['secret keys', 'sudo macserver keys']);
  rows($('connect'), pairs);
}

// ---- network ------------------------------------------------------------------------------

function renderNetwork(s) {
  const d = s.debug;
  if (!d || d.error) { $('lat-rows').textContent = d && d.error ? d.error : 'not collected yet'; return; }
  const lat = $('lat-rows');
  lat.className = 'wide-meters';
  lat.replaceChildren();
  const n = d.net || {};
  for (const [name, ms] of [['dns', n.dns_ms], ['gateway', n.gateway_ms], ['internet', n.internet_ms]]) {
    const cls = ms === null || ms === undefined ? 'bad' : ms > 150 ? 'bad' : ms > 60 ? 'warn' : 'good';
    lat.append(meterRow(name, ms ? Math.min(ms / 2, 100) : 0, ms === null || ms === undefined ? 'no answer' : `${ms.toFixed(0)} ms`, cls));
  }
  lat.append(el('div', `gateway ${n.gateway || '?'} · failed checks: dns ${n.dns_fails || 0}, internet ${n.internet_fails || 0}`, 'foot'));

  const r = d.requests || {};
  hist.req = (r.per_2s || []).slice(-HISTORY);
  graph('g-req', hist.req, { mode: COLORS.cyan });
  const c = r.classes || {};
  $('req-foot').replaceChildren(
    el('span', `${r.total || 0} in ${r.window_s || 60} s`),
    el('span', `2xx ${c['2xx'] || 0}`, 'good'), el('span', `3xx ${c['3xx'] || 0}`),
    el('span', `4xx ${c['4xx'] || 0}`, c['4xx'] ? 'warn' : ''), el('span', `5xx ${c['5xx'] || 0}`, c['5xx'] ? 'bad' : ''),
  );

  const scopeCls = { public: 'bad', lan: 'warn', tailnet: 'good' };
  fill($('listeners'), (d.listeners || []).map((l) => [
    [l.port, 'mono'], [l.addr, 'dim'], [l.scope, scopeCls[l.scope] || ''], [`${l.proc || '?'}${l.svc ? ` (${l.svc})` : ''}`],
  ]));
  const flows = d.flows || {};
  fill($('flows'), [
    ...(flows.out || []).map((f) => [['out', 'dim'], [f.dst], [`${f.svc || f.port}`], [f.n, 'r']]),
    ...(flows.in || []).map((f) => [['in', 'dim'], [f.src], [`${f.svc || f.port}`], [f.n, 'r']]),
  ]);
  const states = Object.entries(flows.states || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  $('flow-foot').textContent = `${flows.tracked || 0} tracked${states ? ` · ${states}` : ''}`;
  items($('top-paths'), r.top_paths, 'no requests yet');
  items($('top-clients'), r.top_clients, 'no clients yet');

  const sys = d.system || {};
  const problems = [
    ...(sys.failed_units || []).map((u) => `failed unit: ${line(u)}`),
    ...(sys.oom_kills ? [`${sys.oom_kills} process(es) killed for lack of memory`] : []),
    ...Object.entries(d.service_errors || {}).map(([name, lines]) => `${name}: ${line(lines[lines.length - 1])}`),
    ...(r.errors || []).map((e) => `request error: ${line(e)}`),
    ...(sys.journal || []).map((j) => `journal: ${line(j)}`),
    ...(sys.kernel || []).map((k) => `kernel: ${line(k)}`),
  ];
  items($('problems'), problems, 'nothing wrong');
}

// ---- header, claude, refresh --------------------------------------------------------------

function ingest(s) {
  if (s.live && !s.live.error) {
    push(hist.cpu, s.live.cpu.total);
    push(hist.rx, s.live.net.rx);
    push(hist.tx, s.live.net.tx);
  }
}

function render(s) {
  latest = s;
  const h = s.host;
  const down = s.containers.filter((c) => c.state !== 'running');
  const temp = h.sensors.cpu_temp_c;
  if (s.stale) setSummary('status is out of date: the collector has stopped', 'bad');
  else if (down.length) setSummary(`${down.length} service(s) not running`, 'bad');
  else if (temp !== null && temp >= 90) setSummary('running hot', 'warn');
  else if (h.reboot_required) setSummary('restart needed to finish updates', 'warn');
  else setSummary('everything is running', 'good');
  $('viewer').textContent = `signed in through Tailscale as ${s.viewer}`;
  $('host-name').textContent = (s.tailscale.name || 'macserver').split('.')[0];

  renderCpu(s); renderMem(s); renderNet(s); renderProcs(s); renderServices(s); renderSystem(s); renderNetwork(s);
  renderClaude(s.claude, s.tailscale.name);
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
    state.textContent = 'A session is running on this Mac. Continue it in claude.ai/code or the Claude app.';
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

// ---- dev tab --------------------------------------------------------------------------------

const DEV_COMMANDS = [
  { cmd: 'macserver status', desc: 'Health of Mac, Tailscale, containers', root: true },
  { cmd: 'macserver dashboard', desc: 'Full-screen live dashboard (on Mac screen)', root: false },
  { cmd: 'macserver keys', desc: 'API keys and Studio password (secret)', root: true },
  { cmd: 'macserver logs [service]', desc: 'Follow Supabase logs (auth, rest, db, storage)', root: true },
  { cmd: 'macserver restart', desc: 'Restart Supabase and public route', root: true },
  { cmd: 'macserver update', desc: 'Backup DB, then update Debian, T2 kernel, containers', root: true },
  { cmd: 'macserver upgrade', desc: 'Install newest MacServer version now', root: true },
  { cmd: 'macserver autoupdate on|off|status', desc: 'Auto-update from GitHub (on by default)', root: true },
  { cmd: 'macserver backup', desc: 'Write compressed DB dump to /var/backups/macserver', root: true },
  { cmd: 'macserver public setup', desc: 'Turn on or change public API route', root: true },
  { cmd: 'macserver public on|off', desc: 'Start/stop public API route', root: true },
  { cmd: 'macserver idle [status|auto|on|off]', desc: 'Low-power mode when nobody connected', root: true },
  { cmd: 'macserver wifi', desc: 'Set up or change built-in Wi-Fi', root: true },
  { cmd: 'macserver doctor', desc: 'Check network, DNS and T2 drivers', root: false },
];

function renderDev() {
  const tbody = $('dev-commands');
  if (!tbody) return;
  tbody.replaceChildren();
  for (const c of DEV_COMMANDS) {
    const tr = el('tr');
    tr.append(el('td', c.cmd, 'mono'), el('td', c.desc), el('td', c.root ? 'yes' : 'no', 'dim'));
    const td = el('td');
    const btn = el('button', 'run');
    btn.type = 'button';
    btn.addEventListener('click', () => runDevCommand(c.cmd, c.root));
    td.append(btn);
    tr.append(td);
    tbody.append(tr);
  }

  $('dev-status-btn').onclick = () => runDevCommand('macserver status', true);
  $('dev-logs-btn').onclick = () => {
    const service = $('dev-log-service').value;
    const lines = $('dev-log-lines').value;
    runDevCommand(`macserver logs ${service} --tail ${lines}`, true);
  };
  $('dev-keys-btn').onclick = () => runDevCommand('macserver keys', true);
  $('dev-doctor-btn').onclick = () => runDevCommand('macserver doctor', false);
  $('dev-backup-btn').onclick = () => runDevCommand('macserver backup', true);
  $('dev-idle-status-btn').onclick = () => runDevCommand('macserver idle status', true);
  $('dev-idle-auto-btn').onclick = () => runDevCommand('macserver idle auto', true);
  $('dev-idle-on-btn').onclick = () => runDevCommand('macserver idle on', true);
  $('dev-idle-off-btn').onclick = () => runDevCommand('macserver idle off', true);
  $('dev-public-status-btn').onclick = () => runDevCommand('macserver public', false);
  $('dev-public-on-btn').onclick = () => runDevCommand('macserver public on', true);
  $('dev-public-off-btn').onclick = () => runDevCommand('macserver public off', true);
  $('dev-update-btn').onclick = () => runDevCommand('macserver update', true);
  $('dev-restart-btn').onclick = () => runDevCommand('macserver restart', true);
}

async function runDevCommand(cmd, needsRoot) {
  const out = needsRoot ? $('dev-output') : null;
  const target = out || $(`dev-${cmd.split(' ')[1]}-output`) || $('dev-output');
  if (!target) return;
  target.hidden = false;
  target.textContent = `$ ${cmd}\n`;
  try {
    const res = await fetch('/api/dev', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd, needsRoot }),
    });
    const text = await res.text();
    target.textContent += text;
    if (!res.ok) target.className = 'detail bad';
    else target.className = 'detail';
  } catch (e) {
    target.textContent += `Error: ${e}`;
    target.className = 'detail bad';
  }
  target.scrollTop = target.scrollHeight;
}

async function refresh() {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    const data = await res.json();
    if (data.error) { setSummary(data.error, 'warn'); return; }
    if (data.generated_at !== lastSample) { lastSample = data.generated_at; ingest(data); }   // one graph column per reading
    render(data);
  } catch {
    setSummary('cannot reach the server', 'bad');
  }
}

// ---- tabs ---------------------------------------------------------------------------------

const TABS = ['overview', 'network', 'terminal', 'dev'];

function showTab(name) {
  if (!TABS.includes(name)) name = 'overview';
  for (const t of TABS) $(`view-${t}`).hidden = t !== name;
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b.dataset.tab === name);
  try { history.replaceState(null, '', `#${name}`); } catch { /* not fatal */ }
  const frame = $('term-frame');
  if (name === 'terminal') {
    if (!frame.getAttribute('src')) frame.src = '/terminal.html';   // the shell starts on first visit and stays open
    frame.contentWindow && frame.contentWindow.postMessage('focus', location.origin);
  }
  if (latest) render(latest);   // canvases in a hidden tab have no size: draw them now
  if (name === 'dev') renderDev();
}

for (const b of document.querySelectorAll('#tabs button')) b.addEventListener('click', () => showTab(b.dataset.tab));
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const i = ['1', '2', '3', '4'].indexOf(e.key);
  if (i >= 0) showTab(TABS[i]);
});
$('term-frame').addEventListener('load', () => {
  $('term-frame').contentWindow.postMessage('focus', location.origin);
});

for (const th of document.querySelectorAll('#proc-table th[data-sort]')) {
  th.addEventListener('click', () => {
    sortDir = sortKey === th.dataset.sort ? -sortDir : -1;
    sortKey = th.dataset.sort;
    if (latest) renderProcs(latest);
  });
}

window.addEventListener('resize', () => { if (latest) render(latest); });

function tick() {
  $('clock').textContent = new Date().toLocaleTimeString([], { hour12: false });
}
tick();
setInterval(tick, 1000);

showTab(location.hash.slice(1));
refresh();
setInterval(refresh, 2000);
