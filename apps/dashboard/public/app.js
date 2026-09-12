const byId = id => document.getElementById(id);
let lastSuccess = 0;

const formatBytes = value => {
  if (value === null || value === undefined) return 'Unavailable';
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024).toFixed(1)} KiB`;
};
const formatDuration = value => {
  if (value === null || value === undefined) return 'Unavailable';
  const days = Math.floor(value / 86400), hours = Math.floor(value % 86400 / 3600), minutes = Math.floor(value % 3600 / 60);
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
};
const text = (id, value) => { byId(id).textContent = value; };
const stateRank = state => ({ unavailable: 1, healthy: 0, degraded: 2, failed: 3 }[state] ?? 1);

function draw(history) {
  const canvas = byId('plot'), context = canvas.getContext('2d');
  const ratio = Math.min(window.devicePixelRatio || 1, 2), width = canvas.clientWidth, height = canvas.clientHeight;
  canvas.width = Math.max(1, Math.floor(width * ratio)); canvas.height = Math.max(1, Math.floor(height * ratio)); context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  const style = getComputedStyle(document.documentElement), line = style.getPropertyValue('--line'), muted = style.getPropertyValue('--muted');
  context.lineWidth = 1; context.strokeStyle = line;
  for (let y = 0; y <= 4; y++) { const py = 20 + y * (height - 40) / 4; context.beginPath(); context.moveTo(20, py); context.lineTo(width - 20, py); context.stroke(); }
  context.fillStyle = muted; context.font = '12px system-ui';
  for (let y = 0; y <= 4; y++) context.fillText(`${100 - y * 25}%`, 24, 16 + y * (height - 40) / 4);
  const series = (key, color) => {
    const points = history.map((item, index) => ({ x: 20 + index * (width - 40) / Math.max(1, history.length - 1), y: item[key] === null ? null : 20 + (100 - item[key]) * (height - 40) / 100 }));
    context.strokeStyle = color; context.lineWidth = 2; context.lineJoin = 'round'; context.beginPath(); let started = false;
    for (const point of points) { if (point.y === null) { started = false; continue; } if (!started) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y); started = true; }
    context.stroke();
  };
  series('memoryPercent', style.getPropertyValue('--blue')); series('cpuPercent', style.getPropertyValue('--mint'));
  const count = history.filter(item => item.cpuPercent !== null || item.memoryPercent !== null).length;
  canvas.setAttribute('aria-label', count ? `CPU and allocated memory trend from ${count} real samples. Latest CPU ${history.at(-1)?.cpuPercent?.toFixed(0) ?? 'unavailable'} percent; memory ${history.at(-1)?.memoryPercent?.toFixed(0) ?? 'unavailable'} percent.` : 'Waiting for host metric samples.');
}

function render(value) {
  const { host, evidence } = value; lastSuccess = Date.now();
  text('cpu', host.cpuPercent === null ? 'Collecting' : `${host.cpuPercent.toFixed(0)}%`);
  text('memory', host.memoryPercent === null ? 'Unavailable' : `${host.memoryPercent}%`);
  const used = host.disk ? host.disk.totalBytes - host.disk.availableBytes : null;
  text('disk', host.disk ? formatBytes(host.disk.availableBytes) : 'Unavailable');
  text('disk-note', host.disk ? `${Math.round(100 * used / host.disk.totalBytes)}% used · root filesystem` : 'Root filesystem unavailable');
  text('temperature', host.temperatureCelsius === null ? 'Unavailable' : `${host.temperatureCelsius.toFixed(0)}°C`);
  text('traffic', host.receiveBytesPerSecond === null ? 'Collecting' : `${formatBytes(host.receiveBytesPerSecond)}/s`);
  text('traffic-note', host.sendBytesPerSecond === null ? 'Aggregate receive / send' : `Receive · send ${formatBytes(host.sendBytesPerSecond)}/s`);
  text('uptime', formatDuration(host.uptimeSeconds)); text('source', `Host: ${host.source} · Collector: ${evidence.collector}`);
  text('sample-time', `Sampled ${new Date(host.at).toLocaleTimeString()}`);
  text('evidence-age', evidence.observedAt ? `${evidence.ageSeconds}s old${evidence.stale ? ' · stale' : ''}` : 'Unavailable');
  text('requests', evidence.requests.perMinute === null ? 'Unavailable' : String(evidence.requests.perMinute));
  text('errors', evidence.requests.errorsPerMinute === null ? 'Unavailable' : String(evidence.requests.errorsPerMinute));
  text('tailscale', evidence.tailscale.state); text('backup', evidence.backup.state);
  draw(host.history);

  const allStates = [evidence.tailscale.state, evidence.backup.state, evidence.requests.state, ...evidence.services.map(item => item.state)];
  const worst = !evidence.observedAt ? 'unavailable' : evidence.stale ? 'degraded' : allStates.reduce((current, candidate) => stateRank(candidate) > stateRank(current) ? candidate : current, 'healthy');
  const verdict = byId('status'); verdict.dataset.state = worst;
  text('verdict-label', ({ healthy: 'NOMINAL', degraded: 'ATTENTION', failed: 'ACTION REQUIRED', unavailable: 'UNVERIFIED' })[worst]);
  text('verdict-title', ({ healthy: 'The appliance is reporting normally', degraded: 'Some evidence needs attention', failed: 'A verified service has failed', unavailable: 'Appliance state is not fully known' })[worst]);
  text('verdict-detail', !evidence.observedAt ? 'No valid collector snapshot is available. Local host readings may still update, but service, network, request, and recovery state are unverified.' : evidence.stale ? 'Collector evidence is stale. Local host readings may still update, but service, network, request, and recovery state must be reverified.' : `State is derived from ${allStates.length} collector observations plus local host readings. Unobserved systems remain unavailable.`);

  const alerts = byId('alerts'); alerts.replaceChildren();
  const items = evidence.observedAt && evidence.stale ? [{ severity: 'warning', title: 'Collector evidence is stale', detail: `Last valid snapshot was ${evidence.ageSeconds} seconds old.` }, ...evidence.alerts] : evidence.alerts;
  for (const item of items) { const li = document.createElement('li'); li.className = item.severity; const title = document.createElement('strong'), detail = document.createElement('span'); title.textContent = item.title; detail.textContent = item.detail; li.append(title, detail); alerts.append(li); }
  if (!items.length) { const li = document.createElement('li'), title = document.createElement('strong'), detail = document.createElement('span'); li.className = 'info'; title.textContent = 'No active collector alerts'; detail.textContent = 'Absence of alerts does not replace the service states below.'; li.append(title, detail); alerts.append(li); }
  text('alert-count', `${items.length} active`);

  const services = byId('services'); services.replaceChildren();
  for (const item of evidence.services) { const li = document.createElement('li'), name = document.createElement('strong'), state = document.createElement('span'), detail = document.createElement('small'); name.textContent = item.name; state.className = `state ${item.state}`; state.textContent = item.state; detail.textContent = item.detail || 'No detail supplied'; li.append(name, state, detail); services.append(li); }
  if (!evidence.services.length) { const li = document.createElement('li'), name = document.createElement('strong'), state = document.createElement('span'), detail = document.createElement('small'); name.textContent = 'No service evidence'; state.className = 'state unavailable'; state.textContent = 'Unavailable'; detail.textContent = 'The collector snapshot has no service observations.'; li.append(name, state, detail); services.append(li); }
  text('service-summary', `${evidence.services.length} observed`); text('connection', 'Local display service connected');
}

async function refresh() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error(); render(await response.json());
  } catch {
    text('connection', `Display service unavailable${lastSuccess ? ` · last response ${Math.floor((Date.now() - lastSuccess) / 1000)}s ago` : ''}`);
    byId('status').dataset.state = 'failed'; text('verdict-label', 'DISPLAY DEGRADED'); text('verdict-title', 'Live status updates are interrupted');
    text('verdict-detail', 'The last successful values remain visible and may be stale. The kiosk watchdog will retry automatically.');
  }
}
function clock() { const now = new Date(); text('clock', now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })); text('date', now.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' })); }
clock(); setInterval(clock, 1000); void refresh(); setInterval(() => { if (!document.hidden) void refresh(); }, 10_000); window.addEventListener('resize', () => void refresh());
