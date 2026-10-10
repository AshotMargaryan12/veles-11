import { defineConfig, loadEnv } from 'vite';
import photos from './plugins/photos.js';
import { meta } from './src/content.js';

const escapeHtml = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

// Заголовок и описание для вкладки и превью ссылки берутся из content.js
function htmlMeta(env) {
  return {
    name: 'dasha-meta',
    transformIndexHtml(html) {
      const site = (env.VITE_SITE_URL || '').replace(/\/+$/, '');
      const ogImage = site
        ? `<meta property="og:image" content="${escapeHtml(site)}/og.jpg" />\n    <meta property="og:url" content="${escapeHtml(site)}/" />`
        : '';
      return html
        .replaceAll('%META_TITLE%', escapeHtml(meta.title))
        .replaceAll('%META_DESCRIPTION%', escapeHtml(meta.description))
        .replace('<!-- %OG_IMAGE% -->', ogImage);
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd());
  return {
    // Относительные пути: сайт работает и в корне домена (Vercel),
    // и в подпапке (GitHub Pages: username.github.io/repo/)
    base: './',
    plugins: [photos({ dir: 'photos' }), htmlMeta(env)],
    build: {
      target: 'es2019',
      assetsInlineLimit: 0,
    },
  };
});
