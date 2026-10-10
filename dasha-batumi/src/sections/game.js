// Мини-игра «Поймай хачапури»: 30 секунд, тарелка ездит за пальцем.
// Хачапури +1, золотой +3, туча (батумский дождь) −2.
// В конце при любом счёте открывается секретное послание.

import { content } from '../lib/state.js';
import { fill } from '../lib/time.js';
import { pop } from '../lib/confetti.js';
import { drawKhachapuri, drawCloud, drawPlate } from '../lib/draw.js';

const SECRET_KEY = 'dasha.secretOpened';
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, k) => a + (b - a) * k;

const storage = {
  get(k) {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* приватный режим — не страшно */
    }
  },
};

function iconCanvas(kind) {
  const c = document.createElement('canvas');
  const size = 44;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  c.width = c.height = size * dpr;
  c.style.width = c.style.height = `${size}px`;
  c.setAttribute('aria-hidden', 'true');
  const ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);
  if (kind === 'cloud') drawCloud(ctx, 22, 18, 17, 0.3);
  else drawKhachapuri(ctx, 22, 22, 17, { golden: kind === 'golden', rot: -0.25, t: 0.4 });
  return c;
}

export function initGame() {
  const g = content.game;
  const root = document.querySelector('.game');
  root.innerHTML = `
    <div class="game__board">
      <canvas class="game__canvas"></canvas>
      <div class="game__hud" hidden>
        <p class="game__score"><b>0</b> <span></span></p>
        <div class="game__timebar"><i></i></div>
      </div>
      <div class="game__overlay game__intro">
        <ul class="game__rules"></ul>
        <p class="game__target"></p>
        <button class="btn js-start" type="button"></button>
      </div>
      <div class="game__overlay game__end" hidden>
        <p class="game__result" aria-live="polite"></p>
        <button class="btn js-secret" type="button"></button>
        <button class="chip chip--light js-again" type="button"></button>
      </div>
      <button class="game__overlay game__pause" type="button" hidden></button>
    </div>
    <article class="note game__secret" hidden>
      <h3 class="note__title"></h3>
    </article>
  `;

  const board = root.querySelector('.game__board');
  const canvas = root.querySelector('.game__canvas');
  const ctx = canvas.getContext('2d');
  const hud = root.querySelector('.game__hud');
  const scoreEl = root.querySelector('.game__score b');
  const timeBar = root.querySelector('.game__timebar i');
  const intro = root.querySelector('.game__intro');
  const end = root.querySelector('.game__end');
  const pauseBtn = root.querySelector('.game__pause');
  const resultEl = root.querySelector('.game__result');
  const secret = root.querySelector('.game__secret');

  root.querySelector('.game__score span').textContent = g.scoreLabel;
  root.querySelector('.game__target').textContent = fill(g.targetNote, { target: g.target });
  root.querySelector('.js-start').textContent = g.start;
  root.querySelector('.js-secret').textContent = g.openSecret;
  root.querySelector('.js-again').textContent = g.again;
  pauseBtn.textContent = g.paused;

  const rules = root.querySelector('.game__rules');
  for (const kind of ['khachapuri', 'golden', 'cloud']) {
    const li = document.createElement('li');
    li.append(iconCanvas(kind));
    const span = document.createElement('span');
    span.textContent = g.rules[kind];
    li.append(span);
    rules.append(li);
  }

  secret.querySelector('.note__title').textContent = g.secret.title;
  for (const line of g.secret.text) {
    const p = document.createElement('p');
    p.textContent = line;
    secret.append(p);
  }
  if (storage.get(SECRET_KEY)) {
    secret.hidden = false;
    secret.classList.add('is-open');
  }

  /* ── Состояние ────────────────────────────────────── */

  let W = 0;
  let H = 0;
  let dpr = 1;
  let mode = 'intro';
  let score = 0;
  let elapsed = 0;
  let items = [];
  let floaters = [];
  let spawnIn = 0;
  let plateX = 0;
  let targetX = 0;
  let shake = 0;
  let raf = 0;
  let last = 0;
  let clock = 0;

  const itemSize = () => clamp(W * 0.075, 20, 34);
  const plateW = () => clamp(W * 0.27, 84, 130);
  const plateY = () => H - 40;

  function resize() {
    const rect = board.getBoundingClientRect();
    W = Math.round(rect.width);
    H = Math.round(rect.height);
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    if (!plateX) plateX = targetX = W / 2;
    plateX = clamp(plateX, 0, W);
    targetX = clamp(targetX, 0, W);
    if (mode !== 'playing') render();
  }

  function spawn() {
    const p = elapsed / g.duration;
    const s = itemSize();
    const r = Math.random();
    const cloudP = 0.17 + 0.1 * p;
    const type = r < cloudP ? 'cloud' : r < cloudP + 0.08 ? 'golden' : 'khachapuri';
    const speed = lerp(150, 285, p) * (H / 480) * (0.85 + Math.random() * 0.3) * (type === 'golden' ? 1.15 : 1);
    items.push({
      type,
      x: lerp(s * 1.3, W - s * 1.3, Math.random()),
      y: -s,
      vy: speed,
      rot: (Math.random() - 0.5) * 1.2,
      vr: type === 'cloud' ? 0 : (Math.random() - 0.5) * 3,
      sway: Math.random() * Math.PI * 2,
    });
    spawnIn = lerp(0.72, 0.42, p) * (0.8 + Math.random() * 0.4);
  }

  function catchItem(it) {
    const value = it.type === 'golden' ? 3 : it.type === 'cloud' ? -2 : 1;
    score = Math.max(0, score + value);
    scoreEl.textContent = score;
    floaters.push({
      x: it.x,
      y: plateY() - 30,
      text: value > 0 ? `+${value}` : `−${-value}`,
      color: value > 0 ? (value > 1 ? '#ffc23d' : '#f4e7d1') : '#9fd0ff',
      life: 0,
    });
    if (it.type === 'cloud') shake = 0.35;
    if (navigator.vibrate && it.type === 'cloud') navigator.vibrate(40);
  }

  function update(dt) {
    elapsed += dt;
    clock += dt;
    spawnIn -= dt;
    if (spawnIn <= 0) spawn();

    plateX += (targetX - plateX) * (1 - Math.exp(-dt * 22));
    shake = Math.max(0, shake - dt);

    const s = itemSize();
    const pw = plateW();
    const py = plateY();
    items = items.filter((it) => {
      it.y += it.vy * dt;
      it.rot += it.vr * dt;
      if (it.type === 'cloud') it.x += Math.sin(clock * 2 + it.sway) * 18 * dt;
      const reach = it.type === 'cloud' ? s * 0.55 : s * 0.4;
      if (it.y + reach >= py - pw * 0.08 && it.y - reach <= py + 6 && Math.abs(it.x - plateX) < pw * 0.5 + s * 0.25) {
        catchItem(it);
        return false;
      }
      return it.y < H + s * 2;
    });

    floaters = floaters.filter((f) => (f.life += dt) < 0.9);

    const left = Math.max(0, g.duration - elapsed);
    timeBar.style.transform = `scaleX(${left / g.duration})`;
    if (left <= 0) finish();
  }

  function render() {
    if (!W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, '#102446');
    bg.addColorStop(0.75, '#25507c');
    bg.addColorStop(1, '#3a6a93');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    // фоновые огоньки
    ctx.fillStyle = 'rgba(255, 228, 170, 0.5)';
    for (let i = 0; i < 18; i++) {
      const x = ((i * 97) % 100) / 100 * W;
      const y = ((i * 53) % 60) / 100 * H;
      ctx.globalAlpha = 0.25 + 0.25 * Math.sin(clock * 1.5 + i);
      ctx.fillRect(x, y, 2, 2);
    }
    ctx.globalAlpha = 1;

    // стол
    ctx.fillStyle = '#d9bf94';
    ctx.fillRect(0, H - 18, W, 18);
    ctx.fillStyle = '#ead6b3';
    ctx.fillRect(0, H - 18, W, 3);

    const s = itemSize();
    for (const it of items) {
      if (it.type === 'cloud') drawCloud(ctx, it.x, it.y, s * 1.05, clock);
      else drawKhachapuri(ctx, it.x, it.y, s, { golden: it.type === 'golden', rot: it.rot, t: clock });
    }

    const sx = shake ? Math.sin(shake * 70) * 5 : 0;
    drawPlate(ctx, plateX + sx, plateY(), plateW());

    ctx.textAlign = 'center';
    ctx.font = '800 22px Manrope, system-ui, sans-serif';
    for (const f of floaters) {
      ctx.globalAlpha = 1 - f.life / 0.9;
      ctx.fillStyle = f.color;
      ctx.fillText(f.text, f.x, f.y - f.life * 40);
    }
    ctx.globalAlpha = 1;
  }

  function loop(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (mode !== 'playing') return;
    update(dt);
    render();
    raf = requestAnimationFrame(loop);
  }

  function run() {
    cancelAnimationFrame(raf);
    last = performance.now();
    raf = requestAnimationFrame(loop);
  }

  function startGame() {
    score = 0;
    elapsed = 0;
    items = [];
    floaters = [];
    spawnIn = 0.4;
    scoreEl.textContent = '0';
    timeBar.style.transform = 'scaleX(1)';
    intro.hidden = true;
    end.hidden = true;
    pauseBtn.hidden = true;
    hud.hidden = false;
    board.classList.add('is-playing');
    mode = 'playing';
    // Чтобы тарелка точно была на экране
    const r = board.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) board.scrollIntoView({ behavior: 'smooth', block: 'center' });
    run();
  }

  function finish() {
    mode = 'ended';
    cancelAnimationFrame(raf);
    board.classList.remove('is-playing');
    items = [];
    render();
    resultEl.textContent = fill(score >= g.target ? g.resultWin : g.resultLose, { score });
    end.hidden = false;
    if (score >= g.target) {
      const r = board.getBoundingClientRect();
      pop(r.left + r.width / 2, r.top + r.height * 0.35, 50);
    }
  }

  function pause() {
    if (mode !== 'playing') return;
    mode = 'paused';
    cancelAnimationFrame(raf);
    board.classList.remove('is-playing');
    pauseBtn.hidden = false;
  }

  function resume() {
    if (mode !== 'paused') return;
    pauseBtn.hidden = true;
    board.classList.add('is-playing');
    mode = 'playing';
    run();
  }

  function openSecret() {
    storage.set(SECRET_KEY, '1');
    secret.hidden = false;
    requestAnimationFrame(() => secret.classList.add('is-open'));
    secret.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  /* ── Управление ───────────────────────────────────── */

  const moveTo = (e) => {
    const rect = canvas.getBoundingClientRect();
    targetX = clamp(e.clientX - rect.left, plateW() * 0.4, W - plateW() * 0.4);
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (mode !== 'playing') return;
    canvas.setPointerCapture?.(e.pointerId);
    moveTo(e);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (mode === 'playing') moveTo(e);
  });
  window.addEventListener('keydown', (e) => {
    if (mode !== 'playing') return;
    if (e.key === 'ArrowLeft') targetX = clamp(targetX - W * 0.12, 0, W);
    if (e.key === 'ArrowRight') targetX = clamp(targetX + W * 0.12, 0, W);
  });

  root.querySelector('.js-start').addEventListener('click', startGame);
  root.querySelector('.js-again').addEventListener('click', startGame);
  root.querySelector('.js-secret').addEventListener('click', openSecret);
  pauseBtn.addEventListener('click', resume);

  document.addEventListener('visibilitychange', () => document.hidden && pause());
  new IntersectionObserver(([e]) => !e.isIntersecting && pause(), { threshold: 0.35 }).observe(board);

  new ResizeObserver(resize).observe(board);
  resize();
}
