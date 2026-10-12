// Rolola web client. No build step: plain ES modules.
import { t, setLang, getLang, DICT } from './i18n.js';

// ------------------------------------------------------------------ state
const S = {
  me: null, state: null, stateAt: 0, connected: false, route: 'now', sources: [],
  search: { q: '', source: 'all', results: {}, pending: new Set(), seq: 0 },
  favorites: new Set(), lyrics: null, lyricsFor: null, showLyrics: false, dragging: false,
  libTab: 'history', adminCache: {}, proxyAuth: false,
};
const store = {
  get(k, d = null) { try { return localStorage.getItem('jb.' + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('jb.' + k, v); } catch {} },
};

// ------------------------------------------------------------------ dom helpers
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren() would print null/false as text; skip them
const setKids = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));

const ICONS = {
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  queue: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  library: '<path d="M4 4v16M9 4v16M14 4l6 16"/>',
  admin: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  play: '<path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="4" width="4.5" height="16" rx="1.2" fill="currentColor" stroke="none"/><rect x="13.5" y="4" width="4.5" height="16" rx="1.2" fill="currentColor" stroke="none"/>',
  skip: '<path d="M5 5l10 7-10 7z" fill="currentColor"/><path d="M19 5v14"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
  heartfill: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="M5 12l5 5L20 7"/>',
  star: '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  starfill: '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z" fill="currentColor"/>',
  up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>', top: '<path d="M6 11l6-6 6 6M12 5v14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>', close: '<path d="M6 6l12 12M18 6L6 18"/>',
  more: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
  volume: '<path d="M11 5L6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  radio: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5"/>',
  tv: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8"/>',
  lyrics: '<path d="M4 6h16M4 10h10M4 14h16M4 18h8"/>', user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  album: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>', list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  shuffle: '<path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>', swap: '<path d="M7 7h13l-4-4M17 17H4l4 4"/>',
  mic: '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M19 11a7 7 0 0 1-14 0M12 18v3"/>',
  warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17.5v.01"/>', refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
};
function icon(name, cls = '') {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'i ' + cls); s.setAttribute('aria-hidden', 'true');
  s.innerHTML = ICONS[name] || ''; return s;
}

const fmt = (s) => {
  if (s === null || s === undefined || isNaN(s)) return '–:––';
  s = Math.max(0, Math.floor(s)); const hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, '0');
  return hh ? `${hh}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};
const fmtLong = (s) => { const m = Math.round((s || 0) / 60); return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`; };
function hue(str) { let x = 0; for (const c of String(str || '?')) x = (x * 31 + c.charCodeAt(0)) % 360; return x; }
function avatar(name, size = 34) {
  return h('span', { class: 'avatar', style: { width: size + 'px', height: size + 'px', background: `hsl(${hue(name)} 55% 45%)`, fontSize: (size * 0.42) + 'px' }, 'aria-hidden': 'true' },
    (name || '?').trim().slice(0, 1).toUpperCase());
}
const SOURCE_NAMES = { youtube: 'YouTube', local: 'Library', radio: 'Radio', soundcloud: 'SoundCloud', bandcamp: 'Bandcamp', jamendo: 'Jamendo', podcast: 'Podcasts', spotify_links: 'Spotify', url: 'Link', system: 'System' };
const srcName = (id) => (S.sources.find((s) => s.id === id)?.name) || SOURCE_NAMES[id] || id;
const srcChip = (id) => h('span', { class: 'chip src-' + id }, SOURCE_NAMES[id] || srcName(id));

function art(track, cls = 'art') {
  const wrap = h('div', { class: cls });
  const fallback = icon(track?.kind === 'station' ? 'radio' : track?.kind === 'album' ? 'album' : track?.kind === 'playlist' || track?.kind === 'podcast' ? 'list' : 'music');
  if (track?.artwork) {
    const img = h('img', { src: track.artwork, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
    img.onerror = () => { img.remove(); wrap.append(fallback); };
    wrap.append(img);
  } else wrap.append(fallback);
  return wrap;
}
function trackSub(tr) {
  const bits = [];
  if (tr.artist) bits.push(tr.artist);
  if (tr.kind === 'track' || tr.kind === 'episode') { if (tr.duration) bits.push(fmt(tr.duration)); }
  if (tr.count) bits.push(t('search.tracks', { n: tr.count }));
  return bits.join(' · ');
}

// ------------------------------------------------------------------ api
class ApiErr extends Error { constructor(msg, data) { super(msg); this.data = data; } }
function errText(data) {
  if (!data) return t('err.network');
  const code = data.error;
  if (typeof code === 'string' && code.startsWith('err.')) {
    const p = { ...(data.params || {}) };
    if (Array.isArray(p.reasons)) p.reasons = p.reasons.join('; ');
    return t(code, p);
  }
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join('; ');
  return code || t('err.network');
}
async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin',
      headers: { 'X-Rolola': '1', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch { throw new ApiErr(t('err.network')); }
  let data = null; try { data = await res.json(); } catch {}
  if (!res.ok) throw new ApiErr(errText(data), data);
  return data;
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, 'success'); return r; }
  catch (e) { toast(e.message, 'error'); return null; }
}

// ------------------------------------------------------------------ toasts & sheets
function toast(msg, level = 'info', ms = 3800) {
  const el = h('div', { class: 'toast ' + level, role: level === 'error' ? 'alert' : 'status' }, msg);
  $('#toasts').append(el);
  setTimeout(() => el.remove(), ms);
}
function floatHeart() {
  const el = h('div', { class: 'float-heart', style: { left: (20 + Math.random() * 60) + 'vw', bottom: '20vh' } }, '❤️');
  document.body.append(el); setTimeout(() => el.remove(), 1700);
}
function sheet(title, build) {
  const prev = document.activeElement;
  const close = () => { bd.remove(); document.removeEventListener('keydown', onKey); prev?.focus?.(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const body = h('div');
  const panel = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'row' }, h('h3', { class: 'grow ellipsis' }, title),
      h('button', { class: 'icon-btn small', 'aria-label': t('common.close'), onclick: close }, icon('close'))),
    body);
  const bd = h('div', { class: 'backdrop', onclick: (e) => { if (e.target === bd) close(); } }, panel);
  document.body.append(bd); document.addEventListener('keydown', onKey);
  build(body, close);
  setTimeout(() => (panel.querySelector('input,button.primary,button') || panel).focus(), 30);
  return close;
}
function confirmSheet(msg, onYes, danger = true) {
  sheet(msg, (b, close) => b.append(h('div', { class: 'row', style: { justifyContent: 'flex-end', marginTop: '10px' } },
    h('button', { class: 'btn', onclick: close }, t('common.cancel')),
    h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), onclick: () => { close(); onYes(); } }, t('common.yes')))));
}

// ------------------------------------------------------------------ websocket
let ws = null, wsRetry = 1000, pingTimer = null;
function connect() {
  const display = S.route === 'display' ? '?display=1' : '';
  const url = new URL('api/ws' + display, location.href); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(url);
  ws.onopen = () => {
    if (!S.connected && S.wasConnected) toast(t('toast.online'), 'success', 2000);
    S.connected = S.wasConnected = true; wsRetry = 1000; renderChrome();
    clearInterval(pingTimer); pingTimer = setInterval(() => ws.readyState === 1 && ws.send('ping'), 25000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'state') onState(msg.state);
    else if (msg.type === 'toast') toast(t(msg.key, msg.params || {}), msg.level || 'info', msg.level === 'error' ? 6000 : 3800);
    else if (msg.type === 'reaction') floatHeart();
  };
  ws.onclose = (ev) => {
    clearInterval(pingTimer);
    if (S.connected) toast(t('toast.offline'), 'error', 2500);
    S.connected = false; renderChrome();
    if (ev.code === 4401) return; // needs a name
    setTimeout(connect, wsRetry); wsRetry = Math.min(wsRetry * 2, 15000);
  };
}
function onState(st) {
  const prevPlay = S.state?.now?.play_id;
  S.state = st; S.stateAt = performance.now();
  if (st.settings?.language && !store.get('lang')) setLang(st.settings.language);
  if (st.now?.play_id !== prevPlay) { S.lyrics = null; S.lyricsFor = null; if (S.showLyrics || S.route === 'display') loadLyrics(); }
  if (S.dragging) return;
  renderChrome();
  if (['now', 'queue', 'display'].includes(S.route)) renderMain();
}
function position() {
  const st = S.state; if (!st) return 0;
  let p = st.position || 0;
  if (st.status === 'playing') p += (performance.now() - S.stateAt) / 1000;
  return st.duration ? Math.min(p, st.duration) : p;
}
async function loadLyrics() {
  const st = S.state; if (!st?.now || !st.settings?.lyrics) return;
  const pid = st.now.play_id; if (S.lyricsFor === pid) return;
  S.lyricsFor = pid;
  try { const r = await api('GET', 'api/lyrics'); if (S.lyricsFor === pid) { S.lyrics = r.lyrics || { lines: [] }; renderMain(); } } catch {}
}

