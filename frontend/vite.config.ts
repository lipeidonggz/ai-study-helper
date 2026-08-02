import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 开发环境代理：/api 转发到后端
    proxy: {
      '/api': 'http://127.0.0.1:8000'
    }
  }
})
