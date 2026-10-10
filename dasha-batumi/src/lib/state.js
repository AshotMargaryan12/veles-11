// Общее состояние сайта: «ждём» или «она уже здесь».
// Тексты берутся из content.js; в режиме arrived — из блока arrived, если он есть.

import * as content from '../content.js';
import { zonedToUtc } from './time.js';

const params = new URLSearchParams(location.search);
const preview = params.get('preview');

export const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

// ?preview=arrived — сразу показать «ты здесь»
// ?preview=20      — таймер дойдёт до нуля через 20 секунд (проверить переход)
const realArrival = zonedToUtc(content.ARRIVAL);
export const arrivalTs =
  preview === 'arrived' ? Date.now() : /^\d+$/.test(preview || '') ? Date.now() + Number(preview) * 1000 : realArrival;
export const realArrivalTs = realArrival;
export const isPreview = preview !== null;

let phase = Date.now() >= arrivalTs ? 'arrived' : 'countdown';
const listeners = new Set();

export const getPhase = () => phase;
export const isArrived = () => phase === 'arrived';

export function setPhase(next) {
  if (next === phase) return;
  phase = next;
  document.documentElement.classList.toggle('is-arrived', phase === 'arrived');
  applyTexts();
  listeners.forEach((fn) => fn(phase));
}

export function onPhase(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// t('map.title') → content.map.arrived.title (если она здесь) или content.map.title
export function t(path) {
  const [section, ...rest] = path.split('.');
  const src = content[section];
  if (!src) return '';
  const get = (obj) => rest.reduce((o, k) => (o == null ? undefined : o[k]), obj);
  const value = phase === 'arrived' ? get(src.arrived) ?? get(src) : get(src);
  return value ?? '';
}

export function applyTexts(root = document) {
  root.querySelectorAll('[data-t]').forEach((el) => {
    const text = t(el.dataset.t);
    el.textContent = text;
    el.hidden = !text;
  });
}

document.documentElement.classList.toggle('is-arrived', phase === 'arrived');

export { content };
