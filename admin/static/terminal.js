'use strict';
// A shell in the browser: xterm.js on one side, the /term WebSocket (terminal.py) on the other.
// Binary frames carry terminal bytes; a text frame is a JSON control message (resize).

const note = document.getElementById('note');
const term = new Terminal({
  cursorBlink: true,
  scrollback: 5000,
  fontSize: 14,
  fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace',
  theme: { background: '#0b0e11', foreground: '#c9d1d9', cursor: '#3fb950', selectionBackground: '#264f78' },
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));

let socket = null;
let closedByServer = false;

function status(text, bad) {
  note.textContent = text;
  note.className = bad ? 'bad' : '';
}

function sendSize() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
  }
}

function connect() {
  closedByServer = false;
  status('connecting…');
  socket = new WebSocket(`wss://${location.host}/term/ws`);
  socket.binaryType = 'arraybuffer';
  socket.addEventListener('open', () => { status(''); fit.fit(); sendSize(); term.focus(); });
  socket.addEventListener('message', (event) => {
    if (event.data instanceof ArrayBuffer) term.write(new Uint8Array(event.data));
  });
  socket.addEventListener('close', () => {
    closedByServer = true;
    status('disconnected: press Enter to open a new shell', true);
  });
  socket.addEventListener('error', () => status('cannot reach the terminal service', true));
}

const encoder = new TextEncoder();
term.onData((data) => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
  else if (closedByServer && data === '\r') { term.reset(); connect(); }
});
term.onResize(sendSize);

// The pane is hidden while another tab shows; refit whenever it gets a size.
new ResizeObserver(() => { try { fit.fit(); } catch { /* not laid out yet */ } }).observe(document.getElementById('wrap'));
window.addEventListener('message', (event) => {
  if (event.origin === location.origin && event.data === 'focus') { fit.fit(); term.focus(); }
});

connect();
