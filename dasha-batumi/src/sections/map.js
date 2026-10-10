// Стилизованная схема Батуми с точками из content.js.
// Тап по точке — снизу появляется карточка «что мы там сделаем».

import { content, t, onPhase } from '../lib/state.js';

// Сглаженная линия через точки (Catmull-Rom → кривые Безье)
function smoothPath(pts) {
  if (pts.length < 2) return '';
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(2)} ${c1[1].toFixed(2)} ${c2[0].toFixed(2)} ${c2[1].toFixed(2)} ${p2[0]} ${p2[1]}`;
  }
  return d;
}

// Береговая линия: море слева, суша справа
const COAST = [
  [62, -2], [57, 7], [52.5, 15], [51.5, 23], [54.5, 32], [60, 41], [57, 47], [50, 50],
  [44, 53], [41, 60], [38, 72], [34, 86], [30, 100], [27, 114], [24, 132],
];
const BOULEVARD = [[46, 53.5], [43.6, 60], [40.8, 72], [36.9, 86], [33, 100], [30, 111]];
const WAVES = [[10, 18], [30, 10], [40, 26], [8, 46], [26, 58], [10, 74], [22, 86], [8, 100], [14, 116]];
const PALMS = [[37.5, 92], [34, 104], [42, 66]];

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function labelAttrs({ x, y, label = 'right' }) {
  switch (label) {
    case 'left':
      return `x="${x - 5.8}" y="${y + 1.4}" text-anchor="end"`;
    case 'top':
      return `x="${x}" y="${y - 6.2}" text-anchor="middle"`;
    case 'bottom':
      return `x="${x}" y="${y + 9.6}" text-anchor="middle"`;
    default:
      return `x="${x + 5.8}" y="${y + 1.4}" text-anchor="start"`;
  }
}

function buildSvg(m) {
  const land = `${smoothPath(COAST)} L100 132 L100 -2 Z`;
  const route = smoothPath(m.points.map((p) => [p.x, p.y]));
  const cable = m.points.find((p) => p.id === 'cablecar');

  const waves = WAVES.map(([x, y]) => `<path d="M${x} ${y} q1.6 -1.6 3.2 0 t3.2 0" />`).join('');
  const palms = PALMS.map(
    ([x, y]) => `<g transform="translate(${x} ${y})"><path d="M0 0 q0.4 -2.6 0 -4.6" /><path d="M0 -4.6 q-1.8 -0.6 -2.8 0.6 M0 -4.6 q1.8 -0.6 2.8 0.6 M0 -4.6 q-1 -1.6 -2.4 -1.4 M0 -4.6 q1 -1.6 2.4 -1.4" /></g>`,
  ).join('');

  const pins = m.points
    .map(
      (p, i) => `
      <g class="pin" data-i="${i}" role="button" tabindex="0" aria-label="${i + 1}. ${esc(p.title)}" transform="translate(${p.x} ${p.y})">
        <circle class="pin__hit" r="7.5" />
        <circle class="pin__pulse" r="4.2" />
        <circle class="pin__dot" r="4.2" />
        <text class="pin__num" y="1.55">${i + 1}</text>
      </g>
      <text class="pin__label" data-i="${i}" ${labelAttrs(p)}>${esc(p.title)}</text>`,
    )
    .join('');

  return `
  <svg class="map__svg" viewBox="0 0 100 130" role="img" aria-label="${esc(m.title)}">
    <defs>
      <filter id="map-rough" x="-5%" y="-5%" width="110%" height="110%">
        <feTurbulence type="fractalNoise" baseFrequency="0.06" numOctaves="2" seed="7" />
        <feDisplacementMap in="SourceGraphic" scale="1.1" />
      </filter>
      <linearGradient id="map-sea" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#2f6f9f" />
        <stop offset="1" stop-color="#173a63" />
      </linearGradient>
      <mask id="map-route-mask" maskUnits="userSpaceOnUse">
        <path class="map__route-mask" d="${route}" pathLength="1" />
      </mask>
    </defs>

    <rect class="map__sea" width="100" height="130" fill="url(#map-sea)" />
    <g class="map__waves">${waves}</g>
    <path class="map__land" d="${land}" filter="url(#map-rough)" />
    <path class="map__boulevard" d="${smoothPath(BOULEVARD)}" />
    <g class="map__palms">${palms}</g>

    <g class="map__hills">
      <path d="M78 96 L83.5 88 L89 96" />
      <path d="M84 108 L90 99 L96 108" />
      <path d="M86 82 L91 74.5 L96 82" />
      <path d="M74 116 L79 109 L84 116" />
    </g>
    <text class="map__tag" x="90" y="124" text-anchor="middle">${esc(m.mountains)}</text>
    <text class="map__tag map__tag--sea" x="17" y="64" transform="rotate(-68 17 64)" text-anchor="middle">${esc(m.sea)}</text>
    <text class="map__city" x="61" y="101" text-anchor="middle">${esc(m.city)}</text>

    ${
      cable
        ? `<g class="map__cable"><path d="M60.5 48 L${cable.x} ${cable.y}" /><rect x="${(60.5 + cable.x) / 2 - 1.3}" y="${(48 + cable.y) / 2 - 0.4}" width="2.6" height="2.2" rx="0.5" /></g>`
        : ''
    }

    <path class="map__route" d="${route}" mask="url(#map-route-mask)" />

    <g class="map__compass" transform="translate(10 122)">
      <circle r="4.6" />
      <path d="M0 -3.4 L1.2 0.4 L0 -0.4 L-1.2 0.4 Z" />
      <text y="-5.8" text-anchor="middle">С</text>
    </g>

    ${pins}
  </svg>`;
}

export function initMap() {
  const m = content.map;
  const root = document.querySelector('.map');
  root.innerHTML = `
    ${buildSvg(m)}
    <div class="map__card" aria-live="polite">
      <p class="map__hint"></p>
      <div class="map__info" hidden>
        <p class="map__when"><span class="map__num"></span><span class="map__when-text"></span></p>
        <h3 class="map__title"></h3>
        <p class="map__text"></p>
        <button class="chip chip--solid map__next" type="button"><span></span> <span aria-hidden="true">›</span></button>
      </div>
    </div>`;

  const svg = root.querySelector('svg');
  const hint = root.querySelector('.map__hint');
  const info = root.querySelector('.map__info');
  hint.textContent = m.hint;
  root.querySelector('.map__next span').textContent = m.next;

  let active = -1;

  function select(i) {
    active = i;
    const p = m.points[i];
    svg.classList.add('has-active');
    svg.querySelectorAll('.pin, .pin__label').forEach((el) => el.classList.toggle('is-active', Number(el.dataset.i) === i));
    hint.hidden = true;
    info.hidden = false;
    root.querySelector('.map__num').textContent = String(i + 1);
    root.querySelector('.map__when-text').textContent = p.when || '';
    root.querySelector('.map__title').textContent = p.title;
    root.querySelector('.map__text').textContent = p.text;
    info.classList.remove('is-swapping');
    void info.offsetWidth;
    info.classList.add('is-swapping');
  }

  svg.addEventListener('click', (e) => {
    const target = e.target.closest('.pin, .pin__label');
    if (target) select(Number(target.dataset.i));
  });
  svg.addEventListener('keydown', (e) => {
    const pin = e.target.closest('.pin');
    if (pin && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      select(Number(pin.dataset.i));
    }
  });
  root.querySelector('.map__next').addEventListener('click', () => select((active + 1) % m.points.length));

  // Подпись карты для скринридера тоже меняется после прилёта
  const syncLabel = () => svg.setAttribute('aria-label', t('map.title'));
  onPhase(syncLabel);
  syncLabel();
}
