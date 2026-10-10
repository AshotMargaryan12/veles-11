// Дописывает в photos/captions.json все фото из папки, у которых ещё нет подписи.
// Существующие подписи не трогает. Запуск: npm run captions

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const photosDir = path.join(root, 'photos');
const captionsFile = path.join(photosDir, 'captions.json');
const IMAGE_RE = /\.(jpe?g|png|webp|avif|tiff?|heic|heif)$/i;

const captions = fs.existsSync(captionsFile) ? JSON.parse(fs.readFileSync(captionsFile, 'utf8')) : {};
const files = fs
  .readdirSync(photosDir)
  .filter((f) => IMAGE_RE.test(f) && !f.startsWith('.'))
  .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));

const added = files.filter((f) => !(f in captions));
for (const f of added) captions[f] = '';

fs.writeFileSync(captionsFile, JSON.stringify(captions, null, 2) + '\n');

const missing = files.filter((f) => !captions[f]);
console.log(`Фото в папке: ${files.length}. Добавлено в captions.json: ${added.length}.`);
if (missing.length) {
  console.log(`Без подписи (возьмётся запасная из content.js): ${missing.length}`);
  for (const f of missing) console.log('  ' + f);
}
