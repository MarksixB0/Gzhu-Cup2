import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // Electron 打包后用 file:// 载入 dist/index.html，资源必须走相对路径
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5180,
    strictPort: true,
  },
})
