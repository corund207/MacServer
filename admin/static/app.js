'use strict';
// The MacServer admin page: a view of /api/status, refreshed every 2 s. Every value goes through textContent,
// never innerHTML. The collector reports raw numbers; the charts are the history this page keeps. Heat is shown
// as a reading and never raises a notice: this Mac sits in the 90s with the fan at full speed by design. The one
// cooling alarm is a fault (a warm CPU whose fan is not turning), the same rule as the Mac's own screen.

const $ = (id) => document.getElementById(id);
const HISTORY = 150;                 // samples per chart: 5 minutes at 2 s
const COOLING_CHECK_C = 85;          // a stopped fan matters once the CPU is this warm (admin/tui.py cooling_fault)
const FAN_STALLED_RPM = 300;         // the T2 fan idles near 1200 rpm

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
const span = (text, cls) => el('span', text, cls);

// ---- formatting ---------------------------------------------------------------------------------------------

function bytes(n, perSecond = false) {
  if (typeof n !== 'number' || Number.isNaN(n)) return '?';
  for (const [div, unit] of [[1e12, 'TB'], [1e9, 'GB'], [1e6, 'MB'], [1e3, 'KB'], [1, 'B']]) {
    if (n >= div || div === 1) {
      const v = n / div;
      return `${div > 1 && v < 100 ? v.toFixed(1) : Math.round(v)} ${unit}${perSecond ? '/s' : ''}`;
    }
  }
  return '0 B';
}
const rate = (n) => bytes(n, true);
const count = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n || 0)));
const pct = (n, digits = 0) => `${Number(n).toFixed(digits)}%`;
const share = (a, b) => (b ? (100 * a) / b : 0);

