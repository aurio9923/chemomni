import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { readdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { brotliCompress, constants, gzip } from 'node:zlib'

import { defineConfig, type Plugin } from 'vite'

/** Pre-compress build assets so the backend never compresses the 11 MB WASM on the fly. */
function precompress(): Plugin {
  const br = promisify(brotliCompress)
  const gz = promisify(gzip)
  return {
    name: 'chemomni-precompress',
    apply: 'build',
    async closeBundle() {
      const dir = join(import.meta.dirname, 'dist', 'assets')
      const files = (await readdir(dir)).filter((f) => /\.(js|css|wasm|svg|json)$/.test(f))
      await Promise.all(
        files.map(async (f) => {
          const data = await readFile(join(dir, f))
          if (data.length < 1024) return
          await writeFile(join(dir, `${f}.gz`), await gz(data, { level: 9 }))
          await writeFile(
            join(dir, `${f}.br`),
            await br(data, { params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: data.length } }),
          )
        }),
      )
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), precompress()],
  // Some of Ketcher's transitive dependencies (dragula, etc.) expect Node globals.
  define: {
    global: 'globalThis',
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV ?? 'development'),
  },
  optimizeDeps: {
    // keep `new Worker(new URL(..., import.meta.url))` resolvable inside the package
    exclude: ['ketcher-standalone'],
  },
  worker: { format: 'es' },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 4000,
  },
})
