import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  BadgeCheck,
  Braces,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Undo2,
} from 'lucide-react'
import { api } from '../api'
import { useToast } from '../components/Toast'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Modal,
  ProgressBar,
  Select,
  Spinner,
  TextArea,
  TextInput,
} from '../components/ui'
import { useAsync } from '../lib/hooks'
import {
  amountDiff,
  dateText,
  money,
  percent,
  riskText,
  INVOICE_STATUS_CLASS,
  INVOICE_STATUS_TEXT,
} from '../lib/format'
import type { Invoice, RiskFlag } from '../types'

/** 明细行的编辑态：数字也用字符串存，否则输入 "1." 会被 Number() 吃掉小数点 */
interface ItemForm {
  id?: string
  item_name: string
  specification: string
  unit: string
  quantity: string
  unit_price: string
  tax_rate: string
  amount: string
  tax_amount: string
  /** 备注：折扣/退货行用来说明原因；导出时该备注单元格会高亮 */
  remark: string
}

interface FormState {
  invoice_type: string
  invoice_code: string
  invoice_number: string
  invoice_date: string
  seller_name: string
  seller_tax_id: string
  buyer_name: string
  buyer_tax_id: string
  /** 票面备注栏原文（发票级，识别自票面；某一行明细自己的备注在 items[].remark） */
  invoice_remark: string
  amount_without_tax: string
  tax_amount: string
  total_amount: string
  expense_category: string
  account_subject: string
  items: ItemForm[]
}

// [字段, 标签, 输入框下面的提示]
const TEXT_FIELDS: Array<[keyof FormState, string, string?]> = [
  ['invoice_type', '发票类型'],
  [
    'invoice_code',
    '发票代码',
    '老式增值税发票才有（10~12 位）。全电发票没有代码，留空即可 —— 填了就会按「老式发票」校验号码（8 位）。',
  ],
  [
    'invoice_number',
    '发票号码',
    '全电发票固定 20 位；老式发票 8 位。位数不对会提示「号码格式错误」。',
  ],
  ['invoice_date', '开票日期'],
  ['seller_name', '销售方名称'],
  ['seller_tax_id', '销售方税号'],
  ['buyer_name', '购买方名称'],
  ['buyer_tax_id', '购买方税号'],
]

const MONEY_FIELDS: Array<[keyof FormState, string]> = [
  ['amount_without_tax', '不含税金额'],
  ['tax_amount', '税额'],
  ['total_amount', '价税合计'],
]

// 明细行的字段布局：名称/规格占满整行（4 格），其余按信息量分配
const ITEM_TEXT_FIELDS: Array<[keyof ItemForm, string, string]> = [
  ['item_name', '项目名称', 'col-span-4'],
  ['specification', '规格型号', 'col-span-4'],
  ['unit', '单位', 'col-span-1'],
  ['quantity', '数量', 'col-span-1'],
  ['unit_price', '单价', 'col-span-2'],
]

const ITEM_MONEY_FIELDS: Array<[keyof ItemForm, string, string]> = [
  ['amount', '金额', 'col-span-2'],
  ['tax_rate', '税率', 'col-span-1'],
  ['tax_amount', '税额', 'col-span-1'],
]

const ITEM_NUMBER_KEYS = new Set<keyof ItemForm>([
  'quantity',
  'unit_price',
  'amount',
  'tax_amount',
])

// 项目名称和规格型号经常很长（50+ 字），单行输入框看不全 → 用两行文本域
const ITEM_LONG_TEXT_KEYS = new Set<keyof ItemForm>(['item_name', 'specification'])

function emptyItem(): ItemForm {
  return {
    item_name: '',
    specification: '',
    unit: '',
    quantity: '',
    unit_price: '',
    tax_rate: '',
    amount: '',
    tax_amount: '',
    remark: '',
  }
}

function toForm(invoice: Invoice): FormState {
  const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))
  return {
    invoice_type: str(invoice.invoice_type),
    invoice_code: str(invoice.invoice_code),
    invoice_number: str(invoice.invoice_number),
    invoice_date: normalizeDateInput(str(invoice.invoice_date)),
    seller_name: str(invoice.seller_name),
    seller_tax_id: str(invoice.seller_tax_id),
    buyer_name: str(invoice.buyer_name),
    buyer_tax_id: str(invoice.buyer_tax_id),
    invoice_remark: str(invoice.invoice_remark),
    amount_without_tax: str(invoice.amount_without_tax),
    tax_amount: str(invoice.tax_amount),
    total_amount: str(invoice.total_amount),
    expense_category: str(invoice.expense_category),
    account_subject: str(invoice.account_subject),
    items: (invoice.items ?? []).map((it) => ({
      id: it.id,
      item_name: it.item_name ?? '',
      specification: it.specification ?? '',
      unit: it.unit ?? '',
      quantity: str(it.quantity),
      unit_price: str(it.unit_price),
      tax_rate: it.tax_rate ?? '',
      amount: str(it.amount),
      tax_amount: str(it.tax_amount),
      remark: it.remark ?? '',
    })),
  }
}

