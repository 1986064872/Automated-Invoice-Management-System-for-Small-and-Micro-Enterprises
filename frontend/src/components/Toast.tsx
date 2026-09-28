import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { CheckCircle2, Info, XCircle } from 'lucide-react'

type ToastKind = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}

const ToastContext = createContext<(kind: ToastKind, text: string) => void>(() => {})

export function useToast() {
  return useContext(ToastContext)
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const push = useCallback((kind: ToastKind, text: string) => {
    const id = Date.now() + Math.random()
    setItems((prev) => [...prev, { id, kind, text }])
    window.setTimeout(() => setItems((prev) => prev.filter((i) => i.id !== id)), 4500)
  }, [])

  const icons = {
    success: <CheckCircle2 size={16} className="text-emerald-600" />,
    error: <XCircle size={16} className="text-red-600" />,
    info: <Info size={16} className="text-blue-600" />,
  }
  const borders = {
    success: 'border-emerald-200',
    error: 'border-red-200',
    info: 'border-blue-200',
  }

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[22rem] flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            className={`animate-in pointer-events-auto flex items-start gap-2 rounded-lg border bg-white px-3.5 py-2.5 text-sm text-slate-700 shadow-lg ${borders[item.kind]}`}
          >
            <span className="mt-0.5 shrink-0">{icons[item.kind]}</span>
            <span className="whitespace-pre-wrap leading-relaxed">{item.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
