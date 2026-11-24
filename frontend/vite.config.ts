import react from '@vitejs/plugin-react';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

const version = process.env.GIT_COMMIT || 'dev';

/** Writes dist/sw.js with the build's version and the list of files to precache. */
function serviceWorker(): Plugin {
  let outDir = 'dist';
  return {
    name: 'spend-sw',
    apply: 'build',
    configResolved(c) { outDir = join(c.root, c.build.outDir); },
    closeBundle() {
      const walk = (dir: string): string[] => readdirSync(dir, { withFileTypes: true })
        .flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
      const files = walk(outDir)
        .map((f) => '/' + relative(outDir, f).split('\\').join('/'))
        .filter((f) => f !== '/sw.js' && !f.endsWith('.map'));
      const stamp = version === 'dev' ? 'dev-' + Date.now() : version;
      const sw = readFileSync(join(__dirname, 'src/sw-template.js'), 'utf8')
        .replaceAll('__VERSION__', stamp).replaceAll('__FILES__', JSON.stringify(files.sort()));
      if (sw.includes('__FILES__') || sw.includes('__VERSION__')) throw new Error('sw.js placeholders were not filled in');
      writeFileSync(join(outDir, 'sw.js'), sw);
    },
  };
}

export default defineConfig({
  plugins: [react(), serviceWorker()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: { port: 5184, proxy: { '/api': 'http://127.0.0.1:8001' } },
  test: { environment: 'jsdom', setupFiles: ['src/test-setup.ts'] },
});
