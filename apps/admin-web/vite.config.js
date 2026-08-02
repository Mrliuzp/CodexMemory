import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    server: {
      port: 5174,
      proxy: {
        '/api': {
          target: env.VITE_ADMIN_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      sourcemap: true,
      chunkSizeWarningLimit: 500,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules/@element-plus/icons-vue')) return 'element-icons'
            if (id.includes('node_modules/@vueuse')) return 'vue-use'
            if (['node_modules/dayjs', 'node_modules/async-validator', 'node_modules/lodash-unified', 'node_modules/@ctrl/tinycolor'].some((entry) => id.includes(entry))) return 'element-utils'
            if (id.includes('node_modules/vue') || id.includes('node_modules/pinia') || id.includes('node_modules/vue-router')) return 'vue-core'
          },
        },
      },
    },
    test: {
      environment: 'node',
      setupFiles: './src/test/setup.js',
      coverage: {
        reporter: ['text', 'json-summary'],
        include: ['src/**/*.{js,vue}'],
      },
    },
  }
})
