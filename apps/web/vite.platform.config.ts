import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Separate acceptance build: fixtures are not an application navigation/auth route.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist/platform-fixture',
    rollupOptions: { input: 'platform-fixture.html' },
  },
});
