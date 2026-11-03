import { createRoot } from 'react-dom/client'

import './index.css'
import App from './App.tsx'

// No StrictMode: Ketcher's editor does not tolerate double mounting.
createRoot(document.getElementById('root')!).render(<App />)
