import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  ChevronRight,
  Download,
  ExternalLink,
  Filter,
  Loader2,
  RotateCcw,
  Search,
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
  INVOICE_STATUS_CLASS,
  INVOICE_STATUS_TEXT,
  amount,
  currentMonth,
  dateText,
  money,
  percent,
} from '../lib/format'
import type { Invoice, LedgerRow } from '../types'

const PAGE_SIZE = 20

type DateScope = 'all' | 'month' | 'range'

const DIRECTION_CLASS: Record<string, string> = {
  input: 'bg-blue-50 text-blue-700 ring-blue-200',
  output: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  unknown: 'bg-amber-50 text-amber-700 ring-amber-200',
}

/** 明细里金额为负 = 票面的折扣/退货行，用红色（和 Excel 导出一致） */
function itemAmountClass(value?: number | null): string {
  return value !== undefined && value !== null && value < 0 ? 'text-red-600' : 'text-slate-700'
}

/**
 * 展开后显示的发票明细。
 *
 * 为什么值得展开看：账本列表一行是一张票，只显示第一条明细的名称，
 * 多行明细的票（比如带折扣行的）在列表里看不出结构。
 * 这里顺便把「明细金额合计 vs 票面不含税」算给用户看 —— 折扣行参与求和，
 * 对得上就说明负数行没被漏掉。
 */
