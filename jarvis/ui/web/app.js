/* ══════════════════════════════════════════════════════════════
   J.A.R.V.I.S. interface logic

   The reactor is the state display. Rather than a spinner that means
   "busy", each state gets its own motion, so what he is doing is
   readable from across the room:

     idle       slow breathing
     listening  rings ripple to the live microphone level
     thinking   fast counter-rotation, arcs tighten
     tool       segmented ring stepping round like a dial
     speaking   pulses on the actual output envelope, in gold
   ══════════════════════════════════════════════════════════════ */

const api = () => window.pywebview && window.pywebview.api;

const STATE_TEXT = {
  booting:   ['BOOTING',   'bringing systems online'],
  idle:      ['STANDBY',   'say "Jarvis" or "Hey Jarvis"'],
  listening: ['LISTENING', 'go ahead'],
  thinking:  ['THINKING',  'working on it'],
  tool:      ['EXECUTING', 'running a tool'],
  speaking:  ['SPEAKING',  ''],
  sleeping:  ['ASLEEP',    'dismissed — say "Jarvis" to wake me'],
  stopping:  ['SHUTTING DOWN', 'goodbye'],
  error:     ['FAULT',     'something went wrong'],
};

let state = 'booting';
let micLevel = 0, outLevel = 0, smoothMic = 0, smoothOut = 0;
let t = 0;

/* ── reactor ─────────────────────────────────────────────────── */
import { Reactor } from './reactor.js';

const reactor = new Reactor(document.getElementById('reactor'));
let bootProgress = 0;

/* The reactor is ambient motion, not a game. Drawing it 60 times a second
   costs twice what 30 does and looks the same -- and while the window is
   minimised to wake mode, which is most of the day, it should cost nothing
   at all. shadowBlur in particular is expensive enough that this matters. */
const FRAME_MS = 1000 / 30;
let lastFrame = 0;

function frame(now) {
  requestAnimationFrame(frame);
  if (document.hidden) return;
  if (now - lastFrame < FRAME_MS) return;
  lastFrame = now;
  reactor.draw();
}
requestAnimationFrame(frame);

/* ── transcript ──────────────────────────────────────────────── */
const transcript = document.getElementById('transcript');

function addMessage(who, text, cls) {
  if (!text) return;
  const el = document.createElement('div');
  el.className = `msg ${cls}`;
  el.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  el.querySelector('.body').textContent = text;
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
  while (transcript.children.length > 120) transcript.firstChild.remove();
}

/* JARVIS speaks in sentences; append to the last bubble rather than
   creating a new one for each, so a reply reads as one utterance. */
let lastJarvis = null, lastJarvisAt = 0;
function addJarvis(text) {
  const now = Date.now();
  if (lastJarvis && now - lastJarvisAt < 9000) {
    const body = lastJarvis.querySelector('.body');
    body.textContent = `${body.textContent} ${text}`.trim();
  } else {
    addMessage('JARVIS', text, 'jarvis');
    lastJarvis = transcript.lastChild;
  }
  lastJarvisAt = now;
  transcript.scrollTop = transcript.scrollHeight;
}

/* ── activity log ────────────────────────────────────────────── */
const actLog = document.getElementById('act-log');
const ticker = document.getElementById('ticker');
function addTicker(text) {
  const ts = new Date().toLocaleTimeString('en-GB', { hour12: false }).slice(0, 5);
  ticker.innerHTML = '';
  const t = document.createElement('span');
  t.className = 't'; t.textContent = ts;
  const n = document.createElement('span');
  n.textContent = truncate(text, 60);
  ticker.append(t, n);
}

function addActivity(text, cls = 'tool') {
  if (cls === 'tool' || cls === 'ok') addTicker(text.replace(/^\s*->\s*/, ''));
  const el = document.createElement('div');
  el.className = `act ${cls}`;
  const ts = new Date().toLocaleTimeString('en-GB', { hour12: false });
  el.innerHTML = `<span class="t">${ts}</span><span class="n"></span>`;
  el.querySelector('.n').textContent = text;
  actLog.appendChild(el);
  actLog.scrollTop = actLog.scrollHeight;
  while (actLog.children.length > 80) actLog.firstChild.remove();
}

