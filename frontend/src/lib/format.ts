/** 展示层格式化工具。 */

export function money(value?: number | null, fallback = '—'): string {
  if (value === undefined || value === null || Number.isNaN(value)) return fallback
  return '¥' + value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function amount(value?: number | null, fallback = '—'): string {
  if (value === undefined || value === null || Number.isNaN(value)) return fallback
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function numberText(value?: number | null, fallback = '—'): string {
  if (value === undefined || value === null || Number.isNaN(value)) return fallback
  return String(value)
}

export function percent(value?: number | null): string {
  if (value === undefined || value === null) return '—'
  return `${Math.round(value * 100)}%`
}

export function dateText(value?: string | null): string {
  if (!value) return '—'
  return value.slice(0, 10)
}

export function dateTimeText(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function fileSize(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export const FILE_STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  processing: '识别中',
  pending_review: '待复核',
  completed: '已完成',
  failed: '失败',
}

export const FILE_STATUS_CLASS: Record<string, string> = {
  queued: 'bg-slate-100 text-slate-600 ring-slate-200',
  processing: 'bg-blue-50 text-blue-700 ring-blue-200',
  pending_review: 'bg-amber-50 text-amber-700 ring-amber-200',
  completed: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  failed: 'bg-red-50 text-red-700 ring-red-200',
}

export const INVOICE_STATUS_TEXT: Record<string, string> = {
  pending_review: '待复核',
  confirmed: '已入账',
}

export const INVOICE_STATUS_CLASS: Record<string, string> = {
  pending_review: 'bg-amber-50 text-amber-700 ring-amber-200',
  confirmed: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
}

export const JOB_STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  processing: '识别中',
  done: '已完成',
  partial: '部分失败',
  failed: '失败',
}

export const JOB_STATUS_CLASS: Record<string, string> = {
  queued: 'bg-slate-100 text-slate-600 ring-slate-200',
  processing: 'bg-blue-50 text-blue-700 ring-blue-200',
  done: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  partial: 'bg-amber-50 text-amber-700 ring-amber-200',
  failed: 'bg-red-50 text-red-700 ring-red-200',
}

export const RISK_CODE_TEXT: Record<string, string> = {
  missing_required: '关键字段缺失',
  invoice_number_format: '发票号码格式错误',
  amount_mismatch: '金额关系不平',
  amount_incomplete: '金额不完整',
  item_sum_mismatch: '明细与合计不符',
  duplicate_invoice: '疑似重复票据',
  duplicate_file: '文件重复上传',
  date_in_future: '日期异常（未来）',
  date_unparsable: '日期无法解析',
  low_confidence: '低置信字段',
  category_unmatched: '类别未命中',
  account_subject_missing: '缺少会计科目',
  manual_entry_required: '需人工录入',
  ocr_note: '识别说明',
}

export function riskText(code: string): string {
  return RISK_CODE_TEXT[code] ?? code
}

export function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** 关键金额字段的勾稽差额，用于复核页实时提示 */
export function amountDiff(
  a?: number | null,
  t?: number | null,
  total?: number | null,
): number | null {
  if (a === undefined || a === null || t === null || t === undefined || total === undefined || total === null) {
    return null
  }
  return Math.round((a + t - total) * 100) / 100
}