/** 金额：按分取整 */
function num(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  return Number.isNaN(parsed) ? null : Math.round(parsed * 100) / 100
}

/** 数量与单价：保留票面原始精度，不要四舍五入到分 */
function looseNumber(value: string): number | null {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isNaN(parsed) ? null : parsed
}

// ---------------------------------------------------------------------------
// 关键字段的实时校验
// 和 backend/app/services/validate.py 的规则 1 / 1b / 2 / 4 保持一致。
// 为什么要在前端也算一遍：服务端的 risk_flags 是**上次保存那一刻**的快照，
// 用户当场把号码补对了、提示却还挂在那里、按钮还是灰的 —— 体验很差。
// 所以这几条「靠当前表单值就能判定」的规则在前端实时算，
// 服务端的同类标记就不再重复显示（见 blockingErrors）。
// 服务端仍然是权威：确认入账时它会再校验一次，不通过会返回 409。
// ---------------------------------------------------------------------------

/** 发票号码位数：全电发票 20 位；老式发票 8 位（且一定有发票代码） */
function expectedNumberLength(hasCode: boolean): number {
  return hasCode ? 8 : 20
}

type NumberIssue = 'missing' | 'non-digit' | 'length' | null

function numberIssue(value: string, hasCode: boolean): NumberIssue {
  const v = value.trim()
  if (v === '') return 'missing'
  if (!/^\d+$/.test(v)) return 'non-digit'
  return v.length === expectedNumberLength(hasCode) ? null : 'length'
}

/**
 * 解析开票日期。**要和后端的 datetime.strptime(v, "%Y-%m-%d") 对齐**：
 *   · 接受不补零的写法（`2026-9-10` 后端是认的）
 *   · 但必须是真实存在的日期（`2026-13-40` 后端会拒）
 * 只用正则会出现两个方向的误判，所以这里真算一遍。
 */
function parseLooseDate(value: string): { ok: boolean; iso: string } {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value.trim())
  if (!m) return { ok: false, iso: '' }
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  const dt = new Date(Date.UTC(year, month - 1, day))
  const real =
    dt.getUTCFullYear() === year && dt.getUTCMonth() === month - 1 && dt.getUTCDate() === day
  return {
    ok: real,
    iso: `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`,
  }
}

/** 载入表单时把日期补零成 yyyy-mm-dd，否则 <input type="date"> 显示不出来 */
function normalizeDateInput(value: string): string {
  const { ok, iso } = parseLooseDate(value)
  return ok ? iso : value
}

/** 前端实时算出的红色错误（与服务端同名规则）。 */
function liveKeyErrors(form: FormState): RiskFlag[] {
  const flags: RiskFlag[] = []
  const hasCode = form.invoice_code.trim() !== ''

  // --- 关键字段缺失（对应后端规则 1）---
  const missing: string[] = []
  if (!form.invoice_number.trim()) missing.push('发票号码')
  if (!form.invoice_date.trim()) missing.push('开票日期')
  if (!form.seller_name.trim()) missing.push('销售方名称')
  if (num(form.total_amount) === null) missing.push('价税合计')
  if (missing.length) {
    flags.push({
      code: 'missing_required',
      level: 'error',
      message: `关键字段缺失：${missing.join('、')}。补齐后才能确认入账。`,
      field: missing.join('、'),
    })
  }

  // --- 发票号码格式（对应后端规则 1b）---
  const issue = numberIssue(form.invoice_number, hasCode)
  if (issue === 'non-digit') {
    flags.push({
      code: 'invoice_number_format',
      level: 'error',
      message: '发票号码只能填数字，当前含有非数字字符。',
      field: 'invoice_number',
    })
  } else if (issue === 'length') {
    const want = expectedNumberLength(hasCode)
    const kind = hasCode ? '老式发票，配发票代码' : '全电发票'
    flags.push({
      code: 'invoice_number_format',
      level: 'error',
      message: `发票号码应为 ${want} 位（${kind}），当前 ${form.invoice_number.trim().length} 位。`,
      field: 'invoice_number',
    })
  }

  // --- 开票日期（对应后端规则 4：能不能解析 + 是不是未来日期）---
  const dateText = form.invoice_date.trim()
  if (dateText) {
    const parsed = parseLooseDate(dateText)
    if (!parsed.ok) {
      flags.push({
        code: 'date_unparsable',
        level: 'error',
        message: `开票日期「${dateText}」不是有效日期，请按 yyyy-mm-dd 填写。`,
        field: 'invoice_date',
      })
    } else {
      const today = new Date()
      const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
      if (parsed.iso > todayIso) {
        flags.push({
          code: 'date_in_future',
          level: 'error',
          message: `开票日期 ${parsed.iso} 晚于今天，属于未来日期。`,
          field: 'invoice_date',
        })
      }
    }
  }

  // --- 金额勾稽（对应后端规则 2）---
  const amountDelta = amountDiff(
    num(form.amount_without_tax),
    num(form.tax_amount),
    num(form.total_amount),
  )
  if (amountDelta !== null && Math.abs(amountDelta) > 0.01) {
    flags.push({
      code: 'amount_mismatch',
      level: 'error',
      message: `金额关系不平：不含税金额 + 税额 与 价税合计 相差 ${amountDelta > 0 ? '+' : ''}${amountDelta.toFixed(2)} 元，超过 0.01 元容差。`,
      field: 'total_amount',
    })
  }

  return flags
}

