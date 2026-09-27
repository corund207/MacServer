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
    ['T2 drivers', `keyboard ${onOff(t2.apple_bce)} · Wi-Fi ${onOff(t2.brcmfmac)} · SMC ${onOff(t2.applesmc)}`],
    ['Uptime', duration(h.uptime_s)],
    ['Load', `${h.load.join(' / ')} on ${h.cpus} threads`],
    ['Memory', `${bytes(h.memory.used)} of ${bytes(h.memory.total)}`],
    ['Disk', `${bytes(h.disk.used)} of ${bytes(h.disk.total)}`],
    ['CPU temp', temp === null ? 'unknown' : `${temp.toFixed(0)} °C`, hot ? 'bad' : ''],
    ['Fans', h.sensors.fans_rpm.length ? h.sensors.fans_rpm.map((r) => `${r} rpm`).join(', ') : 'unknown'],
    ['Battery', bat ? `${bat.percent ?? '?'}% · ${bat.status}${bat.limit ? ` · stops at ${bat.limit}%` : ''}` : 'none'],
    ['Updates', h.updates_pending ? `${h.updates_pending} pending (sudo macserver update)` : 'up to date', h.updates_pending ? 'warn' : ''],
    ['Tailscale', `${s.tailscale.state}${s.tailscale.name ? ` · ${s.tailscale.name}` : ''}`],
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

  const body = $('services');
  body.replaceChildren();
  for (const c of s.containers) {
    const tr = el('tr');
    tr.append(el('td', c.name, 'mono'), el('td', c.project), el('td', c.state, c.state === 'running' ? 'good' : 'bad'), el('td', c.status));
    body.append(tr);
  }
}

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
setInterval(refresh, 15000);
