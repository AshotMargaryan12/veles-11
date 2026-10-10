// Финал: письмо от Ашота и пасхалка — сердечко, которое надо тапнуть 5 раз.

import { content } from '../lib/state.js';
import { pop } from '../lib/confetti.js';

const HEART = `
  <svg viewBox="0 0 64 60" aria-hidden="true">
    <path class="egg__fill" d="M32 54C18 44 6 34 7 21 8 11 17 6 25 9c4 1.5 6 5 7 8 1.5-4 4-7 8-8.4C48 6 57 11 57 21c0 13-12 23-25 33Z" />
    <path class="egg__line" d="M31.5 53.2C18.5 43.6 6.8 33.6 7.6 21.2 8.3 11.6 16.6 6.8 24.6 9.4c4.2 1.4 6.2 4.8 7.2 7.8 1.6-4.2 4.2-7.2 8.4-8.4 7.8-2.2 16.6 2.6 16.2 12.6-.4 12.6-12.2 22.6-24.9 31.8" />
    <path class="egg__line egg__line--sketch" d="M33 52.4c12-8.8 22.8-18.4 23.4-30.2M9.2 18.6c1.6-6.8 8-9.8 14.2-8.2" />
  </svg>`;

export function initLetter() {
  const l = content.letter;
  const root = document.querySelector('.letter');
  root.innerHTML = `
    <article class="letter__paper">
      <p class="letter__greeting"></p>
      <div class="letter__body"></div>
      <p class="letter__ps" data-t="letter.ps"></p>
      <p class="letter__sign"></p>
    </article>
    <div class="egg">
      <button class="egg__heart" type="button" aria-label="сердечко">${HEART}</button>
      <p class="egg__hint" aria-live="polite"></p>
      <div class="egg__secret note" hidden>
        <h3 class="note__title"></h3>
        <p></p>
      </div>
    </div>`;

  root.querySelector('.letter__greeting').textContent = l.greeting;
  const body = root.querySelector('.letter__body');
  for (const text of l.paragraphs) {
    const p = document.createElement('p');
    p.textContent = text;
    body.append(p);
  }
  root.querySelector('.letter__sign').textContent = l.signature;

  const heart = root.querySelector('.egg__heart');
  const hint = root.querySelector('.egg__hint');
  const secret = root.querySelector('.egg__secret');
  secret.querySelector('.note__title').textContent = l.egg.title;
  secret.querySelector('p').textContent = l.egg.text;
  hint.textContent = l.egg.hint;

  let taps = 0;
  heart.addEventListener('click', () => {
    taps += 1;
    const r = heart.getBoundingClientRect();
    heart.classList.remove('is-bump');
    void heart.offsetWidth;
    heart.classList.add('is-bump');

    if (taps < l.egg.taps) {
      heart.style.setProperty('--grow', String(1 + taps * 0.09));
      hint.textContent = l.egg.progress[taps - 1] || '';
      return;
    }
    if (taps === l.egg.taps) {
      heart.classList.add('is-open');
      heart.style.setProperty('--grow', '1');
      hint.textContent = '';
      hint.hidden = true;
      secret.hidden = false;
      requestAnimationFrame(() => secret.classList.add('is-open'));
      pop(r.left + r.width / 2, r.top + r.height / 2, 60);
      return;
    }
    pop(r.left + r.width / 2, r.top + r.height / 2, 14);
  });
}

export function initMusic() {
  const m = content.music;
  if (!m.src) return;
  const audio = new Audio();
  audio.preload = 'none';
  audio.loop = true;
  audio.src = m.src;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'music';
  btn.setAttribute('aria-pressed', 'false');
  btn.setAttribute('aria-label', m.play);
  btn.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 17.5V6.2l10-2.2v11.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="6.5" cy="17.5" r="2.6" fill="currentColor"/><circle cx="16.5" cy="15.3" r="2.6" fill="currentColor"/></svg>';
  btn.addEventListener('click', async () => {
    if (audio.paused) {
      try {
        await audio.play();
      } catch {
        return;
      }
    } else audio.pause();
    const on = !audio.paused;
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', on ? m.pause : m.play);
  });
  document.body.append(btn);
}
