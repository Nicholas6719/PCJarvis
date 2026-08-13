/* ══════════════════════════════════════════════════════════════
   The reactor.

   This is the primary status display, so every state gets its own
   distinct motion rather than a generic spinner. What he is doing
   should be readable from across the room, without reading a word:

     booting     rings assemble from nothing
     idle        slow breathing, particles drifting
     listening   waveform ring driven by the live microphone
     thinking    counter-rotation accelerates, particles pull inward
     tool        a scanner sweeps the dial, segments latch as it passes
     speaking    gold, pulsing on the actual output envelope
     sleeping    dim and cold, one very slow breath -- dismissed, but the
                 wake word is still live
     stopping    rings collapse inward and fade; he is leaving

   State changes fire a shockwave so transitions register peripherally.
   ══════════════════════════════════════════════════════════════ */

export const PALETTE = {
  booting:   [90, 140, 190],
  idle:      [79, 216, 255],
  listening: [90, 240, 220],
  thinking:  [120, 180, 255],
  tool:      [180, 150, 255],
  speaking:  [255, 201, 107],
  sleeping:  [46, 92, 122],    // dormant: dim, cold, obviously off duty
  stopping:  [150, 110, 90],   // winding down for good
  error:     [255, 107, 107],
};

const TAU = Math.PI * 2;
const lerp = (a, b, t) => a + (b - a) * t;
const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