function duration(s) {
  s = Math.max(Math.floor(s), 0);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`;
}
function since(epoch, now = Date.now() / 1000) {
  if (!epoch) return 'never';
  const s = Math.max(now - epoch, 0);
  return s < 90 ? `${Math.floor(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : s < 129600 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
}
const ms = (v) => (v === null || v === undefined ? 'no answer' : `${v.toFixed(0)} ms`);
const level = (frac, warn = 0.85, bad = 0.95) => (frac >= bad ? 'bad' : frac >= warn ? 'warn' : 'ok');          // state dots
const fillLevel = (frac, warn = 0.85, bad = 0.95) => (frac >= bad ? 'bad' : frac >= warn ? 'warn' : 's1');   // capacity bars: neutral until it matters
const shortName = (c) => c.name.split('.').pop().replace(/^supabase-/, '').replace(new RegExp(`^${c.project || '-'}-`), '').replace(/-\d+$/, '');
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ---- state --------------------------------------------------------------------------------------------------

const hist = { cpu: [], mem: [], rx: [], tx: [], rd: [], wr: [], temp: [], load: [], svc: [], tps: [], req: [] };
let latest = null;
let lastSample = 0;
const sorts = { procs: { key: 'cpu', dir: -1 }, svc: { key: 'mem', dir: -1 } };
let openService = null;

function push(name, value) {
  const series = hist[name];
  series.push(value);
  if (series.length > HISTORY) series.shift();
}
const sys = (s) => (s.system && !s.system.error ? s.system : {});
const live = (s) => (s.live && !s.live.error ? s.live : null);

// ---- charts -------------------------------------------------------------------------------------------------

function fit(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;          // its page is hidden
  if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceMax(v) {
  const exp = 10 ** Math.floor(Math.log10(Math.max(v, 1e-9)));
  for (const step of [1, 2, 5, 10]) if (step * exp >= v) return step * exp;
  return v;
}

// One or more series as lines over a soft fill; the scale label sits top-left. `list` = [{values, color}].
function chart(canvas, list, { max = null, fmt = String, bars = false, labels = true } = {}) {
  const area = fit(canvas);
  if (!area) return;
  const { ctx, w, h } = area;
  const top = max || niceMax(Math.max(1, ...list.flatMap((l) => l.values)) * 1.08);
  const padT = labels ? 15 : 3, padB = 2, plot = h - padT - padB;
  ctx.strokeStyle = cssVar('--line-soft');
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const y = Math.round(padT + (plot * i) / 3) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  if (labels) {
    ctx.fillStyle = cssVar('--muted');
    ctx.font = '11px system-ui, sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText(fmt(top), 0, 0);
  }
  const step = w / (HISTORY - 1);
  const yOf = (v) => padT + plot - Math.min(Math.max(v / top, 0), 1) * plot;
  for (const { values, color } of list) {
    if (!values.length) continue;
    const c = cssVar(color);
    const xOf = (i) => w - (values.length - 1 - i) * step;
    if (bars) {
      ctx.fillStyle = c;
      values.forEach((v, i) => { const y = yOf(v); ctx.fillRect(xOf(i) - step * 0.8, y, Math.max(step * 0.8, 1), padT + plot - y); });
      continue;
    }
    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(xOf(i), yOf(v)) : ctx.moveTo(xOf(i), yOf(v))));
    ctx.strokeStyle = c;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.lineTo(xOf(values.length - 1), padT + plot);
    ctx.lineTo(xOf(0), padT + plot);
    ctx.closePath();
    ctx.globalAlpha = list.length > 1 ? 0.1 : 0.16;
    ctx.fillStyle = c;
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

// ---- small building blocks ----------------------------------------------------------------------------------

function meter(name, frac, value, cls, label) {
  const row = el('div', null, 'meter');
  if (label) row.style.setProperty('--label', label);
  const track = el('div', null, 'track');
  const fill = el('div', null, `fill ${cls || ''}`);
  fill.style.width = `${Math.max(0, Math.min(frac * 100, 100))}%`;
  track.append(fill);
  row.append(span(name, 'name'), track, span(value, 'val'));
  return row;
}

function kv(pairs) {
  const dl = el('dl', null, 'kv');
  for (const [label, value, cls, copy] of pairs) {
    if (value === null || value === undefined || value === '') continue;
    const dd = el('dd', value, cls);
    if (copy) {
      const btn = el('button', 'copy');
      btn.type = 'button';
      btn.addEventListener('click', () => navigator.clipboard.writeText(copy).then(() => { btn.textContent = 'copied'; }));
      dd.append(btn);
    }
    dl.append(el('dt', label), dd);
  }
  return dl;
}

function put(node, ...children) { node.replaceChildren(...children.filter(Boolean)); return node; }
const empty = (text) => el('p', text, 'empty');
const group = (title, ...content) => { const g = el('div', null, 'group'); if (title) g.append(el('h3', title, 'group-title')); g.append(...content.filter(Boolean)); return g; };
const stateCell = (text, lvl) => span(text, `state ${lvl || ''}`);
const tag = (text, lvl) => span(text, `tag ${lvl || ''}`);

function legend(items) {
  const row = el('div', null, 'chart-legend');
  for (const [text, value, color] of items) {
    const item = span(text);
    item.style.setProperty('--c', `var(${color})`);
    if (value !== undefined) item.append(el('b', value));
    row.append(item);
  }
  return row;
}

function stack(parts) {
  const bar = el('div', null, 'stack');
  for (const [frac, color] of parts) {
    const seg = el('i');
    seg.style.width = `${Math.max(frac * 100, 0)}%`;
    seg.style.background = `var(${color})`;
    bar.append(seg);
  }
  return bar;
}

function swatches(items) {
  const row = el('div', null, 'legend');
  for (const [text, color] of items) { const s = span(text); s.style.setProperty('--c', `var(${color})`); row.append(s); }
  return row;
}

// A table: columns [{label, align, key (sortable)}] and rows of cells (string, Node or null). `sortKey` marks the active column.
function renderTable(table, columns, rows, { sort = null, onSort = null, rowClass = null } = {}) {
  const head = el('tr');
  for (const col of columns) {
    const th = el('th', col.label, `${col.align === 'r' ? 'r' : ''} ${col.key && onSort ? 'sortable' : ''}`);
    th.scope = 'col';
    if (col.key && sort && sort.key === col.key) th.setAttribute('aria-sort', sort.dir > 0 ? 'ascending' : 'descending');
    if (col.key && onSort) th.addEventListener('click', () => onSort(col.key));
    head.append(th);
  }
  table.tHead.replaceChildren(head);
  const body = table.tBodies[0];
  body.replaceChildren();
  for (const row of rows) {
    if (row.group !== undefined) {
      const tr = el('tr', null, 'group');
      const td = el('td', null);
      td.colSpan = columns.length;
      td.append(...[row.group].flat().filter(Boolean));
      tr.append(td);
      body.append(tr);
      continue;
    }
    const tr = el('tr', null, [row.className, row.onClick ? 'click' : ''].filter(Boolean).join(' '));
    if (row.onClick) tr.addEventListener('click', row.onClick);
    row.cells.forEach((cell, i) => {
      const td = el('td', null, `${columns[i] && columns[i].align === 'r' ? 'r' : ''} ${columns[i] && columns[i].wrap ? 'wrap' : ''}`);
      if (cell instanceof Node) td.append(cell); else if (cell !== null && cell !== undefined) td.textContent = String(cell);
      tr.append(td);
    });
    body.append(tr);
    if (row.detail) {
      const dr = el('tr', null, 'detail');
      const dd = el('td', row.detail);
      dd.colSpan = columns.length;
      dr.append(dd);
      body.append(dr);
    }
  }
  if (!rows.length) {
    const tr = el('tr'); const td = el('td', 'nothing to show', 'muted'); td.colSpan = columns.length; tr.append(td); body.append(tr);
  }
}

function cellBar(frac, text, lvl) {
  const wrap = el('span', null, 'cellbar');
  const track = el('span', null, 'track');
  const fill = el('span', null, `fill ${lvl || ''}`);
  fill.style.display = 'block';
  fill.style.width = `${Math.max(0, Math.min(frac * 100, 100))}%`;
  track.append(fill);
  wrap.append(track, span(text));
  return wrap;
}

// Build a panel body once (it holds canvases that must survive refreshes), then update its parts.
const mounted = {};
function mount(id, build) {
  if (!mounted[id]) { const body = $(id); body.replaceChildren(); mounted[id] = build(body); }
  return mounted[id];
}

const TEXT_COLUMNS = new Set(['name', 'project', 'state', 'user']);
const nextSort = (current, key) => ({ key, dir: current.key === key ? -current.dir : TEXT_COLUMNS.has(key) ? 1 : -1 });

function sortRows(list, sort, getters) {
  const get = getters[sort.key];
  return [...list].sort((a, b) => {
    const x = get(a), y = get(b);
    return (typeof x === 'string' || typeof y === 'string' ? String(x).localeCompare(String(y)) : (x ?? -1) - (y ?? -1)) * sort.dir;
  });
}

// ---- what needs attention -------------------------------------------------------------------------------------

function coolingFault(temp, fans) {
  if (temp === null || temp === undefined || temp < COOLING_CHECK_C || !fans || !fans.length) return null;
  const top = Math.max(...fans);
  return top < FAN_STALLED_RPM ? `The fan reads ${top} rpm while the CPU is at ${temp.toFixed(0)}°C.` : null;
}

function notices(s) {
  const out = [];
  const add = (lvl, text, hint) => out.push({ lvl, text, hint });
  const h = s.host, d = s.debug && !s.debug.error ? s.debug : {}, y = sys(s), l = live(s);
  if (s.stale) add('bad', 'Status is out of date: the collector has stopped.', 'systemctl status macserver-status.timer');
  for (const c of s.containers) {
    if (c.state !== 'running') add('bad', `${shortName(c)} is ${c.state}`, `sudo macserver logs ${shortName(c)}`);
    else if ((c.status || '').includes('unhealthy')) add('warn', `${shortName(c)} is unhealthy`, `sudo macserver logs ${shortName(c)}`);
  }
  if (s.tailscale.state !== 'Running') add('bad', `Tailscale is ${s.tailscale.state}`, 'sudo tailscale up');
  if (s.public_domain && s.public_ok === false) add('bad', `The public API (${s.public_domain}) is not answering`, 'sudo macserver public');
  if (s.clock_synced === false) add('warn', 'The clock is not synchronised', 'timedatectl');
  const failed = ((d.system || {}).failed_units || []).filter((u) => !(u === 'macserver-claude.service' && s.claude && ['login', 'consent'].includes(s.claude.problem)));
  for (const u of failed) add('warn', `Service ${u} failed`, `systemctl status ${u}`);
  if (h.updates_pending) add('warn', `${h.updates_pending} Debian update(s) waiting`, 'sudo macserver update');
  if (h.reboot_required) add('warn', 'A restart is needed to finish updates', 'sudo reboot');
  const mu = s.macserver_update;
  if (mu && ['skipped', 'refused', 'rolled-back', 'error'].includes(mu.state)) add('warn', `MacServer update: ${mu.message}`, 'sudo macserver upgrade');
  const disk = share(h.disk.used, h.disk.total);
  if (disk >= 90) add(disk >= 97 ? 'bad' : 'warn', `The disk is ${disk.toFixed(0)}% full`, 'sudo docker system df');
  const mem = y.memory || h.memory;
  if (mem && mem.total && mem.available !== undefined && mem.available / mem.total < 0.08) add(mem.available / mem.total < 0.04 ? 'bad' : 'warn', `Only ${bytes(mem.available)} of memory is available`, 'sudo docker stats --no-stream');
  if (mem && mem.swap_total && mem.swap_used / mem.swap_total >= 0.85) add('warn', `Swap is ${pct(share(mem.swap_used, mem.swap_total))} used`, null);
  const db = y.db;
  if (db && db.connections !== undefined && db.max_connections && db.connections / db.max_connections >= 0.9) add('warn', `The database has ${db.connections} of ${db.max_connections} connections open`, null);
  const bat = y.power;
  if (bat && bat.ac_online === false && bat.percent !== null && bat.percent < 20) add('warn', `Running on battery: ${bat.percent}% left`, null);
  const backups = d.backups;
  if (backups && backups.count === 0) add('warn', 'There is no database backup yet', 'sudo macserver backup');
  else if (backups && backups.newest_age_s > 7 * 86400) add('warn', `The newest database backup is ${duration(backups.newest_age_s)} old`, 'sudo macserver backup');
  const fans = ((y.sensors || {}).fans || []).map((f) => f.rpm);
  const cool = coolingFault(h.sensors.cpu_temp_c, fans.length ? fans : h.sensors.fans_rpm);
  if (cool) add('bad', cool, 'sudo systemctl status macserver-fans');
  if (d.system && d.system.oom_kills) add('info', `${d.system.oom_kills} process(es) were killed for lack of memory since boot`, null);
  const exposed = (d.listeners || []).filter((p) => ['all', 'public', 'lan'].includes(p.scope));
  if (exposed.length) add('info', `${exposed.length} port(s) answer on the local network: ${exposed.map((p) => p.port).join(', ')}`, 'sudo ss -tlnp');
  return out;
}

function renderAttention(s) {
  const list = notices(s);
  const real = list.filter((n) => n.lvl !== 'info');
  $('attention-meta').textContent = real.length ? `${real.length} to look at` : 'all clear';
  const ul = el('ul', null, 'list');
  if (!list.length) ul.append(el('li', 'Nothing needs attention.', 'ok'));
  for (const n of list) {
    const li = el('li', null, n.lvl === 'info' ? '' : n.lvl);
    const box = el('div', n.text);
    if (n.hint) box.append(el('small', n.hint, 'mono'));
    li.append(box);
    ul.append(li);
  }
  put($('attention'), ul);
  const bad = list.some((n) => n.lvl === 'bad'), warn = list.some((n) => n.lvl === 'warn');
  const chip = $('summary');
  chip.textContent = bad ? `${list.filter((n) => n.lvl === 'bad')[0].text}` : warn ? `${real.length} thing(s) to look at` : 'Everything is running';
  chip.className = `chip ${bad ? 'bad' : warn ? 'warn' : 'good'}`;
}

// ---- the key numbers ----------------------------------------------------------------------------------------

const KPIS = ['cpu', 'mem', 'net', 'disk', 'temp', 'svc', 'power', 'load'];

function renderKpis(s) {
  const strip = $('kpis');
  if (!strip.children.length) {
    for (const id of KPIS) {
      const tile = el('div', null, 'kpi');
      tile.id = `kpi-${id}`;
      tile.append(el('div', null, 'label'), el('div', null, 'value'), el('div', null, 'viz'), el('div', null, 'foot'));
      strip.append(tile);
    }
  }
  const h = s.host, l = live(s), y = sys(s);
  const psi = (((y.cpu || {}).pressure || {}).cpu || {}).some;
  const mem = l ? l.mem : { total: h.memory.total, used: h.memory.used };
  const swap = l ? l.swap : { used: 0 };
  const power = y.power || (h.battery ? { percent: h.battery.percent, status: h.battery.status } : null);
  const fans = ((y.sensors || {}).fans || []).map((f) => f.rpm);
  const fan = (fans.length ? fans : h.sensors.fans_rpm)[0];
  const containers = s.containers, up = containers.filter((c) => c.state === 'running').length, db = y.db;
  const threads = l ? l.cpu.cores.length : h.cpus;
  const freq = l ? l.cpu.freq_mhz.filter(Boolean) : [];
  const diskFrac = share(h.disk.used, h.disk.total) / 100;
  const tiles = {
    cpu: { label: 'CPU', value: l ? pct(l.cpu.total) : '?', series: [hist.cpu, '--s1', 100],
           foot: `${threads} threads${freq.length ? ` · ${(freq.reduce((a, b) => a + b, 0) / freq.length / 1000).toFixed(1)} GHz` : ''}` },
    mem: { label: 'Memory', value: pct(share(mem.used, mem.total)), series: [hist.mem, '--s4', 100], foot: `${bytes(mem.used)} of ${bytes(mem.total)}${swap.used ? ` · swap ${bytes(swap.used)}` : ''}` },
    net: { label: 'Network', value: l ? rate(l.net.rx) : '?', unit: 'in', series: [hist.rx, '--s2', null, hist.tx, '--s3'], foot: l ? `${rate(l.net.tx)} out` : '' },
    disk: { label: 'Disk /', value: pct(diskFrac * 100), series: [hist.wr, '--s5', null], foot: `${bytes(h.disk.used)} of ${bytes(h.disk.total)}${l ? ` · writes ${rate(l.disk_io.write)}` : ''}` },
    temp: { label: 'Temperature', value: h.sensors.cpu_temp_c === null ? '?' : `${h.sensors.cpu_temp_c.toFixed(0)}°C`, series: [hist.temp, '--s5', 110],
            foot: fan ? `fan ${fan} rpm` : 'no fan reading' },
    svc: { label: 'Services', value: containers.length ? `${up}/${containers.length}` : '-', unit: 'running', series: [hist.svc, '--s3', null],
           foot: db && db.connections !== undefined ? `database ${db.connections}/${db.max_connections} · ${db.tps.toFixed(1)}/s` : `${containers.reduce((a, c) => a + (c.cpu || 0), 0).toFixed(0)}% cpu` },
    power: power && power.percent !== null && power.percent !== undefined ? { label: 'Power', value: `${power.percent}%`, bar: [power.percent / 100, fillLevel(1 - power.percent / 100, 0.8, 0.9)],
           foot: `${power.status || ''}${power.health_pct ? ` · health ${power.health_pct.toFixed(0)}%` : ''}` } : { label: 'Power', value: 'no battery', foot: '' },
    load: { label: 'Load', value: h.load[0].toFixed(2), series: [hist.load, '--s1', Math.max(h.cpus * 2, 4)], foot: `5 min ${h.load[1].toFixed(2)} · 15 min ${h.load[2].toFixed(2)}` },
  };
  for (const id of KPIS) {
    const tile = $(`kpi-${id}`), t = tiles[id], [labelEl, valueEl, viz, foot] = tile.children;
    labelEl.textContent = t.label;
    valueEl.replaceChildren(document.createTextNode(t.value));
    if (t.unit) valueEl.append(el('small', t.unit));
    foot.textContent = t.foot;
    if (t.bar) {
      if (!viz.querySelector('.track')) { viz.className = 'bar-space'; viz.replaceChildren(el('div', null, 'track')); viz.firstChild.style.width = '100%'; viz.firstChild.append(el('div', null, 'fill')); }
      const fill = viz.querySelector('.fill');
      fill.className = `fill ${t.bar[1]}`;
      fill.style.width = `${t.bar[0] * 100}%`;
    } else if (t.series) {
      if (!viz.querySelector('canvas')) { viz.className = ''; viz.replaceChildren(el('canvas')); }
      const lines = [{ values: t.series[0], color: t.series[1] }];
      if (t.series[3]) lines.push({ values: t.series[3], color: t.series[4] });
      chart(viz.firstChild, lines, { max: t.series[2], labels: false });
    }
  }
}

// ---- overview -----------------------------------------------------------------------------------------------

const TIME_KINDS = [['user', '--s1'], ['system', '--s3'], ['iowait', '--s2'], ['irq', '--s4'], ['steal', '--s5']];

function cpuParts(s, tall = false) {
  const l = live(s), y = sys(s), info = y.cpu || {};
  const cores = el('div', null, 'cols2');
  if (l) {
    l.cpu.cores.forEach((v, i) => {
      const mhz = l.cpu.freq_mhz[i];
      cores.append(meter(`Thread ${i}`, v / 100, `${v.toFixed(0)}%${mhz ? ` · ${(mhz / 1000).toFixed(1)} GHz` : ''}`, 's1'));
    });
  }
  const times = l && l.cpu.times && Object.keys(l.cpu.times).length ? l.cpu.times : null;
  const parts = [];
  if (times) {
    parts.push(group('Where the time goes', stack(TIME_KINDS.map(([k, c]) => [(times[k] || 0) / 100, c])),
      swatches(TIME_KINDS.map(([k, c]) => [`${k} ${pct(times[k] || 0)}`, c]))));
  }
  const rates = l && l.rates;
  const facts = [['Load average', `${s.host.load.map((v) => v.toFixed(2)).join('  ')}  (1, 5, 15 min)`],
    ['Tasks', l ? `${l.tasks.running} running / ${l.tasks.total} · ${l.tasks.threads} threads` : null],
    ['Switches and interrupts', rates ? `${count(rates.ctxt)}/s · ${count(rates.intr)}/s · forks ${count(rates.forks)}/s` : null],
    ['Governor', info.governor ? `${info.governor} (${info.driver || '?'}) · ${info.min_mhz}-${info.max_mhz} MHz${info.turbo === null || info.turbo === undefined ? '' : info.turbo ? ' · turbo on' : ' · turbo off'}` : null]];
  const press = info.pressure;
  const stall = press ? (() => {
    const rows = [];
    for (const [name, warn] of [['cpu', 60], ['memory', 5], ['io', 10]]) {
      const some = (press[name] || {}).some;
      if (some) rows.push(meter(`${name} stall`, some.avg10 / 100, `${some.avg10.toFixed(1)}% · ${some.avg60.toFixed(1)}% · ${some.avg300.toFixed(1)}%`, some.avg10 >= warn ? 'warn' : 's1', '90px'));
    }
    return rows.length ? group('Waiting for the resource (10 s, 60 s, 5 min)', ...rows) : null;
  })() : null;
  const thr = info.throttle;
  const note = thr && (thr.core || thr.package) ? el('p', `Heat slow-downs since boot: core ${count(thr.core)}, package ${count(thr.package)}. Normal for this Mac.`, 'sub') : null;
  return { cores: group('Threads', cores), parts, facts: kv(facts), stall, note };
}

function renderCpu(s) {
  const info = sys(s).cpu || {};
  $('cpu-meta').textContent = info.model ? `${info.model} · ${info.cores} cores / ${info.threads} threads` : '';
  const m = mount('cpu-body', (body) => {
    const canvas = el('canvas', null, 'chart');
    const rest = el('div');
    body.append(canvas, rest);
    return { canvas, rest };
  });
  chart(m.canvas, [{ values: hist.cpu, color: '--s1' }], { max: 100, fmt: (v) => `${v}%` });
  const p = cpuParts(s);
  put(m.rest, p.cores, ...p.parts, p.stall, group(null, p.facts), p.note);
}

function renderMem(s) {
  const y = sys(s), l = live(s);
  const m = y.memory || { ...(l ? l.mem : s.host.memory), available: (l ? l.mem.total - l.mem.used : s.host.memory.total - s.host.memory.used), swap_total: l ? l.swap.total : 0, swap_used: l ? l.swap.used : 0 };
  $('mem-meta').textContent = `${bytes(m.total)} total`;
  const ui = mount('mem-body', (body) => {
    const canvas = el('canvas', null, 'chart');
    const rest = el('div');
    body.append(canvas, rest);
    return { canvas, rest };
  });
  chart(ui.canvas, [{ values: hist.mem, color: '--s4' }], { max: 100, fmt: (v) => `${v}%` });
  put(ui.rest, memoryRows(m, y, l));
}

function memoryRows(m, y, l) {
  const rows = el('div');
  const used = m.used ?? m.total - m.available;
  const items = [['Used', used, fillLevel(1 - m.available / m.total, 0.9, 0.95)], ['Available', m.available, 's1'], ['Cached', (m.cached || 0) + (m.buffers || 0), 's1'],
    ['Kernel slab', m.slab, 's2'], ['Shared', m.shmem, 's2'], ['Free', m.free, '']];
  for (const [label, v, cls] of items) if (v) rows.append(meter(label, v / m.total, `${bytes(v)} · ${pct(share(v, m.total))}`, cls));
  if (m.swap_total) rows.append(meter('Swap', m.swap_used / m.swap_total, `${bytes(m.swap_used)} of ${bytes(m.swap_total)}`, fillLevel(m.swap_used / m.swap_total, 0.6, 0.85)));
  const notes = [];
  if (y.memory) {
    notes.push(`Dirty ${bytes(m.dirty)} · writeback ${bytes(m.writeback)} · page tables ${bytes(m.page_tables)}`);
    if (m.commit_limit) notes.push(`Committed ${bytes(m.committed)} of a ${bytes(m.commit_limit)} limit (${pct(share(m.committed, m.commit_limit))}): promised, not used.`);
  }
  if (l && l.rates) notes.push(`Swap in ${rate(l.rates.swap_in)} · out ${rate(l.rates.swap_out)} · hard page faults ${l.rates.major_faults}/s`);
  for (const n of notes) rows.append(el('p', n, 'sub'));
  return rows;
}

function netParts(s) {
  const y = sys(s), l = live(s), net = y.network || {};
  const out = [];
  const wifi = net.wifi;
  const reach = (s.debug && !s.debug.error ? s.debug.net : null) || null;
  const pairs = [];
  if (wifi) pairs.push(['Wi-Fi', `${wifi.ssid} · ${wifi.band || ''} channel ${wifi.channel} · ${wifi.rate_mbps} Mbit/s · ${wifi.security || 'open'}${wifi.signal_dbm !== undefined ? ` · ${wifi.signal_dbm} dBm (${wifi.signal_pct}%)` : ''}`]);
  if (reach) pairs.push(['Reach', `router ${reach.gateway || '-'} ${ms(reach.gateway_ms)} · internet ${ms(reach.internet_ms)} · DNS ${ms(reach.dns_ms)}`, reach.internet_ms === null ? 'bad' : '']);
  const tcp = net.tcp, ct = (y.kernel || {}).conntrack;
  if (tcp) pairs.push(['TCP', `${tcp.established} open · ${tcp.time_wait} closing · ${count(tcp.retransmitted || 0)} segments resent · ${count(tcp.failed_attempts || 0)} failed attempts${ct && ct.max ? ` · tracked ${ct.count}/${count(ct.max)}` : ''}`]);
  out.push(kv(pairs));
  return out;
}

function renderNet(s) {
  const l = live(s);
  $('net-meta').textContent = l ? `${Object.keys(l.net.ifaces).find((n) => /^(wl|en|eth)/.test(n)) || ''}` : '';
  const m = mount('net-body', (body) => {
    const canvas = el('canvas', null, 'chart');
    const lg = el('div');
    const rest = el('div', null, 'sub');
    body.append(canvas, lg, rest);
    return { canvas, lg, rest };
  });
  chart(m.canvas, [{ values: hist.rx, color: '--s2' }, { values: hist.tx, color: '--s3' }], { fmt: rate });
  put(m.lg, legend([['Download', l ? rate(l.net.rx) : '?', '--s2'], ['Upload', l ? rate(l.net.tx) : '?', '--s3']]));
  put(m.rest, ...netParts(s));
}

function storageRows(s, detailed) {
  const y = sys(s), l = live(s), store = y.storage || {};
  const out = [];
  const fs = store.filesystems && store.filesystems.length ? store.filesystems : [{ mount: '/', used: s.host.disk.used, total: s.host.disk.total, avail: s.host.disk.total - s.host.disk.used, fstype: '' }];
  const g = el('div');
  for (const f of fs) {
    const frac = f.total ? f.used / f.total : 0;
    const row = meter(f.mount, frac, `${bytes(f.used)} / ${bytes(f.total)} · ${pct(frac * 100)}`, fillLevel(frac), '104px');
    g.append(row);
    if (detailed) g.append(el('p', `${f.device || ''} · ${f.fstype}${f.inodes ? ` · inodes ${pct(share(f.inodes_used, f.inodes))}` : ''} · ${bytes(f.avail)} free${f.readonly ? ' · READ-ONLY' : ''}`, 'sub'));
  }
  out.push(group('Filesystems', g));
  for (const d of store.disks || []) {
    const io = l && l.disks ? l.disks[d.name] : null;
    const temp = ((y.sensors || {}).temps || []).find((t) => t.chip === 'nvme');
    out.push(group(`${d.name}`, kv([['Drive', `${d.model || ''} · ${bytes(d.size)} · ${d.ssd ? 'SSD' : 'disk'}${d.state ? ` · ${d.state}` : ''}${d.firmware ? ` · firmware ${d.firmware}` : ''}${temp ? ` · ${temp.temp_c.toFixed(0)}°C` : ''}`],
      io ? ['Activity', `read ${rate(io.read)} · write ${rate(io.write)} · ${io.riops} + ${io.wiops} operations/s · busy ${pct(io.busy)}`] : null].filter(Boolean))));
  }
  const dk = store.docker;
  if (dk) {
    const rows = [];
    for (const [key, label] of [['images', 'Images'], ['containers', 'Containers'], ['volumes', 'Volumes'], ['build_cache', 'Build cache']]) {
      const v = dk[key];
      if (v) rows.push([label, `${bytes(v.size)}${v.reclaimable ? ` · ${bytes(v.reclaimable)} reclaimable` : ''} · ${v.count}`]);
    }
    out.push(group('Docker', kv(rows)));
  }
  return out;
}

function renderStorage(s) {
  const y = sys(s);
  $('storage-meta').textContent = ((y.storage || {}).filesystems || []).length ? `${y.storage.filesystems.length} filesystems` : '';
  put($('storage-body'), ...storageRows(s, false));
}

const SERVICE_SORT = {
  name: (c) => shortName(c), project: (c) => c.project || '', state: (c) => c.state, cpu: (c) => c.cpu, mem: (c) => c.mem, up: (c) => -(c.started || 0),
  restarts: (c) => c.restarts, rx: (c) => c.net_rx, tx: (c) => c.net_tx, pids: (c) => c.pids, write: (c) => c.blk_write,
};

function serviceDot(c) {
  const running = c.state === 'running';
  const lvl = !running ? 'bad' : (c.health === 'unhealthy' || (c.status || '').includes('unhealthy')) ? 'warn' : 'ok';
  return [stateCell(c.state, lvl), lvl];
}

function renderServicesTable(table, s, detailed) {
  const sort = sorts.svc;
  const ordered = sortRows(s.containers, sort, SERVICE_SORT);
  const onSort = (key) => { sorts.svc = nextSort(sorts.svc, key); if (latest) render(latest); };
  const cols = detailed
    ? [{ label: 'Service', key: 'name' }, { label: 'Project', key: 'project' }, { label: 'State', key: 'state' }, { label: 'Health' }, { label: 'CPU', key: 'cpu' }, { label: 'Memory', key: 'mem', align: 'r' },
       { label: 'In', key: 'rx', align: 'r' }, { label: 'Out', key: 'tx', align: 'r' }, { label: 'Writes', key: 'write', align: 'r' }, { label: 'Processes', key: 'pids', align: 'r' },
       { label: 'Up', key: 'up', align: 'r' }, { label: 'Restarts', key: 'restarts', align: 'r' }, { label: 'Image' }]
    : [{ label: 'Service', key: 'name' }, { label: 'State', key: 'state' }, { label: 'CPU', key: 'cpu' }, { label: 'Memory', key: 'mem', align: 'r' }, { label: 'Up', key: 'up', align: 'r' }];
  const rows = [];
  let lastProject = null;
  for (const c of ordered) {
    if (!detailed && sort.key === 'mem' && c.project !== lastProject) {
      const p = (sys(s).docker || {}).projects ? sys(s).docker.projects.find((x) => x.name === c.project) : null;
      rows.push({ group: [`${c.project || '-'}`, p ? span(`  ${p.running}/${p.total} running · ${p.cpu.toFixed(1)}% cpu · ${bytes(p.mem)}`) : null] });
      lastProject = c.project;
    }
    const [dot] = serviceDot(c);
    const cpu = c.cpu === null || c.cpu === undefined ? '' : cellBar(Math.min(c.cpu / 100, 1), `${c.cpu.toFixed(1)}%`, 's1');
    const detail = openService === c.name ? `${c.name} (${c.project})\n${c.status}${c.logs && c.logs.length ? `\n\n${c.logs.join('\n')}` : ''}` : null;
    const base = [span(shortName(c), 'mono'), ...(detailed ? [c.project || '-'] : []), dot];
    const cells = detailed
      ? [...base, c.health || '-', cpu, c.mem ? bytes(c.mem) : '', c.state === 'running' ? rate(c.net_rx || 0) : '', c.state === 'running' ? rate(c.net_tx || 0) : '',
         c.state === 'running' ? rate(c.blk_write || 0) : '', c.pids ?? '', c.state === 'running' && c.started ? since(c.started) : '', c.restarts ?? '', span(c.image || '', 'mono muted')]
      : [...base, cpu, c.mem ? bytes(c.mem) : '', c.state === 'running' && c.started ? since(c.started) : ''];
    rows.push({ cells, detail, onClick: () => { openService = openService === c.name ? null : c.name; if (latest) render(latest); } });
  }
  renderTable(table, cols, rows, { sort, onSort });
}

function renderOverviewServices(s) {
  const up = s.containers.filter((c) => c.state === 'running').length;
  $('services-meta').textContent = `${up} of ${s.containers.length} running`;
  renderServicesTable($('overview-services'), s, false);
}

function statusRows(s) {
  const rows = [];
  const row = (lvl, label, value) => rows.push({ lvl, label, value });
  const l = live(s), y = sys(s), wifi = (y.network || {}).wifi;
  const iface = l ? Object.keys(l.net.ifaces).find((n) => /^(wl|en|eth)/.test(n)) : null;
  row(iface ? 'ok' : 'bad', 'Internet', iface ? `${wifi ? `Wi-Fi ${wifi.ssid}` : iface}${wifi && wifi.signal_dbm !== undefined ? ` · ${wifi.signal_dbm} dBm` : ''}` : 'not connected');
  row(s.tailscale.state === 'Running' ? 'ok' : 'bad', 'Tailscale', `${s.tailscale.state}${s.tailscale.peers_online !== undefined ? ` · ${s.tailscale.peers_online} device(s) online` : ''}`);
  row(!s.public_domain ? '' : s.public_ok ? 'ok' : 'bad', 'Public API', s.public_domain ? `${s.public_domain} · ${s.public_ok ? 'answering' : 'not answering'}` : 'off, tailnet only');
  row(s.clock_synced ? 'ok' : 'warn', 'Clock', s.clock_synced ? 'synchronised' : 'not synchronised');
  row(s.host.updates_pending ? 'warn' : 'ok', 'Updates', s.host.updates_pending ? `${s.host.updates_pending} Debian update(s) waiting` : 'up to date');
  if (s.macserver_update) row(['skipped', 'refused', 'rolled-back', 'error'].includes(s.macserver_update.state) ? 'warn' : 'ok', 'MacServer', `${s.macserver_update.message}${s.macserver_update.auto === 'off' ? ' (auto-update off)' : ''}`);
  const idle = s.idle || {};
  row('', 'Idle mode', idle.mode === 'off' ? 'off' : idle.idle ? `low-power since ${new Date(idle.since * 1000).toLocaleString()}` : `active (low-power after ${Math.max(Math.round((idle.after_s || 900) / 60), 1)} min quiet)`);
  const dd = s.doomsday;
  if (dd && dd.state && dd.state !== 'normal') row('bad', 'Lockdown', `${dd.state}${dd.trigger ? ` · ${dd.trigger}` : ''}`);
  return rows;
}

function renderStatus(s) {
  const ul = el('ul', null, 'list');
  for (const r of statusRows(s)) {
    const li = el('li', null, r.lvl);
    const box = el('div', r.value);
    box.prepend(el('small', r.label));
    li.append(box);
    ul.append(li);
  }
  put($('status-rows'), ul);
}

function renderConnect(s) {
  const keys = s.public_keys || {};
  const pairs = [['API URL', s.api_url || 'unknown', '', s.api_url], ['Public', s.public_domain ? `https://${s.public_domain}` : 'off (tailnet only)']];
  if (keys.SUPABASE_PUBLISHABLE_KEY) pairs.push(['Publishable key', `${keys.SUPABASE_PUBLISHABLE_KEY.slice(0, 16)}…`, 'mono', keys.SUPABASE_PUBLISHABLE_KEY]);
  if (keys.ANON_KEY) pairs.push(['Anon key (legacy)', `${keys.ANON_KEY.slice(0, 16)}…`, 'mono', keys.ANON_KEY]);
  pairs.push(['Secret keys', 'sudo macserver keys', 'mono']);
  put($('connect'), ...kv(pairs).childNodes);
}

// ---- hardware -----------------------------------------------------------------------------------------------

function renderHardware(s) {
  const y = sys(s), l = live(s), info = y.cpu || {};
  $('hw-cpu-meta').textContent = info.model ? `${info.model} · ${info.cores} cores / ${info.threads} threads` : '';
  const c = mount('hw-cpu', (body) => { const canvas = el('canvas', null, 'chart tall'); const rest = el('div'); body.append(canvas, rest); return { canvas, rest }; });
  chart(c.canvas, [{ values: hist.cpu, color: '--s1' }], { max: 100, fmt: (v) => `${v}%` });
  const p = cpuParts(s);
  put(c.rest, p.cores, ...p.parts, p.stall, group(null, p.facts), p.note);

  // temperatures and the fan: readings, never alarms
  const temps = (y.sensors || {}).temps || [];
  $('hw-thermal-meta').textContent = s.host.sensors.cpu_temp_c === null ? '' : `CPU ${s.host.sensors.cpu_temp_c.toFixed(0)}°C`;
  const th = el('div');
  for (const t of [...temps].sort((a, b) => b.temp_c - a.temp_c)) {
    const limit = t.crit_c || t.max_c || 100;
    th.append(meter(`${t.chip} ${t.label === 'temp' ? '' : t.label}`.trim(), t.temp_c / limit, `${t.temp_c.toFixed(0)}°C of ${limit.toFixed(0)}°C`, 's5', '150px'));
  }
  for (const f of (y.sensors || {}).fans || (s.host.sensors.fans_rpm || []).map((rpm) => ({ rpm }))) {
    th.append(meter('Fan', f.rpm / (f.max || 8000), `${f.rpm} rpm${f.max ? ` of ${f.max}` : ''}`, 's3', '150px'));
    if (f.min) th.append(el('p', `Fan floor ${f.min} rpm${f.target ? ` · asked for ${f.target} rpm` : ''}`, 'sub'));
  }
  const thr = info.throttle;
  th.append(el('p', `100°C is the chip's own limit. Running there under load is normal for this Mac.${thr ? ` Heat slow-downs since boot: core ${count(thr.core)}, package ${count(thr.package)}.` : ''}`, 'sub'));
  put($('hw-thermal'), th);

  // processes
  const procs = l ? l.procs : [];
  $('procs-meta').textContent = l ? `${l.tasks.total} tasks · ${l.tasks.threads} threads` : '';
  const sort = sorts.procs;
  const sorted = sortRows(procs, sort, { cpu: (p) => p.cpu, mem: (p) => p.rss, pid: (p) => p.pid, name: (p) => p.name, user: (p) => p.user, threads: (p) => p.threads });
  renderTable($('proc-table'), [{ label: 'Program', key: 'name' }, { label: 'PID', key: 'pid', align: 'r' }, { label: 'User', key: 'user' }, { label: 'CPU', key: 'cpu', align: 'r' },
    { label: 'Memory', key: 'mem', align: 'r' }, { label: 'Threads', key: 'threads', align: 'r' }],
    sorted.map((p) => ({ cells: [span(p.name, 'mono'), p.pid, p.user, `${p.cpu.toFixed(1)}%`, bytes(p.rss), p.threads] })), { sort, onSort: (key) => { sorts.procs = nextSort(sorts.procs, key); if (latest) render(latest); } });

  // power
  const b = y.power;
  $('hw-power-meta').textContent = b ? (b.ac_online ? 'on mains power' : 'on battery') : '';
  put($('hw-power'), b ? el('div') : empty('No battery in this machine.'));
  if (b) {
    const box = el('div');
    box.append(meter('Charge', (b.percent || 0) / 100, `${b.percent}% · ${b.status}`, fillLevel(1 - (b.percent || 0) / 100, 0.8, 0.9), '70px'));
    box.append(kv([['Health', b.health_pct ? `${b.health_pct.toFixed(1)}% of new (${b.capacity_wh} of ${b.design_wh} Wh)` : null], ['Charge cycles', b.cycles !== null && b.cycles !== undefined ? `${b.cycles}${b.technology ? ` · ${b.technology}` : ''}` : null],
      ['Power flow', b.power_w !== null && b.power_w !== undefined ? `${b.power_w.toFixed(1)} W · ${(b.voltage_v || 0).toFixed(2)} V${b.current_a ? ` · ${b.current_a.toFixed(2)} A` : ''}` : null],
      ['Cell temperature', b.temp_c !== null && b.temp_c !== undefined ? `${b.temp_c.toFixed(1)}°C` : null], [b.status === 'Charging' ? 'Time to full' : 'Time left', b.seconds_left ? duration(b.seconds_left) : null],
      ['Charge limit', b.limit ? `${b.limit}%` : null]]));
    put($('hw-power'), box);
  }

  // memory and storage in full
  const mem = y.memory;
  $('hw-mem-meta').textContent = mem ? `${bytes(mem.total)} total` : '';
  put($('hw-mem'), mem ? memoryRows(mem, y, l) : empty('Waiting for the collector.'));
  $('hw-storage-meta').textContent = '';
  put($('hw-storage'), ...storageRows(s, true));

  // the machine's facts
  const host = y.host || {}, hs = s.host;
  const t2 = hs.t2_modules || {};
  const on = (v) => (v ? 'on' : 'off');
  const left = [['Model', hs.model], ['Firmware', host.firmware], ['Operating system', host.os], ['Kernel', `${hs.kernel}${hs.t2_kernel ? '' : ' (not the T2 kernel)'}`, hs.t2_kernel ? '' : 'warn'], ['Architecture', host.arch],
    ['T2 drivers', `keyboard ${on(t2.keyboard)} · Wi-Fi ${on(t2.brcmfmac)} · SMC ${on(t2.applesmc)}`]];
  const right = [['Running since', host.boot_time ? `${new Date(host.boot_time * 1000).toLocaleString()} (${duration(hs.uptime_s)})` : duration(hs.uptime_s)], ['Time zone', host.timezone],
    ['Docker', host.docker], ['Tailscale', (y.tailscale || {}).version], ['Logins', (host.sessions || []).map((x) => `${x.user} on ${x.tty}`).join(', ') || 'none'],
    ['Restart needed', hs.reboot_required ? 'yes, to finish updates' : 'no', hs.reboot_required ? 'warn' : '']];
  $('hw-machine-meta').textContent = hs.model || '';
  put($('hw-machine'), kv(left.filter((p) => p[1])), kv(right.filter((p) => p[1])));
}

// ---- network ------------------------------------------------------------------------------------------------

function renderNetworkPage(s) {
  const y = sys(s), l = live(s), net = y.network || {}, d = s.debug && !s.debug.error ? s.debug : null;
  const rates = l ? l.net.ifaces : {};
  const ifaces = (net.interfaces || []).filter((i) => i.kind !== 'bridge' || i.state === 'up');
  $('ifaces-meta').textContent = ifaces.length ? `${ifaces.length} interfaces` : '';
  renderTable($('iface-table'), [{ label: 'Interface' }, { label: 'Kind' }, { label: 'State' }, { label: 'Address' }, { label: 'In now', align: 'r' }, { label: 'Out now', align: 'r' },
    { label: 'In total', align: 'r' }, { label: 'Out total', align: 'r' }, { label: 'Errors', align: 'r' }, { label: 'Drops', align: 'r' }, { label: 'Speed', align: 'r' }],
    ifaces.map((i) => {
      const r = rates[i.name];
      return { cells: [span(i.kind === 'container' ? `containers ×${i.count}` : i.name, 'mono'), i.kind, i.kind === 'container' ? '' : stateCell(i.state, i.state === 'up' ? 'ok' : i.state === 'down' ? '' : ''), (i.addresses || []).join(', '),
        r ? rate(r.rx) : '', r ? rate(r.tx) : '', bytes(i.rx_bytes), bytes(i.tx_bytes), i.rx_errors + i.tx_errors, i.rx_drops + i.tx_drops, i.speed_mbps ? `${i.speed_mbps} Mbit/s` : ''] };
    }), {});

  const reach = [];
  if (d && d.net) {
    const n = d.net;
    for (const [name, v] of [['Router', n.gateway_ms], ['Internet', n.internet_ms], ['DNS', n.dns_ms]]) reach.push(meter(name, v ? Math.min(v / 200, 1) : 0, ms(v), v === null || v === undefined ? 'bad' : v > 150 ? 'warn' : 'ok', '70px'));
    reach.push(el('p', `Router ${n.gateway || '?'} · failed checks: DNS ${n.dns_fails || 0}, internet ${n.internet_fails || 0}`, 'sub'));
  }
  const wifi = net.wifi;
  put($('net-reach'), group('Latency', ...reach), wifi ? group('Wi-Fi', kv([['Network', wifi.ssid], ['Band', `${wifi.band || ''} · channel ${wifi.channel}`], ['Link rate', `${wifi.rate_mbps} Mbit/s`], ['Signal', wifi.signal_dbm !== undefined ? `${wifi.signal_dbm} dBm (${wifi.signal_pct}%)` : `${wifi.signal_pct}%`], ['Security', wifi.security || 'open']])) : null,
    net.tcp ? group('TCP', kv([['Open connections', net.tcp.established], ['Closing', net.tcp.time_wait], ['Segments resent', count(net.tcp.retransmitted || 0)], ['Failed attempts', count(net.tcp.failed_attempts || 0)], ['Resets', count(net.tcp.resets || 0)],
      ['Tracked flows', (y.kernel || {}).conntrack && y.kernel.conntrack.max ? `${y.kernel.conntrack.count} of ${count(y.kernel.conntrack.max)}` : null]])) : null);

  const ts = y.tailscale || {};
  const peers = ts.peers || [];
  $('tailnet-meta').textContent = peers.length ? `${peers.filter((p) => p.online).length} of ${peers.length} online${ts.version ? ` · Tailscale ${ts.version}` : ''}` : '';
  renderTable($('tailnet-table'), [{ label: 'Device' }, { label: 'System' }, { label: 'Address' }, { label: 'Route' }, { label: 'Received', align: 'r' }, { label: 'Sent', align: 'r' }, { label: 'Last seen', align: 'r' }],
    peers.map((p) => ({ cells: [stateCell(p.name, p.online ? 'ok' : ''), p.os, span(p.ip, 'mono'), p.route, bytes(p.rx_bytes), bytes(p.tx_bytes), p.online ? 'now' : `${since(p.last_seen)} ago`] })), {});
  const notes = el('ul', null, 'list plain');
  for (const hn of ts.health || []) notes.append(el('li', hn, 'warn'));
  if (ts.key_expiry) notes.append(el('li', `This Mac's Tailscale key expires in ${duration(ts.key_expiry - Date.now() / 1000)}.`));
  if (ts.funnel_relays) notes.append(el('li', `${ts.funnel_relays} Tailscale Funnel relays are connected (not counted as devices).`));
  if (ts.update_available) notes.append(el('li', 'A newer Tailscale is available.', 'warn'));
  put($('tailnet-notes'), notes.children.length ? notes : null);

  const scopeLvl = { public: 'bad', all: 'bad', lan: 'warn', tailnet: 'ok', loopback: '', open: '', blocked: '' };
  const listeners = d ? d.listeners || [] : [];
  const exposedPorts = listeners.filter((p) => ['all', 'public', 'lan'].includes(p.scope)).length;
  const openPorts = listeners.filter((p) => p.scope === 'open').length;
  $('listen-meta').textContent = listeners.length ? `${exposedPorts} reach the local network${openPorts ? ` · ${openPorts} open by firewall rule` : ''}` : '';
  renderTable($('listen-table'), [{ label: 'Port' }, { label: 'Reaches' }, { label: 'Address' }, { label: 'Program' }],
    listeners.map((p) => ({ cells: [span(p.port, 'mono'), tag({ all: 'all networks', public: 'public', lan: 'local network', open: 'firewall lets it in', blocked: 'blocked by firewall', tailnet: 'tailnet', loopback: 'this Mac only' }[p.scope] || p.scope, scopeLvl[p.scope]), p.addr, `${p.proc || '?'}${p.svc ? ` (${p.svc})` : ''}`] })), {});

  const flows = d ? d.flows || {} : {};
  $('flows-meta').textContent = flows.tracked ? `${flows.tracked} tracked${flows.states ? ` · ${Object.entries(flows.states).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(' · ')}` : ''}` : '';
  renderTable($('flow-table'), [{ label: 'Direction' }, { label: 'From or to' }, { label: 'Service' }, { label: 'Count', align: 'r' }],
    [...(flows.in || []).map((f) => ({ cells: ['in', `${f.src} → :${f.port}`, f.svc || '', f.n] })), ...(flows.out || []).map((f) => ({ cells: ['out', `${f.who} → ${f.dst}:${f.port}`, f.svc || '', f.n] }))], {});

  const r = d ? d.requests : null;
  if (r) hist.req = (r.per_2s || []).slice(-HISTORY);
  $('req-meta').textContent = r ? `${r.total} in the last ${r.window_s} s` : '';
  const q = mount('req-body', (body) => { const canvas = el('canvas', null, 'chart'); const rest = el('div'); body.append(canvas, rest); return { canvas, rest }; });
  chart(q.canvas, [{ values: hist.req, color: '--s3' }], { bars: true, fmt: (v) => `${v} / 2 s` });
  if (r) {
    const c = r.classes || {};
    put(q.rest, legend([['2xx', String(c['2xx'] || 0), '--ok'], ['3xx', String(c['3xx'] || 0), '--s1'], ['4xx', String(c['4xx'] || 0), '--warn'], ['5xx', String(c['5xx'] || 0), '--bad']]));
  } else put(q.rest, empty('No API gateway container found.'));
  const top = (rows, none) => { const ul = el('ul', null, 'list plain'); if (!rows || !rows.length) ul.append(el('li', none, 'muted')); for (const [name, n] of rows || []) ul.append(el('li', `${n}×  ${name}`)); return ul; };
  put($('req-tops'), group('Requests', top(r && r.top_paths, 'no requests yet')), group('Callers', top(r && r.top_clients, 'no callers yet')));

  const sysd = d ? d.system || {} : {};
  const problems = [
    ...(sysd.failed_units || []).map((u) => ['warn', `failed unit: ${u}`]),
    ...(sysd.oom_kills ? [['', `${sysd.oom_kills} process(es) killed for lack of memory since boot`]] : []),
    ...Object.entries(d ? d.service_errors || {} : {}).map(([name, lines]) => ['warn', `${name}: ${lines[lines.length - 1]}`]),
    ...((r && r.errors) || []).map((e) => ['warn', `request error: ${e.method} ${e.path} → ${e.status} for ${e.client}`]),
    ...(sysd.journal || []).map((j) => ['', `journal: ${j}`]),
    ...(sysd.kernel || []).map((k) => ['', `kernel: ${k}`]),
  ];
  $('problems-meta').textContent = problems.length ? `${problems.length} lines` : '';
  const ul = el('ul', null, 'list');
  if (!problems.length) ul.append(el('li', 'Nothing wrong.', 'ok'));
  for (const [lvl, text] of problems.slice(0, 40)) ul.append(el('li', text, lvl));
  put($('problems'), ul);
}

// ---- services page -------------------------------------------------------------------------------------------

function renderServicesPage(s) {
  const up = s.containers.filter((c) => c.state === 'running').length, y = sys(s);
  $('svc-meta').textContent = `${up} of ${s.containers.length} running · click a row for its status and last log lines`;
  renderServicesTable($('svc-table'), s, true);
  const projects = (y.docker || {}).projects || [];
  renderTable($('project-table'), [{ label: 'Project' }, { label: 'Running', align: 'r' }, { label: 'CPU', align: 'r' }, { label: 'Memory', align: 'r' }],
    projects.map((p) => ({ cells: [p.name, stateCell(`${p.running}/${p.total}`, p.running < p.total ? 'bad' : p.unhealthy ? 'warn' : 'ok'), `${p.cpu.toFixed(1)}%`, bytes(p.mem)] })), {});

  const db = y.db;
  $('db-meta').textContent = db && db.version ? `PostgreSQL ${db.version} · up ${duration(db.uptime_s)}` : '';
  if (!db) put($('db-body'), empty('No database container found.'));
  else if (db.error) put($('db-body'), empty(db.error));
  else {
    const hit = db.cache_hit_pct;
    const box = el('div');
    box.append(meter('Connections', db.connections / db.max_connections, `${db.connections} of ${db.max_connections}`, fillLevel(db.connections / db.max_connections, 0.7, 0.9), '100px'));
    box.append(kv([['Size', bytes(db.size)], ['Active now', `${db.active} (${db.idle_in_transaction} idle in transaction)`], ['Transactions', `${db.tps.toFixed(1)}/s`],
      ['Cache hit', hit === null ? '-' : pct(hit, 2), hit !== null && hit < 95 ? 'warn' : ''], ['Committed', count(db.commits)], ['Rolled back', count(db.rollbacks)], ['Deadlocks', db.deadlocks, db.deadlocks ? 'warn' : ''],
      ['Temp files', bytes(db.temp_bytes)], ['Rows read', count(db.rows_returned)], ['Rows written', count(db.rows_inserted + db.rows_updated)], ['Rows deleted', count(db.rows_deleted)],
      ['App users', db.users], ['Stored files', db.storage_objects]]));
    put($('db-body'), box);
  }
  const dk = (y.storage || {}).docker;
  put($('docker-body'), dk ? kv([...[['images', 'Images'], ['containers', 'Containers'], ['volumes', 'Volumes'], ['build_cache', 'Build cache']].filter(([k]) => dk[k]).map(([k, label]) => [label, `${bytes(dk[k].size)} · ${dk[k].count} (${dk[k].active} in use)${dk[k].reclaimable ? ` · ${bytes(dk[k].reclaimable)} reclaimable` : ''}`])]) : empty('Waiting for the collector.'));
}

// ---- header, claude, refresh ----------------------------------------------------------------------------------

function ingest(s) {
  const l = live(s), y = sys(s);
  if (l) {
    push('cpu', l.cpu.total);
    push('mem', share(l.mem.used, l.mem.total));
    push('rx', l.net.rx); push('tx', l.net.tx);
    push('rd', l.disk_io.read); push('wr', l.disk_io.write);
  }
  if (s.host.sensors.cpu_temp_c !== null) push('temp', s.host.sensors.cpu_temp_c);
  push('load', s.host.load[0]);
  push('svc', s.containers.reduce((a, c) => a + (c.cpu || 0), 0));
  if (y.db && y.db.tps !== undefined) push('tps', y.db.tps);
}

function render(s) {
  latest = s;
  $('viewer').textContent = `signed in through Tailscale as ${s.viewer}`;
  const name = (s.tailscale.name || 'macserver').split('.')[0];
  $('host-name').textContent = `${name} · up ${duration(s.host.uptime_s)}`;
  document.title = `${name} · MacServer`;
  const run = (fn) => { try { fn(s); } catch (e) { console.error(fn.name, e); } };   // one broken panel never blanks the page
  const page = currentTab;
  run(renderAttention); run(renderKpis);
  if (page === 'overview') { run(renderCpu); run(renderMem); run(renderNet); run(renderStorage); run(renderOverviewServices); run(renderStatus); run(renderConnect); }
  if (page === 'hardware') run(renderHardware);
  if (page === 'network') run(renderNetworkPage);
  if (page === 'services') run(renderServicesPage);
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
  state.className = 'empty';
  if (!c || !c.installed) {
    state.textContent = 'Claude Code is not installed. Over SSH run: sudo /opt/macserver-src/install.sh --redo claude';
  } else if (c.problem === 'login') {
    state.textContent = `Sign in once first: ${ssh}, run claude, and follow the login link. Then start the session here.`;
    state.className = 'empty warn';
  } else if (c.problem === 'consent') {
    state.textContent = `Allow Remote Control once: ${ssh}, run claude remote-control, answer y, then press Ctrl+C. Then start it here.`;
    state.className = 'empty warn';
  } else if (running && c.url && c.url.startsWith('https://claude.ai/code')) {
    state.textContent = 'A session is running on this Mac. Continue it in claude.ai/code or the Claude app.';
    state.className = 'empty ok';
    open.href = c.url;
    open.hidden = false;
  } else if (running) {
    state.textContent = 'Starting… (the link appears here in a few seconds; it is also listed at claude.ai/code)';
  } else if (c.state === 'failed') {
    state.textContent = 'The last session stopped with an error. Try again, or check: journalctl -u macserver-claude';
    state.className = 'empty bad';
  } else {
    state.textContent = 'No session running. Start one to get help connecting apps and backends.';
  }
}

async function claudeAction(action) {
  const buttons = [$('claude-start'), $('claude-stop')];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const res = await fetch('/api/claude', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) });
    if (!res.ok) throw new Error(String(res.status));
    $('claude-state').textContent = action === 'start' ? 'Starting…' : 'Stopping…';
    for (let i = 0; i < 12; i++) setTimeout(refresh, 3000 * (i + 1));
  } catch {
    $('claude-state').textContent = 'The server refused the request.';
    $('claude-state').className = 'empty bad';
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}
$('claude-start').addEventListener('click', () => claudeAction('start'));
$('claude-stop').addEventListener('click', () => claudeAction('stop'));

// ---- tools tab ----------------------------------------------------------------------------------------------

const DEV_COMMANDS = [
  { cmd: 'macserver status', desc: 'Health of the Mac, Tailscale and containers', root: true },
  { cmd: 'macserver dashboard', desc: 'Full-screen live dashboard (on the Mac screen)', root: false },
  { cmd: 'macserver keys', desc: 'API keys and Studio password (secret)', root: true },
  { cmd: 'macserver logs [service]', desc: 'Follow Supabase logs (auth, rest, db, storage)', root: true },
  { cmd: 'macserver restart', desc: 'Restart Supabase and the public route', root: true },
  { cmd: 'macserver update', desc: 'Back up the database, then update Debian, the T2 kernel and containers', root: true },
  { cmd: 'macserver upgrade', desc: 'Install the newest MacServer version now', root: true },
  { cmd: 'macserver autoupdate on|off|status', desc: 'Auto-update from GitHub (on by default)', root: true },
  { cmd: 'macserver backup', desc: 'Write a compressed database dump to /var/backups/macserver', root: true },
  { cmd: 'macserver public setup', desc: 'Turn on or change the public API route', root: true },
  { cmd: 'macserver public on|off', desc: 'Start or stop the public API route', root: true },
  { cmd: 'macserver idle [status|auto|on|off]', desc: 'Low-power mode when nobody is connected', root: true },
  { cmd: 'macserver wifi', desc: 'Set up or change the built-in Wi-Fi', root: true },
  { cmd: 'macserver doctor', desc: 'Check network, DNS and T2 drivers', root: false },
];

async function runDevCommand(cmd, needsRoot, outputId = 'dev-output') {
  const target = $(outputId) || $('dev-output');
  target.hidden = false;
  target.className = 'detail';
  target.textContent = `$ ${cmd}\n`;
  try {
    const res = await fetch('/api/dev', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cmd, needsRoot }) });
    target.textContent += await res.text();
    if (!res.ok) target.className = 'detail bad';
  } catch (e) {
    target.textContent += `Error: ${e}`;
    target.className = 'detail bad';
  }
  target.scrollTop = target.scrollHeight;
}

