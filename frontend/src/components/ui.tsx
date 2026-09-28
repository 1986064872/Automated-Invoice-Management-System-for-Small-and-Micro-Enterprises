/** 共用 UI 组件（Tailwind 手写，不引入 shadcn 的 CLI 依赖）。 */

import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, Loader2, X } from 'lucide-react'

// --------------------------------------------------------------------------
// 容器
// --------------------------------------------------------------------------
export function Card({
  children,
  className = '',
  padded = true,
}: {
  children: ReactNode
  className?: string
  padded?: boolean
}) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white shadow-sm ${padded ? 'p-5' : ''} ${className}`}
    >
      {children}
    </section>
  )
}

export function CardTitle({
  title,
  subtitle,
  right,
}: {
  title: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
}) {
  return (
    <header className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-[15px] font-semibold text-slate-800">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p> : null}
      </div>
      {right}
    </header>
  )
}

export function PageHeader({
  title,
  description,
  right,
}: {
  title: string
  description?: string
  right?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
      </div>
      {right}
    </div>
  )
}

// --------------------------------------------------------------------------
// 徽章 / 提示
// --------------------------------------------------------------------------
export function Badge({
  children,
  className = 'bg-slate-100 text-slate-600 ring-slate-200',
  title,
}: {
  children: ReactNode
  className?: string
  /** 悬浮提示，用来放「为什么是这个状态」的说明 */
  title?: string
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${className}`}
    >
      {children}
    </span>
  )
}

export function Alert({
  level = 'warning',
  title,
  children,
  onClose,
}: {
  level?: 'error' | 'warning' | 'info' | 'success'
  title?: ReactNode
  children?: ReactNode
  onClose?: () => void
}) {
  const styles = {
    error: 'border-red-200 bg-red-50 text-red-800',
    warning: 'border-amber-200 bg-amber-50 text-amber-800',
    info: 'border-blue-200 bg-blue-50 text-blue-800',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  }[level]

  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${styles}`}>
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <div className="flex-1 leading-relaxed">
        {title ? <div className="font-semibold">{title}</div> : null}
        {children}
      </div>
      {onClose ? (
        <button onClick={onClose} className="shrink-0 rounded p-0.5 hover:bg-black/5">
          <X size={14} />
        </button>
      ) : null}
    </div>
  )
}

export function Spinner({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      <Inbox size={30} className="text-slate-300" />
      <div className="text-sm font-medium text-slate-600">{title}</div>
      {description ? <div className="max-w-md text-xs text-slate-400">{description}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

// --------------------------------------------------------------------------
// 按钮
// --------------------------------------------------------------------------
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'

export function Button({
  children,
  variant = 'secondary',
  size = 'md',
  className = '',
  ...rest
}: {
  children: ReactNode
  variant?: ButtonVariant
  size?: 'sm' | 'md'
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants: Record<ButtonVariant, string> = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-200',
    secondary:
      'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:text-slate-400',
    ghost: 'text-slate-600 hover:bg-slate-100',
    danger: 'bg-red-600 text-white hover:bg-red-700 disabled:bg-red-200',
    success: 'bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-emerald-200',
  }
  const sizes = { sm: 'px-2.5 py-1 text-xs', md: 'px-3.5 py-2 text-sm' }

  return (
    <button
      {...rest}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {children}
    </button>
  )
}

// --------------------------------------------------------------------------
// 表单
// --------------------------------------------------------------------------
export function Field({
  label,
  hint,
  badge,
  children,
  className = '',
}: {
  label: string
  hint?: string
  badge?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <label className={`block ${className}`}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs font-medium text-slate-600">{label}</span>
        {badge}
      </div>
      {children}
      {hint ? <p className="mt-1 text-[11px] text-slate-400">{hint}</p> : null}
    </label>
  )
}

const inputClass =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-slate-50'

export function TextInput({
  lowConfidence = false,
  className = '',
  ...rest
}: { lowConfidence?: boolean } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...rest}
      className={`${inputClass} ${
        lowConfidence ? 'border-amber-400 bg-amber-50/60 focus:border-amber-500 focus:ring-amber-100' : ''
      } ${className}`}
    />
  )
}

export function TextArea({
  className = '',
  rows = 2,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...rest}
      rows={rows}
      className={`${inputClass} min-h-[2.6rem] resize-y leading-relaxed ${className}`}
    />
  )
}

export function Select({
  className = '',
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={`${inputClass} ${className}`}>
      {children}
    </select>
  )
}

export function ProgressBar({ value, className = '' }: { value: number; className?: string }) {
  return (
    <div className={`h-2 w-full overflow-hidden rounded-full bg-slate-100 ${className}`}>
      <div
        className="h-full rounded-full bg-brand-500 transition-all duration-300"
        style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
      />
    </div>
  )
}

// --------------------------------------------------------------------------
// 轻量确认框（浏览器原生 confirm 太丑，且无法自定义文案样式）
// --------------------------------------------------------------------------
export function Modal({
  open,
  title,
  children,
  footer,
  onClose,
  width = 'max-w-lg',
}: {
  open: boolean
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  width?: string
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className={`animate-in w-full ${width} rounded-xl bg-white shadow-xl`}>
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5">
          <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100">
            <X size={16} />
          </button>
        </header>
        <div className="max-h-[70vh] overflow-auto px-5 py-4 text-sm text-slate-700">{children}</div>
        {footer ? (
          <footer className="flex justify-end gap-2 border-t border-slate-200 px-5 py-3">{footer}</footer>
        ) : null}
      </div>
    </div>
  )
}