// ------------------------------------------------------------------ routing & chrome
const ROUTES = ['now', 'search', 'queue', 'library', 'admin', 'display'];
function route() {
  const r = (location.hash.replace(/^#\/?/, '').split('?')[0]) || 'now';
  S.route = ROUTES.includes(r) ? r : 'now';
  if (S.route === 'admin' && !S.me?.is_admin) S.route = 'now';
  render();
}
window.addEventListener('hashchange', route);

let shellBuilt = false;
function render() {
  const app = $('#app');
  if (S.route === 'display') { shellBuilt = false; setKids(app, ); renderDisplay(app); return; }
  if (!S.me) { shellBuilt = false; renderJoin(app); return; }
  if (!shellBuilt) {
    setKids(app, 
      h('div', { class: 'shell' },
        h('header', { class: 'topbar', id: 'topbar' }),
        h('nav', { class: 'bottomnav', id: 'nav', 'aria-label': 'Main' }),
        h('main', { id: 'main', tabindex: '-1' }),
        h('aside', { class: 'side', id: 'side', 'aria-label': t('nav.now') })),
      h('div', { class: 'mini hidden', id: 'mini' }));
    shellBuilt = true;
  }
  renderChrome(); renderMain(true);
}
function renderChrome() {
  if (!shellBuilt) return;
  const st = S.state;
  const n = st?.listeners?.length || 0;
  setKids($('#topbar'), 
    h('a', { class: 'brand', href: '#/now', style: { color: 'inherit', textDecoration: 'none' } }, h('span', { class: 'logo' }, icon('music')), t('app.name')),
    h('span', { class: 'spacer' }),
    h('span', { class: 'pill' + (S.connected ? '' : ' off'), title: (st?.listeners || []).map((l) => l.name).join(', ') },
      h('span', { class: 'dot' }), t('me.listeners', { n })),
    h('button', { class: 'avatar', style: { background: `hsl(${hue(S.me.name)} 55% 45%)` }, 'aria-label': S.me.name, onclick: userMenu },
      S.me.name.slice(0, 1).toUpperCase()));
  const q = st?.queue_total || 0;
  const links = [['now', 'home', t('nav.now')], ['search', 'search', t('nav.search')], ['queue', 'queue', t('nav.queue'), q], ['library', 'library', t('nav.library')]];
  if (S.me.is_admin) links.push(['admin', 'admin', t('nav.admin')]);
  setKids($('#nav'), ...links.map(([r, ic, label, badge]) => h('a', { href: '#/' + r, 'aria-current': S.route === r ? 'page' : null },
    icon(ic), h('span', {}, label), badge ? h('span', { class: 'badge', 'aria-label': `${badge}` }, badge > 99 ? '99+' : badge) : null)));
  // desktop side panel & mobile mini player
  const side = $('#side');
  if (S.route !== 'now') { setKids(side, nowCard(true)); } else { setKids(side, upNext(12)); }
  const mini = $('#mini');
  const cur = st?.now;
  if (cur && S.route !== 'now') {
    mini.classList.remove('hidden');
    setKids(mini, art(cur.track), h('div', { class: 'meta' },
      h('div', { class: 'ellipsis', style: { fontWeight: 650 } }, cur.track.title),
      h('div', { class: 'ellipsis small muted' }, cur.icy_title || cur.track.artist || '')),
      playPauseBtn('icon-btn'), h('div', { class: 'bar', id: 'minibar' }));
    mini.onclick = (e) => { if (!e.target.closest('button')) location.hash = '#/now'; };
  } else mini.classList.add('hidden');
}
function renderMain(focus = false) {
  if (!shellBuilt && S.route !== 'display') return;
  if (S.route === 'display') { const app = $('#app'); setKids(app, ); renderDisplay(app); return; }
  const main = $('#main');
  if (S.route === 'search' && main.dataset.route === 'search') return; // keep the input alive
  const scroll = window.scrollY;
  main.dataset.route = S.route;
  const v = { now: viewNow, search: viewSearch, queue: viewQueue, library: viewLibrary, admin: viewAdmin }[S.route];
  setKids(main, ...[v()].flat());
  if (focus) { window.scrollTo(0, 0); } else window.scrollTo(0, scroll);
}

// ------------------------------------------------------------------ join
function renderJoin(app) {
  const input = h('input', { type: 'text', maxlength: '32', placeholder: t('join.placeholder'), 'aria-label': t('join.placeholder'), autocomplete: 'nickname', value: store.get('name', '') });
  const err = h('div', { class: 'small', style: { color: 'var(--bad)', minHeight: '1.2em' }, role: 'alert' });
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api('POST', 'api/session/join', { name: input.value.trim() });
      store.set('name', input.value.trim()); S.me = r.user; boot2();
    } catch (ex) { err.textContent = ex.message; }
  } }, input, err, h('button', { class: 'btn primary', type: 'submit' }, t('join.button')));
  setKids(app, h('div', { class: 'join' }, h('div', { class: 'card' },
    h('div', { class: 'logo' }, icon('music')), h('h1', {}, t('join.title')), h('p', { class: 'muted' }, t('join.subtitle')), form,
    h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '14px' } }, langSelect()))));
  setTimeout(() => input.focus(), 50);
}
function langSelect() {
  return h('select', { 'aria-label': t('me.language'), style: { width: 'auto' }, onchange: (e) => { store.set('lang', e.target.value); setLang(e.target.value); shellBuilt = false; render(); } },
    ...Object.keys(DICT).map((l) => h('option', { value: l, selected: getLang() === l }, l === 'es' ? 'Español' : 'English')));
}

