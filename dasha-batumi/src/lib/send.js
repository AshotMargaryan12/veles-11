// Отправка ответов Даши. Ключи — в .env (см. README).
// Telegram приоритетнее; если ключей нет совсем — sendMode === 'none',
// и сайт предложит переслать ответы через «Поделиться».

const env = import.meta.env;
const TG_TOKEN = (env.VITE_TELEGRAM_BOT_TOKEN || '').trim();
const TG_CHAT = (env.VITE_TELEGRAM_CHAT_ID || '').trim();
const FORMSPREE_ID = (env.VITE_FORMSPREE_ID || '').trim();

export const sendMode = TG_TOKEN && TG_CHAT ? 'telegram' : FORMSPREE_ID ? 'formspree' : 'none';

async function withTimeout(promise, ms = 12000) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('timeout')), ms);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

// text — готовое сообщение, fields — те же ответы по полям (для письма)
export async function sendAnswers({ text, fields }) {
  if (sendMode === 'telegram') {
    // form-urlencoded — «простой» запрос, браузер не делает лишний preflight
    const body = new URLSearchParams({ chat_id: TG_CHAT, text, disable_web_page_preview: 'true' });
    const res = await withTimeout(fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, { method: 'POST', body }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.description || `Telegram ${res.status}`);
    return;
  }

  if (sendMode === 'formspree') {
    const body = new FormData();
    body.append('_subject', 'Даша ответила на вопросы');
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    body.append('message', text);
    const res = await withTimeout(
      fetch(`https://formspree.io/f/${FORMSPREE_ID}`, { method: 'POST', body, headers: { Accept: 'application/json' } }),
    );
    if (!res.ok) throw new Error(`Formspree ${res.status}`);
    return;
  }

  throw new Error('no-backend');
}

export async function shareAnswers({ title, text }) {
  if (navigator.share) {
    await navigator.share({ title, text });
    return 'shared';
  }
  await navigator.clipboard.writeText(text);
  return 'copied';
}
