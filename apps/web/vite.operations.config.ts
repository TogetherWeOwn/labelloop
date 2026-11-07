import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Separate output: fixtures cannot become the production application entry.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist/operations', rollupOptions: { input: 'operational-fixtures.html' } },
});
