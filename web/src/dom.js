// Small DOM and formatting helpers shared by the HUD modules.
// Every string that comes from the server is written with textContent, never as HTML.

export const $ = (id) => document.getElementById(id);

export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

export function setText(node, value) {
  const s = String(value);
  if (node.textContent !== s) node.textContent = s;
}

export function isNum(v) {
  return v !== null && v !== undefined && Number.isFinite(Number(v));
}

// Scores: whole numbers as they are, anything else with one decimal.
export function fmtScore(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '0';
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// Points gained or lost: always signed, up to two decimals ("+1.38", "+3", "-1").
export function fmtPoints(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '+0';
  const s = Number.isInteger(n) ? String(Math.abs(n)) : Math.abs(n).toFixed(2).replace(/0$/, '');
  return `${n < 0 ? '-' : '+'}${s}`;
}

export function fmtPct(v, digits = 0) {
  if (!isNum(v)) return '--';
  return (Number(v) * 100).toFixed(digits) + '%';
}

// Danger of a card: small values keep one decimal so 0.4% does not read as 0%.
export function fmtDanger(v) {
  if (!isNum(v)) return '--';
  const p = Number(v) * 100;
  return (p < 10 ? p.toFixed(1) : p.toFixed(0)) + '%';
}

export function fmtInt(v) {
  return isNum(v) ? Number(v).toLocaleString('en-US') : '--';
}

export function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// A match length in words: "5 minutes", "2 min 30 s", "45 seconds".
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  if (m === 0) return `${s} seconds`;
  if (s % 60 === 0) return m === 1 ? '1 minute' : `${m} minutes`;
  return `${m} min ${s % 60} s`;
}

export function plural(n, one, many) {
  return `${n} ${Number(n) === 1 ? one : many || one + 's'}`;
}

// A button that never keeps keyboard focus: Enter is player 2's patch key.
export function onClick(btn, fn) {
  btn.addEventListener('click', (e) => {
    btn.blur();
    fn(e);
  });
}
