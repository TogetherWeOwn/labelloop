import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist/management-fixture',
    rollupOptions: { input: 'management-fixture.html' },
  },
});
