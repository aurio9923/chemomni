import { lazy, Suspense, useEffect } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'

import { ChatPanel } from './components/ChatPanel'
import { PropertyBar } from './components/PropertyBar'
import { TabBar } from './components/TabBar'
import { Toasts } from './components/Toasts'
import { useWorkspace } from './store/workspace'

const KetcherEditor = lazy(() => import('./ketcher/KetcherEditor'))

export default function App() {
  const init = useWorkspace((s) => s.init)
  const error = useWorkspace((s) => s.error)

  useEffect(() => {
    void init()
  }, [init])

  useEffect(() => {
    const flush = () => void useWorkspace.getState().flush()
    window.addEventListener('pagehide', flush)
    return () => window.removeEventListener('pagehide', flush)
  }, [])

  return (
    <div className="flex h-dvh flex-col bg-panel text-ink">
      <Group orientation="horizontal" className="min-h-0 flex-1">
        <Panel defaultSize="60%" minSize="35%">
          <main className="flex h-full min-w-0 flex-col">
            <TabBar />
            <div className="relative min-h-0 flex-1 bg-white" data-testid="editor-host">
              <Suspense fallback={<div className="p-6 text-sm text-muted">Loading editor…</div>}>
                <KetcherEditor />
              </Suspense>
              <Toasts />
            </div>
            <PropertyBar />
          </main>
        </Panel>
        <Separator className="w-px bg-line transition-colors hover:bg-accent data-[separator=active]:bg-accent" />
        <Panel defaultSize="40%" minSize="25%">
          <ChatPanel />
        </Panel>
      </Group>
      {error && (
        <div role="alert" className="absolute right-4 bottom-4 rounded-md bg-danger px-3 py-2 text-sm text-white">
          {error}
        </div>
      )}
    </div>
  )
}
