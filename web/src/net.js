// WebSocket link to the game server with automatic reconnect.
// Message shapes are fixed by docs/CONTRACTS.md.

export function createNet({ onMessage, onStatus }) {
  let ws = null;
  let closed = false;
  let retryMs = 500;
  let retryTimer = null;
  let attempts = 0;

  function url() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws`;
  }

  function connect() {
    if (closed) return;
    attempts += 1;
    onStatus('connecting', attempts > 1 ? `RECONNECTING (${attempts - 1})` : 'CONNECTING');
    let sock;
    try {
      sock = new WebSocket(url());
    } catch (err) {
      schedule();
      return;
    }
    ws = sock;

    sock.onopen = () => {
      if (ws !== sock) return;
      retryMs = 500;
      attempts = 0;
      onStatus('open', 'LIVE');
    };
    sock.onmessage = (ev) => {
      if (ws !== sock) return;
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (err) {
        return; // ignore anything that is not JSON
      }
      if (msg && typeof msg === 'object') onMessage(msg);
    };
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null;
      schedule();
    };
    sock.onerror = () => {
      // onclose follows; nothing to do here
    };
  }

  function schedule() {
    if (closed) return;
    onStatus('down', 'OFFLINE, RETRYING');
    clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, retryMs);
    retryMs = Math.min(5000, Math.round(retryMs * 1.7));
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function isOpen() {
    return !!ws && ws.readyState === WebSocket.OPEN;
  }

  function close() {
    closed = true;
    clearTimeout(retryTimer);
    if (ws) ws.close();
  }

  connect();
  return { send, isOpen, close, mode: 'live' };
}

const CONTROL_ROUTES = {
  start: '/api/match/start',
  reset: '/api/match/reset',
  advance: '/api/world/advance',
};

// Fallback for the control buttons when the socket is not open.
export async function postControl(cmd) {
  const route = CONTROL_ROUTES[cmd];
  if (!route) return false;
  try {
    const res = await fetch(route, { method: 'POST' });
    return res.ok;
  } catch (err) {
    return false;
  }
}

// Server facts the state message does not carry (tick length). Not in CONTRACTS.md; null on failure.
export async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    return res.ok ? await res.json() : null;
  } catch (err) {
    return null;
  }
}

export async function fetchLedger(limit = 200) {
  try {
    const res = await fetch(`/api/ledger?limit=${limit}`);
    if (!res.ok) return [];
    const data = await res.json();
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.rows)) return data.rows;
    if (data && Array.isArray(data.ledger)) return data.ledger;
    return [];
  } catch (err) {
    return [];
  }
}
