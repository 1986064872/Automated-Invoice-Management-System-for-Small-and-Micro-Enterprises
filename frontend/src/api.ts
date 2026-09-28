/** 后端 API 封装。所有路径都用相对地址，靠 Vite 代理转发到 FastAPI。 */
import { notifyDataChanged } from './lib/refresh'

import type {
  CategoryRule,
  Dashboard,
  Invoice,
  InvoiceList,
  Job,
  LedgerPage,
  ProviderInfo,
  RuleOptions,
  UploadResult,
} from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }

  /** 把后端返回的 detail 转成能直接显示的中文文案 */
  get message(): string {
    const d = this.detail as Record<string, unknown> | undefined
    if (!d) return '请求失败'
    if (typeof d === 'string') return d
    if (typeof d.message === 'string') return d.message
    if (Array.isArray((d as { errors?: unknown }).errors)) {
      const errors = (d as { errors: Array<{ message: string }> }).errors
      return errors.map((e) => e.message).join('\n')
    }
    return JSON.stringify(d)
  }
}

function qs(params?: Record<string, unknown>): string {
  if (!params) return ''
  const usable = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== '' && v !== false,
  )
  if (!usable.length) return ''
  return '?' + usable.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&')
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, init)
  if (!resp.ok) {
    let detail: unknown = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      detail = data?.detail ?? data
    } catch {
      /* 响应不是 JSON，保留状态码 */
    }
    throw new ApiError(resp.status, detail)
  }

  // 写操作成功后广播「数据变了」，让侧边栏角标、首页概览这类全局数据重新拉取。
  // 放在这里而不是各个调用点，是为了以后新增写操作时不会忘记通知。
  const method = (init?.method ?? 'GET').toUpperCase()
  if (method !== 'GET') {
    notifyDataChanged()
  }

  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  // ---------- 系统 ----------
  health: () => request<{ ok: boolean; ocr_provider: string }>('/health'),
  providers: (deep = false) =>
    request<{ providers: ProviderInfo[]; active: string }>(`/system/providers${qs({ deep })}`),
  config: () => request<Record<string, unknown>>('/system/config'),

  // ---------- 上传 ----------
  upload(files: File[], onProgress?: (percent: number) => void): Promise<UploadResult> {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    // 用 XHR 而不是 fetch：只有 XHR 能拿到上传进度
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/invoices/upload`)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
        } else {
          let detail: unknown = `HTTP ${xhr.status}`
          try {
            detail = JSON.parse(xhr.responseText)?.detail ?? detail
          } catch {
            /* ignore */
          }
          reject(new ApiError(xhr.status, detail))
        }
      }
      xhr.onerror = () => reject(new ApiError(0, '网络错误，请确认后端服务是否在运行'))
      xhr.send(form)
    })
  },

  // ---------- 批次 ----------
  getJob: (id: string) => request<Job>(`/jobs/${id}${qs({ with_files: true })}`),
  listJobs: (limit = 10) => request<Job[]>(`/jobs${qs({ limit })}`),

  // ---------- 票据 ----------
  listInvoices: (params?: Record<string, unknown>) => request<InvoiceList>(`/invoices${qs(params)}`),
  getInvoice: (id: string) => request<Invoice>(`/invoices/${id}`),
  updateInvoice: (id: string, body: Record<string, unknown>) =>
    request<Invoice>(`/invoices/${id}`, jsonInit('PATCH', body)),
  confirmInvoice: (id: string, confirmedBy = '本机用户') =>
    request<Invoice>(`/invoices/${id}/confirm`, jsonInit('POST', { confirmed_by: confirmedBy })),
  revokeInvoice: (id: string) => request<Invoice>(`/invoices/${id}/revoke`, jsonInit('POST')),
  retryInvoice: (id: string) => request<Invoice>(`/invoices/${id}/retry`, jsonInit('POST')),
  deleteInvoice: (id: string, deleteFile = true) =>
    request<{ ok: boolean; message: string }>(
      `/invoices/${id}${qs({ delete_file: deleteFile })}`,
      { method: 'DELETE' },
    ),
  /** 批量删除。原票一样走回收站；后端逐张提交，返回成功/失败明细 */
  batchDeleteInvoices: (ids: string[], deleteFile = true) =>
    request<{
      ok: boolean
      deleted: number
      failed: { id: string; reason: string }[]
      message: string
    }>('/invoices/batch-delete', jsonInit('POST', { ids, delete_file: deleteFile })),
  rawResponse: (id: string) =>
    request<{ provider: string; captured_at: string; raw: unknown }>(`/invoices/${id}/raw-response`),

  // ---------- 账本 ----------
  getLedger: (params?: Record<string, unknown>) => request<LedgerPage>(`/ledger${qs(params)}`),
  categories: () => request<{ categories: string[]; map: Record<string, string> }>('/ledger/categories'),

  // ---------- 导出 ----------
  exportPreview: (params: Record<string, unknown>) =>
    request<{ count: number; invoice_count: number; total_amount: number; file_name: string }>(
      `/exports/preview${qs(params)}`,
    ),

  async exportExcel(params: Record<string, unknown>): Promise<void> {
    const resp = await fetch(`${BASE}/exports/excel`, jsonInit('POST', params))
    if (!resp.ok) {
      let detail: unknown = `HTTP ${resp.status}`
      try {
        detail = (await resp.json())?.detail ?? detail
      } catch {
        /* ignore */
      }
      throw new ApiError(resp.status, detail)
    }
    // 文件名优先用后端 Content-Disposition 里的（已做过中文编码）
    const disposition = resp.headers.get('Content-Disposition') ?? ''
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
    const fileName = match ? decodeURIComponent(match[1]) : '费用明细.xlsx'

    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  // ---------- 规则 ----------
  listRules: (params?: Record<string, unknown>) => request<CategoryRule[]>(`/rules${qs(params)}`),
  ruleOptions: () => request<RuleOptions>('/rules/options'),
  createRule: (body: Record<string, unknown>) => request<CategoryRule>('/rules', jsonInit('POST', body)),
  updateRule: (id: string, body: Record<string, unknown>) =>
    request<CategoryRule>(`/rules/${id}`, jsonInit('PATCH', body)),
  deleteRule: (id: string) =>
    request<{ ok: boolean; message: string; disabled: boolean }>(`/rules/${id}`, { method: 'DELETE' }),
  toggleRule: (id: string) => request<CategoryRule>(`/rules/${id}/toggle`, jsonInit('POST')),
  moveRule: (id: string, direction: 'up' | 'down') =>
    request<CategoryRule[]>(`/rules/${id}/move${qs({ direction })}`, jsonInit('POST')),
  testRule: (params: { seller_name?: string; item_name?: string }) =>
    request<{ expense_category: string; rule_source: string; account_subject: string }>(
      `/rules/test${qs(params)}`,
    ),

  // ---------- 概览 ----------
  dashboard: (month?: string) => request<Dashboard>(`/dashboard/summary${qs({ month })}`),
}