export class Reactor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.dpr = window.devicePixelRatio || 1;

    this.state = 'booting';
    this.t = 0;
    this.spin = 0;            // integrated rotation, so speed changes never jump
    this.energy = 0;
    this.colour = [...PALETTE.booting];
    this.micLevel = 0;
    this.outLevel = 0;
    this.smoothMic = 0;
    this.smoothOut = 0;
    this.bootProgress = 0;
    this.stopProgress = 0;

    // Waveform history ring, fed by whichever level is relevant right now.
    this.bins = new Array(72).fill(0);
    this.shockwaves = [];
    this.particles = Array.from({ length: 46 }, () => this._newParticle());

    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  _newParticle() {
    const a = Math.random() * TAU;
    return {
      angle: a,
      radius: 0.55 + Math.random() * 0.5,   // fraction of R
      speed: (0.15 + Math.random() * 0.5) * (Math.random() < 0.5 ? -1 : 1),
      size: 0.6 + Math.random() * 1.5,
      phase: Math.random() * TAU,
    };
  }

  resize() {
    const css = this.canvas.clientWidth || 300;
    this.canvas.width = this.canvas.height = css * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.size = css;
  }

  setState(next) {
    if (next === this.state) return;
    this.state = next;
    // A shockwave on every transition: the eye catches the movement even when
    // it is not looking directly at the reactor.
    this.shockwaves.push({ r: 0.16, life: 1 });
    if (this.shockwaves.length > 4) this.shockwaves.shift();
  }

  setLevels(mic, out) {
    this.micLevel = mic || 0;
    this.outLevel = out || 0;
  }

  setBootProgress(p) { this.bootProgress = Math.max(this.bootProgress, p); }

  /* ── per-frame ────────────────────────────────────────────── */
  draw() {
    const dt = 0.016;
    this.t += dt;

    const st = this.state;
    const target = PALETTE[st] || PALETTE.idle;
    for (let i = 0; i < 3; i++) {
      this.colour[i] = lerp(this.colour[i], target[i], 0.06);
    }

    this.smoothMic += (this.micLevel - this.smoothMic) * 0.3;
    this.smoothOut += (this.outLevel - this.smoothOut) * 0.36;

    // What the reactor is reacting to, by state.
    let targetEnergy = 0.12 + Math.sin(this.t * 1.4) * 0.035;
    let spinRate = 0.36;
    if (st === 'listening') {
      targetEnergy = 0.20 + Math.min(this.smoothMic * 1.8, 0.7);
      spinRate = 0.7;
    } else if (st === 'thinking') {
      targetEnergy = 0.42 + Math.sin(this.t * 7) * 0.06;
      spinRate = 2.9;
    } else if (st === 'tool') {
      targetEnergy = 0.38;
      spinRate = 1.9;
    } else if (st === 'speaking') {
      targetEnergy = 0.24 + Math.min(this.smoothOut * 2.0, 0.75);
      spinRate = 0.55;
    } else if (st === 'booting') {
      targetEnergy = 0.08 + this.bootProgress * 0.2;
      spinRate = 1.2;
    } else if (st === 'sleeping') {
      // One slow breath every eight seconds. Unmistakably dormant, but
      // never fully dark -- he is still listening for his name.
      targetEnergy = 0.05 + (Math.sin(this.t * 0.78) + 1) * 0.025;
      spinRate = 0.08;
    } else if (st === 'stopping') {
      targetEnergy = Math.max(0, 0.3 - this.t * 0.0);
      spinRate = 0.15;
    } else if (st === 'error') {
      targetEnergy = 0.3 + Math.sin(this.t * 12) * 0.15;
      spinRate = 0.2;
    }
    this.energy += (targetEnergy - this.energy) * 0.12;
    this.spin += spinRate * dt;

    // Feed the waveform ring.
    const level = st === 'speaking' ? this.smoothOut
                : st === 'listening' ? this.smoothMic
                : 0;
    this.bins.push(level);
    this.bins.shift();

    this._render();
  }

  _render() {
    const { ctx } = this;
    const w = this.size, h = this.size;
    const cx = w / 2, cy = h / 2;
    const R = Math.min(w, h) * 0.42;
    const c = this.colour;
    const E = Math.min(this.energy, 1);
    let assembling = this.state === 'booting'
      ? Math.min(this.bootProgress, 1) : 1;
    if (this.state === 'stopping') {
      // The assembly animation, run in reverse: the rings draw back in
      // over about a second and a half, then he is gone.
      this.stopProgress = Math.min(1, (this.stopProgress || 0) + 0.011);
      assembling = 1 - this.stopProgress;
    }

    ctx.clearRect(0, 0, w, h);

    // ── ambient glow ──
    const glow = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.55);
    glow.addColorStop(0, rgba(c, 0.15 + E * 0.3));
    glow.addColorStop(0.5, rgba(c, 0.05));
    glow.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    // ── shockwaves from state changes ──
    for (const s of this.shockwaves) {
      s.r += 0.022;
      s.life -= 0.021;
      if (s.life <= 0) continue;
      ctx.strokeStyle = rgba(c, s.life * 0.5);
      ctx.lineWidth = 2.2 * s.life;
      ctx.beginPath();
      ctx.arc(cx, cy, R * s.r * 2.4, 0, TAU);
      ctx.stroke();
    }
    this.shockwaves = this.shockwaves.filter((s) => s.life > 0);

    // ── drifting particles ──
    // They orbit outside the rings and are drawn inward while he thinks, so
    // "thinking" reads as the machine gathering itself.
    const pull = this.state === 'thinking' ? 0.72
               : this.state === 'tool' ? 0.85 : 1.0;
    for (const p of this.particles) {
      p.angle += p.speed * 0.006 * (this.state === 'thinking' ? 3.2 : 1);
      const wobble = Math.sin(this.t * 1.6 + p.phase) * 0.03;
      const r = R * (p.radius * pull + wobble);
      const x = cx + Math.cos(p.angle) * r;
      const y = cy + Math.sin(p.angle) * r;
      const a = (0.16 + E * 0.5) * (0.4 + 0.6 * Math.sin(this.t * 2 + p.phase) ** 2);
      ctx.fillStyle = rgba(c, a * assembling);
      ctx.beginPath();
      ctx.arc(x, y, p.size, 0, TAU);
      ctx.fill();
    }

    // ── outer tick ring ──
    ctx.save();
    ctx.globalAlpha = assembling;
    ctx.strokeStyle = rgba(c, 0.2);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU * assembling); ctx.stroke();

    const ticks = 72;
    for (let i = 0; i < ticks * assembling; i++) {
      const a = (i / ticks) * TAU - Math.PI / 2;
      const major = i % 6 === 0;
      const r1 = R * (major ? 0.92 : 0.955);
      ctx.globalAlpha = (major ? 0.55 : 0.2) * assembling;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
      ctx.stroke();
    }
    ctx.restore();

    // ── waveform ring: the live audio, drawn as radial bars ──
    if (this.state === 'listening' || this.state === 'speaking') {
      const n = this.bins.length;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * TAU - Math.PI / 2;
        const v = Math.min(this.bins[i] * 2.6, 1);
        if (v < 0.005) continue;
        const r0 = R * 0.98, r1 = R * (0.98 + v * 0.30);
        ctx.strokeStyle = rgba(c, 0.35 + v * 0.6);
        ctx.lineWidth = 2.4;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
        ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
        ctx.stroke();
      }
    }

    // ── rotating arc rings ──
    const rings = [
      { r: 0.86, from: 0.0, len: 1.5, dir: 1, w: 2.0 },
      { r: 0.74, from: 2.2, len: 1.1, dir: -1, w: 1.5 },
      { r: 0.62, from: 4.1, len: 1.9, dir: 1, w: 1.2 },
    ];
    ctx.shadowColor = rgba(c, 0.75);
    rings.forEach((ring, i) => {
      const a0 = ring.from + this.spin * ring.dir * (1 + i * 0.18);
      ctx.strokeStyle = rgba(c, (0.5 + E * 0.45) * assembling);
      ctx.lineWidth = ring.w;
      ctx.lineCap = 'round';
      ctx.shadowBlur = 14;
      ctx.beginPath();
      ctx.arc(cx, cy, R * ring.r, a0, a0 + ring.len * assembling);
      ctx.stroke();
    });
    ctx.shadowBlur = 0;

    // ── tool: a scanner sweeping the dial ──
    if (this.state === 'tool') {
      const sweep = (this.t * 1.7) % TAU;
      const grad = ctx.createConicGradient
        ? ctx.createConicGradient(sweep, cx, cy) : null;
      if (grad) {
        grad.addColorStop(0, rgba(c, 0.42));
        grad.addColorStop(0.10, rgba(c, 0));
        grad.addColorStop(1, rgba(c, 0));
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 0.92, 0, TAU);
        ctx.fill();
      }
      const seg = 16;
      for (let i = 0; i < seg; i++) {
        const a = (i / seg) * TAU;
        const delta = ((sweep - a) % TAU + TAU) % TAU;
        const hot = delta < 0.9 ? 1 - delta / 0.9 : 0;   // latches as it passes
        ctx.fillStyle = rgba(c, 0.16 + hot * 0.84);
        const rr = 2.2 + hot * 2.4;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a - Math.PI / 2) * R * 0.52,
                cy + Math.sin(a - Math.PI / 2) * R * 0.52, rr, 0, TAU);
        ctx.fill();
      }
    }

    // ── listening ripples ──
    if (this.state === 'listening' && this.smoothMic > 0.01) {
      for (let i = 0; i < 3; i++) {
        const phase = (this.t * 0.9 + i / 3) % 1;
        ctx.strokeStyle = rgba(c,
          (1 - phase) * 0.42 * Math.min(this.smoothMic * 4, 1));
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(cx, cy, R * (0.3 + phase * 0.66), 0, TAU);
        ctx.stroke();
      }
    }

    // ── core ──
    const coreR = R * (0.25 + E * 0.17) * assembling;
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(coreR, 1));
    core.addColorStop(0, '#FFFFFF');
    core.addColorStop(0.32, rgba(c, 0.95));
    core.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = core;
    ctx.shadowBlur = 36; ctx.shadowColor = rgba(c, 0.85);
    ctx.beginPath(); ctx.arc(cx, cy, Math.max(coreR, 1), 0, TAU); ctx.fill();
    ctx.shadowBlur = 0;

    // counter-rotating triangles in the core
    ctx.strokeStyle = rgba(c, 0.85 * assembling);
    ctx.lineWidth = 1.4;
    for (let k = 0; k < 2; k++) {
      ctx.beginPath();
      for (let i = 0; i < 3; i++) {
        const a = this.spin * 0.35 * (k ? -1 : 1)
                + (i / 3) * TAU + k * Math.PI / 3;
        const r = R * 0.185;
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.closePath(); ctx.stroke();
    }
  }
}
