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
  idle:      ['STANDBY',   'listening for "Hey JARVIS"'],
  listening: ['LISTENING', 'go ahead'],
  thinking:  ['THINKING',  'working on it'],
  tool:      ['EXECUTING', 'running a tool'],
  speaking:  ['SPEAKING',  ''],
  error:     ['FAULT',     'something went wrong'],
};

let state = 'booting';
let micLevel = 0, outLevel = 0, smoothMic = 0, smoothOut = 0;
let t = 0;

/* ── reactor ─────────────────────────────────────────────────── */
const canvas = document.getElementById('reactor');
const ctx = canvas.getContext('2d');
const DPR = window.devicePixelRatio || 1;

function sizeCanvas() {
  const css = canvas.clientWidth || 300;
  canvas.width = canvas.height = css * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  return css;
}
let size = sizeCanvas();
window.addEventListener('resize', () => { size = sizeCanvas(); });

const CYAN = [79, 216, 255];
const GOLD = [255, 201, 107];

const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function draw() {
  t += 0.016;
  smoothMic += (micLevel - smoothMic) * 0.28;
  smoothOut += (outLevel - smoothOut) * 0.34;

  const w = size, h = size, cx = w / 2, cy = h / 2;
  const R = Math.min(w, h) * 0.42;
  ctx.clearRect(0, 0, w, h);

  const speaking = state === 'speaking';
  const col = speaking ? GOLD : CYAN;

  // energy: what the reactor is reacting to right now
  let energy = 0.10 + Math.sin(t * 1.5) * 0.03;              // idle breath
  if (state === 'listening') energy = 0.16 + smoothMic * 1.5;
  else if (state === 'thinking' || state === 'tool') energy = 0.34;
  else if (speaking) energy = 0.20 + smoothOut * 1.7;
  energy = Math.min(energy, 1.0);

  // ── outer glow ──
  const glow = ctx.createRadialGradient(cx, cy, R * 0.15, cx, cy, R * 1.5);
  glow.addColorStop(0, rgba(col, 0.16 + energy * 0.26));
  glow.addColorStop(0.55, rgba(col, 0.05));
  glow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);

  // ── static outer ring with tick marks ──
  ctx.strokeStyle = rgba(col, 0.22);
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();

  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * Math.PI * 2;
    const major = i % 5 === 0;
    const r1 = R * (major ? 0.93 : 0.96);
    ctx.globalAlpha = major ? 0.5 : 0.22;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // ── rotating arc rings ──
  const spin = (state === 'thinking' || state === 'tool') ? 2.6 : 0.42;
  const rings = [
    { r: R * 0.86, from: 0.00, len: 1.5, dir:  1, w: 2.0 },
    { r: R * 0.74, from: 2.20, len: 1.1, dir: -1, w: 1.5 },
    { r: R * 0.62, from: 4.10, len: 1.9, dir:  1, w: 1.2 },
  ];
  rings.forEach((ring, i) => {
    const a0 = ring.from + t * spin * ring.dir * (1 + i * 0.16);
    ctx.strokeStyle = rgba(col, 0.55 + energy * 0.4);
    ctx.lineWidth = ring.w;
    ctx.lineCap = 'round';
    ctx.shadowBlur = 14; ctx.shadowColor = rgba(col, 0.7);
    ctx.beginPath();
    ctx.arc(cx, cy, ring.r, a0, a0 + ring.len);
    ctx.stroke();
    ctx.shadowBlur = 0;
  });

  // ── tool mode: a stepping dial ──
  if (state === 'tool') {
    const seg = 12, step = Math.floor(t * 7) % seg;
    for (let i = 0; i < seg; i++) {
      const a = (i / seg) * Math.PI * 2 - Math.PI / 2;
      ctx.globalAlpha = i === step ? 1 : 0.18;
      ctx.fillStyle = rgba(col, 1);
      ctx.beginPath();
      ctx.arc(cx + Math.cos(a) * R * 0.52, cy + Math.sin(a) * R * 0.52,
              2.6, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // ── listening: ripples driven by the microphone ──
  if (state === 'listening' && smoothMic > 0.012) {
    for (let i = 0; i < 3; i++) {
      const phase = (t * 0.85 + i / 3) % 1;
      ctx.strokeStyle = rgba(col, (1 - phase) * 0.4 * Math.min(smoothMic * 4, 1));
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(cx, cy, R * (0.30 + phase * 0.68), 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // ── core ──
  const coreR = R * (0.26 + energy * 0.16);
  const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
  core.addColorStop(0, '#FFFFFF');
  core.addColorStop(0.3, rgba(col, 0.95));
  core.addColorStop(1, rgba(col, 0));
  ctx.fillStyle = core;
  ctx.shadowBlur = 34; ctx.shadowColor = rgba(col, 0.85);
  ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = 0;

  // triangular core detail, echoing the reactor housing
  ctx.strokeStyle = rgba(col, 0.8);
  ctx.lineWidth = 1.4;
  for (let k = 0; k < 2; k++) {
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = t * 0.3 * (k ? -1 : 1) + (i / 3) * Math.PI * 2 + k * Math.PI / 3;
      const r = R * 0.19;
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath(); ctx.stroke();
  }

  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);

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
  const [label, detail] = STATE_TEXT[s] || [s.toUpperCase(), ''];
  document.getElementById('state-label').textContent = label;
  document.getElementById('state-detail').textContent = detail;
  document.getElementById('btn-talk').classList.toggle('active',
    s === 'listening');
}

/* ── events from Python ──────────────────────────────────────── */
window.onJarvis = function (ev) {
  switch (ev.type) {
    case 'state':
      setState(ev.state);
      if (ev.state !== 'booting') hideBoot();
      break;

    case 'boot':
      document.querySelector('.boot-step').textContent = ev.step || '';
      break;

    case 'boot_failed':
      document.querySelector('.boot-text').textContent = 'CANNOT START';
      document.querySelector('.boot-step').textContent = ev.message || '';
      document.querySelector('.boot-ring').style.animation = 'none';
      break;

    case 'ready':
      hideBoot();
      addActivity('all systems online', 'ok');
      break;

    case 'wake.detected':
      lastJarvis = null;
      addActivity('wake word detected');
      break;

    case 'listen.transcript':
      addMessage('YOU', ev.text, 'user');
      lastJarvis = null;
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
document.getElementById('btn-min').onclick = () => {
  if (window.pywebview) window.pywebview.api.trigger && window.top.blur();
};

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
});

/* ── polling ─────────────────────────────────────────────────── */
setInterval(async () => {
  if (!api()) return;
  try {
    const l = await api().levels();
    micLevel = l.mic || 0;
    outLevel = l.out || 0;
  } catch (_) { /* backend still booting */ }
}, 60);

setInterval(async () => {
  if (!api()) return;
  try {
    const s = await api().telemetry();
    setGauge('cpu', s.cpu, `${Math.round(s.cpu)}%`);
    setGauge('mem', s.mem, `${Math.round(s.mem)}%`);
    if (s.battery != null) {
      setGauge('bat', s.battery, `${s.battery}%${s.charging ? '+' : ''}`);
    }
  } catch (_) { /* not up yet */ }
}, 2500);

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