// ------------------------------------------------------------------ now playing
function can(perm) { return S.me?.is_admin || S.state?.settings?.[perm]; }
function playPauseBtn(cls = 'big-btn') {
  const st = S.state; const playing = st?.status === 'playing';
  const allowed = can('user_can_pause');
  return h('button', { class: cls, disabled: !allowed || (!st?.now && !st?.paused), 'aria-label': playing ? t('ctl.pause') : t('ctl.play'),
    onclick: (e) => { e.stopPropagation(); act(() => api('POST', playing ? 'api/player/pause' : 'api/player/play')); } },
  icon(playing ? 'pause' : 'play', 'fill'));
}
function banners() {
  const st = S.state; if (!st) return [];
  const out = [];
  if (st.audio_fault) out.push(h('div', { class: 'banner bad', role: 'alert' }, icon('warn'), h('div', {}, st.audio_fault)));
  if (st.status === 'quiet_hours') out.push(h('div', { class: 'banner warn' }, icon('warn'), t('now.quiet', { when: st.quiet_until || '' })));
  if (st.muted && S.me?.is_admin) out.push(h('div', { class: 'banner warn' }, icon('volume'), t('admin.panic')));
  return out;
}
function nowCard(compact = false) {
  const st = S.state;
  if (!st) return h('div', { class: 'empty' }, h('div', { class: 'spinner', style: { margin: '0 auto' } }));
  const cur = st.now;
  if (!cur) {
    if (st.loading) {
      return h('div', { class: 'now' }, h('div', { class: 'cover' }, st.loading.track.artwork ? h('img', { src: st.loading.track.artwork, alt: '' }) : icon('music')),
        h('div', { class: 't1 ellipsis' }, st.loading.track.title), h('div', { class: 't2 row', style: { justifyContent: 'center' } }, h('span', { class: 'spinner' }), t('now.loading')));
    }
    return h('div', { class: 'empty' }, icon('music'), h('div', { style: { fontWeight: 700, color: 'var(--text)' } }, st.paused ? t('now.paused') : t('now.nothing')),
      h('p', {}, t('now.nothing_hint')),
      st.paused ? playPauseBtn('btn primary') : h('a', { class: 'btn primary', href: '#/search' }, icon('search'), t('nav.search')));
  }
  const tr = cur.track;
  const liked = cur.liked_by?.includes(S.me?.id);
  const mine = cur.user?.id === S.me?.id;
  const voted = st.votes?.voters?.includes(S.me?.id);
  const fav = S.favorites.has(tr.key);
  const cover = h('div', { class: 'cover' }, tr.artwork ? h('img', { src: tr.artwork, alt: '', referrerpolicy: 'no-referrer', onerror: (e) => e.target.replaceWith(icon('music')) }) : icon(tr.live ? 'radio' : 'music'));
  const who = h('div', { class: 'who' }, srcChip(tr.source), tr.live ? h('span', { class: 'chip live' }, t('now.live')) : null,
    cur.autoplay ? h('span', { class: 'chip' }, t('now.autoplay')) : cur.user ? h('span', { class: 'chip' }, t('now.added_by', { name: cur.user.name })) : null);
  const subst = tr.substituted_from ? h('div', { class: 'sub-note' }, icon('swap'), ' ', t('now.via', { source: srcName(tr.source), orig: SOURCE_NAMES[tr.substituted_from.source] || tr.substituted_from.source }), ' · ',
    h('a', { href: '#', onclick: (e) => { e.preventDefault(); wrongMatch(tr); } }, t('now.wrong_match'))) : null;

  let progress = null;
  if (!tr.live && st.duration) {
    const canSeek = can('user_can_seek') || mine;
    if (canSeek) {
      const range = h('input', { type: 'range', min: 0, max: Math.floor(st.duration), step: 1, value: Math.floor(position()), id: 'seek', 'aria-label': 'Seek',
        onpointerdown: () => (S.dragging = true), onchange: (e) => { S.dragging = false; act(() => api('POST', 'api/player/seek', { position: +e.target.value })); },
        onblur: () => (S.dragging = false) });
      progress = h('div', { class: 'progress' }, range, h('div', { class: 'times' }, h('span', { id: 'tpos' }, fmt(position())), h('span', {}, fmt(st.duration))));
    } else {
      progress = h('div', { class: 'progress' }, h('div', { class: 'track-bar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': Math.floor(st.duration), 'aria-valuenow': Math.floor(position()) }, h('div', { id: 'pbar' })),
        h('div', { class: 'times' }, h('span', { id: 'tpos' }, fmt(position())), h('span', {}, fmt(st.duration))));
    }
  }
  const skipLabel = S.me?.is_admin || (mine && st.settings?.owner_can_skip) ? t('ctl.skip') : voted ? t('ctl.unvote') : t('ctl.vote');
  const controls = h('div', { class: 'controls' },
    h('button', { class: 'icon-btn' + (liked ? ' on' : ''), 'aria-pressed': liked ? 'true' : 'false', 'aria-label': t('ctl.like'), onclick: () => act(() => api('POST', 'api/player/like')) },
      icon(liked ? 'heartfill' : 'heart')),
    h('span', { class: 'small muted', style: { minWidth: '14px' } }, cur.likes || ''),
    playPauseBtn(),
    h('button', { class: 'icon-btn', 'aria-label': skipLabel, title: skipLabel, onclick: () => {
      if (voted && !(S.me?.is_admin || mine)) act(() => api('DELETE', 'api/player/vote')); else act(() => api('POST', 'api/player/skip'));
    } }, icon('skip')),
    h('button', { class: 'icon-btn', 'aria-label': t('ctl.fav'), 'aria-pressed': fav ? 'true' : 'false', onclick: () => toggleFav(tr) }, icon(fav ? 'starfill' : 'star')));
  const votes = st.votes?.count ? h('div', { class: 'vote' }, h('div', { class: 'small muted', style: { marginBottom: '4px' } }, t('ctl.votes', st.votes)),
    h('div', { class: 'track-bar' }, h('div', { style: { width: Math.min(100, 100 * st.votes.count / st.votes.required) + '%' } }))) : null;
  const vol = can('user_can_volume') ? h('div', { class: 'volume' }, icon('volume'),
    h('input', { type: 'range', min: 0, max: st.settings?.max_volume ?? 100, value: st.volume, 'aria-label': t('ctl.volume'),
      onpointerdown: () => (S.dragging = true), onblur: () => (S.dragging = false),
      onchange: (e) => { S.dragging = false; act(() => api('POST', 'api/player/volume', { volume: +e.target.value })); } }),
    h('span', { class: 'small', style: { width: '28px', textAlign: 'right' } }, st.volume)) : null;
  const extra = h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '8px' } },
    st.settings?.lyrics && !tr.live ? h('button', { class: 'btn small ghost', 'aria-pressed': S.showLyrics ? 'true' : 'false', onclick: () => { S.showLyrics = !S.showLyrics; if (S.showLyrics) loadLyrics(); renderMain(); renderChrome(); } }, icon('mic'), t('now.lyrics')) : null,
    h('a', { class: 'btn small ghost', href: '#/display' }, icon('tv'), t('ctl.tv')));
  const lyr = S.showLyrics ? lyricsPanel() : null;
  return h('section', { class: 'now', 'aria-label': t('nav.now') }, cover,
    h('div', { class: 't1 ellipsis', title: tr.title }, tr.title),
    h('div', { class: 't2 ellipsis' }, cur.icy_title || tr.artist || ''), who, subst, progress, controls, votes, vol, compact ? null : extra, lyr);
}
function lyricsPanel() {
  const L = S.lyrics;
  if (!L) return h('div', { class: 'card', style: { marginTop: '14px', width: '100%' } }, h('div', { class: 'spinner', style: { margin: 'auto' } }));
  if (!L.lines?.length) return h('div', { class: 'card muted small', style: { marginTop: '14px', width: '100%' } }, t('now.no_lyrics'));
  return h('div', { class: 'card lyrics-panel', id: 'lyrics', style: { marginTop: '14px', width: '100%', maxHeight: '320px', overflow: 'auto', textAlign: 'center' } },
    ...L.lines.map((l, i) => h('p', { 'data-i': i }, l.text || '♪')), h('div', { class: 'faint small' }, 'LRCLIB'));
}
function upNext(n = 5) {
  const st = S.state; if (!st) return h('div');
  const items = (st.queue || []).slice(0, n);
  const list = h('div', { class: 'list' }, ...items.map((q) => queueRow(q, false)));
  return h('section', { 'aria-label': t('now.up_next') }, h('h2', {}, t('now.up_next')),
    items.length ? list : h('div', { class: 'muted small', style: { padding: '8px' } }, t('now.queue_empty'),
      st.autoplay?.next ? h('div', { class: 'item', style: { opacity: .75 } }, art(st.autoplay.next), h('div', { class: 'grow' },
        h('div', { class: 'title ellipsis' }, st.autoplay.next.title), h('div', { class: 'sub ellipsis' }, t('now.autoplay'), ' · ', st.autoplay.next.artist || ''))) : null),
    st.queue_total > n ? h('a', { class: 'btn small ghost', href: '#/queue' }, `+${st.queue_total - n}`) : null);
}
function viewNow() { return [...banners(), nowCard(), h('div', { class: 'only-mobile' }, upNext())]; }

async function wrongMatch(tr) {
  const key = tr.substituted_from.key;
  const r = await act(() => api('GET', 'api/match?key=' + encodeURIComponent(key)));
  if (!r) return;
  sheet(t('now.wrong_match'), (b, close) => {
    if (!r.alternatives.length) { b.append(h('p', { class: 'muted' }, t('search.none'))); return; }
    b.append(h('p', { class: 'muted small' }, tr.substituted_from.artist ? `${tr.substituted_from.artist} — ${tr.substituted_from.title}` : tr.substituted_from.title),
      h('div', { class: 'list' }, ...r.alternatives.map((a) => h('div', { class: 'item tap', tabindex: 0, role: 'button', onclick: async () => {
        await act(() => api('POST', 'api/match', { wanted_key: key, track: a }), t('toast.saved')); close();
      } }, art(a), h('div', { class: 'grow' }, h('div', { class: 'title ellipsis' }, a.title), h('div', { class: 'sub ellipsis' }, srcName(a.source), ' · ', trackSub(a)))))));
  });
}

