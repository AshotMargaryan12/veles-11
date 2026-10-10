// Плагин подхватывает все фото из папки photos/, сжимает их в webp
// и отдаёт список в код как модуль `virtual:photos`.
//
// Каждое фото ужимается до 1100px по длинной стороне (хватает для экрана
// телефона с плотными пикселями) и весит обычно 80–200 КБ.
//
// Результаты кешируются в node_modules/.photos-cache, поэтому повторный
// запуск не пережимает уже готовые файлы.

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import sharp from 'sharp';

const VIRTUAL_ID = 'virtual:photos';
const RESOLVED_ID = '\0' + VIRTUAL_ID;
const IMAGE_RE = /\.(jpe?g|png|webp|avif|tiff?|heic|heif)$/i;
const URL_PREFIX = 'img/';

const SIZES = {
  card: { size: 1100, quality: 78 },
};

const naturalSort = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });

export default function photosPlugin({ dir = 'photos' } = {}) {
  let root;
  let photosDir;
  let cacheDir;
  let isBuild = false;
  let manifest = [];
  let pending = null;

  async function processOne(file) {
    const src = path.join(photosDir, file);
    const stat = fs.statSync(src);
    const hash = crypto
      .createHash('sha1')
      .update(`${file}:${stat.size}:${stat.mtimeMs}:${JSON.stringify(SIZES)}`)
      .digest('hex')
      .slice(0, 10);
    const base = file.replace(/\.[^.]+$/, '').replace(/[^\w-]+/g, '_').slice(0, 40) || 'photo';

    const entry = { file };
    for (const [kind, { size, quality }] of Object.entries(SIZES)) {
      const name = `${base}-${hash}-${kind}.webp`;
      const out = path.join(cacheDir, name);
      const metaOut = out + '.json';
      if (!fs.existsSync(out) || !fs.existsSync(metaOut)) {
        const info = await sharp(src, { failOn: 'none' })
          .rotate() // учитываем ориентацию из EXIF (вертикальные фото с телефона)
          .resize({ width: size, height: size, fit: 'inside', withoutEnlargement: true })
          .webp({ quality, effort: 5 })
          .toFile(out);
        fs.writeFileSync(metaOut, JSON.stringify({ w: info.width, h: info.height }));
      }
      const { w, h } = JSON.parse(fs.readFileSync(metaOut, 'utf8'));
      entry[kind] = URL_PREFIX + name;
      if (kind === 'card') Object.assign(entry, { w, h });
    }
    return entry;
  }

  async function processAll() {
    fs.mkdirSync(cacheDir, { recursive: true });
    const files = fs.existsSync(photosDir)
      ? fs.readdirSync(photosDir).filter((f) => IMAGE_RE.test(f) && !f.startsWith('.')).sort(naturalSort)
      : [];

    const result = [];
    for (const file of files) {
      try {
        result.push(await processOne(file));
      } catch (err) {
        const hint = /\.hei[cf]$/i.test(file) ? ' (HEIC не поддерживается — сохрани это фото как JPG)' : '';
        console.warn(`\n[photos] Пропускаю ${file}: ${err.message}${hint}`);
      }
    }
    manifest = result;
    console.log(`[photos] Фото в альбоме: ${manifest.length}`);
    return manifest;
  }

  function refresh() {
    pending = processAll().finally(() => {
      pending = null;
    });
    return pending;
  }

  return {
    name: 'dasha-photos',

    configResolved(config) {
      root = config.root;
      isBuild = config.command === 'build';
      photosDir = path.resolve(root, dir);
      cacheDir = path.resolve(root, 'node_modules/.photos-cache');
    },

    async buildStart() {
      await refresh();
    },

    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_ID;
    },

    async load(id) {
      if (id !== RESOLVED_ID) return;
      if (pending) await pending;
      if (isBuild) {
        for (const p of manifest) {
          for (const kind of Object.keys(SIZES)) {
            const name = p[kind].slice(URL_PREFIX.length);
            this.emitFile({
              type: 'asset',
              fileName: p[kind],
              source: fs.readFileSync(path.join(cacheDir, name)),
            });
          }
        }
      }
      return `export default ${JSON.stringify(manifest)};`;
    },

    configureServer(server) {
      // Отдаём сжатые фото в режиме разработки
      server.middlewares.use((req, res, next) => {
        const url = decodeURIComponent((req.url || '').split('?')[0]);
        const match = url.match(/\/img\/([^/]+\.webp)$/);
        if (!match) return next();
        const file = path.join(cacheDir, match[1]);
        if (!fs.existsSync(file)) return next();
        res.setHeader('Content-Type', 'image/webp');
        res.setHeader('Cache-Control', 'no-cache');
        fs.createReadStream(file).pipe(res);
      });

      // Добавил или удалил фото — пересобираем список и перезагружаем страницу
      server.watcher.add(photosDir);
      const onChange = async (file) => {
        if (!file.startsWith(photosDir) || !IMAGE_RE.test(file)) return;
        await refresh();
        const mod = server.moduleGraph.getModuleById(RESOLVED_ID);
        if (mod) server.moduleGraph.invalidateModule(mod);
        server.ws.send({ type: 'full-reload' });
      };
      server.watcher.on('add', onChange);
      server.watcher.on('unlink', onChange);
      server.watcher.on('change', onChange);
    },
  };
}