function InvoiceDetail({ invoice }: { invoice: Invoice }) {
  const items = invoice.items ?? []
  const itemSum = Math.round(items.reduce((acc, it) => acc + (it.amount ?? 0), 0) * 100) / 100
  const paper = invoice.amount_without_tax
  const hasPaper = paper !== null && paper !== undefined
  const balanced = hasPaper && Math.abs(itemSum - paper) <= 0.01
  const negativeCount = items.filter((it) => (it.amount ?? 0) < 0).length

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
        <span>
          共 <b>{items.length}</b> 行明细
          {negativeCount > 0 ? (
            <span className="ml-1 text-red-600">（含 {negativeCount} 行折扣/退货）</span>
          ) : null}
        </span>
        <span>原文件：{invoice.file?.original_name || '—'}</span>
        {hasPaper ? (
          <span className={balanced ? 'text-emerald-600' : 'text-amber-600'}>
            {balanced ? '✔' : '⚠'} 明细金额合计 {money(itemSum)} / 票面不含税 {money(paper)}
          </span>
        ) : null}
      </div>

      {/* 票面备注（工程名称/开户银行等）—— 展开时一并看，不用再点进复核页 */}
      {invoice.invoice_remark ? (
        <div className="mb-2 rounded-lg bg-white/70 px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200">
          <span className="mr-1 font-medium text-slate-500">票面备注</span>
          {invoice.invoice_remark.split('\n').map((line, i) => (
            <div key={i} className="leading-relaxed">
              {line}
            </div>
          ))}
        </div>
      ) : null}

      {items.length === 0 ? (
        <p className="text-xs text-slate-400">
          这张票没有解析出明细行，导出 Excel 时会按票面合计记成一行。
        </p>
      ) : (
        <div className="max-h-[420px] overflow-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-xs [&_td]:whitespace-nowrap [&_th]:whitespace-nowrap">
            <thead className="sticky top-0 z-20 bg-slate-50 text-slate-500">
              <tr className="border-b border-slate-200">
                <th className="px-3 py-1.5 text-left font-medium">#</th>
                <th className="px-3 py-1.5 text-left font-medium">项目名称</th>
                <th className="px-3 py-1.5 text-left font-medium">规格型号</th>
                <th className="px-3 py-1.5 text-left font-medium">单位</th>
                <th className="px-3 py-1.5 text-right font-medium">数量</th>
                <th className="px-3 py-1.5 text-right font-medium">单价</th>
                <th className="px-3 py-1.5 text-right font-medium">金额</th>
                <th className="px-3 py-1.5 text-center font-medium">税率</th>
                <th className="px-3 py-1.5 text-right font-medium">税额</th>
                <th className="px-3 py-1.5 text-left font-medium">备注</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={it.id ?? i} className="border-b border-slate-100 last:border-0">
                  <td className="px-3 py-1.5 text-slate-400">{i + 1}</td>
                  <td className="px-3 py-1.5 text-slate-700">{it.item_name || '—'}</td>
                  <td className="px-3 py-1.5 text-slate-600">{it.specification || '—'}</td>
                  <td className="px-3 py-1.5 text-slate-600">{it.unit || '—'}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">
                    {it.quantity ?? '—'}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">
                    {it.unit_price ?? '—'}
                  </td>
                  <td
                    className={`px-3 py-1.5 text-right font-medium tabular-nums ${itemAmountClass(it.amount)}`}
                  >
                    {it.amount ?? '—'}
                  </td>
                  <td className="px-3 py-1.5 text-center text-slate-600">{it.tax_rate || '—'}</td>
                  <td className={`px-3 py-1.5 text-right tabular-nums ${itemAmountClass(it.tax_amount)}`}>
                    {it.tax_amount ?? '—'}
                  </td>
                  <td className="px-3 py-1.5">
                    {it.remark ? (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                        {it.remark}
                      </span>
                    ) : (it.amount ?? 0) < 0 ? (
                      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">
                        票面折扣/退货行
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function Ledger() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()

  const [status, setStatus] = useState(params.get('status') ?? 'all')
  const [direction, setDirection] = useState(params.get('direction') ?? '')
  // 默认看全部账目：进页面就应该是「所有票据」，不该先让人去点一下自定义
  const [scope, setScope] = useState<DateScope>(params.get('month') ? 'month' : 'all')
  const [month, setMonth] = useState(params.get('month') ?? currentMonth())
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [category, setCategory] = useState('')
  const [q, setQ] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [page, setPage] = useState(1)
  const [abnormalOnly, setAbnormalOnly] = useState(params.get('abnormal') === '1')
  const [confirmRow, setConfirmRow] = useState<LedgerRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportTarget, setExportTarget] = useState<{
    invoiceIds?: string[]
    label: string
  }>({ label: '当前筛选' })
  const [companyName, setCompanyName] = useState('')
  const [companyTaxId, setCompanyTaxId] = useState('')
  const [companyAliases, setCompanyAliases] = useState('')
  // 展开看明细：存 invoice_id（同一个 id 在多行里是同一个值，按票展开）。
  // 初始值读地址栏的 ?open=xxx —— 这样明细可以链接直达，刷新也不会收起。
  const [expandedId, setExpandedId] = useState<string | null>(params.get('open'))
  const [detail, setDetail] = useState<Invoice | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  /** 点行展开/收起明细。列表里没带明细，展开时才按需拉一次（见下面的 effect）。 */
  const toggleDetail = useCallback(
    (row: LedgerRow) => {
      const next = expandedId === row.invoice_id ? null : row.invoice_id
      setExpandedId(next)

      // 同步到地址栏（replace，不往历史里塞）—— 可分享、刷新保持
      const nextParams = new URLSearchParams(params)
      if (next) {
        nextParams.set('open', next)
      } else {
        nextParams.delete('open')
      }
      setParams(nextParams, { replace: true })
    },
    [expandedId, params, setParams],
  )

  // 展开哪张就拉哪张的明细。放 effect 里而不是 toggle 里，
  // 是为了「用 ?open=xxx 直接打开」这种情况也能加载出来。
  useEffect(() => {
    if (!expandedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    api
      .getInvoice(expandedId)
      .then((inv) => {
        if (!cancelled) setDetail(inv)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        toast('error', err instanceof Error ? err.message : String(err))
        setExpandedId(null)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [expandedId, toast])

  /** 日期筛选统一映射成接口参数，列表和导出共用，保证「导出的和看到的一致」 */
  const dateParams = useMemo(
    () => ({
      month: scope === 'month' ? month || undefined : undefined,
      date_from: scope === 'range' ? dateFrom || undefined : undefined,
      date_to: scope === 'range' ? dateTo || undefined : undefined,
    }),
    [scope, month, dateFrom, dateTo],
  )

  const filters = useMemo(
    () => ({
      status,
      direction: direction || undefined,
      ...dateParams,
      category: category || undefined,
      q: q || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [status, direction, dateParams, category, q, page],
  )

  const { data, loading, error, reload } = useAsync(() => api.getLedger(filters), [JSON.stringify(filters)])
  const { data: categoryOptions } = useAsync(() => api.categories(), [])
  const { data: company } = useAsync(() => api.getCompany(), [])
  const { data: companySuggestions } = useAsync(() => api.companySuggestions(12), [])

  useEffect(() => {
    if (!company) return
    setCompanyName(company.name || '')
    setCompanyTaxId(company.tax_id || '')
    setCompanyAliases((company.aliases || []).join('\n'))
  }, [company])

  // 导出预览：让按钮上直接显示「几张票 / 几行明细」，点之前就知道会导出什么
  const exportFilters = useMemo(
    () => ({
      status,
      direction: direction || undefined,
      ...dateParams,
      category: category || undefined,
      q: q || undefined,
      only_confirmed: status === 'confirmed',
    }),
    [status, direction, dateParams, category, q],
  )
  const { data: preview } = useAsync(
    () => api.exportPreview(exportFilters),
    [JSON.stringify(exportFilters)],
  )

  // 异常过滤在前端做（后端列表接口已支持，这里保持导出条件一致所以走本地筛选计数提示）
  const rows: LedgerRow[] = data?.items ?? []
  const visibleRows = abnormalOnly ? rows.filter((r) => r.error_count > 0) : rows

  useEffect(() => {
    setPage(1)
  }, [status, direction, scope, month, dateFrom, dateTo, category, q, abnormalOnly])

  const applySearch = useCallback(() => {
    setQ(searchInput.trim())
    setPage(1)
    // 即使关键词没变，也强制重新请求一次；按钮点击必须有明确反馈。
    reload()
  }, [reload, searchInput])

  const resetFilters = () => {
    setStatus('all')
    setDirection('')
    setScope('all')
    setMonth(currentMonth())
    setDateFrom('')
    setDateTo('')
    setCategory('')
    setQ('')
    setSearchInput('')
    setAbnormalOnly(false)
    setParams({})
  }

  const openExport = (invoiceIds?: string[]) => {
    setExportTarget(
      invoiceIds && invoiceIds.length > 0
        ? { invoiceIds, label: `勾选的 ${invoiceIds.length} 张票据` }
        : { label: '当前筛选' },
    )
    setExportOpen(true)
  }

  const doExport = async () => {
    if (!companyName.trim() && !companyTaxId.trim()) {
      toast('error', '请先填写当前企业名称或纳税人识别号')
      return
    }
    setExporting(true)
    try {
      await api.saveCompany({
        name: companyName.trim(),
        tax_id: companyTaxId.trim() || null,
        aliases: companyAliases
          .split(/\r?\n|[,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      const payload = exportTarget.invoiceIds
        ? { invoice_ids: exportTarget.invoiceIds }
        : exportFilters
      await api.exportExcel(payload)
      toast('success', `Excel 已开始下载，将按方向拆分工作表`)
      setExportOpen(false)
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  // ---------- 多选 ----------
  // 选的是 invoice_id（账本一行 = 一张票）。跨页保留选择，所以用 Set 存 id。
  // ⚠️ 这几行必须放在 revoke/remove **之前** —— 它们会用到 dropSelection，
  // 放在后面会踩「变量在初始化前被访问」。
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmBatch, setConfirmBatch] = useState(false)

  const toggleSelect = useCallback((invoiceId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(invoiceId)) {
        next.delete(invoiceId)
      } else {
        next.add(invoiceId)
      }
      return next
    })
  }, [])

  const dropSelection = useCallback((ids: string[]) => {
    setSelected((prev) => {
      const next = new Set(prev)
      ids.forEach((id) => next.delete(id))
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelected(new Set()), [])

  const revoke = useCallback(
    async (row: LedgerRow) => {
      setBusy(true)
      try {
        await api.revokeInvoice(row.invoice_id)
        toast('success', '已撤销入账，票据回到待复核')
        reload()
      } catch (err) {
        toast('error', err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [reload, toast],
  )

  const remove = useCallback(
    async (row: LedgerRow) => {
      setBusy(true)
      try {
        const result = await api.deleteInvoice(row.invoice_id, true)
        toast('success', result.message)
        setConfirmRow(null)
        dropSelection([row.invoice_id])
        reload()
      } catch (err) {
        toast('error', err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [dropSelection, reload, toast],
  )

  // ---------- 多选的派生量 + 批量操作 ----------
  /** 当前页里能勾选的那些 */
  const pageIds = useMemo(() => visibleRows.map((r) => r.invoice_id), [visibleRows])
  const pageAllSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))

  const toggleSelectPage = useCallback(() => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (pageIds.length > 0 && pageIds.every((id) => next.has(id))) {
        pageIds.forEach((id) => next.delete(id))
      } else {
        pageIds.forEach((id) => next.add(id))
      }
      return next
    })
  }, [pageIds])

  /** 导出勾选的票据（只导这几张，不再叠加当前筛选条件） */
  const exportSelected = () => {
    if (selected.size === 0) return
    openExport(Array.from(selected))
  }

  const batchRemove = async () => {
    const ids = Array.from(selected)
    if (ids.length === 0) return
    setBusy(true)
    try {
      const result = await api.batchDeleteInvoices(ids, true)
      if (result.failed.length > 0) {
        toast('error', `${result.message} 失败：${result.failed[0].reason}`)
      } else {
        toast('success', result.message)
      }
      setConfirmBatch(false)
      clearSelection()
      reload()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">账本</h1>
          <p className="mt-1 text-sm text-slate-500">
            搜索、按日期与状态筛选，导出的 Excel 与当前筛选结果完全一致
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={reload}>
            <RotateCcw size={14} />
            刷新
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => openExport()}
            disabled={exporting || (preview?.count ?? 0) === 0}
            title={
              (preview?.count ?? 0) === 0
                ? '当前筛选没有数据'
                : `导出 ${preview?.invoice_count ?? 0} 张发票的 ${preview?.count ?? 0} 行明细`
            }
          >
            <Download size={14} />
            {exporting
              ? '生成中…'
              : `导出 Excel（${preview?.invoice_count ?? 0} 张票 / ${preview?.count ?? 0} 行明细）`}
          </Button>
        </div>
      </div>

      {/* ---------------- 筛选条 ---------------- */}
      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <div className="mb-1 text-xs font-medium text-slate-600">日期范围</div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border border-slate-300 bg-white p-0.5">
                {(
                  [
                    ['all', '全部'],
                    ['month', '按月'],
                    ['range', '自定义'],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setScope(value)}
                    className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                      scope === value
                        ? 'bg-brand-600 text-white'
                        : 'text-slate-500 hover:bg-slate-50'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {scope === 'month' ? (
                <TextInput
                  type="month"
                  value={month}
                  onChange={(e) => setMonth(e.target.value)}
                  className="!w-40"
                />
              ) : scope === 'range' ? (
                <>
                  <TextInput
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                    className="!w-40"
                  />
                  <span className="text-xs text-slate-400">至</span>
                  <TextInput
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                    className="!w-40"
                  />
                </>
              ) : (
                <span className="text-xs text-slate-400">不限时间，显示全部账目</span>
              )}
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs font-medium text-slate-600">状态</div>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} className="!w-32">
              <option value="all">全部</option>
              <option value="pending_review">待复核</option>
              <option value="confirmed">已入账</option>
            </Select>
          </div>

          <div>
            <div className="mb-1 text-xs font-medium text-slate-600">票据方向</div>
            <Select
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
              className="!w-32"
            >
              <option value="">全部</option>
              <option value="input">进项</option>
              <option value="output">销项</option>
              <option value="unknown">待判断</option>
            </Select>
          </div>

          <div>
            <div className="mb-1 text-xs font-medium text-slate-600">费用分类</div>
            <Select value={category} onChange={(e) => setCategory(e.target.value)} className="!w-40">
              <option value="">全部</option>
              {categoryOptions?.categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </div>

          <div className="min-w-[16rem] flex-1">
            <div className="mb-1 text-xs font-medium text-slate-600">搜索</div>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                applySearch()
              }}
            >
              <TextInput
                placeholder="发票号码 / 销售方 / 购买方 / 文件名 / 分类"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
              <Button size="md" type="submit">
                <Search size={14} />
              </Button>
            </form>
          </div>

          <div className="flex items-center gap-2 pb-1">
            <label className="flex items-center gap-1.5 whitespace-nowrap text-xs text-slate-600">
              <input
                type="checkbox"
                checked={abnormalOnly}
                onChange={(e) => setAbnormalOnly(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300"
              />
              只看有错误的
            </label>
            <Button size="sm" variant="ghost" onClick={resetFilters}>
              <Filter size={13} />
              重置
            </Button>
          </div>
        </div>
      </Card>

      {/* ---------------- 汇总 ---------------- */}
      {data ? (
        <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { label: '记录条数', value: `${data.summary.count} 条` },
            { label: '不含税金额', value: money(data.summary.amount_without_tax) },
            { label: '税额', value: money(data.summary.tax_amount) },
            { label: '价税合计', value: money(data.summary.total_amount) },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"
            >
              <div className="text-xs text-slate-500">{item.label}</div>
              <div className="mt-1 text-lg font-semibold text-slate-900">{item.value}</div>
            </div>
          ))}
        </div>
      ) : null}

      {error ? (
        <Alert level="error" title="加载失败">
          {error}
        </Alert>
      ) : null}

      {company === null ? (
        <div className="mb-4">
          <Alert level="warning" title="尚未设置当前企业">
            进项和销项需要先确定当前企业。可以到“设置 → 企业档案”保存，也可以点击右上角
            “导出 Excel”直接填写并保存。
          </Alert>
        </div>
      ) : null}

      {/* ---------------- 表格 ---------------- */}
      <Card padded={false}>
        {loading && !data ? (
          <Spinner />
        ) : visibleRows.length === 0 ? (
          <EmptyState
            title="当前筛选没有记录"
            description="换个月份、或者清空筛选条件再看看"
            action={
              <Button size="sm" onClick={resetFilters}>
                重置筛选
              </Button>
            }
          />
        ) : (
          <>
            {/* 选中后的批量操作条。跨页保留选择，所以这里显示的是总数 */}
            {selected.size > 0 ? (
              <div className="flex flex-wrap items-center gap-3 border-b border-brand-100 bg-brand-50/70 px-4 py-2.5">
                <span className="text-xs text-brand-800">
                  已选 <b>{selected.size}</b> 张票据
                  <span className="ml-1 text-brand-600/80">（翻页不会丢）</span>
                </span>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="ghost" onClick={() => void exportSelected()} disabled={exporting}>
                    <Download size={13} />
                    导出选中
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => setConfirmBatch(true)}
                    disabled={busy}
                  >
                    <Trash2 size={13} />
                    批量删除
                  </Button>
                  <button
                    onClick={clearSelection}
                    className="text-xs text-slate-500 underline-offset-2 hover:text-slate-700 hover:underline"
                  >
                    取消选择
                  </button>
                </div>
              </div>
            ) : null}

            <div className="max-h-[calc(100vh-320px)] min-h-[420px] overflow-auto overscroll-contain">
              {/* 表头、左侧日期和右侧操作列冻结；表格本身滚动，避免横向滚动条被长列表推到页面最底部。 */}
              <table className="w-full text-sm [&_td]:whitespace-nowrap [&_th]:whitespace-nowrap">
                <thead className="sticky top-0 z-30 bg-slate-50">
                  <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                    {/* 勾选列：勾的是 invoice_id，一行一张票 */}
                    <th className="sticky left-0 z-40 w-10 bg-slate-50 px-4 py-2.5">
                      <input
                        type="checkbox"
                        aria-label="全选本页"
                        checked={pageAllSelected}
                        ref={(el) => {
                          if (el) el.indeterminate = selected.size > 0 && !pageAllSelected
                        }}
                        onChange={toggleSelectPage}
                        className="h-3.5 w-3.5 cursor-pointer accent-brand-600"
                      />
                    </th>
                    <th className="sticky left-10 z-40 bg-slate-50 px-4 py-2.5 font-medium">
                      开票日期
                    </th>
                    <th className="px-3 py-2.5 font-medium">发票号码</th>
                    <th className="px-3 py-2.5 font-medium">票据方向</th>
                    <th className="px-3 py-2.5 font-medium">往来单位</th>
                    <th className="px-3 py-2.5 font-medium">项目名称</th>
                    <th className="px-3 py-2.5 text-right font-medium">不含税</th>
                    <th className="px-3 py-2.5 text-right font-medium">税额</th>
                    <th className="px-3 py-2.5 text-right font-medium">价税合计</th>
                    <th className="px-3 py-2.5 font-medium">费用分类</th>
                    <th className="px-3 py-2.5 font-medium">会计科目</th>
                    <th className="px-3 py-2.5 font-medium">状态</th>
                    <th className="sticky right-0 z-40 min-w-[120px] bg-slate-50 px-4 py-2.5 font-medium shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.55)]">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => (
                    <Fragment key={row.id}>
                      <tr
                        onClick={() => toggleDetail(row)}
                        title="点一下看这张票的明细"
                        className={`cursor-pointer border-b border-slate-100 transition last:border-0 ${
                          expandedId === row.invoice_id
                            ? 'bg-brand-50/50'
                            : 'hover:bg-slate-50/60'
                        }`}
                      >
                      <td
                        className="sticky left-0 z-20 whitespace-nowrap bg-white px-4 py-2.5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          aria-label="选择这张票"
                          checked={selected.has(row.invoice_id)}
                          onChange={() => toggleSelect(row.invoice_id)}
                          className="h-3.5 w-3.5 cursor-pointer accent-brand-600"
                        />
                      </td>
                      <td className="sticky left-10 z-20 whitespace-nowrap bg-white px-4 py-2.5 text-slate-700">
                        <span className="inline-flex items-center gap-1.5">
                          <ChevronRight
                            size={13}
                            className={`shrink-0 text-slate-400 transition-transform ${
                              expandedId === row.invoice_id ? 'rotate-90' : ''
                            }`}
                          />
                          {dateText(row.entry_date)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs text-slate-600">
                        {row.invoice_number || '—'}
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge
                          className={DIRECTION_CLASS[row.direction] ?? DIRECTION_CLASS.unknown}
                          title={row.direction_reason}
                        >
                          {row.direction_text || '待判断'}
                        </Badge>
                      </td>
                      <td
                        className="max-w-[12rem] truncate px-3 py-2.5 text-slate-700"
                        title={row.counterparty_name || row.seller_name || ''}
                      >
                        {row.counterparty_name || row.seller_name || '—'}
                      </td>
                      <td className="max-w-[14rem] truncate px-3 py-2.5 text-slate-600" title={row.item_name ?? ''}>
                        {row.item_name || '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                        {amount(row.amount_without_tax)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                        {amount(row.tax_amount)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-900">
                        {amount(row.total_amount)}
                      </td>
                      <td className="px-3 py-2.5 text-slate-700">{row.expense_category || '—'}</td>
                      <td className="max-w-[10rem] truncate px-3 py-2.5 text-xs text-slate-500">
                        {row.account_subject || '—'}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <Badge className={INVOICE_STATUS_CLASS[row.status]}>
                            {INVOICE_STATUS_TEXT[row.status] ?? row.status}
                          </Badge>
                          {row.error_count > 0 ? (
                            <Badge className="bg-red-50 text-red-700 ring-red-200">
                              {row.error_count} 错
                            </Badge>
                          ) : row.warning_count > 0 ? (
                            <Badge className="bg-amber-50 text-amber-700 ring-amber-200">
                              {row.warning_count} 警
                            </Badge>
                          ) : null}
                        </div>
                      </td>
                      <td className="sticky right-0 z-20 whitespace-nowrap bg-white px-4 py-2.5 shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.55)]">
                        {/* stopPropagation：这几个按钮不该触发行展开 */}
                        <div
                          className="flex items-center gap-2 text-xs"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <a
                            href={row.raw_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-slate-500 hover:text-brand-600"
                            title="查看原票"
                          >
                            <ExternalLink size={12} />
                          </a>
                          {row.status === 'pending_review' ? (
                            <button
                              onClick={() => navigate(`/review?id=${row.invoice_id}`)}
                              className="font-medium text-brand-600 hover:underline"
                            >
                              去复核
                            </button>
                          ) : (
                            <button
                              onClick={() => void revoke(row)}
                              disabled={busy}
                              className="inline-flex items-center gap-1 text-slate-500 hover:text-amber-600"
                              title="撤销入账"
                            >
                              <Undo2 size={12} />
                              撤销
                            </button>
                          )}
                          <button
                            onClick={() => setConfirmRow(row)}
                            className="text-slate-400 hover:text-red-600"
                            title="删除票据与原票"
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </td>
                    </tr>

                    {/* 展开的明细：点行才拉取，列表本身不带明细 */}
                    {expandedId === row.invoice_id ? (
                      <tr className="border-b border-slate-100">
                        <td colSpan={13} className="bg-slate-50/70 px-4 py-3">
                          {detailLoading ? (
                            <span className="flex items-center gap-2 text-xs text-slate-400">
                              <Loader2 size={14} className="animate-spin" />
                              正在读取明细…
                            </span>
                          ) : detail ? (
                            <InvoiceDetail invoice={detail} />
                          ) : (
                            <span className="text-xs text-slate-400">没读到明细</span>
                          )}
                        </td>
                      </tr>
                    ) : null}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 分页 */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-xs text-slate-500">
              <span>
                共 {data?.total ?? 0} 条，第 {page} / {totalPages} 页
                {abnormalOnly ? `（本页有错误的 ${visibleRows.length} 条）` : ''}
              </span>
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
                  上一页
                </Button>
                <Button
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                >
                  下一页
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>

      <p className="mt-3 text-xs text-slate-400">
        提示：导出文件名会自动带上月份和条数；金额保留两位小数、日期统一 yyyy-mm-dd、首行冻结并启用筛选。
      </p>

      <Modal
        open={exportOpen}
        title="导出票据台账"
        onClose={() => setExportOpen(false)}
        footer={
          <>
            <Button size="sm" onClick={() => setExportOpen(false)} disabled={exporting}>
              取消
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void doExport()}
              disabled={exporting || (!companyName.trim() && !companyTaxId.trim())}
            >
              <Download size={13} />
              {exporting ? '生成中…' : '保存企业档案并导出'}
            </Button>
          </>
        }
      >
        <Alert level="info" title="默认按票据方向分工作表">
          一张工作表只放一个方向，避免把进项和销项混在一起。没有匹配到的票据会进入「待判断」，
          不会硬分成进项或销项。
        </Alert>

        {companySuggestions && companySuggestions.length > 0 ? (
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-slate-600">
              从已登记票据中快速选择
            </div>
            <div className="flex flex-wrap gap-2">
              {companySuggestions.slice(0, 8).map((item) => {
                const roleText =
                  item.roles.includes('buyer') && item.roles.includes('seller')
                    ? '购买方/销售方'
                    : item.roles.includes('buyer')
                      ? '购买方'
                      : '销售方'
                return (
                  <button
                    key={`${item.name}-${item.tax_id ?? ''}`}
                    type="button"
                    onClick={() => {
                      setCompanyName(item.name)
                      setCompanyTaxId(item.tax_id || '')
                      setCompanyAliases('')
                    }}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-left transition hover:border-brand-300 hover:bg-brand-50"
                  >
                    <div className="text-xs font-medium text-slate-800">{item.name}</div>
                    <div className="mt-0.5 text-[11px] text-slate-500">
                      {roleText} · 出现在 {item.invoice_count} 张票据
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        ) : null}

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="当前企业名称" hint="例如：南京轩海贸易有限公司">
            <TextInput
              value={companyName}
              placeholder="请填写公司全称"
              onChange={(e) => setCompanyName(e.target.value)}
            />
          </Field>
          <Field label="纳税人识别号" hint="税号匹配优先级高于名称">
            <TextInput
              value={companyTaxId}
              placeholder="统一社会信用代码 / 纳税人识别号"
              onChange={(e) => setCompanyTaxId(e.target.value)}
            />
          </Field>
        </div>
        <Field
          label="企业别名"
          hint="每行一个，用于处理 OCR 偏差、历史名称或简称"
          className="mt-3"
        >
          <TextArea
            rows={3}
            value={companyAliases}
            placeholder="例如：轩海贸易"
            onChange={(e) => setCompanyAliases(e.target.value)}
          />
        </Field>

        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-600">
          <div>
            导出范围：<b className="text-slate-800">{exportTarget.label}</b>
          </div>
          <div className="mt-1">
            将生成：汇总 / 进项发票 / 销项发票 / 待判断 / 导出说明。没有数据的工作表不会生成。
          </div>
          {!exportTarget.invoiceIds && preview ? (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              {preview.by_direction.map((item) => (
                <span key={item.direction}>
                  {item.label} {item.invoice_count} 张
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </Modal>

      <Modal
        open={Boolean(confirmRow)}
        title="确认删除这张票据？"
        onClose={() => setConfirmRow(null)}
        footer={
          <>
            <Button size="sm" onClick={() => setConfirmRow(null)}>
              取消
            </Button>
            <Button size="sm" variant="danger" onClick={() => confirmRow && void remove(confirmRow)}>
              <Trash2 size={13} />
              删除票据（原票进回收站）
            </Button>
          </>
        }
      >
        <p className="leading-relaxed">
          将删除发票号码为 <b>{confirmRow?.invoice_number || '（空）'}</b> 的票据记录、
          明细、风险标记与账目。
          {/* 删除是「移进回收站」不是硬删 —— 文案要说准，用户才敢点 */}
          原票文件「{confirmRow?.original_name}」会<b>移到回收站</b>（<code>data/trash/</code>），
          不会直接销毁，误删了还能捞回来。
        </p>
        <p className="mt-2 text-red-600">此操作不可撤销。</p>
      </Modal>

      {/* 批量删除的确认框。数量写清楚 —— 批量删除最怕「不知道删了几张」 */}
      <Modal
        open={confirmBatch}
        title={`确认删除选中的 ${selected.size} 张票据？`}
        onClose={() => setConfirmBatch(false)}
        footer={
          <>
            <Button size="sm" onClick={() => setConfirmBatch(false)}>
              取消
            </Button>
            <Button size="sm" variant="danger" onClick={() => void batchRemove()} disabled={busy}>
              <Trash2 size={13} />
              删除这 {selected.size} 张（原票进回收站）
            </Button>
          </>
        }
      >
        <p className="leading-relaxed">
          将删除选中的 <b>{selected.size}</b> 张票据记录，以及它们的明细、风险标记与账目。
        </p>
        <p className="mt-2 leading-relaxed">
          对应的原票文件会<b>移到回收站</b>（<code>data/trash/</code>），
          不会直接销毁，误删了还能捞回来。
        </p>
        <p className="mt-2 text-xs text-slate-500">
          后端一次最多删 200 张；逐张提交，个别失败会单独报告，不会牵连已经删掉的。
        </p>
        <p className="mt-2 text-red-600">应用内没有「撤销」按钮，此操作不可撤销。</p>
      </Modal>
    </div>
  )
}