// ------------------------------------------------------------------ search
function searchSources() { return S.sources.filter((s) => s.capabilities.includes('search')); }
let searchTimer = null;
function viewSearch() {
  const sq = S.search;
  const input = h('input', { type: 'search', id: 'q', value: sq.q, placeholder: t('search.placeholder'), 'aria-label': t('nav.search'), enterkeyhint: 'search', autocomplete: 'off',
    oninput: (e) => { sq.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 450); },
    onkeydown: (e) => { if (e.key === 'Enter') { clearTimeout(searchTimer); runSearch(); e.target.blur(); } } });
  const chips = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Sources' },
    [{ id: 'all', name: t('search.all') }, ...searchSources()].map((s) => h('button', { class: 'tab', role: 'tab', 'aria-selected': sq.source === s.id ? 'true' : 'false',
      onclick: () => { sq.source = s.id; $('#main').dataset.route = ''; renderMain(); if (sq.q) runSearch(); } }, s.name)));
  setTimeout(() => { if (!sq.q && document.activeElement?.tagName !== 'INPUT') $('#q')?.focus(); }, 30);
  return [h('div', { class: 'searchbar' }, h('div', { class: 'searchbox' }, icon('search'), input), chips), h('div', { id: 'results' }, resultsView())];
}
function resultsView() {
  const sq = S.search;
  if (!sq.q.trim()) return h('div', { class: 'empty' }, icon('search'), h('p', {}, t('search.hint')));
  const isUrl = /^(https?:\/\/|spotify:)/i.test(sq.q.trim());
  const ids = sq.source === 'all' ? S.sources.filter((s) => s.capabilities.includes(isUrl ? 'urls' : 'search') || (isUrl && s.capabilities.includes('search'))).map((s) => s.id) : [sq.source];
  const groups = [];
  for (const id of ids) {
    const r = sq.results[id];
    const pending = sq.pending.has(id);
    if (!pending && r && !r.results?.length && !r.error) continue;
    if (!pending && !r) continue;
    const limit = sq.source === 'all' ? 5 : 50;
    groups.push(h('section', { 'aria-label': srcName(id) },
      h('div', { class: 'group-h' }, h('h2', {}, srcName(id)), pending ? h('span', { class: 'spinner', 'aria-label': t('search.searching') }) :
        (sq.source === 'all' && r.results.length > limit ? h('button', { class: 'btn small ghost', onclick: () => { sq.source = id; $('#main').dataset.route = ''; renderMain(); } }, `+${r.results.length - limit}`) : null)),
      r?.error ? h('div', { class: 'banner warn small' }, icon('warn'), r.error) : null,
      h('div', { class: 'list' }, ...(r?.results || []).slice(0, limit).map(resultRow))));
  }
  if (!groups.length) return h('div', { class: 'empty' }, t('search.none'));
  return groups;
}
function rerenderResults() { const el = $('#results'); if (el) setKids(el, ...[resultsView()].flat()); }
async function runSearch() {
  const sq = S.search; const q = sq.q.trim(); if (!q) { rerenderResults(); return; }
  const seq = ++sq.seq; sq.results = {};
  const isUrl = /^(https?:\/\/|spotify:)/i.test(q);
  const ids = sq.source === 'all' ? S.sources.filter((s) => s.capabilities.includes('search') || (isUrl && s.capabilities.includes('urls'))).map((s) => s.id) : [sq.source];
  sq.pending = new Set(ids); rerenderResults();
  await Promise.all(ids.map(async (id) => {
    let r;
    try { r = await api('GET', `api/search?source=${encodeURIComponent(id)}&q=${encodeURIComponent(q)}&limit=${sq.source === 'all' ? 12 : 30}`); }
    catch (e) { r = { results: [], error: e.message }; }
    if (seq !== sq.seq) return;
    sq.results[id] = r; sq.pending.delete(id); rerenderResults();
  }));
}
function resultRow(tr) {
  const container = ['album', 'playlist', 'podcast', 'artist'].includes(tr.kind);
  const queued = (S.state?.queue || []).some((q) => q.track.key === tr.key && q.user.id === S.me.id);
  const addBtn = h('button', { class: 'add-btn' + (queued ? ' done' : ''), 'aria-label': t('search.add', { title: tr.title }), disabled: tr.blocked || tr.too_long,
    onclick: (e) => { e.stopPropagation(); addTrack(tr, addBtn); } }, icon(queued ? 'check' : 'plus'));
  const row = h('div', { class: 'item tap', tabindex: 0, role: 'button', 'aria-label': container ? t('search.open', { title: tr.title }) : t('search.add', { title: tr.title }),
    onclick: () => (container ? openContainer(tr) : !(tr.blocked || tr.too_long) && addTrack(tr, addBtn)),
    onkeydown: (e) => { if (e.key === 'Enter') row.click(); } },
  art(tr), h('div', { class: 'grow' },
    h('div', { class: 'title ellipsis' }, tr.title),
    h('div', { class: 'sub ellipsis' }, tr.kind !== 'track' ? h('span', { class: 'chip', style: { marginRight: '6px' } }, tr.kind) : null,
      tr.live ? h('span', { class: 'chip live', style: { marginRight: '6px' } }, t('now.live')) : null,
      tr.blocked ? h('span', { class: 'chip bad', style: { marginRight: '6px' } }, t('search.blocked')) : null,
      tr.too_long ? h('span', { class: 'chip warn', style: { marginRight: '6px' } }, t('search.too_long', { m: S.state?.settings?.max_track_minutes })) : null, trackSub(tr))),
  h('button', { class: 'icon-btn small', 'aria-label': 'More', onclick: (e) => { e.stopPropagation(); trackMenu(tr); } }, icon('more')),
  addBtn);
  return row;
}
async function addTrack(tr, btn) {
  if (btn) btn.disabled = true;
  const r = await act(() => api('POST', 'api/queue', { track: tr }));
  if (btn) btn.disabled = false;
  if (!r) return;
  if (btn) { btn.classList.add('done'); setKids(btn, icon('check')); }
  if (r.added === 1) toast(t('toast.added', { title: tr.title, pos: r.position || 1 }), 'success');
  else toast(t('toast.added_many', { n: r.added }), 'success');
  if (r.dropped_by_limit) toast(t('toast.limit_dropped', { n: r.dropped_by_limit }), 'error');
}
function openContainer(tr) {
  sheet(tr.title, async (b, close) => {
    b.append(h('div', { class: 'spinner', style: { margin: '20px auto' } }));
    const r = await act(() => api('POST', 'api/expand', { track: tr }));
    setKids(b, );
    if (!r) { close(); return; }
    b.append(h('div', { class: 'row', style: { marginBottom: '8px' } }, h('div', { class: 'grow muted small' }, tr.artist || ''),
      h('button', { class: 'btn primary small', onclick: async () => {
        const res = await act(() => api('POST', 'api/queue', { tracks: r.tracks.filter((x) => x.kind !== 'podcast') }));
        if (res) { toast(t('toast.added_many', { n: res.added }), 'success'); if (res.dropped_by_limit) toast(t('toast.limit_dropped', { n: res.dropped_by_limit }), 'error'); close(); }
      } }, icon('plus'), t('search.add_all', { n: r.tracks.length }))),
    h('div', { class: 'list' }, ...r.tracks.map(resultRow)));
  });
}
function trackMenu(tr) {
  sheet(tr.title, async (b, close) => {
    const fav = S.favorites.has(tr.key);
    b.append(h('div', { class: 'list' },
      h('button', { class: 'btn', onclick: () => { close(); addTrack(tr); } }, icon('plus'), t('menu.add_queue')),
      h('button', { class: 'btn', onclick: () => { close(); toggleFav(tr); } }, icon(fav ? 'starfill' : 'star'), t(fav ? 'toast.unfaved' : 'ctl.fav')),
      h('button', { class: 'btn', onclick: () => { close(); addToPlaylist(tr); } }, icon('list'), t('lib.add_to_playlist')),
      tr.url ? h('a', { class: 'btn', href: tr.url, target: '_blank', rel: 'noopener' }, icon('music'), srcName(tr.source)) : null,
      S.me.is_admin ? h('button', { class: 'btn danger', onclick: async () => { close(); await act(() => api('POST', 'api/admin/blocklist', { kind: 'track', value: tr.key }), t('toast.saved')); } }, icon('close'), t('admin.block')) : null));
  });
}
async function toggleFav(tr) {
  if (S.favorites.has(tr.key)) {
    if (await act(() => api('DELETE', 'api/favorites?key=' + encodeURIComponent(tr.key)))) { S.favorites.delete(tr.key); toast(t('toast.unfaved')); }
  } else if (await act(() => api('POST', 'api/favorites', { track: tr }))) { S.favorites.add(tr.key); toast(t('toast.faved'), 'success'); }
  renderMain(); renderChrome();
}
async function addToPlaylist(tr) {
  const lists = await act(() => api('GET', 'api/playlists')); if (!lists) return;
  sheet(t('lib.add_to_playlist'), (b, close) => {
    b.append(h('div', { class: 'list' }, ...lists.map((p) => h('button', { class: 'btn', onclick: async () => {
      close(); await act(() => api('POST', `api/playlists/${p.id}/items`, { track: tr }), t('toast.saved'));
    } }, icon('list'), p.name, p.shared ? h('span', { class: 'chip' }, t('lib.shared')) : null))),
    newPlaylistForm(async (id) => { close(); await act(() => api('POST', `api/playlists/${id}/items`, { track: tr }), t('toast.saved')); }));
  });
}

// ------------------------------------------------------------------ queue
function queueRow(q, actions = true) {
  const mine = q.user?.id === S.me?.id;
  const canEdit = mine || S.me?.is_admin;
  return h('div', { class: 'item' + (mine ? ' mine' : '') }, art(q.track),
    h('div', { class: 'grow' },
      h('div', { class: 'title ellipsis' }, q.track.title),
      h('div', { class: 'sub ellipsis' }, q.pinned ? h('span', { class: 'chip warn', style: { marginRight: '6px' } }, t('queue.pinned')) : null,
        [q.track.artist, q.track.duration ? fmt(q.track.duration) : null].filter(Boolean).join(' · '), ' · ', t('lib.added_by', { name: q.user?.name || '?' }))),
    actions && canEdit ? h('button', { class: 'icon-btn small', 'aria-label': 'More', onclick: () => queueMenu(q) }, icon('more')) : null,
    actions && canEdit ? h('button', { class: 'icon-btn small', 'aria-label': t('queue.remove'), onclick: () => act(() => api('DELETE', 'api/queue/' + q.id)) }, icon('close')) : null);
}
function queueMenu(q) {
  const mv = (a) => act(() => api('POST', `api/queue/${q.id}/move`, { action: a }));
  sheet(q.track.title, (b, close) => b.append(h('div', { class: 'list' },
    h('button', { class: 'btn', onclick: () => { close(); mv('top'); } }, icon('top'), t('queue.top')),
    h('button', { class: 'btn', onclick: () => { close(); mv('up'); } }, icon('up'), t('queue.up')),
    h('button', { class: 'btn', onclick: () => { close(); mv('down'); } }, icon('down'), t('queue.down')),
    q.pinned && S.me.is_admin ? h('button', { class: 'btn', onclick: () => { close(); mv('unpin'); } }, t('queue.unpin')) : null,
    h('button', { class: 'btn danger', onclick: () => { close(); act(() => api('DELETE', 'api/queue/' + q.id)); } }, icon('trash'), t('queue.remove')))));
}
function viewQueue() {
  const st = S.state; if (!st) return h('div');
  const total = (st.queue || []).reduce((a, q) => a + (q.track.duration || 0), 0);
  const mineCount = (st.queue || []).filter((q) => q.user?.id === S.me.id).length;
  return [
    h('div', { class: 'row' }, h('h1', { class: 'grow' }, t('queue.title')),
      mineCount ? h('button', { class: 'btn small', onclick: () => confirmSheet(t('queue.clear_mine') + '?', () => act(() => api('DELETE', 'api/queue?mine=true'))) }, t('queue.clear_mine')) : null,
      S.me.is_admin && st.queue_total ? h('button', { class: 'btn small danger', onclick: () => confirmSheet(t('admin.clear_queue') + '?', () => act(() => api('DELETE', 'api/queue?mine=false'))) }, t('admin.clear_queue')) : null),
    h('div', { class: 'muted small', style: { marginBottom: '8px' } }, t('queue.total', { n: st.queue_total, time: fmtLong(total) }),
      st.settings?.fairness_enabled ? ' · ' + t('queue.fair') : ''),
    ...banners(),
    st.queue?.length ? h('div', { class: 'list' }, ...st.queue.map((q) => queueRow(q))) :
      h('div', { class: 'empty' }, icon('queue'), h('p', {}, t('queue.empty')), h('a', { class: 'btn primary', href: '#/search' }, icon('search'), t('nav.search'))),
  ];
}