function setupDev() {
  const tbody = $('dev-commands');
  tbody.replaceChildren();
  for (const c of DEV_COMMANDS) {
    const tr = el('tr');
    const btn = el('button', 'Run');
    btn.type = 'button';
    btn.setAttribute('aria-label', `Run ${c.cmd}`);
    btn.addEventListener('click', () => runDevCommand(c.cmd, c.root));
    const td = el('td');
    td.append(btn);
    tr.append(el('td', c.cmd, 'mono'), el('td', c.desc, 'wrap'), el('td', c.root ? 'yes' : 'no', 'muted'), td);
    tbody.append(tr);
  }
  const wire = (id, cmd, root, out) => { $(id).onclick = () => runDevCommand(typeof cmd === 'function' ? cmd() : cmd, root, out); };
  wire('dev-status-btn', 'macserver status', true, 'dev-status-output');
  wire('dev-logs-btn', () => `macserver logs ${$('dev-log-service').value} --tail ${$('dev-log-lines').value}`, true, 'dev-logs-output');
  wire('dev-keys-btn', 'macserver keys', true, 'dev-keys-output');
  wire('dev-doctor-btn', 'macserver doctor', false, 'dev-doctor-output');
  wire('dev-backup-btn', 'macserver backup', true, 'dev-backup-output');
  for (const mode of ['status', 'auto', 'on', 'off']) wire(`dev-idle-${mode}-btn`, `macserver idle ${mode}`, true, 'dev-idle-status');
  wire('dev-public-status-btn', 'macserver public', false, 'dev-public-status');
  wire('dev-public-on-btn', 'macserver public on', true, 'dev-public-status');
  wire('dev-public-off-btn', 'macserver public off', true, 'dev-public-status');
  wire('dev-update-btn', 'macserver update', true, 'dev-update-output');
  wire('dev-restart-btn', 'macserver restart', true, 'dev-restart-output');
}

