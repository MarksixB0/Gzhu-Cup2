import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// 开发时把 /api 代理到本地 FastAPI 服务(entrypoints/api.py)。
// 构建产物给 Electron 主窗口用 file:// 加载，因此 base 必须是相对路径。
export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
}))
