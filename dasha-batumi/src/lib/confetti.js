// Конфетти на canvas поверх страницы. Цвета — из палитры сайта, без сердечек.

import { reducedMotion } from './state.js';

const COLORS = ['#ffc23d', '#f4e7d1', '#f2a65a', '#7fb7d9', '#ffffff', '#2f6f9f'];

let canvas;
let ctx;
let particles = [];
let raf = 0;
let last = 0;

function ensureCanvas() {
  if (canvas) return;
  canvas = document.createElement('canvas');
  canvas.className = 'confetti';
  canvas.setAttribute('aria-hidden', 'true');
  document.body.appendChild(canvas);
  ctx = canvas.getContext('2d');
  resize();
  window.addEventListener('resize', resize);
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = innerWidth * dpr;
  canvas.height = innerHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function spawn(x, y, count, spread, power) {
  for (let i = 0; i < count; i++) {
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * spread;
    const speed = power * (0.55 + Math.random() * 0.6);
    particles.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      size: 5 + Math.random() * 6,
      color: COLORS[(Math.random() * COLORS.length) | 0],
      shape: Math.random() < 0.7 ? 'rect' : 'dot',
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 12,
      flip: Math.random() * Math.PI * 2,
      vf: 6 + Math.random() * 8,
      life: 0,
    });
  }
}

function tick(now) {
  const dt = Math.min(0.033, (now - last) / 1000 || 0.016);
  last = now;
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  particles = particles.filter((p) => p.y < innerHeight + 40 && p.life < 7);
  for (const p of particles) {
    p.life += dt;
    p.vy += 900 * dt;
    p.vx *= 1 - 1.6 * dt;
    p.vy *= 1 - 1.1 * dt;
    p.x += (p.vx + Math.sin(p.flip) * 30) * dt;
    p.y += p.vy * dt;
    p.rot += p.vr * dt;
    p.flip += p.vf * dt;
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.rot);
    ctx.scale(1, Math.cos(p.flip));
    ctx.fillStyle = p.color;
    if (p.shape === 'rect') ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
    else {
      ctx.beginPath();
      ctx.arc(0, 0, p.size / 3, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }
  if (particles.length) raf = requestAnimationFrame(tick);
  else {
    raf = 0;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
  }
}

// Большой праздничный залп: из обоих нижних углов и сверху
export function celebrate() {
  if (reducedMotion.matches) return;
  ensureCanvas();
  const w = innerWidth;
  const h = innerHeight;
  const power = Math.max(h, 600) * 1.35;
  spawn(w * 0.05, h, 70, 0.9, power);
  spawn(w * 0.95, h, 70, 0.9, power);
  setTimeout(() => spawn(w / 2, h * 0.15, 60, Math.PI * 1.6, 380), 350);
  start();
}

// Маленький хлопок из точки (например, из сердечка)
export function pop(x, y, count = 40) {
  if (reducedMotion.matches) return;
  ensureCanvas();
  spawn(x, y, count, Math.PI * 1.2, 520);
  start();
}

function start() {
  if (!raf) {
    last = performance.now();
    raf = requestAnimationFrame(tick);
  }
}