/* ── state ───────────────────────────────────────────────────── */
function setState(s) {
  state = s;
  reactor.setState(s);
  document.body.dataset.state = s;   // drives the ambient CSS
  const [label, detail] = STATE_TEXT[s] || [s.toUpperCase(), ''];
  document.getElementById('state-label').textContent = label;
  document.getElementById('state-detail').textContent = detail;
  document.getElementById('btn-talk').classList.toggle('active',
    s === 'listening');
}

/* ── events from Python ──────────────────────────────────────── */
/* The engine sends batches, coalesced at 30Hz by a writer thread, so the page
   never blocks the audio pipeline and never falls behind it. */
window.onJarvisBatch = function (batch) {
  for (const ev of batch) {
    try {
      window.onJarvis(ev);
    } catch (err) {
      // One malformed event used to take the rest of its batch with it. A
      // missing helper threw on every telemetry tick and silently stopped
      // everything queued behind it in the same message.
      console.warn('event failed', ev && ev.type, err);
    }
  }
};

window.onJarvis = function (ev) {
  switch (ev.type) {
    case 'state':
      setState(ev.state);
      if (ev.state !== 'booting') { reactor.setBootProgress(1); hideBoot(); }
      break;

    case 'boot':
      document.querySelector('.boot-step').textContent = ev.step || '';
      bootProgress = Math.min(bootProgress + 0.34, 0.9);
      reactor.setBootProgress(bootProgress);
      break;

    case 'boot_failed':
      document.querySelector('.boot-text').textContent = 'CANNOT START';
      document.querySelector('.boot-step').textContent = ev.message || '';
      document.querySelector('.boot-ring').style.animation = 'none';
      break;

    case 'ready':
      reactor.setBootProgress(1);
      hideBoot();
      addActivity('all systems online', 'ok');
      break;

    case 'levels':
      micLevel = ev.mic || 0;
      outLevel = ev.out || 0;
      reactor.setLevels(micLevel, outLevel);
      break;

    case 'telemetry':
      setGauge('cpu', ev.cpu, `${Math.round(ev.cpu)}%`);
      setGauge('mem', ev.mem, `${Math.round(ev.mem)}%`);
      // The gauges live in the status card now, which only exists while it is
      // on screen. setGauge already no-ops on a missing element.
      if (ev.battery != null) {
        setGauge('bat', ev.battery, `${ev.battery}%${ev.charging ? '+' : ''}`);
      }
      break;

    case 'hud':
      if (ev.started_at != null) startedAt = ev.started_at;
      break;

    case 'panel':
      showPanel(ev);
      break;

    case 'panel.clear':
      hidePanel();
      break;

    case 'wake.detected':
      lastJarvis = null;
      addActivity('wake word detected');
      break;

    case 'conversation.open':
      openConversation(ev.seconds || 15);
      break;

    case 'conversation.ended':
      closeConversation();
      addActivity('back to wake mode');
      break;

    case 'window.minimize':
      closeConversation();
      addActivity('standing down — still listening');
      break;

    case 'window.restore':
      addActivity('awake');
      break;

    case 'app.quit':
      closeConversation();
      addActivity('shutting down', 'err');
      break;

    case 'barge_in':
      addActivity('interrupted', 'err');
      break;

    case 'listen.started':
      addActivity('listening');
      break;

    case 'listen.transcript':
      addMessage('YOU', ev.text, 'user');
      lastJarvis = null;
      // He has moved on; whatever was on screen is about to be replaced or
      // is no longer what he is asking about.
      window.dispatchEvent(new Event('jarvis-user-spoke'));
      break;

    case 'listen.ready':
      addActivity('listening');
      break;

    case 'listen.empty':
      addActivity('woke, but heard nothing');
      break;

    case 'speaking':
      addJarvis(ev.text);
      break;

    case 'tool':
      addActivity(`${ev.name}(${fmtArgs(ev.arguments)})`, 'tool');
      break;

    case 'tool_result':
      addActivity(`  -> ${truncate(ev.result, 88)}`, 'ok');
      break;

    case 'confirm':
      addActivity(`awaiting confirmation: ${ev.name}`, 'err');
      break;

    case 'error':
      addActivity(ev.text || 'error', 'err');
      addMessage('SYSTEM', ev.text || 'error', 'system');
      break;
  }
};

