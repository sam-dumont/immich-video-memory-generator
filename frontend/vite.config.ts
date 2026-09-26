import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte({ compilerOptions: { customElement: true } })],
  build: {
    outDir: '../src/immich_memories/ui/static/review',
    emptyOutDir: true,
    license: { fileName: 'licenses.md' },
    lib: { entry: 'src/CutReview.svelte', formats: ['es'], fileName: () => 'review.js' },
  },
});
