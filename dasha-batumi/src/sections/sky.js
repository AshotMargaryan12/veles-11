// Вечерний Батуми, нарисованный кодом: закат над морем, горы, силуэт города
// с Алфавитной башней и колесом обозрения, огни набережной и их отражения.
// Пока ждём — по небу летит маленький самолёт. Когда она здесь — салют.

import { reducedMotion } from '../lib/state.js';

function mulberry32(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, k) => a + (b - a) * k;

const WINDOW_COLORS = ['#ffd67a', '#ffe6ad', '#ffc565', '#ffd67a', '#cfe4ff'];
const FIREWORK_COLORS = ['#ffc23d', '#f4e7d1', '#7fb7d9', '#f2a65a', '#ffffff'];

export function createSky(canvas) {
  const ctx = canvas.getContext('2d');
  const bg = document.createElement('canvas');
  const bgx = bg.getContext('2d');

  let W = 0;
  let H = 0;
  let dpr = 1;
  let s = 1; // масштаб мелких деталей
  let horizon = 0;
  let sun = {};
  let stars = [];
  let twinkleWindows = [];
  let lamps = [];
  let shimmer = [];
  let wheel = null;
  let tower = null;
  let fireworks = [];
  let nextFirework = 0;
  let glowSprite = null;

  let arrived = false;
  let visible = true;
  let raf = 0;
  let lastFrame = 0;
  const start = performance.now();

  /* ── Сцена ─────────────────────────────────────────────── */

  function build() {
    const rect = canvas.getBoundingClientRect();
    W = Math.round(rect.width);
    H = Math.round(rect.height);
    if (!W || !H) return false;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = bg.width = Math.round(W * dpr);
    canvas.height = bg.height = Math.round(H * dpr);
    s = clamp(W / 420, 0.85, 1.5);
    horizon = Math.round(H * 0.72);

    const rnd = mulberry32(20261015);
    const cityStart = W * 0.42;

    sun = { x: W * 0.17, r: clamp(Math.min(W, H) * 0.07, 18, 60) };
    sun.y = horizon + sun.r * 0.45;

    stars = Array.from({ length: Math.round((W * H) / 7000) }, () => ({
      x: rnd() * W,
      y: Math.pow(rnd(), 1.4) * horizon * 0.62,
      r: 0.4 + rnd() * 0.9,
      a: 0.25 + rnd() * 0.6,
      sp: 0.6 + rnd() * 2,
      ph: rnd() * Math.PI * 2,
    }));

    // Колесо обозрения у кромки воды
    const wr = clamp(H * 0.06, 26, 56);
    wheel = { x: cityStart + wr * 0.9, y: horizon - wr - 5 * s, r: wr };

    // Алфавитная башня: тонкая, с двойной спиралью огней и шаром наверху
    tower = { x: cityStart + (W - cityStart) * 0.36, h: clamp(H * 0.2, 90, 190), w: 4 * s };

    // Фонари набережной
    lamps = [];
    for (let x = W * 0.33; x < W + 6; x += 12 * s) {
      lamps.push({ x: x + rnd() * 3, ph: rnd() * Math.PI * 2 });
    }

    // Блики на воде
    shimmer = Array.from({ length: Math.round(W / 6) }, () => {
      const k = Math.pow(rnd(), 0.8);
      return {
        x: rnd() * W,
        y: horizon + 3 + k * (H - horizon - 3),
        len: (6 + 26 * k) * s * (0.5 + rnd()),
        a: 0.05 + rnd() * 0.12,
        sp: 0.4 + rnd(),
        ph: rnd() * Math.PI * 2,
      };
    });

    glowSprite = makeGlow(Math.round(9 * s * dpr));
    paintBackground(rnd, cityStart);
    return true;
  }

  // Мягкое тёплое пятно света — рисуем один раз и потом только копируем
  function makeGlow(radius) {
    const c = document.createElement('canvas');
    c.width = c.height = radius * 2;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(radius, radius, 0, radius, radius, radius);
    grad.addColorStop(0, 'rgba(255, 214, 140, 0.85)');
    grad.addColorStop(0.25, 'rgba(255, 190, 110, 0.35)');
    grad.addColorStop(1, 'rgba(255, 180, 100, 0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, radius * 2, radius * 2);
    return c;
  }

  function ridge(g, rnd, x0, x1, height, color, bumps) {
    const phases = Array.from({ length: 3 }, () => rnd() * Math.PI * 2);
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(x0, horizon);
    for (let x = x0; x <= x1 + 4; x += 4) {
      const k = (x - x0) / (x1 - x0);
      const fade = clamp(k / 0.25, 0, 1);
      const n =
        0.55 +
        0.3 * Math.sin(k * bumps + phases[0]) +
        0.15 * Math.sin(k * bumps * 2.3 + phases[1]) +
        0.08 * Math.sin(k * bumps * 5.1 + phases[2]);
      g.lineTo(x, horizon - height * n * fade * fade);
    }
    g.lineTo(x1 + 4, horizon);
    g.closePath();
    g.fill();
  }

  function buildings(g, rnd, cityStart, { scale, color, litP, alpha }) {
    let x = cityStart - 6 * s + rnd() * 10;
    while (x < W) {
      const bw = clamp(W * (0.028 + rnd() * 0.05), 9 * s, 46 * s);
      const pos = clamp((x - cityStart) / (W - cityStart), 0, 1);
      const envelope = 0.5 + 0.5 * Math.sin(Math.PI * (0.15 + pos * 0.75));
      const bh = (H * (0.03 + rnd() * 0.07) * envelope + 8 * s) * scale;
      const top = horizon - bh;
      g.fillStyle = color;
      g.fillRect(x, top, bw, bh);
      // иногда — антенна или ступенька на крыше
      if (rnd() < 0.25) g.fillRect(x + bw * 0.4, top - 6 * s, Math.max(1, s), 6 * s);
      else if (rnd() < 0.3) g.fillRect(x + bw * 0.15, top - 4 * s, bw * 0.7, 4 * s);

      const cw = 4.2 * s;
      const ch = 5.6 * s;
      const cols = Math.floor((bw - 3 * s) / cw);
      const rows = Math.floor((bh - 6 * s) / ch);
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          if (rnd() > litP) continue;
          const win = {
            x: x + 2 * s + c * cw,
            y: top + 4 * s + r * ch,
            w: 1.9 * s,
            h: 2.6 * s,
            color: WINDOW_COLORS[(rnd() * WINDOW_COLORS.length) | 0],
            a: (0.5 + rnd() * 0.5) * alpha,
          };
          if (rnd() < 0.1) {
            twinkleWindows.push({ ...win, sp: 0.15 + rnd() * 0.4, ph: rnd() * Math.PI * 2 });
          } else {
            g.globalAlpha = win.a;
            g.fillStyle = win.color;
            g.fillRect(win.x, win.y, win.w, win.h);
            g.globalAlpha = 1;
          }
        }
      }
      x += bw + rnd() * 3 * s;
    }
  }

  function paintBackground(rnd, cityStart) {
    const g = bgx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    twinkleWindows = [];

    // Небо
    const sky = g.createLinearGradient(0, 0, 0, horizon);
    sky.addColorStop(0, '#081429');
    sky.addColorStop(0.4, '#132a52');
    sky.addColorStop(0.66, '#2a4977');
    sky.addColorStop(0.84, '#6f6b86');
    sky.addColorStop(0.94, '#d38d5c');
    sky.addColorStop(1, '#f0bc78');
    g.fillStyle = sky;
    g.fillRect(0, 0, W, horizon + 1);

    // Свечение заката над морем (солнце садится в море, на западе)
    const glow = g.createRadialGradient(sun.x, horizon, 0, sun.x, horizon, W * 0.9);
    glow.addColorStop(0, 'rgba(255,186,105,0.55)');
    glow.addColorStop(0.35, 'rgba(255,160,90,0.16)');
    glow.addColorStop(1, 'rgba(255,160,90,0)');
    g.fillStyle = glow;
    g.fillRect(0, 0, W, horizon);

    g.save();
    g.beginPath();
    g.rect(0, 0, W, horizon);
    g.clip();
    const sg = g.createRadialGradient(sun.x, sun.y - sun.r * 0.4, sun.r * 0.1, sun.x, sun.y, sun.r);
    sg.addColorStop(0, '#fff1c9');
    sg.addColorStop(1, '#f6a24e');
    g.fillStyle = sg;
    g.beginPath();
    g.arc(sun.x, sun.y, sun.r, 0, Math.PI * 2);
    g.fill();
    g.restore();

    // Горы Аджарии за городом
    ridge(g, rnd, W * 0.3, W, H * 0.09, '#2e466f', 7);
    ridge(g, rnd, W * 0.36, W, H * 0.055, '#223860', 9);

    // Море
    const sea = g.createLinearGradient(0, horizon, 0, H);
    sea.addColorStop(0, '#3a5679');
    sea.addColorStop(0.16, '#22406a');
    sea.addColorStop(1, '#091631');
    g.fillStyle = sea;
    g.fillRect(0, horizon, W, H - horizon);
    const haze = g.createLinearGradient(0, 0, W * 0.6, 0);
    haze.addColorStop(0, 'rgba(255,205,140,0.55)');
    haze.addColorStop(1, 'rgba(255,205,140,0)');
    g.fillStyle = haze;
    g.fillRect(0, horizon, W * 0.6, 1.2);

    // Город: дальний ряд светлее, ближний темнее
    buildings(g, rnd, cityStart + 10 * s, { scale: 1.25, color: '#182c4f', litP: 0.22, alpha: 0.55 });

    // Башня
    const t = tower;
    g.fillStyle = '#0d1a31';
    g.beginPath();
    g.moveTo(t.x - t.w, horizon);
    g.lineTo(t.x - t.w * 0.45, horizon - t.h);
    g.lineTo(t.x + t.w * 0.45, horizon - t.h);
    g.lineTo(t.x + t.w, horizon);
    g.fill();
    g.fillRect(t.x - 0.5 * s, horizon - t.h - 16 * s, Math.max(1, s), 12 * s);

    buildings(g, rnd, cityStart, { scale: 0.85, color: '#0c182d', litP: 0.32, alpha: 1 });

    // Набережная и свет города на воде
    g.fillStyle = '#081225';
    g.fillRect(W * 0.31, horizon - 2, W, 3);
    const cityGlow = g.createLinearGradient(0, horizon, 0, horizon + 26 * s);
    cityGlow.addColorStop(0, 'rgba(255,200,120,0.18)');
    cityGlow.addColorStop(1, 'rgba(255,200,120,0)');
    g.fillStyle = cityGlow;
    g.fillRect(W * 0.33, horizon + 1, W, 26 * s);
  }

  /* ── Кадр ──────────────────────────────────────────────── */

  function draw(now, isStill = false) {
    const t = (now - start) / 1000;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.drawImage(bg, 0, 0, W, H);

    // Звёзды
    for (const st of stars) {
      const a = st.a * (0.65 + 0.35 * Math.sin(t * st.sp + st.ph));
      ctx.globalAlpha = a;
      ctx.fillStyle = '#fff6df';
      ctx.fillRect(st.x, st.y, st.r * 1.6, st.r * 1.6);
    }
    ctx.globalAlpha = 1;

    // Мерцающие окна
    for (const w of twinkleWindows) {
      const on = arrived || Math.sin(t * w.sp + w.ph) > -0.2;
      if (!on) continue;
      ctx.globalAlpha = w.a;
      ctx.fillStyle = w.color;
      ctx.fillRect(w.x, w.y, w.w, w.h);
    }
    ctx.globalAlpha = 1;

    drawTowerLights(t);
    drawWheel(t);
    drawLamps(t);
    drawWater(t);
    if (!arrived) drawPlane(t);
    else if (!isStill) drawFireworks(t);
  }

  function drawTowerLights(t) {
    const { x, h, w } = tower;
    const n = 26;
    for (let helix = 0; helix < 2; helix++) {
      for (let k = 1; k < n; k++) {
        const y = horizon - (k / n) * h;
        const ph = k * 0.62 + t * 1.3 + helix * Math.PI;
        const front = Math.cos(ph);
        ctx.globalAlpha = 0.25 + 0.55 * Math.max(0, front);
        ctx.fillStyle = helix ? '#ffd98a' : '#fff1cc';
        const width = w * (1.6 - (k / n) * 0.7);
        ctx.fillRect(x + Math.sin(ph) * width - 1, y, 2 * s, 1.4 * s);
      }
    }
    const pulse = 0.75 + 0.25 * Math.sin(t * 2);
    const ty = horizon - h - 3 * s;
    const g = ctx.createRadialGradient(x, ty, 0, x, ty, 14 * s);
    g.addColorStop(0, `rgba(255,226,160,${0.55 * pulse})`);
    g.addColorStop(1, 'rgba(255,226,160,0)');
    ctx.globalAlpha = 1;
    ctx.fillStyle = g;
    ctx.fillRect(x - 14 * s, ty - 14 * s, 28 * s, 28 * s);
    ctx.fillStyle = '#ffe7b0';
    ctx.beginPath();
    ctx.arc(x, ty, 3.4 * s, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawWheel(t) {
    const { x, y, r } = wheel;
    const rot = t * 0.12;
    ctx.strokeStyle = '#0c182d';
    ctx.lineWidth = 2.4 * s;
    ctx.beginPath();
    ctx.moveTo(x - r * 0.55, horizon - 1);
    ctx.lineTo(x, y);
    ctx.lineTo(x + r * 0.55, horizon - 1);
    ctx.stroke();

    ctx.lineWidth = Math.max(0.8, 0.8 * s);
    ctx.strokeStyle = 'rgba(255,220,160,0.22)';
    ctx.beginPath();
    for (let i = 0; i < 12; i++) {
      const a = rot + (i * Math.PI) / 6;
      ctx.moveTo(x, y);
      ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
    }
    ctx.stroke();
    ctx.strokeStyle = 'rgba(255,220,160,0.4)';
    ctx.lineWidth = 1.4 * s;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();

    for (let i = 0; i < 24; i++) {
      const a = rot + (i * Math.PI) / 12;
      const lx = x + Math.cos(a) * r;
      const ly = y + Math.sin(a) * r;
      const blink = arrived ? 0.75 + 0.25 * Math.sin(t * 6 + i) : 0.85;
      ctx.globalAlpha = blink;
      ctx.fillStyle = i % 3 === 0 ? '#9fd3ff' : '#ffd36b';
      ctx.beginPath();
      ctx.arc(lx, ly, 1.7 * s, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#ffd36b';
    ctx.beginPath();
    ctx.arc(x, y, 2.2 * s, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawLamps(t) {
    const y = horizon - 3;
    for (let i = 0; i < lamps.length; i++) {
      const l = lamps[i];
      const a = 0.8 + 0.2 * Math.sin(t * 3 + l.ph);
      const gr = 9 * s;
      ctx.globalAlpha = 0.55 * a;
      ctx.drawImage(glowSprite, l.x - gr, y - gr, gr * 2, gr * 2);
      ctx.globalAlpha = a;
      ctx.fillStyle = '#fff0c4';
      ctx.fillRect(l.x - 0.8 * s, y - 0.8 * s, 1.7 * s, 1.7 * s);

      // Отражение фонаря — дрожащие штрихи на воде
      for (let k = 0; k < 4; k++) {
        const ry = horizon + 4 * s + k * 6 * s;
        const wob = Math.sin(t * 2.2 + k * 1.7 + l.ph) * 2.2 * s;
        ctx.globalAlpha = (0.32 - k * 0.07) * a;
        ctx.fillStyle = '#ffcf7a';
        ctx.fillRect(l.x - 1.5 * s + wob, ry, (3 - k * 0.4) * s, 1.3 * s);
      }
    }
    ctx.globalAlpha = 1;
  }

  function drawWater(t) {
    // Общая рябь
    ctx.fillStyle = '#bcd6f2';
    for (const w of shimmer) {
      const a = w.a * (0.5 + 0.5 * Math.sin(t * w.sp + w.ph));
      ctx.globalAlpha = a;
      ctx.fillRect(w.x + Math.sin(t * 0.5 + w.ph) * 6, w.y, w.len, Math.max(1, s));
    }

    // Солнечная дорожка
    const n = 34;
    for (let i = 0; i < n; i++) {
      const k = i / n;
      const y = horizon + 2 + Math.pow(k, 1.25) * (H - horizon - 2);
      const width = (sun.r * 0.8 + k * W * 0.22) * (0.55 + 0.45 * Math.sin(t * 1.6 + i * 1.9));
      const x = sun.x + Math.sin(t * 0.8 + i * 1.3) * width * 0.2 - width / 2;
      ctx.globalAlpha = 0.5 * (1 - k) + 0.05;
      ctx.fillStyle = i % 2 ? '#ffc98a' : '#ffb067';
      ctx.fillRect(x, y, width, Math.max(1.2, 1.6 * s * (0.6 + k)));
    }
    ctx.globalAlpha = 1;
  }

  function drawPlane(t) {
    const period = 46;
    const p = (t % period) / (period - 8);
    if (p > 1) return;
    const x = lerp(-0.06 * W, 1.06 * W, p);
    const y = lerp(0.16 * H, 0.31 * H, p) - Math.sin(p * Math.PI) * 0.03 * H;

    const trail = ctx.createLinearGradient(x - 70 * s, y - 9 * s, x, y);
    trail.addColorStop(0, 'rgba(255,240,220,0)');
    trail.addColorStop(1, 'rgba(255,240,220,0.22)');
    ctx.strokeStyle = trail;
    ctx.lineWidth = 1.2 * s;
    ctx.beginPath();
    ctx.moveTo(x - 70 * s, y - 9 * s);
    ctx.lineTo(x - 3 * s, y - 0.4 * s);
    ctx.stroke();

    ctx.fillStyle = '#fff8ea';
    ctx.globalAlpha = 0.9;
    ctx.fillRect(x - 1, y - 1, 2.2 * s, 2.2 * s);
    if (t % 1.3 < 0.12) {
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(x, y, 3.2 * s, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = (t + 0.6) % 1.6 < 0.8 ? 0.95 : 0.35;
    ctx.fillStyle = '#ff6b5a';
    ctx.fillRect(x - 3.5 * s, y + 1.2 * s, 1.6 * s, 1.6 * s);
    ctx.globalAlpha = 1;
  }

  function drawFireworks(t) {
    if (t > nextFirework) {
      nextFirework = t + 1.4 + Math.random() * 1.8;
      fireworks.push({
        x: lerp(W * 0.12, W * 0.9, Math.random()),
        y0: horizon,
        y1: lerp(H * 0.3, H * 0.58, Math.random()),
        born: t,
        color: FIREWORK_COLORS[(Math.random() * FIREWORK_COLORS.length) | 0],
        sparks: Array.from({ length: 54 }, (_, i) => ({
          a: (i / 54) * Math.PI * 2 + Math.random() * 0.2,
          v: 62 + Math.random() * 58,
        })),
      });
    }
    fireworks = fireworks.filter((f) => t - f.born < 3.2);
    for (const f of fireworks) {
      const age = t - f.born;
      if (age < 0.9) {
        const k = age / 0.9;
        const y = lerp(f.y0, f.y1, 1 - Math.pow(1 - k, 2));
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = '#fff1cc';
        ctx.fillRect(f.x - 1, y, 2 * s, 4 * s);
        continue;
      }
      const e = age - 0.9;
      const fade = clamp(1 - e / 2.2, 0, 1);
      // вспышка в момент взрыва
      if (e < 0.35) {
        const fr = 34 * s;
        ctx.globalAlpha = (1 - e / 0.35) * 0.9;
        ctx.drawImage(glowSprite, f.x - fr, f.y1 - fr, fr * 2, fr * 2);
      }
      ctx.fillStyle = f.color;
      for (const sp of f.sparks) {
        const dist = sp.v * s * (1 - Math.exp(-e * 2.4));
        const x = f.x + Math.cos(sp.a) * dist;
        const y = f.y1 + Math.sin(sp.a) * dist + 14 * s * e * e;
        ctx.globalAlpha = fade * (0.65 + 0.35 * Math.sin(e * 20 + sp.a * 5));
        ctx.fillRect(x - 1.4 * s, y - 1.4 * s, 2.8 * s, 2.8 * s);
        // короткий хвостик искры
        ctx.globalAlpha *= 0.45;
        ctx.fillRect(x - Math.cos(sp.a) * 5 * s - 0.8 * s, y - Math.sin(sp.a) * 5 * s - 0.8 * s, 1.6 * s, 1.6 * s);
      }
    }
    ctx.globalAlpha = 1;
  }

  /* ── Цикл ──────────────────────────────────────────────── */

  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (now - lastFrame < 32) return; // ~30 кадров в секунду — бережём батарею
    lastFrame = now;
    draw(now);
  }

  function play() {
    if (raf || reducedMotion.matches || !visible || document.hidden) return;
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    cancelAnimationFrame(raf);
    raf = 0;
  }
  function still() {
    // Один неподвижный кадр — для prefers-reduced-motion
    if (W && H) draw(start + 9000, true);
  }

  function refresh() {
    if (!build()) return;
    still();
    play();
  }

  const ro = new ResizeObserver(() => {
    const rect = canvas.getBoundingClientRect();
    if (Math.round(rect.width) !== W || Math.abs(Math.round(rect.height) - H) > 80) refresh();
  });
  ro.observe(canvas);

  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    visible ? play() : stop();
  }).observe(canvas);

  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : play()));
  reducedMotion.addEventListener?.('change', () => {
    stop();
    still();
    play();
  });

  refresh();

  return {
    setArrived(value) {
      arrived = value;
      nextFirework = 0;
      fireworks = [];
      still();
    },
  };
}