const truncate = (s, n) =>
  !s ? '' : (s.length > n ? s.slice(0, n) + '...' : s).replace(/\s+/g, ' ');

function fmtArgs(a) {
  if (!a || !Object.keys(a).length) return '';
  return Object.entries(a)
    .map(([k, v]) => `${k}=${truncate(String(v), 24)}`).join(', ');
}

function hideBoot() {
  document.getElementById('boot-overlay').classList.add('gone');
}

/* ── conversation window ─────────────────────────────────────────
   The single most important affordance in the interface: while this is
   counting down you can just keep talking, no wake word. If it is not
   visible on screen, nobody discovers the feature exists. */
let convoUntil = 0;
const convoEl = document.getElementById('conversation');
const convoBar = document.getElementById('convo-bar');

function openConversation(seconds) {
  convoUntil = Date.now() + seconds * 1000;
  convoEl.classList.add('on');
  convoEl.dataset.total = seconds;
}
function closeConversation() {
  convoUntil = 0;
  convoEl.classList.remove('on');
  convoBar.style.width = '0%';
}
setInterval(() => {
  if (!convoUntil) return;
  const total = (Number(convoEl.dataset.total) || 15) * 1000;
  const left = convoUntil - Date.now();
  if (left <= 0) { closeConversation(); return; }
  convoBar.style.width = `${Math.max(0, (left / total) * 100)}%`;
}, 100);

/* ── controls ────────────────────────────────────────────────── */
document.getElementById('btn-talk').onclick = () => api() && api().trigger();
document.getElementById('btn-stop').onclick = () => api() && api().interrupt();

const muteBtn = document.getElementById('btn-mute');
let muted = false;
muteBtn.onclick = () => {
  muted = !muted;
  muteBtn.classList.toggle('muted', muted);
  muteBtn.textContent = muted ? 'MIC OFF' : 'MIC ON';
  if (api()) api().set_mute(muted);
};

document.getElementById('btn-quit').onclick = () => api() && api().quit();
document.getElementById('btn-min').onclick = () => api() && api().minimize();

const fullBtn = document.getElementById('btn-full');
function toggleFullscreen() {
  if (!api()) return;
  api().toggle_fullscreen().then((on) => {
    fullBtn.classList.toggle('on', !!on);
    fullBtn.title = on ? 'Exit full screen (F11)' : 'Full screen (F11)';
  });
}
fullBtn.onclick = toggleFullscreen;

const input = document.getElementById('input');
function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMessage('YOU', text, 'user');
  lastJarvis = null;
  if (api()) api().ask(text);
}
document.getElementById('btn-send').onclick = send;
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

/* space bar = push to talk, unless typing */
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement !== input) {
    e.preventDefault();
    if (api()) api().trigger();
  }
  if (e.key === 'Escape' && api()) api().interrupt();
  if (e.key === 'F11') { e.preventDefault(); toggleFullscreen(); }
});

/* ── no polling ──────────────────────────────────────────────────
   Levels and telemetry are pushed from Python on the writer thread.
   The page used to poll api().levels() every 60ms, which meant sixteen
   blocking round trips a second competing with the audio pipeline for
   the same bridge. Push-only is both smoother and cheaper. */

function setGauge(id, pct, label) {
  if (pct == null || isNaN(pct)) return;
  const bar = document.getElementById(`g-${id}`);
  const val = document.getElementById(`v-${id}`);
  if (!bar) return;
  bar.style.width = `${Math.min(pct, 100)}%`;
  if (val) val.textContent = label;

  // Severity in the bar as well as the number, so pressure reads at a
  // glance instead of having to compare figures. Power runs the other way
  // round: low is the problem there, not high.
  const bad  = id === 'bat' ? pct <= 20 : pct >= 92;
  const near = id === 'bat' ? pct <= 35 : pct >= 80;
  for (const el of [bar, val]) {
    if (!el) continue;
    el.classList.toggle('crit', bad);
    el.classList.toggle('warn', !bad && near);
  }
}