// ------------------------------------------------------------------ library
function viewLibrary() {
  const tabs = [['history', t('lib.history')], ['favorites', t('lib.favorites')], ['playlists', t('lib.playlists')], ['stats', t('lib.stats')]];
  const body = h('div', { id: 'libbody' }, h('div', { class: 'spinner', style: { margin: '30px auto' } }));
  loadLib(body);
  return [h('h1', {}, t('nav.library')),
    h('div', { class: 'tabs', role: 'tablist' }, ...tabs.map(([k, label]) => h('button', { class: 'tab', role: 'tab', 'aria-selected': S.libTab === k ? 'true' : 'false',
      onclick: () => { S.libTab = k; renderMain(); } }, label))), body];
}
function simpleRow(tr, sub, extraBtns = []) {
  const addBtn = h('button', { class: 'add-btn', 'aria-label': t('search.add', { title: tr.title }), onclick: () => addTrack(tr, addBtn) }, icon('plus'));
  return h('div', { class: 'item' }, art(tr), h('div', { class: 'grow' }, h('div', { class: 'title ellipsis' }, tr.title), h('div', { class: 'sub ellipsis' }, sub)), ...extraBtns, addBtn);
}
async function loadLib(body) {
  const fill = (...els) => setKids(body, ...els.flat());
  try {
    if (S.libTab === 'history') {
      const mine = store.get('histMine') === '1';
      const rows = await api('GET', 'api/history?limit=80' + (mine ? '&mine=true' : ''));
      const toggle = h('label', { class: 'switch' }, h('span', {}, t('lib.mine_only')), h('input', { type: 'checkbox', checked: mine, onchange: (e) => { store.set('histMine', e.target.checked ? '1' : '0'); loadLib(body); } }));
      fill(toggle, rows.length ? h('div', { class: 'list' }, ...rows.map((r) => simpleRow(r.track, [
        r.outcome && r.outcome !== 'played' ? h('span', { class: 'chip ' + (r.outcome === 'error' ? 'bad' : 'warn'), style: { marginRight: '6px' }, title: r.error || '' }, t('lib.outcome.' + r.outcome)) : null,
        srcName(r.track.source), ' · ', r.autoplay ? t('now.autoplay') + ' · ' : r.user ? t('lib.added_by', { name: r.user }) + ' · ' : '',
        new Date(r.started_at * 1000).toLocaleTimeString(getLang(), { hour: '2-digit', minute: '2-digit' })]))) : h('div', { class: 'empty' }, t('lib.empty')));
    } else if (S.libTab === 'favorites') {
      const rows = await api('GET', 'api/favorites');
      fill(rows.length ? h('div', { class: 'list' }, ...rows.map((tr) => simpleRow(tr, [srcName(tr.source), ' · ', trackSub(tr)],
        [h('button', { class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: async () => { await toggleFav(tr); loadLib(body); } }, icon('starfill'))]))) : h('div', { class: 'empty' }, icon('star'), h('p', {}, t('lib.empty'))));
    } else if (S.libTab === 'playlists') {
      const lists = await api('GET', 'api/playlists');
      fill(h('div', { class: 'list' }, ...lists.map((p) => h('div', { class: 'item tap', tabindex: 0, role: 'button', onclick: () => openPlaylist(p.id, () => loadLib(body)) },
        h('div', { class: 'art' }, icon('list')), h('div', { class: 'grow' }, h('div', { class: 'title ellipsis' }, p.name),
          h('div', { class: 'sub' }, t('search.tracks', { n: p.count }), ' · ', p.shared ? t('lib.shared') : p.owner))))),
      newPlaylistForm(() => loadLib(body)));
    } else {
      const s = await api('GET', 'api/stats/me');
      const stat = (label, v) => h('div', { class: 'card', style: { textAlign: 'center' } }, h('div', { style: { fontSize: '26px', fontWeight: 800 } }, v ?? 0), h('div', { class: 'small muted' }, label));
      fill(h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' } },
        stat(t('stats.added'), s.added), stat(t('stats.played'), s.played), stat(t('stats.skipped'), s.skipped), stat(t('stats.likes'), s.likes_received)),
      h('h2', {}, t('stats.top')), h('div', { class: 'list' }, ...s.top_artists.map((a) => h('div', { class: 'item' }, avatar(a.artist, 40), h('div', { class: 'grow' }, a.artist), h('span', { class: 'chip' }, a.n)))));
    }
  } catch (e) { fill(h('div', { class: 'banner bad' }, e.message)); }
}
function newPlaylistForm(onCreated) {
  const name = h('input', { type: 'text', placeholder: t('lib.playlist_name'), maxlength: 80, 'aria-label': t('lib.playlist_name') });
  const shared = h('input', { type: 'checkbox' });
  return h('form', { class: 'card', style: { marginTop: '14px' }, onsubmit: async (e) => {
    e.preventDefault(); if (!name.value.trim()) return;
    const r = await act(() => api('POST', 'api/playlists', { name: name.value.trim(), shared: shared.checked }));
    if (r) { name.value = ''; onCreated(r.id); }
  } }, h('div', { style: { fontWeight: 650, marginBottom: '8px' } }, t('lib.new_playlist')), name,
  h('label', { class: 'switch' }, h('span', {}, t('lib.shared')), shared), h('button', { class: 'btn primary', type: 'submit', style: { marginTop: '10px' } }, icon('plus'), t('lib.new_playlist')));
}
async function openPlaylist(id, onChange) {
  const p = await act(() => api('GET', 'api/playlists/' + id)); if (!p) return;
  sheet(p.name, (b, close) => {
    b.append(h('div', { class: 'row wrap', style: { marginBottom: '10px' } },
      h('button', { class: 'btn primary small', disabled: !p.items.length, onclick: async () => { const r = await act(() => api('POST', `api/playlists/${id}/enqueue`)); if (r) { toast(t('toast.added_many', { n: r.added }), 'success'); close(); } } }, icon('plus'), t('lib.play_all')),
      h('button', { class: 'btn small', disabled: !p.items.length, onclick: async () => { const r = await act(() => api('POST', `api/playlists/${id}/enqueue?shuffle=true`)); if (r) { toast(t('toast.added_many', { n: r.added }), 'success'); close(); } } }, icon('shuffle'), t('lib.shuffle')),
      p.mine || S.me.is_admin ? h('button', { class: 'btn small danger', onclick: () => confirmSheet(t('lib.delete') + ' “' + p.name + '”?', async () => { await act(() => api('DELETE', 'api/playlists/' + id)); close(); onChange(); }) }, icon('trash'), t('lib.delete')) : null),
    p.items.length ? h('div', { class: 'list' }, ...p.items.map((it) => simpleRow(it.track, trackSub(it.track),
      [h('button', { class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: async () => { await act(() => api('DELETE', `api/playlists/${id}/items/${it.id}`)); close(); openPlaylist(id, onChange); } }, icon('close'))]))) :
      h('div', { class: 'empty' }, t('lib.empty')));
  });
}

