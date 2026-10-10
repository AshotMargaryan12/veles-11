// Стопка полароидов: смахни верхний пальцем в любую сторону,
// тапни — перевернётся, на обороте подпись.
// В DOM живут только 4 верхние карточки, фото грузятся по мере приближения.

import photos from 'virtual:photos';
import captions from '../../photos/captions.json';
import { content } from '../lib/state.js';
import { fill } from '../lib/time.js';

const VISIBLE = 4;
const PRELOAD = 3;

function shuffle(list) {
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

function captionFor(file, i, fallbacks) {
  const raw = captions[file];
  const fb = fallbacks[i % fallbacks.length] || '';
  if (raw && typeof raw === 'object') return { back: raw.back || fb, front: raw.front || '', pos: raw.pos || '' };
  return { back: raw || fb, front: '', pos: '' };
}

export function initPolaroids() {
  const root = document.querySelector('.polaroids');
  const c = content.photos;

  let items = photos.map((p, i) => ({ ...p, ...captionFor(p.file, i, c.fallbackCaptions) }));
  const isEmpty = items.length === 0;
  if (isEmpty) {
    items = c.fallbackCaptions.slice(0, 4).map((back, i) => ({ placeholder: i, back, front: '', pos: '' }));
  }
  if (c.shuffle) shuffle(items);
  const n = items.length;

  root.innerHTML = `
    <div class="stack" role="group" aria-roledescription="стопка фотографий"></div>
    <p class="stack__hint"></p>
    <div class="stack__bar">
      <button class="chip stack__prev" type="button"><span aria-hidden="true">‹</span> <span class="js-prev"></span></button>
      <button class="chip chip--solid stack__flip" type="button"></button>
      <button class="chip stack__next" type="button"><span class="js-next"></span> <span aria-hidden="true">›</span></button>
    </div>
    <p class="stack__counter" aria-live="polite"></p>
    ${isEmpty ? '<p class="stack__empty"></p>' : ''}
  `;

  const stack = root.querySelector('.stack');
  const hint = root.querySelector('.stack__hint');
  const counter = root.querySelector('.stack__counter');
  hint.textContent = c.tapHint;
  root.querySelector('.js-prev').textContent = c.prev;
  root.querySelector('.js-next').textContent = c.next;
  root.querySelector('.stack__flip').textContent = c.flip;
  if (isEmpty) root.querySelector('.stack__empty').textContent = c.empty;
  if (n < 2) root.querySelector('.stack__bar').classList.add('is-single');

  const cards = new Map(); // индекс фото → элемент
  let pos = 0;
  let near = false;
  let busy = false;

  const tiltFor = (idx) => (((idx * 37 + 11) % 11) - 5) * 1.05;

  function makeCard(idx) {
    const item = items[idx];
    const el = document.createElement('div');
    el.className = 'polaroid';
    el.style.setProperty('--tilt', `${tiltFor(idx)}deg`);
    el.innerHTML = `
      <div class="polaroid__inner">
        <div class="polaroid__face polaroid__front">
          <div class="polaroid__photo">${
            item.placeholder !== undefined
              ? `<div class="polaroid__placeholder" data-variant="${item.placeholder}"></div>`
              : '<img alt="" decoding="async" draggable="false" />'
          }</div>
          <p class="polaroid__note"></p>
        </div>
        <div class="polaroid__face polaroid__back">
          <p class="polaroid__caption"></p>
          <span class="polaroid__stamp"></span>
        </div>
      </div>`;
    el.querySelector('.polaroid__note').textContent = item.front;
    el.querySelector('.polaroid__caption').textContent = item.back;
    el.querySelector('.polaroid__stamp').textContent = c.stamp;
    const img = el.querySelector('img');
    if (img) {
      img.alt = item.back;
      if (item.pos) img.style.objectPosition = item.pos;
    } else {
      el.classList.add('is-loaded');
    }
    return el;
  }

  function loadImage(el, idx) {
    const img = el.querySelector('img');
    if (!img || img.getAttribute('src')) return;
    img.onload = () => el.classList.add('is-loaded');
    img.onerror = () => el.classList.add('is-loaded', 'is-broken');
    img.src = items[idx].card;
  }

  function layout(enterClass = 'is-entering') {
    const want = [];
    for (let k = 0; k < Math.min(VISIBLE, n); k++) want.push((pos + k) % n);

    for (const [idx, el] of cards) {
      if (!want.includes(idx)) {
        el.remove();
        cards.delete(idx);
      }
    }

    want.forEach((idx, k) => {
      let el = cards.get(idx);
      if (!el) {
        el = makeCard(idx);
        el.classList.add(k === 0 ? enterClass : 'is-entering');
        stack.appendChild(el);
        cards.set(idx, el);
        requestAnimationFrame(() =>
          requestAnimationFrame(() => el.classList.remove('is-entering', 'is-returning')),
        );
      }
      el.style.zIndex = String(VISIBLE - k);
      el.style.setProperty('--depth', k);
      el.classList.toggle('is-top', k === 0);
      el.setAttribute('aria-hidden', k === 0 ? 'false' : 'true');
      el.tabIndex = k === 0 ? 0 : -1;
      if (near && k < PRELOAD) loadImage(el, idx);
    });

    counter.textContent = n > 1 ? fill(c.counter, { n: pos + 1, total: n }) : '';
  }

  const topCard = () => cards.get(pos);

  function flip(el = topCard()) {
    if (!el) return;
    el.classList.toggle('is-flipped');
    hint.classList.add('is-hidden');
  }

  function throwOut(el, dir, dy = 0) {
    if (!el || busy || n < 2) return;
    busy = true;
    const width = stack.clientWidth;
    el.classList.remove('is-dragging');
    el.classList.add('is-leaving');
    el.style.transform = `translate(${dir * width * 1.35}px, ${dy * 0.35 + 24}px) rotate(${dir * 26}deg)`;
    setTimeout(() => {
      el.remove();
      for (const [idx, card] of cards) if (card === el) cards.delete(idx);
      pos = (pos + 1) % n;
      layout();
      busy = false;
    }, 300);
  }

  function back() {
    if (busy || n < 2) return;
    pos = (pos - 1 + n) % n;
    const existing = cards.get(pos);
    if (existing) {
      existing.remove();
      cards.delete(pos);
    }
    layout('is-returning');
  }

  /* ── Жесты ─────────────────────────────────────────── */

  let drag = null;

  stack.addEventListener('pointerdown', (e) => {
    const el = e.target.closest('.polaroid.is-top');
    if (!el || busy || (e.pointerType === 'mouse' && e.button !== 0)) return;
    drag = { el, id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), dx: 0, dy: 0, moved: false, vx: 0, lx: e.clientX, lt: performance.now() };
    el.setPointerCapture?.(e.pointerId);
  });

  stack.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const now = performance.now();
    drag.dx = e.clientX - drag.x0;
    drag.dy = e.clientY - drag.y0;
    if (now > drag.lt) {
      drag.vx = (e.clientX - drag.lx) / (now - drag.lt);
      drag.lx = e.clientX;
      drag.lt = now;
    }
    if (!drag.moved && Math.hypot(drag.dx, drag.dy) > 7) {
      drag.moved = true;
      drag.el.classList.add('is-dragging');
    }
    if (drag.moved) {
      const tilt = parseFloat(drag.el.style.getPropertyValue('--tilt')) || 0;
      drag.el.style.transform = `translate(${drag.dx}px, ${drag.dy * 0.35}px) rotate(${tilt + drag.dx * 0.06}deg)`;
    }
  });

  function endDrag(e, cancelled) {
    if (!drag || e.pointerId !== drag.id) return;
    const { el, dx, dy, moved, vx, t0 } = drag;
    drag = null;
    el.classList.remove('is-dragging');
    if (cancelled) {
      el.style.transform = '';
      return;
    }
    if (!moved && performance.now() - t0 < 500) {
      flip(el);
      return;
    }
    const far = Math.abs(dx) > stack.clientWidth * 0.3;
    const fast = Math.abs(vx) > 0.55 && Math.abs(dx) > 30;
    if ((far || fast) && n > 1) throwOut(el, Math.sign(dx || vx), dy);
    else el.style.transform = '';
  }

  stack.addEventListener('pointerup', (e) => endDrag(e, false));
  stack.addEventListener('pointercancel', (e) => endDrag(e, true));

  stack.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') throwOut(topCard(), 1);
    else if (e.key === 'ArrowLeft') back();
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      flip();
    }
  });

  root.querySelector('.stack__next').addEventListener('click', () => throwOut(topCard(), 1));
  root.querySelector('.stack__prev').addEventListener('click', back);
  root.querySelector('.stack__flip').addEventListener('click', () => flip());

  // Фото начинают грузиться, только когда блок почти на экране
  new IntersectionObserver(
    ([e], io) => {
      if (!e.isIntersecting) return;
      near = true;
      io.disconnect();
      layout();
    },
    { rootMargin: '700px 0px' },
  ).observe(root);

  layout();
}