/* the backend may already be up before the page finishes loading */
window.addEventListener('pywebviewready', async () => {
  try {
    const s = await api().get_state();
    if (s && s.state) {
      setState(s.state);
      if (s.state !== 'booting') hideBoot();
    }
  } catch (_) { /* ignore */ }
});

/* ── the drawer ──────────────────────────────────────────────────
   The full transcript, out of the way until asked for. Tab opens it,
   Escape closes it, and typing anywhere opens it and starts the message --
   which is how you discover it exists without being told. */
const drawer = document.getElementById('drawer');
const drawerBtn = document.getElementById('btn-drawer');

function openDrawer(focus = true) {
  drawer.hidden = false;
  drawerBtn.classList.add('active');
  if (focus) document.getElementById('input').focus();
  const t = document.getElementById('transcript');
  t.scrollTop = t.scrollHeight;
}
function closeDrawer() {
  drawer.hidden = true;
  drawerBtn.classList.remove('active');
  document.getElementById('input').blur();
}
function toggleDrawer() { drawer.hidden ? openDrawer() : closeDrawer(); }

drawerBtn.addEventListener('click', toggleDrawer);
document.getElementById('btn-drawer-close').addEventListener('click', closeDrawer);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Tab' && !e.ctrlKey && !e.altKey) {
    e.preventDefault(); toggleDrawer(); return;
  }
  if (e.key === 'Escape' && !drawer.hidden) { closeDrawer(); return; }
  // A printable key with nothing focused means he wants to type at it.
  if (drawer.hidden && e.key.length === 1 && !e.ctrlKey && !e.altKey &&
      !e.metaKey && document.activeElement === document.body) {
    openDrawer();
    document.getElementById('input').value = e.key;
    e.preventDefault();
  }
});

/* ══════════════════════════════════════════════════════════════
   The working panel

   Empty until a tool says otherwise. Nothing here inspects results and
   decides for itself -- Python asks for a specific kind of card, which is
   what stops this becoming the old permanent dashboard again.

   Two rules he set, and they are the whole design:

     It fades. Twenty seconds after the last thing arrives, the panel goes
     and the reactor takes the screen back. The empty screen is the point;
     anything that stays forever defeats it.

     It carries no transcript. His voice is already saying the words. What
     goes here is the thing worth looking at, and nothing else.
   ══════════════════════════════════════════════════════════════ */

function fmtUptime(s) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  const h = Math.floor(s / 3600);
  return `${h}h ${Math.round((s - h * 3600) / 60)}m`;
}

/* Uptime ticks here rather than arriving as a finished number once a minute,
   which is how it managed to read "9s" for sixty seconds. */
let startedAt = null;
setInterval(() => {
  if (startedAt == null) return;
  const el = document.getElementById('f-uptime');
  if (el) el.textContent = fmtUptime(Date.now() / 1000 - startedAt);
}, 1000);

const FADE_AFTER_MS = 20000;

const panel = document.getElementById('panel');
const panelBody = document.getElementById('panel-body');
const panelKind = document.getElementById('panel-kind');
const panelSrc = document.getElementById('panel-src');
let fadeTimer = null;

function hidePanel() {
  clearTimeout(fadeTimer);
  document.body.classList.remove('working');
  // Let the slide finish before pulling it out of the layout, or the reactor
  // jumps back to centre while the panel is still visibly on screen.
  setTimeout(() => {
    if (!document.body.classList.contains('working')) panel.hidden = true;
  }, 700);
}

