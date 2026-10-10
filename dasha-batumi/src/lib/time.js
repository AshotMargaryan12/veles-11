// Время прилёта задаётся как «стенное» время в конкретном часовом поясе.
// Здесь переводим его в точный момент (UTC), не завися от пояса телефона.

const BATUMI_FALLBACK_OFFSET = 4 * 3600e3; // если Intl вдруг не умеет в пояса

function tzOffset(ts, timeZone) {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const p = {};
  for (const { type, value } of dtf.formatToParts(new Date(ts))) p[type] = value;
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second);
  return asUtc - Math.floor(ts / 1000) * 1000;
}

export function zonedToUtc({ date, time, timeZone }) {
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  try {
    let ts = wall - tzOffset(wall, timeZone);
    ts = wall - tzOffset(ts, timeZone); // второй проход на случай перехода на летнее время
    return ts;
  } catch {
    return wall - BATUMI_FALLBACK_OFFSET;
  }
}

export function splitDuration(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return {
    days: Math.floor(total / 86400),
    hours: Math.floor((total % 86400) / 3600),
    minutes: Math.floor((total % 3600) / 60),
    seconds: total % 60,
  };
}

const pluralRules = new Intl.PluralRules('ru-RU');
export function plural(n, [one, few, many]) {
  const rule = pluralRules.select(n);
  return rule === 'one' ? one : rule === 'few' ? few : many;
}

export function formatArrival(ts, timeZone) {
  const fmt = (opts, tz) => {
    try {
      return new Intl.DateTimeFormat('ru-RU', { ...opts, timeZone: tz }).format(ts);
    } catch {
      return new Intl.DateTimeFormat('ru-RU', opts).format(ts);
    }
  };
  const timeOpts = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
  const there = fmt(timeOpts, timeZone);
  const here = fmt(timeOpts, undefined);
  return {
    weekday: fmt({ weekday: 'long' }, timeZone),
    date: fmt({ day: 'numeric', month: 'long' }, timeZone),
    time: there,
    localTime: here,
    sameZone: here === there,
  };
}

export const fill = (template, vars) =>
  String(template).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