async function refresh() {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    const data = await res.json();
    if (data.error) { const chip = $('summary'); chip.textContent = data.error; chip.className = 'chip warn'; return; }
    if (data.generated_at !== lastSample) { lastSample = data.generated_at; ingest(data); }   // one chart column per reading
    render(data);
  } catch {
    const chip = $('summary');
    chip.textContent = 'Cannot reach the server';
    chip.className = 'chip bad';
  }
}

// ---- pages --------------------------------------------------------------------------------------------------

const TABS = ['overview', 'hardware', 'network', 'services', 'terminal', 'dev'];
let currentTab = 'overview';

function showTab(name, focus = false) {
  if (!TABS.includes(name)) name = 'overview';
  currentTab = name;
  for (const t of TABS) $(`view-${t}`).hidden = t !== name;
  for (const b of document.querySelectorAll('#tabs button')) {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
  try { history.replaceState(null, '', `#${name}`); } catch { /* not fatal */ }
  const frame = $('term-frame');
  if (name === 'terminal') {
    if (!frame.getAttribute('src')) frame.src = '/terminal.html';   // the shell starts on first visit and stays open
    if (frame.contentWindow) frame.contentWindow.postMessage('focus', location.origin);
  }
  if (name === 'dev') setupDev();
  if (latest) render(latest);   // a page's canvases have no size until it is shown: draw it now
}

for (const b of document.querySelectorAll('#tabs button')) b.addEventListener('click', () => showTab(b.dataset.tab));
$('tabs').addEventListener('keydown', (e) => {          // arrow keys move between tabs, as in any tab list
  const i = TABS.indexOf(currentTab);
  if (e.key === 'ArrowRight') { e.preventDefault(); showTab(TABS[(i + 1) % TABS.length], true); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); showTab(TABS[(i + TABS.length - 1) % TABS.length], true); }
});
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || '')) return;
  const i = ['1', '2', '3', '4', '5', '6'].indexOf(e.key);
  if (i >= 0) showTab(TABS[i]);
});
$('term-frame').addEventListener('load', () => { $('term-frame').contentWindow.postMessage('focus', location.origin); });
window.addEventListener('resize', () => { if (latest) render(latest); });

function tick() { $('clock').textContent = new Date().toLocaleTimeString([], { hour12: false }); }
tick();
setInterval(tick, 1000);

showTab(location.hash.slice(1));
refresh();
setInterval(refresh, 2000);