// ------------------------------------------------------------------ user menu
function userMenu() {
  sheet(S.me.name, (b, close) => {
    const name = h('input', { type: 'text', value: S.me.name, maxlength: 32, 'aria-label': t('me.rename') });
    const theme = h('select', { 'aria-label': t('me.theme'), onchange: (e) => { store.set('theme', e.target.value); if (e.target.value === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = e.target.value; } },
      ...['auto', 'dark', 'light'].map((v) => h('option', { value: v, selected: (store.get('theme', 'auto') === v) }, t('me.theme.' + v))));
    const pw = h('input', { type: 'password', placeholder: t('me.password'), 'aria-label': t('me.password'), autocomplete: 'current-password' });
    b.append(
      S.me.kind === 'guest' ? h('form', { class: 'row', onsubmit: async (e) => { e.preventDefault(); if (await act(() => api('POST', 'api/session/rename', { name: name.value.trim() }), t('toast.saved'))) { S.me.name = name.value.trim(); store.set('name', S.me.name); renderChrome(); } } },
        name, h('button', { class: 'btn', type: 'submit' }, t('me.rename'))) : h('p', { class: 'muted small' }, S.me.name),
      h('label', { class: 'field' }, h('span', {}, t('me.language')), langSelect()),
      h('label', { class: 'field' }, h('span', {}, t('me.theme')), theme),
      S.me.is_admin ? (S.me.kind === 'guest' ? h('button', { class: 'btn', style: { width: '100%', marginTop: '8px' }, onclick: async () => { await act(() => api('POST', 'api/session/admin/logout')); close(); await refreshMe(); } }, t('me.admin_logout')) : null) :
        S.me.kind === 'guest' ? h('form', { class: 'row', style: { marginTop: '8px' }, onsubmit: async (e) => { e.preventDefault(); if (await act(() => api('POST', 'api/session/admin', { password: pw.value }))) { close(); await refreshMe(); location.hash = '#/admin'; } } },
          pw, h('button', { class: 'btn', type: 'submit' }, t('me.admin_login'))) : null,
      h('div', { class: 'row wrap', style: { marginTop: '14px' } },
        h('a', { class: 'btn small', href: '#/display', onclick: close }, icon('tv'), t('ctl.tv')),
        h('a', { class: 'btn small', href: 'api/docs', target: '_blank' }, t('me.api')),
        S.me.can_logout ? h('button', { class: 'btn small danger', onclick: async () => { await act(() => api('POST', 'api/session/logout')); location.reload(); } }, t('me.logout')) : null));
  });
}
async function refreshMe() { const r = await api('GET', 'api/me'); S.me = r.user; shellBuilt = false; ws?.close(); render(); }

// ------------------------------------------------------------------ admin
const SETTING_META = {
  fairness_enabled: 'bool', max_queued_per_user: 'int', max_track_minutes: 'int', owner_can_skip: 'bool',
  skip_mode: ['percent', 'count'], skip_value: 'int', max_volume: 'int', volume_cooldown_s: 'int', user_can_pause: 'bool',
  user_can_seek: 'bool', user_can_volume: 'bool', autoplay_mode: ['similar', 'local_shuffle', 'history', 'playlist', 'radio', 'off'],
  autoplay_yield: 'bool', normalize: 'bool', target_lufs: 'float', stream_gain_db: 'float', sponsorblock: 'bool', lyrics: 'bool',
  substitute_unavailable: 'bool', public_display: 'bool', allow_private_urls: 'bool', language: ['en', 'es'], prefetch_count: 'int',
};
function settingInput(key, value, type) {
  if (type === 'bool') return h('label', { class: 'switch' }, h('span', {}, t('set.' + key)), h('input', { type: 'checkbox', 'data-key': key, 'data-type': 'bool', checked: !!value }));
  if (Array.isArray(type)) return h('label', { class: 'field' }, h('span', {}, t('set.' + key)), h('select', { 'data-key': key, 'data-type': 'str' },
    ...type.map((o) => h('option', { value: o, selected: o === value }, key === 'autoplay_mode' ? t('auto.' + o) : o))));
  return h('label', { class: 'field' }, h('span', {}, t('set.' + key)), h('input', { type: 'number', step: type === 'float' ? '0.5' : '1', value: value ?? '', 'data-key': key, 'data-type': type }));
}
function collect(root) {
  const out = {};
  root.querySelectorAll('[data-key]').forEach((el) => {
    const k = el.dataset.key, ty = el.dataset.type;
    if (ty === 'bool') out[k] = el.checked; else if (ty === 'int') out[k] = parseInt(el.value || '0', 10);
    else if (ty === 'float') out[k] = parseFloat(el.value || '0'); else out[k] = el.value;
  });
  return out;
}
function section(title, iconName, build, open = false) {
  const body = h('div', { class: 'body' });
  const d = h('details', { class: 'card', open }, h('summary', {}, icon(iconName), title), body);
  let loaded = false;
  const load = () => { if (!loaded) { loaded = true; build(body); } };
  d.addEventListener('toggle', () => d.open && load());
  if (open) load();
  return d;
}
function statusChip(s) { const cls = { connected: 'ok', needs_config: 'warn', disabled: '', broken: 'bad', rate_limited: 'warn', offline: 'bad', planned: '' }[s] ?? ''; return h('span', { class: 'chip ' + cls }, t('status.' + s)); }

function viewAdmin() {
  const st = S.state;
  const quick = h('div', { class: 'row wrap', style: { marginBottom: '12px' } },
    h('button', { class: 'btn ' + (st?.muted ? 'primary' : 'danger'), onclick: () => act(() => api('POST', 'api/admin/panic', { on: !st?.muted })) }, icon(st?.muted ? 'play' : 'pause'), st?.muted ? t('admin.unpanic') : t('admin.panic')),
    h('button', { class: 'btn', disabled: !st?.now, onclick: () => act(() => api('POST', 'api/player/skip')) }, icon('skip'), t('admin.force_skip')),
    h('button', { class: 'btn', onclick: () => act(() => api('POST', 'api/admin/test-tone')) }, icon('volume'), t('admin.test_tone')));
  return [h('h1', {}, t('nav.admin')), ...banners(), quick, h('div', { class: 'admin-grid' },
    section(t('admin.playback'), 'admin', adminSettings, true),
    section(t('admin.sources'), 'music', adminSources),
    section(t('admin.audio'), 'volume', adminAudio),
    section(t('admin.schedule'), 'refresh', adminSchedule),
    section(t('admin.stations'), 'radio', adminStations),
    section(t('admin.block'), 'close', adminBlocklist),
    section(t('admin.users'), 'user', adminUsers),
    section(t('admin.tokens'), 'list', adminTokens),
    section(t('admin.logs'), 'lyrics', adminLogs))];
}
async function adminSettings(body) {
  const r = await act(() => api('GET', 'api/admin/settings')); if (!r) return;
  const s = r.settings; const form = h('form');
  const lists = await api('GET', 'api/playlists').catch(() => []);
  for (const [k, ty] of Object.entries(SETTING_META)) form.append(settingInput(k, s[k]?.value, ty));
  form.append(h('label', { class: 'field' }, h('span', {}, t('set.autoplay_playlist_id')), h('select', { 'data-key': 'autoplay_playlist_id', 'data-type': 'int' },
    h('option', { value: '' }, '—'), ...lists.map((p) => h('option', { value: p.id, selected: s.autoplay_playlist_id?.value === p.id }, p.name)))));
  form.append(h('button', { class: 'btn primary', type: 'submit', style: { marginTop: '12px' } }, t('admin.save')));
  form.onsubmit = async (e) => { e.preventDefault(); const v = collect(form); if (!v.autoplay_playlist_id) v.autoplay_playlist_id = null; await act(() => api('PUT', 'api/admin/settings', { values: v }), t('admin.saved')); };
  setKids(body, form);
}
async function adminSources(body) {
  const list = await act(() => api('GET', 'api/admin/providers')); if (!list) return;
  setKids(body, ...list.map((p) => {
    const form = h('form');
    for (const f of p.fields) {
      const v = p.config[f.key];
      if (f.type === 'bool') form.append(h('label', { class: 'switch' }, h('span', {}, f.label), h('input', { type: 'checkbox', 'data-key': f.key, 'data-type': 'bool', checked: !!v })));
      else if (f.type === 'textarea') form.append(h('label', { class: 'field' }, h('span', {}, f.label), h('textarea', { 'data-key': f.key, 'data-type': 'str', placeholder: v === '***' ? '•••• (saved)' : '' })), f.help ? h('div', { class: 'small faint' }, f.help) : null);
      else form.append(h('label', { class: 'field' }, h('span', {}, f.label), h('input', { type: f.type === 'secret' ? 'password' : f.type === 'number' ? 'number' : 'text', 'data-key': f.key, 'data-type': f.type === 'number' ? 'float' : 'str', value: f.type === 'secret' ? '' : (v ?? ''), placeholder: v === '***' ? '•••• (saved)' : '' })), f.help ? h('div', { class: 'small faint' }, f.help) : null);
    }
    const result = h('div', { class: 'small', role: 'status' });
    const submit = async (e) => {
      e.preventDefault(); const vals = collect(form);
      for (const f of p.fields) if ((f.type === 'secret' || f.key.includes('cookie')) && !vals[f.key]) delete vals[f.key];
      if (await act(() => api('PUT', 'api/admin/providers/' + p.id, { values: vals }), t('admin.saved'))) { S.sources = await api('GET', 'api/sources'); adminSources(body); }
    };
    if (p.fields.length) form.append(h('div', { class: 'row', style: { marginTop: '10px' } }, h('button', { class: 'btn primary small', type: 'submit' }, t('admin.save')),
      h('button', { class: 'btn small', type: 'button', onclick: async () => { result.textContent = '…'; const r = await act(() => api('POST', `api/admin/providers/${p.id}/test`)); if (r) { result.textContent = r.message; result.style.color = r.ok ? 'var(--good)' : 'var(--bad)'; } } }, t('admin.test')), result));
    form.onsubmit = submit;
    return h('div', { class: 'src-card' },
      h('div', { class: 'row' }, h('strong', { class: 'grow' }, p.name), h('span', { class: 'chip ' + { low: 'ok', medium: 'warn', high: 'bad' }[p.risk] }, t('admin.risk', { r: p.risk })), statusChip(p.status)),
      h('div', { class: 'small muted', style: { margin: '6px 0' } }, p.description), p.detail ? h('div', { class: 'small faint' }, p.detail) : null,
      h('details', { style: { margin: '6px 0' } }, h('summary', { class: 'small', style: { cursor: 'pointer' } }, t('admin.legal')),
        h('div', { class: 'small muted' }, h('p', {}, h('b', {}, t('admin.account') + ': '), p.account), h('p', {}, p.legal))), form);
  }));
}
async function adminAudio(body) {
  const d = await act(() => api('GET', 'api/admin/diagnostics')); if (!d) return;
  const a = d.audio;
  const options = new Map();
  for (const dev of a.mpv_devices) options.set(dev.name.replace(/^alsa\//, ''), dev.description);
  for (const c of a.alsa_cards) options.set(c.suggested_device, `${c.name} (${c.id})`);
  const custom = h('input', { type: 'text', value: a.configured_device, 'aria-label': t('admin.device'), list: 'devlist' });
  const kv = (obj) => h('div', { class: 'kv' }, ...Object.entries(obj).flatMap(([k, v]) => [h('div', {}, k), h('div', { style: { wordBreak: 'break-all' } }, typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—'))]));
  setKids(body, 
    h('label', { class: 'field' }, h('span', {}, t('admin.device')), custom, h('datalist', { id: 'devlist' }, ...[...options].map(([v, label]) => h('option', { value: v }, label)))),
    h('div', { class: 'small faint' }, 'env AUDIO_DEVICE = ' + a.env_device),
    h('div', { class: 'row wrap', style: { margin: '10px 0' } },
      h('button', { class: 'btn primary small', onclick: async () => { await act(() => api('POST', 'api/admin/audio-device', { device: custom.value.trim() || null }), t('admin.saved')); setTimeout(() => adminAudio(body), 1500); } }, t('admin.apply')),
      h('button', { class: 'btn small', onclick: () => act(() => api('POST', 'api/admin/test-tone')) }, t('admin.test_tone')),
      h('button', { class: 'btn small', onclick: async () => { toast('…'); const r = await act(() => api('POST', 'api/admin/ytdlp/update')); if (r) toast('yt-dlp ' + r.result, r.result.startsWith('ok') ? 'success' : 'error'); adminAudio(body); } }, t('admin.ytdlp_update')),
      h('button', { class: 'btn small', onclick: () => act(() => api('POST', 'api/admin/library/rescan'), t('admin.saved')) }, t('admin.rescan')),
      h('button', { class: 'btn small', onclick: () => confirmSheet(t('admin.clear_cache') + '?', () => act(() => api('POST', 'api/admin/cache/clear'), t('admin.saved'))) }, t('admin.clear_cache')),
      h('button', { class: 'icon-btn small', 'aria-label': 'Refresh', onclick: () => adminAudio(body) }, icon('refresh'))),
    h('h2', {}, 'Audio'), kv({ device: a.configured_device, engine: a.engine_alive ? 'running' : 'down', restarts: a.engine_restarts, output: a.current_ao, fault: a.fault, '/dev/snd': a.dev_snd.present ? (a.dev_snd.writable ? 'ok (writable)' : 'present, NOT writable — check group_add') : 'missing — map /dev/snd', groups: (a.dev_snd.groups || []).join(','), mpv: a.mpv_version }),
    a.alsa_cards.length ? h('div', { class: 'small', style: { marginTop: '8px' } }, ...a.alsa_cards.map((c) => h('div', {}, `card ${c.index}: ${c.id} — ${c.name} → `, h('code', {}, c.suggested_device)))) : null,
    a.mpv_log.length ? h('pre', { class: 'logs' }, a.mpv_log.join('\n')) : null,
    h('h2', {}, 'yt-dlp'), kv(d.ytdlp), h('h2', {}, 'Storage'), kv(d.storage), h('h2', {}, 'Library'), kv({ path: d.library.path, mounted: d.library.mounted, tracks: d.library.tracks, last_scan: d.library.last_scan }),
    h('h2', {}, 'System'), kv({ version: d.version, uptime: fmt(d.uptime_s), python: d.python, load: d.load.map((x) => x.toFixed(2)).join(' '), clients: d.clients, listeners: d.listeners, base_path: d.proxy.base_path, auth_header: d.proxy.auth_header, trusted_proxies: d.proxy.trusted_proxies.join(', ') }),
    h('h2', {}, t('admin.sources')), h('div', { class: 'list' }, ...d.providers.map((p) => h('div', { class: 'row small', style: { padding: '4px 0' } }, h('span', { class: 'grow' }, p.name), h('span', { class: 'faint ellipsis', style: { maxWidth: '45%' } }, p.detail || ''), statusChip(p.status)))),
    h('h2', {}, t('admin.password')), passwordForm());
}
function passwordForm() {
  const pw = h('input', { type: 'password', minlength: 8, autocomplete: 'new-password', 'aria-label': t('admin.password') });
  return h('form', { class: 'row', onsubmit: async (e) => { e.preventDefault(); if (await act(() => api('POST', 'api/admin/password', { password: pw.value }), t('admin.saved'))) pw.value = ''; } }, pw, h('button', { class: 'btn', type: 'submit' }, t('admin.save')));
}
async function adminSchedule(body) {
  const r = await act(() => api('GET', 'api/admin/settings')); if (!r) return;
  let windows = JSON.parse(JSON.stringify(r.settings.schedule.value || []));
  const enabled = h('input', { type: 'checkbox', checked: !!r.settings.schedule_enabled.value });
  const days = t('admin.days').split(',');
  const list = h('div');
  const draw = () => setKids(list, ...windows.map((w, i) => h('div', { class: 'src-card' },
    h('div', { class: 'tabs' }, ...days.map((d, di) => h('button', { type: 'button', class: 'tab', 'aria-pressed': w.days.includes(di) ? 'true' : 'false',
      onclick: () => { w.days = w.days.includes(di) ? w.days.filter((x) => x !== di) : [...w.days, di].sort(); draw(); } }, d))),
    h('div', { class: 'row' }, h('input', { type: 'time', value: w.start, 'aria-label': 'start', onchange: (e) => (w.start = e.target.value) }), '–',
      h('input', { type: 'time', value: w.end, 'aria-label': 'end', onchange: (e) => (w.end = e.target.value) }),
      h('button', { type: 'button', class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: () => { windows.splice(i, 1); draw(); } }, icon('trash'))))));
  draw();
  setKids(body, h('label', { class: 'switch' }, h('span', {}, t('set.schedule_enabled')), enabled), h('p', { class: 'small muted' }, t('admin.windows')), list,
    h('div', { class: 'row' }, h('button', { class: 'btn small', onclick: () => { windows.push({ days: [0, 1, 2, 3, 4], start: '09:00', end: '18:00' }); draw(); } }, icon('plus'), t('admin.add_window')),
      h('button', { class: 'btn primary small', onclick: () => act(() => api('PUT', 'api/admin/settings', { values: { schedule: windows, schedule_enabled: enabled.checked } }), t('admin.saved')) }, t('admin.save'))));
}
async function adminStations(body) {
  const rows = await act(() => api('GET', 'api/admin/stations')); if (!rows) return;
  const name = h('input', { type: 'text', placeholder: 'Name', 'aria-label': 'Name' }), url = h('input', { type: 'url', placeholder: 'https://…', 'aria-label': 'URL' });
  const autoBtn = (s) => h('button', { class: 'btn small', onclick: () => act(() => api('PUT', 'api/admin/settings', { values: { autoplay_mode: 'radio', autoplay_station: { source: 'radio', id: 'custom:' + s.id, kind: 'station', title: s.name, live: true } } }), t('admin.saved')) }, t('auto.radio'));
  setKids(body, h('div', { class: 'list' }, ...rows.map((s) => h('div', { class: 'item' }, h('div', { class: 'art' }, icon('radio')), h('div', { class: 'grow' }, h('div', { class: 'title' }, s.name), h('div', { class: 'sub ellipsis' }, s.url)),
    autoBtn(s), h('button', { class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: async () => { await act(() => api('DELETE', 'api/admin/stations/' + s.id)); adminStations(body); } }, icon('trash'))))),
  h('form', { class: 'row wrap', style: { marginTop: '8px' }, onsubmit: async (e) => { e.preventDefault(); if (await act(() => api('POST', 'api/admin/stations', { name: name.value, url: url.value }))) adminStations(body); } },
    name, url, h('button', { class: 'btn', type: 'submit' }, icon('plus'), t('admin.add_station'))));
}
async function adminBlocklist(body) {
  const rows = await act(() => api('GET', 'api/admin/blocklist')); if (!rows) return;
  const kind = h('select', { 'aria-label': 'Type', style: { width: 'auto' } }, ...['artist', 'keyword', 'track'].map((k) => h('option', { value: k }, k)));
  const value = h('input', { type: 'text', placeholder: 'e.g. Baby Shark', 'aria-label': 'Value' });
  setKids(body, h('div', { class: 'list' }, ...rows.map((r) => h('div', { class: 'row', style: { padding: '6px 0' } }, h('span', { class: 'chip' }, r.kind), h('span', { class: 'grow ellipsis' }, r.value),
    h('button', { class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: async () => { await act(() => api('DELETE', 'api/admin/blocklist/' + r.id)); adminBlocklist(body); } }, icon('trash'))))),
  h('form', { class: 'row', style: { marginTop: '8px' }, onsubmit: async (e) => { e.preventDefault(); if (await act(() => api('POST', 'api/admin/blocklist', { kind: kind.value, value: value.value }))) adminBlocklist(body); } },
    kind, value, h('button', { class: 'btn', type: 'submit' }, t('admin.add_rule'))));
}
async function adminUsers(body) {
  const rows = await act(() => api('GET', 'api/admin/users')); if (!rows) return;
  const patch = async (id, v) => { await act(() => api('PATCH', 'api/admin/users/' + id, v)); adminUsers(body); };
  setKids(body, h('div', { class: 'list' }, ...rows.map((u) => h('div', { class: 'item' }, avatar(u.name, 36),
    h('div', { class: 'grow' }, h('div', { class: 'title ellipsis' }, u.name, ' ', u.online ? h('span', { class: 'chip ok' }, t('admin.online')) : null, u.role === 'admin' ? h('span', { class: 'chip warn' }, 'admin') : null, u.banned ? h('span', { class: 'chip bad' }, 'banned') : null),
      h('div', { class: 'sub' }, `${u.kind} · ${u.queued} queued · ${u.played} played`)),
    h('button', { class: 'btn small', onclick: () => patch(u.id, { role: u.role === 'admin' ? 'user' : 'admin' }) }, u.role === 'admin' ? t('admin.remove_admin') : t('admin.make_admin')),
    h('button', { class: 'btn small ' + (u.banned ? '' : 'danger'), onclick: () => patch(u.id, { banned: !u.banned }) }, u.banned ? t('admin.unban') : t('admin.ban'))))));
}
async function adminTokens(body) {
  const rows = await act(() => api('GET', 'api/admin/tokens')); if (!rows) return;
  const name = h('input', { type: 'text', placeholder: 'Slack bot', 'aria-label': 'Name' }); const adm = h('input', { type: 'checkbox' });
  setKids(body, h('p', { class: 'small muted' }, 'Authorization: Bearer <token> · ', h('a', { href: 'api/docs', target: '_blank' }, t('me.api'))),
    h('div', { class: 'list' }, ...rows.map((r) => h('div', { class: 'row', style: { padding: '6px 0' } }, h('span', { class: 'grow' }, r.name, r.is_admin ? ' (admin)' : ''),
      h('span', { class: 'small faint' }, r.last_used ? new Date(r.last_used * 1000).toLocaleString() : '—'),
      h('button', { class: 'icon-btn small', 'aria-label': t('common.remove'), onclick: async () => { await act(() => api('DELETE', 'api/admin/tokens/' + r.id)); adminTokens(body); } }, icon('trash'))))),
    h('form', { class: 'row wrap', onsubmit: async (e) => {
      e.preventDefault(); const r = await act(() => api('POST', 'api/admin/tokens', { name: name.value, admin: adm.checked })); if (!r) return;
      sheet(t('admin.new_token'), (b) => b.append(h('p', {}, t('admin.token_once')), h('pre', { class: 'logs', style: { userSelect: 'all' } }, r.token),
        h('button', { class: 'btn', onclick: () => { navigator.clipboard?.writeText(r.token); toast(t('toast.copied')); } }, t('common.copy'))));
      adminTokens(body);
    } }, name, h('label', { class: 'row small' }, adm, 'admin'), h('button', { class: 'btn', type: 'submit' }, t('admin.new_token'))));
}
async function adminLogs(body) {
  const level = h('select', { style: { width: 'auto' }, 'aria-label': 'Level', onchange: () => load() }, ...['INFO', 'WARNING', 'ERROR'].map((l) => h('option', { value: l }, l)));
  const pre = h('pre', { class: 'logs' });
  const load = async () => {
    const rows = await act(() => api('GET', 'api/admin/logs?limit=300&level=' + level.value)); if (!rows) return;
    pre.textContent = rows.map((r) => { const { ts, level: lv, logger, msg, ...rest } = r; return `${new Date(ts * 1000).toLocaleTimeString()} ${lv.padEnd(7)} ${logger.replace('rolola.', '')}: ${msg}${Object.keys(rest).length ? ' ' + JSON.stringify(rest) : ''}`; }).reverse().join('\n');
  };
  setKids(body, h('div', { class: 'row', style: { marginBottom: '8px' } }, level, h('button', { class: 'icon-btn small', 'aria-label': 'Refresh', onclick: load }, icon('refresh'))), pre);
  load();
}

// ------------------------------------------------------------------ display / TV
function renderDisplay(app) {
  const st = S.state; const cur = st?.now;
  const next = (st?.queue || []).slice(0, 3);
  const lines = S.lyrics?.synced ? S.lyrics.lines : null;
  const joinUrl = location.href.replace(/#.*$/, '');
  app.append(h('div', { class: 'display', role: 'region', 'aria-label': t('ctl.tv') },
    h('div', { class: 'bgart', style: { backgroundImage: cur?.track?.artwork ? `url("${cur.track.artwork}")` : 'none' } }),
    h('a', { class: 'exit icon-btn', href: '#/now', 'aria-label': t('common.close') }, icon('close')),
    h('div', { class: 'cover' }, cur?.track?.artwork ? h('img', { src: cur.track.artwork, alt: '', referrerpolicy: 'no-referrer' }) : null),
    h('div', {},
      h('div', { class: 'd-title' }, cur ? cur.track.title : t('now.nothing')),
      h('div', { class: 'd-artist' }, cur ? (cur.icy_title || cur.track.artist || '') : t('now.nothing_hint')),
      cur ? h('div', { class: 'd-who' }, cur.autoplay ? t('now.autoplay') : cur.user ? t('now.added_by', { name: cur.user.name }) : '', cur.likes ? `  ·  ❤ ${cur.likes}` : '',
        st.votes?.count ? `  ·  ${t('ctl.votes', st.votes)}` : '') : null,
      cur && st.duration ? h('div', { class: 'track-bar' }, h('div', { id: 'pbar' })) : null,
      lines ? h('div', { class: 'lyrics', id: 'dlyrics' }, ...lines.map((l, i) => h('p', { 'data-i': i }, l.text || '♪'))) : null,
      next.length ? h('div', { class: 'next' }, h('b', {}, t('now.up_next') + ': '), next.map((q) => `${q.track.title} (${q.user?.name})`).join('  ·  ')) : null,
      h('div', { class: 'next', style: { opacity: .45 } }, t('display.scan', { url: joinUrl }))),
  ));
  if (st?.settings?.lyrics && cur && !S.lyrics) loadLyrics();
}

// ------------------------------------------------------------------ animation loop (progress + lyrics)
function tick() {
  const st = S.state;
  if (st?.now) {
    const p = position();
    const pct = st.duration ? Math.min(100, 100 * p / st.duration) + '%' : '0%';
    document.querySelectorAll('#pbar, #minibar').forEach((el) => (el.style.width = pct));
    const tp = $('#tpos'); if (tp) tp.textContent = fmt(p);
    const seek = $('#seek'); if (seek && !S.dragging) seek.value = Math.floor(p);
    const box = $('#lyrics') || $('#dlyrics');
    if (box && S.lyrics?.synced) {
      let idx = -1; S.lyrics.lines.forEach((l, i) => { if (l.t <= p + 0.3) idx = i; });
      if (box.dataset.idx !== String(idx)) {
        box.dataset.idx = idx;
        box.querySelectorAll('p.on').forEach((el) => el.classList.remove('on'));
        const on = box.querySelector(`p[data-i="${idx}"]`);
        if (on) { on.classList.add('on'); box.scrollTo({ top: on.offsetTop - box.clientHeight / 2 + on.clientHeight, behavior: 'smooth' }); }
      }
    }
  }
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------ boot
async function boot() {
  setLang(store.get('lang') || (navigator.language || 'en').slice(0, 2));
  route();
  try {
    const r = await api('GET', 'api/me');
    S.me = r.user; S.proxyAuth = r.proxy_auth;
  } catch (e) { toast(e.message, 'error'); }
  if (S.me || S.route === 'display') await boot2(); else render();
  requestAnimationFrame(tick);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}
async function boot2() {
  try { S.sources = S.me ? await api('GET', 'api/sources') : []; } catch {}
  try { if (S.me) (await api('GET', 'api/favorites')).forEach((f) => S.favorites.add(f.key)); } catch {}
  try { onState(await api('GET', 'api/state')); } catch {}
  route();
  if (!ws || ws.readyState > 1) connect();
}
boot();
