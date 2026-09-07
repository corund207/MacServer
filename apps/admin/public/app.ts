const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
let csrf = '', expires = 0, filePath = '', filesEnabled = false, lastSample = 0, busy = false;
const notice = (text: string) => { el('notice').textContent = text; };
const element = (tag: string, text: string, className = '') => { const node = document.createElement(tag); node.textContent = text; node.className = className; return node; };
function signedOut() {
  csrf = ''; expires = 0; el('workspace').hidden = true; el('login').hidden = false;
  el('logout').hidden = true; el('refresh').hidden = true;
  for (const id of ['metric-cards', 'metric-rows', 'audit-rows', 'file-rows', 'service-list', 'service-summary', 'cpu-chart', 'memory-chart']) el(id).replaceChildren();
}
async function api(path: string, body?: object) {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(10_000), ...(body ? { method: 'POST', headers: { 'content-type': 'application/json', 'x-admin-request': '1', 'x-csrf-token': csrf }, body: JSON.stringify(body) } : {}) });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) signedOut();
    const value = await response.json().catch(() => ({})); throw new Error(value.error ?? 'Request unavailable');
  }
  return response.json();
}
const percent = (n: number | null) => n === null ? 'Collecting…' : `${Math.round(n)}%`;
const bytes = (n: number | null) => n === null ? 'Unavailable' : n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GiB` : n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MiB` : `${(n / 1024).toFixed(1)} KiB`;
function rows(target: string, data: string[][]) {
  const table = el(target); table.replaceChildren();
  for (const row of data) { const tr = document.createElement('tr'); for (const value of row) tr.append(element('td', value)); table.append(tr); }
  if (!data.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 5; td.textContent = 'No events or entries to display.'; tr.append(td); table.append(tr); }
}
function chart(id: string, samples: any[], key: 'cpu' | 'memory') {
  const root = el(id), values = samples.filter(v => v[key] !== null); root.replaceChildren();
  if (values.length < 2) { root.append(element('p', 'Waiting for a second real sample. No history is fabricated.', 'muted')); return; }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 400 110'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${key === 'cpu' ? 'CPU utilization' : 'Allocated memory'} trend from ${values.length} real samples, latest ${percent(values.at(-1)[key])}. Values are also in the samples table.`);
  const line = document.createElementNS(svg.namespaceURI, 'polyline'); line.setAttribute('points', values.map((v, i) => `${i * 400 / (values.length - 1)},${105 - v[key]}`).join(' ')); svg.append(line); root.append(svg, element('p', `0–100% · ${values.length} samples · latest ${percent(values.at(-1)[key])}`, 'chart-caption'));
}
function render(data: any) {
  const m = data.metrics; lastSample = Date.parse(m.at); filesEnabled = data.filesEnabled;
  const cards = el('metric-cards'); cards.replaceChildren();
  const diskUsed = m.disk ? 100 * (1 - m.disk.available / m.disk.total) : null;
  for (const [name, value, detail] of [['CPU utilization', percent(m.cpu), 'Between observed samples'], ['Allocated memory', percent(m.memory), `${bytes(m.memoryTotal)} total · includes cache`], ['Root filesystem', m.disk ? percent(diskUsed) : 'Unavailable', m.disk ? `${bytes(m.disk.available)} available` : 'No filesystem reading'], ['Host uptime', `${Math.floor(m.uptime / 3600)}h ${Math.floor(m.uptime % 3600 / 60)}m`, 'Temperature unavailable']]) {
    const card = element('article', '', 'metric-card'); card.append(element('p', name), element('strong', value), element('small', detail)); cards.append(card);
  }
  el('alerts').replaceChildren(...data.alerts.map((s: string) => element('li', s)));
  for (const id of ['service-list', 'service-summary']) {
    el(id).replaceChildren(...data.services.map((s: any) => { const box = element('div', '', 'service'), text = element('div', s.name); text.append(element('small', s.detail)); box.append(text, element('span', s.state)); return box; }));
  }
  el('metric-source').textContent = m.source;
  rows('metric-rows', [...m.history].reverse().slice(0, 20).map((v: any) => [new Date(v.at).toLocaleTimeString(), percent(v.cpu), percent(v.memory), v.rx === null ? 'Collecting…' : `${bytes(v.rx)}/s`, v.tx === null ? 'Collecting…' : `${bytes(v.tx)}/s`]));
  chart('cpu-chart', m.history, 'cpu'); chart('memory-chart', m.history, 'memory');
  el('tailscale-state').textContent = `${data.tailscale.state}${data.tailscale.addresses.length ? ' · ' + data.tailscale.addresses.join(', ') : ''}`;
  el('files-state').textContent = filesEnabled ? 'Curated read-only root is configured.' : 'No curated file root configured.';
  el('file-config').textContent = filesEnabled ? 'Curated root · read-only' : 'Not configured';
  el('session-expiry').textContent = `Expires ${new Date(expires).toLocaleTimeString()}`;
  freshness();
}
function freshness() {
  if (!csrf) return;
  const age = Math.max(0, Math.floor((Date.now() - lastSample) / 1000));
  el('freshness').textContent = !lastSample ? 'Telemetry unavailable' : age > 30 ? `Stale telemetry · ${age}s since sample` : `Sampled ${new Date(lastSample).toLocaleTimeString()} · ${age}s ago`;
  if (Date.now() >= expires) { signedOut(); notice('Your session expired. Sign in again.'); }
}
async function audit() {
  const value = await api(`/api/audit?${new URLSearchParams(el<HTMLSelectElement>('audit-filter').value ? { outcome: el<HTMLSelectElement>('audit-filter').value } : {})}`);
  el('audit-scope').textContent = value.scope;
  rows('audit-rows', value.events.map((e: any) => [new Date(e.at).toLocaleString(), e.event, e.actor, e.outcome]));
}
async function files() {
  el('file-path').textContent = '/' + filePath; el<HTMLButtonElement>('files-up').disabled = !filePath || !filesEnabled;
  if (!filesEnabled) { rows('file-rows', []); return; }
  const result = await api('/api/files?' + new URLSearchParams({ path: filePath }));
  const root = el('file-rows'); root.replaceChildren();
  for (const entry of result.entries) {
    const path = [filePath, entry.name].filter(Boolean).join('/'), tr = document.createElement('tr'), action = document.createElement('td');
    if (entry.directory) { const button = element('button', `Open ${entry.name}`); button.onclick = () => { filePath = path; void files().catch(e => notice(e.message)); }; action.append(button); }
    else { const link = document.createElement('a'); link.textContent = `Download ${entry.name}`; link.href = '/api/files/download?' + new URLSearchParams({ path }); action.append(link); }
    tr.append(element('td', entry.name), element('td', entry.directory ? 'Folder' : 'Text export'), action); root.append(tr);
  }
  if (!result.entries.length) rows('file-rows', []);
}
async function view() {
  const name = location.hash.slice(1), target = document.querySelector<HTMLElement>(`.view[id="${['overview','services','metrics','network','backups','files','database','updates','audit','configuration'].includes(name) ? name : 'overview'}"]`)!;
  document.querySelectorAll<HTMLElement>('.view').forEach(v => { v.hidden = v !== target; });
  document.querySelectorAll('nav a').forEach(a => { if (a.getAttribute('href') === '#' + target.id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  el('page-title').textContent = target.id === 'overview' ? 'Overview' : target.querySelector('h2')!.textContent;
  if (!csrf) return;
  if (target.id === 'audit') await audit(); if (target.id === 'files') await files();
}
async function refresh() {
  if (!csrf || busy) return; busy = true; el<HTMLButtonElement>('refresh').disabled = true;
  try { const data = await api('/api/overview'); if (!csrf) return; render(data); await view(); notice(''); }
  catch (e) { notice((e as Error).message + '. Last successful readings may be stale.'); }
  finally { busy = false; el<HTMLButtonElement>('refresh').disabled = false; }
}
function activate(session: any) {
  csrf = session.csrf; expires = session.expires; el('login').hidden = true; el('workspace').hidden = false; el('refresh').hidden = false; el('logout').hidden = false;
}
el('login-form').addEventListener('submit', async event => {
  event.preventDefault(); const input = el<HTMLInputElement>('secret'), button = el('login-form').querySelector('button')!; button.disabled = true;
  const secret = input.value; input.value = '';
  try { activate(await api('/api/login', { secret, confirm: true })); await refresh(); el('main').focus(); }
  catch (e) { notice((e as Error).message); input.focus(); } finally { button.disabled = false; }
});
el('logout').onclick = async () => {
  try { await api('/api/logout', { confirm: true }); signedOut(); notice('Signed out.'); }
  catch (e) { signedOut(); notice(`Sign-out response unavailable. Close this browser; server sessions expire within 15 minutes. ${(e as Error).message}`); }
};
el('refresh').onclick = () => { void refresh(); };
el('files-up').onclick = () => { filePath = filePath.split('/').slice(0, -1).join('/'); void files().catch(e => notice(e.message)); };
el('audit-filter').onchange = () => { void audit().catch(e => notice(e.message)); };
function theme(value: string) { document.documentElement.dataset.theme = value; el('theme').textContent = value === 'light' ? 'Dark theme' : 'Light theme'; }
try { theme(localStorage.getItem('admin-theme') ?? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')); } catch { theme('dark'); }
el('theme').onclick = () => { const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; theme(next); try { localStorage.setItem('admin-theme', next); } catch { /* Preference persistence is optional. */ } };
window.addEventListener('hashchange', () => { void view().catch(e => notice(e.message)); });
setInterval(freshness, 1000); setInterval(() => { if (!document.hidden) void refresh(); }, 15_000);
void view();
void api('/api/session').then(async s => { activate(s); await refresh(); }).catch(() => { signedOut(); notice('Sign in with your separate administration token.'); });
