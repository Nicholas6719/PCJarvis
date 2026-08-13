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

function frame() {
  reactor.draw();
  requestAnimationFrame(frame);
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
function addActivity(text, cls = 'tool') {
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
  for (const ev of batch) window.onJarvis(ev);
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
      if (ev.battery != null) {
        setGauge('bat', ev.battery, `${ev.battery}%${ev.charging ? '+' : ''}`);
      }
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
  document.getElementById(`g-${id}`).style.width = `${Math.min(pct, 100)}%`;
  document.getElementById(`v-${id}`).textContent = label;
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
