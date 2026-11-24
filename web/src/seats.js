// Player seats: a browser takes Player 1 or Player 2 under a name, and the server then accepts
// that player's keys and clicks from this browser only. A free seat is open to anyone.
//
// This browser keeps a secret token (sent with its own messages, never shown) and a short public
// id. The state message lists each seat's name and public id, which is how the page knows which
// seat is its own.

const TOKEN_KEY = 'pt-token';
const ID_KEY = 'pt-seat-id';
const NAME_KEY = 'pt-name';
const MINE_KEY = 'pt-my-seats';

function rand(n) {
  const a = new Uint8Array(n);
  (window.crypto || window.msCrypto).getRandomValues(a);
  return Array.from(a, (b) => (b % 36).toString(36)).join('');
}

function stored(key, make) {
  try {
    let v = window.localStorage.getItem(key);
    if (!v && make) {
      v = make();
      window.localStorage.setItem(key, v);
    }
    return v || '';
  } catch (err) {
    return make ? make() : '';
  }
}

function save(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (err) {
    /* storage blocked: the seat still works until the page reloads */
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/**
 * createSeats({ host, send, onMine })
 *   host    the element that holds both player panels (the bars are positioned inside it)
 *   send    (msg) -> bool, sends one message to the server
 *   onMine  (playerNo) called when this browser takes a seat
 * Returns { token, isMine(n), takenByOther(n), ownerName(n), onState(state), onOpen() }.
 */
export function createSeats({ host, send, onMine }) {
  const token = stored(TOKEN_KEY, () => rand(24));
  const pubId = stored(ID_KEY, () => rand(8));
  let wanted = new Set((stored(MINE_KEY) || '').split(',').filter(Boolean).map(Number)); // seats to re-take after a reconnect
  let seats = { p1: null, p2: null };
  const bars = {};

  function remember() {
    save(MINE_KEY, [...wanted].join(','));
  }

  function isMine(n) {
    const s = seats[`p${n}`];
    return !!s && s.id === pubId;
  }
  function takenByOther(n) {
    const s = seats[`p${n}`];
    return !!s && !!s.id && s.id !== pubId;      // an empty id is a free seat that Coach is covering
  }
  function covered(n) {
    const s = seats[`p${n}`];
    return !!s && !!s.auto;
  }
  function ownerName(n) {
    const s = seats[`p${n}`];
    return s ? String(s.name || `Player ${n}`) : '';
  }

  function claim(n, name) {
    const clean = String(name || '').trim().slice(0, 16);
    if (clean) save(NAME_KEY, clean);
    wanted.add(n);
    remember();
    send({ t: 'seat', cmd: 'claim', player: n, name: clean, id: pubId, token });
  }
  function release(n) {
    wanted.delete(n);
    remember();
    send({ t: 'seat', cmd: 'release', player: n, token });
  }

  for (const n of [1, 2]) {
    const bar = el('div', `seatbar seatbar-p${n}`);
    const input = el('input', 'seat-name');
    input.type = 'text';
    input.maxLength = 16;
    input.placeholder = 'your name';
    input.setAttribute('aria-label', `Your name, to play as Player ${n}`);
    input.value = stored(NAME_KEY);
    const take = el('button', 'seat-btn', `Play as P${n}`);
    take.type = 'button';
    take.title = `Take Player ${n}: only this browser can then control it`;
    const label = el('span', 'seat-label');
    const leave = el('button', 'seat-btn seat-leave', 'Release');
    leave.type = 'button';
    leave.title = 'Give this seat up so someone else can take it';
    const go = () => claim(n, input.value);
    take.addEventListener('click', (e) => {
      e.stopPropagation();
      take.blur();
      go();
    });
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
        go();
      }
    });
    input.addEventListener('click', (e) => e.stopPropagation());
    leave.addEventListener('click', (e) => {
      e.stopPropagation();
      leave.blur();
      release(n);
    });
    bar.append(input, take, label, leave);
    if (host) host.append(bar);
    bars[n] = { bar, input, take, label, leave, shown: '' };
  }

  function paint() {
    for (const n of [1, 2]) {
      const b = bars[n];
      const mode = isMine(n) ? 'mine' : takenByOther(n) ? 'other' : 'free';
      const key = `${mode}|${ownerName(n)}|${covered(n)}`;
      if (key === b.shown) continue;
      b.shown = key;
      b.bar.dataset.mode = mode;
      b.input.hidden = mode !== 'free';
      b.take.hidden = mode !== 'free';
      b.leave.hidden = mode !== 'mine';
      const auto = covered(n) ? ' Inactive: Hardener is covering. Press a key to take over.' : '';
      b.label.hidden = mode === 'free' && !auto;
      b.label.textContent = mode === 'mine' ? `You are ${ownerName(n)}.${auto || ` Only this browser controls P${n}.`}`
        : mode === 'other' ? `${ownerName(n)} is playing P${n}.${covered(n) ? ' Inactive: Hardener is covering.' : ''}` : auto.trim();
    }
  }
  paint();

  return {
    token,
    isMine,
    takenByOther,
    ownerName,
    onState(state) {
      const next = state && state.seats ? state.seats : { p1: null, p2: null };
      const before = [isMine(1), isMine(2)];
      seats = { p1: next.p1 || null, p2: next.p2 || null };
      for (const n of [1, 2]) {
        if (isMine(n) && !before[n - 1] && onMine) onMine(n);
        // Someone else holds a seat this browser wanted: stop asking for it.
        if (takenByOther(n) && wanted.has(n)) {
          wanted.delete(n);
          remember();
        }
      }
      paint();
    },
    // The socket (re)opened: say who we are, and take back the seats we held (the server forgets
    // them when it restarts, and after 30 s without us).
    onOpen() {
      send({ t: 'hello', token });
      const name = stored(NAME_KEY);
      for (const n of wanted) send({ t: 'seat', cmd: 'claim', player: n, name, id: pubId, token });
    },
  };
}
