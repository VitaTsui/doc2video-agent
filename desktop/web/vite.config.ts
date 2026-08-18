import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Fixed port: tauri.conf.json's devUrl has to know where to look, and Vite
// silently moving to the next free port would leave the window blank.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5273, strictPort: true },
  build: { target: 'es2022', outDir: 'dist', emptyOutDir: true },
})
