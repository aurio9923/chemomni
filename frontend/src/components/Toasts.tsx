import { X } from 'lucide-react'

import { useToast } from '../store/toast'

export function Toasts() {
  const { toasts, dismiss } = useToast()
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none absolute bottom-12 left-4 z-50 flex flex-col gap-2" aria-live="polite">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          data-testid="toast"
          className="pointer-events-auto flex items-center gap-3 rounded-lg bg-ink px-3.5 py-2 text-sm text-white shadow-lg"
        >
          <span>{t.message}</span>
          {t.action && (
            <button
              type="button"
              className="font-medium text-sky-300 hover:text-sky-200"
              onClick={() => {
                t.action?.run()
                dismiss(t.id)
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" aria-label="Dismiss" className="text-white/60 hover:text-white" onClick={() => dismiss(t.id)}>
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