/** 这些规则前端会实时重算，就不再显示服务端那份可能过期的标记 */
const LIVE_RULE_CODES = new Set([
  'missing_required',
  'invoice_number_format',
  'date_unparsable',
  'date_in_future',
  'amount_mismatch',
])

/** 右侧字段面板里的一行 */
function FieldRow({
  label,
  value,
  onChange,
  low,
  error,
  invalid,
  hint,
  badge,
  type,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  low?: boolean
  error?: boolean
  /** 有值但不合格（比如号码位数不对）—— 边框标红，但徽标由 badge 自己给 */
  invalid?: boolean
  hint?: string
  /** 覆盖默认徽标（默认是 低置信 / 缺失） */
  badge?: ReactNode
  type?: string
}) {
  const bad = Boolean(error || invalid)
  return (
    <Field
      label={label}
      hint={hint}
      badge={
        badge ??
        (low ? (
          <Badge className="bg-amber-50 text-amber-700 ring-amber-200">低置信</Badge>
        ) : error ? (
          <Badge className="bg-red-50 text-red-700 ring-red-200">缺失</Badge>
        ) : null)
      }
    >
      <TextInput
        type={type}
        value={value}
        lowConfidence={low}
        className={bad ? 'border-red-400 bg-red-50/60' : ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}

/** 明细行内的勾稽提示：数量 × 单价 应等于金额 */
function ItemCheck({ item }: { item: ItemForm }) {
  const qty = looseNumber(item.quantity)
  const price = looseNumber(item.unit_price)
  const amount = num(item.amount)
  if (qty === null || price === null || amount === null) return null

  const computed = qty * price
  const diff = Math.round((computed - amount) * 100) / 100
  if (Math.abs(diff) <= 0.02) {
    return (
      <p className="mt-2 text-[11px] text-emerald-600">
        ✔ 数量 × 单价 = 金额（{qty} × {price} ≈ {amount.toFixed(2)}）
      </p>
    )
  }
  return (
    <p className="mt-2 text-[11px] text-amber-600">
      数量 × 单价 = {computed.toFixed(2)}，与票面金额 {amount.toFixed(2)} 相差 {diff.toFixed(2)} 元
      —— 不含税单价有长小数时属正常舍入差，差额大就要核对票面
    </p>
  )
}

export default function Review() {
  const [params, setParams] = useSearchParams()
  const toast = useToast()

  const {
    data: queue,
    loading,
    error,
    reload,
  } = useAsync(() => api.listInvoices({ status: 'pending_review', page_size: 200 }), [])
  const { data: options } = useAsync(() => api.ruleOptions(), [])

  const invoices = useMemo(() => queue?.items ?? [], [queue])
  const targetId = params.get('id')
  const [index, setIndex] = useState(0)
  const [form, setForm] = useState<FormState | null>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveAsRule, setSaveAsRule] = useState(true)
  const [showRaw, setShowRaw] = useState(false)
  const [raw, setRaw] = useState<unknown>(null)

  const current: Invoice | undefined = useMemo(() => {
    if (!invoices.length) return undefined
    if (targetId) {
      const found = invoices.findIndex((i) => i.id === targetId)
      if (found >= 0) return invoices[found]
    }
    return invoices[Math.min(index, invoices.length - 1)]
  }, [invoices, index, targetId])

  useEffect(() => {
    if (current) {
      setForm(toForm(current))
      setDirty(false)
    } else {
      setForm(null)
    }
  }, [current])

  const go = useCallback(
    (delta: number) => {
      if (!invoices.length) return
      const currentIndex = current ? invoices.findIndex((i) => i.id === current.id) : 0
      const next = Math.max(0, Math.min(invoices.length - 1, currentIndex + delta))
      setIndex(next)
      setParams({ id: invoices[next].id })
    },
    [current, invoices, setParams],
  )

  // ---------- 实时勾稽 ----------
  const diff = form
    ? amountDiff(num(form.amount_without_tax), num(form.tax_amount), num(form.total_amount))
    : null

  // 红色错误 = 服务端标记里那些「前端算不出来」的（重复票据等）
  //          + 前端按当前表单实时算的那几条（缺字段 / 号码格式 / 日期 / 金额勾稽）。
  // 这样用户当场把号码补对，提示立刻消失、按钮立刻可点，不用先保存再刷新。
  const blockingErrors: RiskFlag[] = useMemo(() => {
    const server = (current?.risk_flags ?? []).filter((f) => f.level === 'error')
    if (!form) return server // 表单还没加载出来，先照服务端的显示
    return [
      ...server.filter((f) => !LIVE_RULE_CODES.has(f.code)),
      ...liveKeyErrors(form),
    ]
  }, [current, form])
  const warnings: RiskFlag[] = (current?.risk_flags ?? []).filter((f) => f.level === 'warning')

  const confOf = (field: string) => current?.field_confidence?.[field]
  const isLow = (field: string) => {
    const score = confOf(field)
    return score !== undefined && score < 0.85
  }

  // ---------- 操作 ----------
  const addItem = () => {
    if (!form) return
    setForm({ ...form, items: [...form.items, emptyItem()] })
    setDirty(true)
  }

  const removeItem = (index: number) => {
    if (!form) return
    setForm({ ...form, items: form.items.filter((_, i) => i !== index) })
    setDirty(true)
  }

  const buildPayload = () => {
    if (!form) return {}
    return {
      invoice_type: form.invoice_type,
      invoice_code: form.invoice_code,
      invoice_number: form.invoice_number,
      invoice_date: form.invoice_date,
      seller_name: form.seller_name,
      seller_tax_id: form.seller_tax_id,
      buyer_name: form.buyer_name,
      buyer_tax_id: form.buyer_tax_id,
      invoice_remark: form.invoice_remark,
      amount_without_tax: num(form.amount_without_tax),
      tax_amount: num(form.tax_amount),
      total_amount: num(form.total_amount),
      expense_category: form.expense_category,
      account_subject: form.account_subject,
      items: form.items.map((it) => ({
        id: it.id,
        item_name: it.item_name,
        specification: it.specification,
        unit: it.unit,
        // 数量/单价保留原始精度（不含税单价常常有很多位小数）
        quantity: looseNumber(it.quantity),
        unit_price: looseNumber(it.unit_price),
        tax_rate: it.tax_rate,
        amount: num(it.amount),
        tax_amount: num(it.tax_amount),
        remark: it.remark,
      })),
    }
  }

  const save = async (): Promise<Invoice | null> => {
    if (!current || !form) return null
    setBusy(true)
    try {
      const updated = await api.updateInvoice(current.id, {
        ...buildPayload(),
        save_as_rule: saveAsRule,
      })
      setDirty(false)
      toast('success', saveAsRule ? '已保存，并把分类沉淀为规则' : '已保存')
      return updated
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
      return null
    } finally {
      setBusy(false)
    }
  }

  const saveAndConfirm = async () => {
    if (!current || !form) return
    setBusy(true)
    try {
      const updated = await api.updateInvoice(current.id, {
        ...buildPayload(),
        save_as_rule: saveAsRule,
      })
      await api.confirmInvoice(updated.id)
      toast('success', `「${updated.invoice_number || updated.seller_name}」已入账`)
      setDirty(false)
      reload()
      // 确认后自动跳到下一张
      const remaining = invoices.filter((i) => i.id !== current.id)
      setIndex(Math.min(index, Math.max(0, remaining.length - 1)))
      setParams({})
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      toast('error', message)
      reload()
    } finally {
      setBusy(false)
    }
  }

  const openRaw = async () => {
    if (!current) return
    try {
      setRaw(await api.rawResponse(current.id))
      setShowRaw(true)
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  // ---------- 快捷键 ----------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        void saveAndConfirm()
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void save()
      } else if (e.altKey && e.key === 'ArrowLeft') {
        go(-1)
      } else if (e.altKey && e.key === 'ArrowRight') {
        go(1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (loading) return <Spinner />
  if (error) {
    return (
      <Alert level="error" title="加载失败">
        {error}
      </Alert>
    )
  }
  if (!current || !form) {
    return (
      <Card>
        <EmptyState
          title="没有待复核的票据"
          description="所有票据都已确认入账，或者还没有上传。去「票据中心」上传一批试试。"
        />
      </Card>
    )
  }

  const position = invoices.findIndex((i) => i.id === current.id) + 1

  return (
    // 整页做成「视口高度 + 内部各自滚动」：左边原票和风险提示钉住不动，
    // 只有右边表单滚 —— 对照复核时才不会把票面滚出屏幕。
    <div className="flex flex-col gap-3 xl:h-[calc(100vh-3rem)]">
      {/* ---------------- 顶部操作条（不滚动） ---------------- */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-slate-900">复核工作台</h1>
          <Badge className={INVOICE_STATUS_CLASS[current.status]}>
            {INVOICE_STATUS_TEXT[current.status] ?? current.status}
          </Badge>
          <span className="text-sm text-slate-500">
            第 {position} / {invoices.length} 张待复核
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => go(-1)} disabled={position <= 1}>
            <ChevronLeft size={14} />
            上一张
          </Button>
          <Button size="sm" onClick={() => go(1)} disabled={position >= invoices.length}>
            下一张
            <ChevronRight size={14} />
          </Button>
          <Button size="sm" onClick={() => void openRaw()}>
            <Braces size={14} />
            OCR 原始响应
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={busy}>
            <Save size={14} />
            保存
          </Button>
          <Button
            size="sm"
            variant="success"
            onClick={() => void saveAndConfirm()}
            disabled={busy || blockingErrors.length > 0}
            title={blockingErrors.length ? '存在红色错误，需先修正' : 'Ctrl + Enter'}
          >
            <BadgeCheck size={14} />
            保存并确认入账
          </Button>
        </div>
      </div>

      {current.file?.error_code && current.file.status !== 'completed' ? (
        <div className="shrink-0">
          <Alert level="warning" title="这张票不是自动识别出来的">
            {current.file.error_message}
            {current.source_note ? ` （${current.source_note}）` : ''}
            请对照左侧原票手工录入字段。
          </Alert>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:min-h-0 xl:flex-1 xl:grid-cols-2">
        {/* ---------------- 左侧：原票（钉住不动） ---------------- */}
        <Card padded={false} className="flex flex-col overflow-hidden xl:min-h-0">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-slate-700">
                {current.file?.original_name ?? '原票'}
              </div>
              <div className="text-[11px] text-slate-400">
                {current.file?.page_count ?? 1} 页 ·{' '}
                {current.file?.ocr_provider ? `识别引擎 ${current.file.ocr_provider}` : '未识别'}
                {current.confidence !== null && current.confidence !== undefined
                  ? ` · 置信度 ${percent(current.confidence)}`
                  : ''}
              </div>
            </div>
            <a
              href={current.raw_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex shrink-0 items-center gap-1 text-xs text-slate-500 hover:text-brand-600"
            >
              <ExternalLink size={12} />
              新窗口打开
            </a>
          </div>
          <div className="h-[28rem] bg-slate-100 xl:h-auto xl:min-h-0 xl:flex-1">
            {current.file?.file_type === 'pdf' ? (
              // 浏览器自带 PDF 阅读器，不需要额外渲染库
              <iframe
                key={current.id}
                src={current.raw_url}
                title="原票预览"
                className="h-full w-full border-0"
              />
            ) : (
              <div className="flex h-full items-start justify-center overflow-auto p-3">
                <img
                  src={current.raw_url}
                  alt="原票"
                  className="max-w-full rounded shadow-sm"
                />
              </div>
            )}
          </div>
        </Card>

        {/* ---------------- 右侧：字段表单 ---------------- */}
        <div className="flex flex-col gap-3 xl:min-h-0">
          {/* 风险提示：钉在这里不随表单滚动 —— 边看票边能看到当前还有什么问题 */}
          <div className="shrink-0 space-y-2">
            {blockingErrors.length > 0 ? (
              <Alert level="error" title={`${blockingErrors.length} 个红色错误（阻止入账）`}>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {blockingErrors.map((f, i) => (
                    <li key={i}>
                      <b>{riskText(f.code)}</b>：{f.message}
                    </li>
                  ))}
                </ul>
              </Alert>
            ) : null}

            {warnings.length > 0 ? (
              <Alert level="warning" title={`${warnings.length} 个黄色提醒（可确认）`}>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {warnings.map((f, i) => (
                    <li key={i}>
                      <b>{riskText(f.code)}</b>：{f.message}
                    </li>
                  ))}
                </ul>
              </Alert>
            ) : null}

            {blockingErrors.length === 0 && warnings.length === 0 ? (
              <Alert level="success" title="校验全部通过">
                字段齐全、金额勾稽一致、没有重复票据，可以直接确认入账。
              </Alert>
            ) : null}
          </div>

          {/* 复核表单：只有这块随滚轮滚动 */}
          <div className="space-y-4 xl:min-h-0 xl:flex-1 xl:overflow-y-auto xl:pr-1 xl:pb-1">
          <Card>
            <CardTitle
              title="票面字段"
              subtitle="黄色输入框表示识别置信度偏低，请重点核对"
              right={
                dirty ? (
                  <Badge className="bg-amber-50 text-amber-700 ring-amber-200">有未保存修改</Badge>
                ) : (
                  <Badge className="bg-slate-100 text-slate-500 ring-slate-200">已同步</Badge>
                )
              }
            />

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {TEXT_FIELDS.map(([key, label, baseHint]) => {
                const raw = String(form[key] ?? '')
                const setField = (v: string) => {
                  setForm({ ...form, [key]: v })
                  setDirty(true)
                }

                // ---- 发票号码：提示 20 位 / 位数不够就报「号码格式错误」----
                if (key === 'invoice_number') {
                  const hasCode = form.invoice_code.trim() !== ''
                  const want = expectedNumberLength(hasCode)
                  const issue = numberIssue(raw, hasCode)
                  const digits = raw.trim()
                  return (
                    <FieldRow
                      key={key}
                      label={label}
                      hint={`${baseHint ?? ''}  当前 ${digits.length} 位`}
                      value={raw}
                      low={isLow(key)}
                      invalid={issue !== null}
                      badge={
                        issue === null ? null : (
                          <Badge className="bg-red-50 text-red-700 ring-red-200">
                            {issue === 'missing' ? '缺失' : '号码格式错误'}
                          </Badge>
                        )
                      }
                      onChange={setField}
                    />
                  )
                }

                // ---- 开票日期：用日期选择器；识别结果解析不了时才退回文本框 ----
                if (key === 'invoice_date') {
                  const text = raw.trim()
                  const parsed = parseLooseDate(text)
                  const valid = text === '' || parsed.ok
                  // <input type="date"> 只认补零的 yyyy-mm-dd，否则会显示成空
                  const pickerReady = text === '' || (parsed.ok && parsed.iso === text)
                  return (
                    <FieldRow
                      key={key}
                      label={label}
                      type={pickerReady ? 'date' : 'text'}
                      hint={
                        !valid
                          ? `「${text}」不是有效日期，请点右边日历图标选一个`
                          : pickerReady
                            ? `${baseHint ?? ''}  点右边日历图标直接选`
                            : `值「${text}」能保存，但日历控件只认 yyyy-mm-dd，改一下就能用选择器`
                      }
                      value={raw}
                      low={isLow(key)}
                      invalid={!valid}
                      badge={
                        !valid ? (
                          <Badge className="bg-red-50 text-red-700 ring-red-200">日期格式错误</Badge>
                        ) : null
                      }
                      onChange={setField}
                    />
                  )
                }

                return (
                  <FieldRow
                    key={key}
                    label={label}
                    hint={baseHint}
                    value={raw}
                    low={isLow(key)}
                    error={!raw.trim() && key === 'seller_name'}
                    onChange={setField}
                  />
                )
              })}
            </div>

            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
              {MONEY_FIELDS.map(([key, label]) => (
                <FieldRow
                  key={key}
                  label={label}
                  value={String(form[key] ?? '')}
                  low={isLow(key)}
                  error={!String(form[key] ?? '').trim()}
                  onChange={(v) => {
                    setForm({ ...form, [key]: v })
                    setDirty(true)
                  }}
                />
              ))}
            </div>

            <div className="mt-3">
              {diff === null ? (
                <p className="text-xs text-slate-400">
                  金额字段不完整，无法自动勾稽（不含税金额 + 税额 应等于 价税合计）
                </p>
              ) : Math.abs(diff) <= 0.01 ? (
                <p className="text-xs text-emerald-600">
                  ✔ 金额勾稽通过：不含税金额 + 税额 = 价税合计
                </p>
              ) : (
                <p className="text-xs text-red-600">
                  ✘ 金额关系不平，差额 {diff > 0 ? '+' : ''}
                  {diff.toFixed(2)} 元（容差 0.01 元）
                </p>
              )}
            </div>

            {/* 票面备注栏：工程类发票会在这里写开户银行/账号/工程名称/工程地址。
                这是**发票级**信息，别和明细行自己的备注（折扣/退货原因）混在一起 */}
            <div className="mt-3">
              <Field
                label="票面备注"
                hint={
                  form.invoice_remark.trim()
                    ? '识别自发票左下角的备注栏，可以改。导出时每行明细的「备注」列都会带上它'
                    : '发票左下角「备注」栏有内容时会自动填到这里；识别不到就留空，也可以手动补'
                }
                badge={
                  form.invoice_remark.trim() ? (
                    <Badge className="bg-sky-50 text-sky-700 ring-sky-200">已识别</Badge>
                  ) : null
                }
              >
                <TextArea
                  value={form.invoice_remark}
                  rows={3}
                  placeholder="例：销方开户银行:…;  银行账号:…;  工程名称:…"
                  onChange={(e) => {
                    setForm({ ...form, invoice_remark: e.target.value })
                    setDirty(true)
                  }}
                />
              </Field>
            </div>
          </Card>

          {/* 明细：紧跟在票面字段后面，核对时是连着看的 */}
          <Card>
            <CardTitle
              title={`发票明细（${form.items.length} 行）`}
              subtitle="项目名称与规格给足了宽度，金额可与票面逐项对照"
              right={
                <Button size="sm" variant="ghost" onClick={addItem}>
                  <Plus size={13} />
                  加一行
                </Button>
              }
            />
            {form.items.length === 0 ? (
              <EmptyState
                title="这张票没有解析出明细行"
                description="人工录入的票据可以自己补一行"
                action={
                  <Button size="sm" onClick={addItem}>
                    加一行明细
                  </Button>
                }
              />
            ) : (
              <div className="space-y-3">
                {form.items.map((item, i) => {
                  const edit = (patch: Partial<ItemForm>) => {
                    const next = [...form.items]
                    next[i] = { ...next[i], ...patch }
                    setForm({ ...form, items: next })
                    setDirty(true)
                  }
                  const amountValue = num(item.amount)
                  // 金额为负 = 票面上的折扣行 / 退货行。
                  // 票面用负数行表达「减一笔」，很容易看漏，所以这里：
                  //   ① 标题上挂个醒目徽标  ② 在备注框下面提醒写清原因
                  const isNegative = amountValue !== null && amountValue < 0
                  return (
                    <div
                      key={item.id ?? `new-${i}`}
                      className={`rounded-lg border p-3 ${
                        isNegative
                          ? 'border-amber-300 bg-amber-50/50'
                          : 'border-slate-200 bg-slate-50/60'
                      }`}
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-slate-500">
                            明细 {i + 1}
                          </span>
                          {isNegative ? (
                            <Badge className="bg-amber-100 text-amber-800 ring-amber-300">
                              金额为负 · 折扣/退货
                            </Badge>
                          ) : null}
                          {!isNegative && item.remark.trim() ? (
                            <Badge className="bg-sky-50 text-sky-700 ring-sky-200">
                              已写备注
                            </Badge>
                          ) : null}
                        </div>
                        <button
                          onClick={() => removeItem(i)}
                          className="inline-flex items-center gap-1 text-xs text-slate-400 transition hover:text-red-600"
                        >
                          <Trash2 size={12} />
                          删除本行
                        </button>
                      </div>
                      <div className="grid grid-cols-4 gap-2.5">
                        {[...ITEM_TEXT_FIELDS, ...ITEM_MONEY_FIELDS].map(([key, label, span]) => (
                          <Field key={key} label={label} className={span}>
                            {ITEM_LONG_TEXT_KEYS.has(key) ? (
                              <TextArea
                                value={item[key]}
                                onChange={(e) => edit({ [key]: e.target.value } as Partial<ItemForm>)}
                              />
                            ) : (
                              <TextInput
                                value={item[key]}
                                inputMode={ITEM_NUMBER_KEYS.has(key) ? 'decimal' : undefined}
                                onChange={(e) =>
                                  edit({ [key]: e.target.value } as Partial<ItemForm>)
                                }
                              />
                            )}
                          </Field>
                        ))}
                      </div>
                      <div className="mt-2.5">
                        <Field
                          label="备注"
                          hint={
                            isNegative
                              ? '导出 Excel 时会自动标出这一行（金额用红字 + 备注格高亮）；这里写了原因，就替代默认的「票面折扣/退货行」'
                              : '可留空。写了备注的行，导出 Excel 时备注格会高亮'
                          }
                        >
                          <TextInput
                            value={item.remark}
                            placeholder={
                              isNegative ? '例：与上一行同项目的折扣，票面以负数行体现' : ''
                            }
                            onChange={(e) => edit({ remark: e.target.value })}
                          />
                        </Field>
                      </div>
                      <ItemCheck item={item} />
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          {/* 分类 */}
          <Card>
            <CardTitle
              title="费用分类"
              subtitle={current.rule_source ? `推荐来源：${current.rule_source}` : '未命中任何规则'}
              right={
                current.rule_source?.startsWith('自定义') ? (
                  <Badge className="bg-emerald-50 text-emerald-700 ring-emerald-200">
                    <Sparkles size={11} />
                    企业规则
                  </Badge>
                ) : null
              }
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="费用分类">
                <Select
                  value={form.expense_category}
                  onChange={(e) => {
                    setForm({ ...form, expense_category: e.target.value })
                    setDirty(true)
                  }}
                >
                  <option value="">（未选择）</option>
                  {options?.categories.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="会计科目" hint="可直接手填，如：管理费用—差旅费">
                <TextInput
                  value={form.account_subject}
                  onChange={(e) => {
                    setForm({ ...form, account_subject: e.target.value })
                    setDirty(true)
                  }}
                />
              </Field>
            </div>
            <label className="mt-3 flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={saveAsRule}
                onChange={(e) => setSaveAsRule(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300"
              />
              保存修改时，把「项目名称 / 销售方 → 费用分类」沉淀成企业自定义规则，下次自动命中
            </label>
          </Card>

          {/* 审计信息 */}
          <Card>
            <CardTitle title="审计信息" subtitle="可追溯：原文件 / OCR 原始响应 / 修改痕迹" />
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">识别引擎</dt>
                <dd className="text-slate-700">{current.file?.ocr_provider ?? '—'}</dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">识别时间</dt>
                <dd className="text-slate-700">{dateText(current.file?.updated_at)}</dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">业务去重指纹</dt>
                <dd className="truncate font-mono text-[11px] text-slate-600" title={current.dedupe_key ?? ''}>
                  {current.dedupe_key || '—'}
                </dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">人工修改次数</dt>
                <dd className="text-slate-700">{current.edit_history?.length ?? 0} 次</dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">已确认入账</dt>
                <dd className="text-slate-700">
                  {current.confirmed_at ? dateText(current.confirmed_at) : '—'}
                </dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-slate-100 py-1">
                <dt className="text-slate-500">原票存储</dt>
                <dd className="truncate text-slate-600" title={current.file?.original_name ?? ''}>
                  本地 {current.file?.original_name ?? '—'}
                </dd>
              </div>
            </dl>

            {current.edit_history && current.edit_history.length > 0 ? (
              <details className="mt-3">
                <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">
                  展开修改痕迹（{current.edit_history.length} 条）
                </summary>
                <ul className="mt-2 space-y-1 text-[11px] text-slate-500">
                  {current.edit_history.slice(-12).map((entry, i) => {
                    const row = entry as Record<string, unknown>
                    return (
                      <li key={i} className="flex gap-2">
                        <span className="text-slate-400">{String(row.at ?? '')}</span>
                        <span className="font-medium text-slate-600">{String(row.field ?? '')}</span>
                        <span className="truncate">
                          {String(row.old ?? '空')} → {String(row.new ?? '空')}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </details>
            ) : null}
          </Card>

          <div className="flex items-center justify-between gap-3 pb-6">
            <p className="text-xs text-slate-400">
              快捷键：Ctrl+S 保存 · Ctrl+Enter 保存并入账 · Alt+← / → 切换票据
            </p>
            {dirty ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setForm(toForm(current))
                  setDirty(false)
                }}
              >
                <Undo2 size={13} />
                撤销修改
              </Button>
            ) : null}
          </div>
          </div>
        </div>
      </div>

      {/* 底部状态条（不随滚轮移动）：随时知道这一批还剩多少张 */}
      <div className="shrink-0 border-t border-slate-200 pt-2.5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="whitespace-nowrap text-xs text-slate-500">
            还剩 {Math.max(0, invoices.length - position)} 张待复核
          </span>
          <ProgressBar
            value={invoices.length ? position / invoices.length : 0}
            className="max-w-md"
          />
          {blockingErrors.length > 0 ? (
            <span className="flex items-center gap-1 whitespace-nowrap text-xs text-red-600">
              <CircleAlert size={12} />
              当前这张有 {blockingErrors.length} 个红色错误，不能入账
            </span>
          ) : (
            <span className="flex items-center gap-1 whitespace-nowrap text-xs text-emerald-600">
              <BadgeCheck size={12} />
              当前这张可以直接入账
            </span>
          )}
        </div>
      </div>

      <Modal
        open={showRaw}
        title="OCR 原始响应（可追溯）"
        onClose={() => setShowRaw(false)}
        width="max-w-3xl"
        footer={
          <Button size="sm" onClick={() => setShowRaw(false)}>
            关闭
          </Button>
        }
      >
        <pre className="max-h-[60vh] overflow-auto rounded-lg bg-slate-900 p-4 text-[11px] leading-relaxed text-slate-100">
          {JSON.stringify(raw, null, 2)}
        </pre>
      </Modal>
    </div>
  )
}
