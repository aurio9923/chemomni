import { create } from 'zustand'

export interface Toast {
  id: number
  message: string
  action?: { label: string; run: () => void }
}

interface ToastState {
  toasts: Toast[]
  show: (message: string, action?: Toast['action'], ms?: number) => void
  dismiss: (id: number) => void
}

let nextId = 1

export const useToast = create<ToastState>((set, get) => ({
  toasts: [],
  show: (message, action, ms = 8000) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts.slice(-2), { id, message, action }] }))
    setTimeout(() => get().dismiss(id), ms)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