function armFade() {
  clearTimeout(fadeTimer);
  fadeTimer = setTimeout(hidePanel, FADE_AFTER_MS);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

const RENDER = {
  results(d) {
    const out = [];
    for (const item of (d.items || []).slice(0, 5)) {
      const card = el('div', 'p-card');
      card.append(el('div', 't', item.title || ''));
      if (item.snippet) card.append(el('div', 's', item.snippet));
      if (item.url) {
        try {
          card.append(el('div', 'u', new URL(item.url).hostname.replace(/^www\./, '')));
        } catch { /* a malformed url is not worth a broken card */ }
      }
      out.push(card);
    }
    return out;
  },

  images(d) {
    const grid = el('div', 'p-shots');
    for (const item of (d.items || []).slice(0, 6)) {
      const box = el('div', 'p-shot');
      const img = new Image();
      img.loading = 'lazy';
      img.alt = item.title || '';
      img.src = item.thumb;
      // A dead thumbnail should leave a quiet empty tile, not a broken icon.
      img.addEventListener('error', () => img.remove());
      box.append(img);
      grid.append(box);
    }
    return [grid];
  },

  status(d) {
    const rows = el('div', 'p-rows');
    const bar = (v, warn, crit) => {
      const m = el('div', 'p-meter');
      const i = el('i');
      i.style.width = `${Math.min(v, 100)}%`;
      if (v >= crit) i.classList.add('crit');
      else if (v >= warn) i.classList.add('warn');
      m.append(i);
      return m;
    };
    const row = (label, value, cls) => {
      const r = el('div', 'p-row');
      r.append(el('span', null, label));
      r.append(el('b', cls, value));
      return r;
    };
    if (d.memory != null) {
      rows.append(row('Memory', `${d.memory}%`,
                      d.memory >= 92 ? 'crit' : d.memory >= 80 ? 'warn' : null));
      rows.append(bar(d.memory, 80, 92));
    }
    if (d.cpu != null) { rows.append(row('Processor', `${d.cpu}%`)); rows.append(bar(d.cpu, 80, 92)); }
    if (d.disk != null) { rows.append(row('Disk', `${d.disk}% used`)); rows.append(bar(d.disk, 85, 94)); }
    if (d.battery != null) {
      rows.append(row('Power', `${d.battery}%${d.charging ? ' · plugged in' : ''}`,
                      !d.charging && d.battery <= 20 ? 'crit' : null));
    }
    return [rows];
  },

  weather(d) {
    const wrap = el('div');
    wrap.append(el('div', 'p-big', `${d.now}°`));
    wrap.append(el('div', 'p-sub', `${d.sky} · feels like ${d.feels}°`));
    const inline = el('div', 'p-inline');
    for (const [k, v] of [['High', `${d.high}°`], ['Low', `${d.low}°`],
                          ['Rain', `${d.rain}%`], ['Wind', `${d.wind} mph`]]) {
      const cell = el('div', null, k);
      cell.append(el('b', null, v));
      inline.append(cell);
    }
    wrap.append(inline);
    return [wrap];
  },

  playing(d) {
    const wrap = el('div');
    wrap.append(el('div', 'p-big', d.title || ''));
    const bits = [d.artist, d.app].filter(Boolean).join(' · ');
    if (bits) wrap.append(el('div', 'p-sub', bits));
    return [wrap];
  },

  screen(d) { return [el('div', 'p-text', d.body || '')]; },
  text(d)   { return [el('div', 'p-text', d.body || '')]; },
};

const KIND_LABEL = {
  results: 'FOUND', images: 'SHOWING', status: 'SYSTEM',
  weather: 'WEATHER', playing: 'PLAYING', screen: 'ON SCREEN', text: '',
};

function showPanel(ev) {
  const render = RENDER[ev.kind];
  if (!render) return;

  let nodes;
  try {
    nodes = render(ev) || [];
  } catch (err) {
    // A malformed payload must not take the interface down with it.
    console.warn('panel render failed', ev.kind, err);
    return;
  }
  if (!nodes.length) return;

  panelBody.replaceChildren(...nodes);
  panelKind.textContent = KIND_LABEL[ev.kind] ?? '';
  panelSrc.textContent = ev.title || '';

  panel.hidden = false;
  // A frame between unhiding and animating, or the transition has nothing to
  // move from and the panel simply appears.
  requestAnimationFrame(() => document.body.classList.add('working'));
  armFade();
}

/* Anything he says next means he has moved on, so the old panel should not
   sit there looking current. */
window.addEventListener('jarvis-user-spoke', hidePanel);
