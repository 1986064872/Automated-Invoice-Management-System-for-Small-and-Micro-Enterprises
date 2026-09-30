/** 与后端 app/schemas.py 一一对应的类型定义。 */

export type RiskLevel = 'error' | 'warning'

export interface RiskFlag {
  code: string
  level: RiskLevel
  message: string
  field?: string
}

export interface InvoiceItem {
  id: string
  item_name?: string | null
  specification?: string | null
  unit?: string | null
  quantity?: number | null
  unit_price?: number | null
  tax_rate?: string | null
  amount?: number | null
  tax_amount?: number | null
  /** 明细行备注：折扣/退货（金额为负）这类行用来写清原因，导出时该备注单元格会高亮 */
  remark?: string | null
}

export type FileStatus = 'queued' | 'processing' | 'pending_review' | 'completed' | 'failed'
export type InvoiceStatus = 'pending_review' | 'confirmed'

export interface InvoiceFileInfo {
  id: string
  job_id?: string | null
  invoice_id?: string | null
  original_name: string
  file_type: string
  file_size: number
  page_count: number
  status: FileStatus
  error_code?: string | null
  error_message?: string | null
  ocr_provider?: string | null
  created_at?: string | null
  updated_at?: string | null
  raw_url: string
}

export interface Invoice {
  id: string
  file_id: string
  invoice_type?: string | null
  invoice_code?: string | null
  invoice_number?: string | null
  invoice_date?: string | null
  seller_name?: string | null
  seller_tax_id?: string | null
  buyer_name?: string | null
  buyer_tax_id?: string | null
  amount_without_tax?: number | null
  tax_amount?: number | null
  total_amount?: number | null
  currency?: string | null
  expense_category?: string | null
  account_subject?: string | null
  confidence?: number | null
  field_confidence?: Record<string, number>
  rule_source?: string | null
  risk_flags: RiskFlag[]
  dedupe_key?: string | null
  status: InvoiceStatus
  edit_history?: Array<Record<string, unknown>>
  source_note?: string | null
  /**
   * 发票备注栏的原文（识别自票面左下角，可能多行）。
   * 工程类发票在这里写开户银行/账号/工程名称/工程地址 —— 属于**发票级**信息。
   * 某一行明细自己的备注看 `items[].remark`。
   */
  invoice_remark?: string | null
  confirmed_by?: string | null
  confirmed_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  items: InvoiceItem[]
  file?: InvoiceFileInfo | null
  error_count: number
  warning_count: number
  raw_url: string
}

export interface Job {
  id: string
  total: number
  succeeded: number
  failed: number
  pending_review: number
  completed: number
  status: 'queued' | 'processing' | 'done' | 'partial' | 'failed'
  ocr_provider?: string | null
  error_message?: string | null
  created_at?: string | null
  finished_at?: string | null
  progress: number
  files: InvoiceFileInfo[]
}

export interface UploadResult {
  job_id: string | null
  accepted: InvoiceFileInfo[]
  rejected: Array<{ name: string; reason: string }>
  message: string
}

export interface CategoryStat {
  category: string
  count: number
  total_amount: number
}

export interface Summary {
  count: number
  total_amount: number
  amount_without_tax: number
  tax_amount: number
  by_category: CategoryStat[]
}

export interface InvoiceList {
  items: Invoice[]
  total: number
  page: number
  page_size: number
  summary: Summary
}

export interface LedgerRow {
  id: string
  invoice_id: string
  entry_date?: string | null
  invoice_type?: string | null
  invoice_code?: string | null
  invoice_number?: string | null
  seller_name?: string | null
  seller_tax_id?: string | null
  buyer_name?: string | null
  direction: 'input' | 'output' | 'unknown'
  direction_text: string
  company_role: string
  counterparty_name: string
  counterparty_tax_id: string
  direction_reason: string
  item_name?: string | null
  amount_without_tax?: number | null
  tax_amount?: number | null
  total_amount?: number | null
  expense_category?: string | null
  account_subject?: string | null
  original_name?: string | null
  status: InvoiceStatus
  file_id?: string
  confirmed_by?: string | null
  confirmed_at?: string | null
  confidence?: number | null
  error_count: number
  warning_count: number
  raw_url: string
}

export interface LedgerPage {
  items: LedgerRow[]
  summary: Summary
  total: number
  page: number
  page_size: number
}

export interface CategoryRule {
  id: string
  rule_type: 'custom' | 'system'
  match_field: 'seller_name' | 'item_name'
  keyword: string
  expense_category: string
  account_subject?: string | null
  priority: number
  enabled: boolean
  hits: number
  note?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RuleOptions {
  categories: string[]
  pending_category: string
  match_fields: Array<{ value: string; label: string }>
  rule_types: Array<{ value: string; label: string }>
}

export interface Dashboard {
  month: string
  invoice_count: number
  total_amount: number
  pending_review: number
  /** 全部时间的待复核数（侧边栏角标用） */
  pending_review_total: number
  abnormal: number
  confirmed: number
  failed: number
  by_category: CategoryStat[]
  recent_jobs: Job[]
  provider: {
    configured: string
    ocr_provider_setting: string
    baidu_ready: boolean
    paddle_ready: boolean
    paddle_detail: string
    rapidocr_ready: boolean
    daily_ocr_limit: number
    daily_ocr_used: number
  }
}

export interface ProviderInfo {
  name: string
  display_name: string
  ready: boolean
  note: string
  detail?: string
}

export interface ExportPreview {
  count: number
  invoice_count: number
  total_amount: number
  file_name: string
  company_ready: boolean
  by_direction: Array<{
    direction: 'input' | 'output' | 'unknown'
    label: string
    invoice_count: number
    item_count: number
    total_amount: number
  }>
}

export interface CompanyProfile {
  name: string
  tax_id?: string | null
  aliases: string[]
  updated_at?: string | null
}

export interface CompanySuggestion {
  name: string
  tax_id?: string | null
  roles: Array<'buyer' | 'seller'>
  invoice_count: number
  buyer_count: number
  seller_count: number
}

export interface LedgerFilters {
  status?: string
  direction?: string
  month?: string
  date_from?: string
  date_to?: string
  category?: string
  q?: string
  page?: number
  page_size?: number
}
