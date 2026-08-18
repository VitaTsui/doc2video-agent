import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Fixed port: tauri.conf.json's devUrl has to know where to look, and Vite
// silently moving to the next free port would leave the window blank.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // The component library and this app both import @iconify/react. Two
    // instances mean two icon registries: the icons we bake in land in ours,
    // the library's <Icon> looks in its own, finds nothing, and goes to the
    // network — which the desktop build's CSP blocks, so no icon appears.
    dedupe: ['@iconify/react', 'react', 'react-dom', 'antd'],
  },
  clearScreen: false,
  server: { port: 5273, strictPort: true },
  build: { target: 'es2022', outDir: 'dist', emptyOutDir: true },
})
