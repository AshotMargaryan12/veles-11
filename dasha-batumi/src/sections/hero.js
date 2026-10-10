// Первый экран: обратный отсчёт до прилёта, а после — «вместе уже».

import { content, t, arrivalTs, realArrivalTs, isPreview, getPhase, setPhase, onPhase } from '../lib/state.js';
import { splitDuration, plural, formatArrival, fill } from '../lib/time.js';
import { celebrate } from '../lib/confetti.js';
import { createSky } from './sky.js';

const pad = (n) => String(n).padStart(2, '0');

export function initHero() {
  const root = document.querySelector('.hero');
  const sky = createSky(root.querySelector('.hero__sky'));
  const nums = Object.fromEntries([...root.querySelectorAll('[data-unit]')].map((el) => [el.dataset.unit, el]));
  const labels = Object.fromEntries([...root.querySelectorAll('[data-label]')].map((el) => [el.dataset.label, el]));
  const phraseEl = root.querySelector('.hero__phrase');
  const whenEl = root.querySelector('.hero__when');
  const yourTimeEl = root.querySelector('.hero__your-time');
  const titleEl = root.querySelector('.hero__title');
  const timerEl = root.querySelector('.timer');

  // В превью «через N секунд» показываем реальную дату прилёта в подписи
  const shownTs = isPreview ? realArrivalTs : arrivalTs;
  const when = formatArrival(shownTs, content.ARRIVAL.timeZone);
  let lastSeconds = -1;

  function renderStatic() {
    whenEl.textContent = fill(t('hero.when'), when);
    const showLocal = getPhase() === 'countdown' && !when.sameZone;
    yourTimeEl.textContent = showLocal ? fill(content.hero.yourTime, { time: when.localTime }) : '';
    yourTimeEl.hidden = !showLocal;
    timerEl.setAttribute('aria-label', getPhase() === 'arrived' ? t('hero.togetherLabel') : t('hero.title'));
  }

  function phraseFor(msLeft) {
    const hours = msLeft / 3600e3;
    const list = [...content.hero.phrases].sort((a, b) => a.hoursLeft - b.hoursLeft);
    return (list.find((p) => hours < p.hoursLeft) || list[list.length - 1]).text;
  }

  function tick() {
    const now = Date.now();
    const arrived = getPhase() === 'arrived';
    if (!arrived && now >= arrivalTs) {
      setPhase('arrived');
      return;
    }
    const diff = arrived ? now - arrivalTs : arrivalTs - now;
    const parts = splitDuration(diff);
    if (parts.seconds === lastSeconds) return;
    lastSeconds = parts.seconds;

    for (const unit of Object.keys(nums)) {
      const value = parts[unit];
      const text = unit === 'days' ? String(value) : pad(value);
      if (nums[unit].textContent !== text) {
        nums[unit].textContent = text;
        nums[unit].classList.remove('is-ticking');
        void nums[unit].offsetWidth;
        nums[unit].classList.add('is-ticking');
      }
      labels[unit].textContent = plural(value, content.hero.units[unit]);
    }
    phraseEl.textContent = arrived ? t('hero.phrase') : phraseFor(diff);
  }

  onPhase((phase) => {
    lastSeconds = -1;
    renderStatic();
    tick();
    sky.setArrived(phase === 'arrived');
    if (phase === 'arrived') {
      celebrate();
      // Возвращаем наверх, чтобы она увидела новый первый экран, — но не мешаем, если она что-то печатает
      const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
      if (!typing) window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  // Тап по «ты здесь.» — ещё конфетти
  titleEl.addEventListener('click', () => {
    if (getPhase() === 'arrived') celebrate();
  });

  renderStatic();
  tick();
  sky.setArrived(getPhase() === 'arrived');
  if (getPhase() === 'arrived') setTimeout(celebrate, 500);
  setInterval(tick, 250);
}
