/**
 * Loaded with React.lazy so the app shell renders before Ketcher (PLAN §9).
 * The binaryWasm entry keeps the Indigo WASM as a separate, cacheable asset.
 */
import 'ketcher-react/dist/index.css'

import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone/dist/binaryWasm'

import { attach } from './bridge'

// Created once at module scope: one Indigo worker for the whole app.
const structServiceProvider = new StandaloneStructServiceProvider()

export default function KetcherEditor() {
  return (
    <Editor
      staticResourcesUrl=""
      structServiceProvider={structServiceProvider}
      disableMacromoleculesEditor
      errorHandler={(message: string) => console.error('[ketcher]', message)}
      onInit={(ketcher) => {
        attach(ketcher)
      }}
    />
  )
}
