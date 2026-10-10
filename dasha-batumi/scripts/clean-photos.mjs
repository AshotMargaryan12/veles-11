// Готовит фото в photos/ к коммиту в публичный репозиторий:
// разворачивает по EXIF, уменьшает до 2048px по длинной стороне
// и удаляет все метаданные (геолокацию, модель телефона, дату).
// Уже чистые фото не трогает. Запуск: npm run photos:clean

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const photosDir = path.join(root, 'photos');
const MAX = 2048;
const IMAGE_RE = /\.(jpe?g|png|webp|avif|tiff?)$/i;

const files = fs.readdirSync(photosDir).filter((f) => IMAGE_RE.test(f) && !f.startsWith('.'));
let cleaned = 0;

for (const file of files) {
  const src = path.join(photosDir, file);
  const meta = await sharp(src).metadata();
  const hasMeta = Boolean(meta.exif || meta.xmp || meta.iptc || (meta.orientation && meta.orientation !== 1));
  const tooBig = Math.max(meta.width || 0, meta.height || 0) > MAX;
  if (!hasMeta && !tooBig) continue;

  const out = file.replace(/\.[^.]+$/, '.jpg');
  const buf = await sharp(src, { failOn: 'none' })
    .rotate()
    .resize({ width: MAX, height: MAX, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 88, mozjpeg: true })
    .toBuffer(); // без .withMetadata() — sharp ничего из EXIF не переносит
  fs.writeFileSync(path.join(photosDir, out), buf);
  if (out !== file) fs.unlinkSync(src);
  cleaned++;
  console.log(`  ${file} → ${out} (${Math.round(buf.length / 1024)} КБ)`);
}

console.log(`Готово: почищено ${cleaned} из ${files.length}.`);
