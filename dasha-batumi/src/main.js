import './styles.css';
import { applyTexts } from './lib/state.js';
import { initReveal } from './lib/reveal.js';
import { initHero } from './sections/hero.js';
import { initPolaroids } from './sections/polaroids.js';
import { initGame } from './sections/game.js';
import { initMap } from './sections/map.js';
import { initQuiz } from './sections/quiz.js';
import { initLetter, initMusic } from './sections/letter.js';

applyTexts();

// Если в одном блоке что-то сломается, остальные всё равно покажутся
for (const init of [initHero, initPolaroids, initGame, initMap, initQuiz, initLetter, initMusic]) {
  try {
    init();
  } catch (err) {
    console.error(`[${init.name}]`, err);
  }
}

applyTexts();
initReveal();
