// «Её мнение»: вопросы карточками по одному, в конце свободное поле и отправка.

import { content, isArrived } from '../lib/state.js';
import { fill } from '../lib/time.js';
import { sendAnswers, sendMode, shareAnswers } from '../lib/send.js';
import { pop } from '../lib/confetti.js';

const STORE_KEY = 'dasha.quiz';
const LETTERS = ['а', 'б', 'в', 'г', 'д', 'е'];

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
  } catch {
    return {};
  }
}
function save(data) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
  } catch {
    /* ничего страшного */
  }
}

export function initQuiz() {
  const q = content.quiz;
  const root = document.querySelector('.quiz');
  const steps = [...q.questions.map((x) => ({ kind: 'choice', ...x })), { kind: 'free', ...q.free }];
  const total = steps.length;

  const stored = load();
  const answers = stored.answers || {};
  let step = 0;
  let sent = Boolean(stored.sent);
  let busy = false;

  root.innerHTML = `
    <div class="quiz__card">
      <div class="quiz__progress" aria-hidden="true">${steps.map(() => '<span></span>').join('')}</div>
      <p class="quiz__step"></p>
      <div class="quiz__slide"></div>
      <button class="quiz__back chip chip--light" type="button"><span aria-hidden="true">‹</span> <span></span></button>
    </div>
    <div class="quiz__done" hidden>
      <svg class="quiz__plane" viewBox="0 0 120 80" aria-hidden="true">
        <path class="quiz__trail" d="M6 70 C 30 66 40 40 62 46 S 86 40 96 22" />
        <g class="quiz__plane-body">
          <path d="M84 18 L112 8 L96 34 L92 24 Z" />
          <path d="M92 24 L112 8 L88 22 Z" class="quiz__plane-fold" />
        </g>
      </svg>
      <h3 class="quiz__done-title"></h3>
      <p class="quiz__done-text"></p>
      <button class="chip chip--light quiz__edit" type="button"></button>
    </div>`;

  const card = root.querySelector('.quiz__card');
  const segs = [...root.querySelectorAll('.quiz__progress span')];
  const stepEl = root.querySelector('.quiz__step');
  const slide = root.querySelector('.quiz__slide');
  const backBtn = root.querySelector('.quiz__back');
  const done = root.querySelector('.quiz__done');
  backBtn.querySelector('span:last-child').textContent = q.back;
  root.querySelector('.quiz__edit').textContent = q.edit;

  const persist = () => save({ answers, sent });

  function render(direction = 0) {
    const s = steps[step];
    segs.forEach((el, i) => {
      el.classList.toggle('is-done', i < step || (steps[i].kind === 'choice' && answers[steps[i].id] !== undefined));
      el.classList.toggle('is-current', i === step);
    });
    stepEl.textContent = fill(q.step, { n: step + 1, total });
    backBtn.hidden = step === 0;

    slide.innerHTML = '';
    const h = document.createElement('h3');
    h.className = 'quiz__question';
    h.textContent = s.text;
    slide.append(h);

    if (s.kind === 'choice') {
      const grid = document.createElement('div');
      grid.className = 'quiz__options';
      s.options.forEach((label, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'option';
        b.setAttribute('aria-pressed', String(answers[s.id] === i));
        b.innerHTML = '<span class="option__letter"></span><span class="option__text"></span>';
        b.querySelector('.option__letter').textContent = LETTERS[i] || '•';
        b.querySelector('.option__text').textContent = label;
        b.addEventListener('click', () => choose(s, i, b));
        grid.append(b);
      });
      slide.append(grid);
    } else {
      const area = document.createElement('textarea');
      area.className = 'quiz__textarea';
      area.rows = 4;
      area.maxLength = 1500;
      area.placeholder = s.placeholder;
      area.value = answers[s.id] || '';
      area.setAttribute('aria-label', s.text);
      area.addEventListener('input', () => {
        answers[s.id] = area.value;
        persist();
      });
      const submit = document.createElement('button');
      submit.type = 'button';
      submit.className = 'btn quiz__submit';
      submit.textContent = q.submit;
      submit.addEventListener('click', () => send(submit));
      const error = document.createElement('div');
      error.className = 'quiz__error';
      error.hidden = true;
      slide.append(area, submit, error);
    }

    slide.classList.remove('is-from-right', 'is-from-left');
    if (direction) {
      void slide.offsetWidth;
      slide.classList.add(direction > 0 ? 'is-from-right' : 'is-from-left');
    }
  }

  function go(next) {
    const dir = Math.sign(next - step);
    step = Math.max(0, Math.min(total - 1, next));
    render(dir);
  }

  let advanceTimer = 0;
  function choose(s, i, button) {
    answers[s.id] = i;
    persist();
    slide.querySelectorAll('.option').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    clearTimeout(advanceTimer);
    advanceTimer = setTimeout(() => go(step + 1), 420);
  }

  function buildMessage() {
    const lines = [`${q.messageTitle}${isArrived() ? ' (уже в Батуми)' : ''}`, ''];
    const fields = {};
    for (const s of steps) {
      const raw = answers[s.id];
      const value =
        s.kind === 'choice' ? (raw !== undefined ? s.options[raw] : q.noAnswer) : (raw || '').trim() || q.noAnswer;
      lines.push(s.text, `→ ${value}`, '');
      fields[s.text] = value;
    }
    return { text: lines.join('\n').trim(), fields };
  }

  function showError(submit) {
    const box = slide.querySelector('.quiz__error');
    box.hidden = false;
    box.innerHTML = '<p></p><div class="quiz__error-actions"></div>';
    box.querySelector('p').textContent = q.errorText;
    const actions = box.querySelector('.quiz__error-actions');
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'chip chip--light';
    retry.textContent = q.retry;
    retry.addEventListener('click', () => send(submit));
    const share = document.createElement('button');
    share.type = 'button';
    share.className = 'chip chip--light';
    share.textContent = q.share;
    share.addEventListener('click', () => viaShare());
    actions.append(retry, share);
  }

  async function viaShare() {
    try {
      const how = await shareAnswers({ title: q.messageTitle, text: buildMessage().text });
      if (how === 'copied') success(q.copied, q.copiedTitle);
      else success(q.successText);
    } catch (err) {
      if (err?.name !== 'AbortError') console.warn('share failed', err);
    }
  }

  async function send(submit) {
    if (busy) return;
    if (sendMode === 'none') {
      viaShare();
      return;
    }
    busy = true;
    submit.disabled = true;
    submit.textContent = q.sending;
    slide.querySelector('.quiz__error').hidden = true;
    try {
      await sendAnswers(buildMessage());
      success(q.successText);
    } catch (err) {
      console.warn('send failed', err);
      submit.disabled = false;
      submit.textContent = q.submit;
      showError(submit);
    } finally {
      busy = false;
    }
  }

  function success(text, title = q.successTitle) {
    sent = true;
    persist();
    showDone(text, true, title);
  }

  function showDone(text, animate, title = q.successTitle) {
    root.querySelector('.quiz__done-title').textContent = title;
    root.querySelector('.quiz__done-text').textContent = text;
    card.hidden = true;
    done.hidden = false;
    done.classList.toggle('is-animated', animate);
    if (animate) {
      const r = done.getBoundingClientRect();
      pop(r.left + r.width / 2, r.top + 40, 30);
      done.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  root.querySelector('.quiz__edit').addEventListener('click', () => {
    done.hidden = true;
    card.hidden = false;
    go(0);
  });
  backBtn.addEventListener('click', () => go(step - 1));

  render();
  if (sent) showDone(q.successText, false);
}
