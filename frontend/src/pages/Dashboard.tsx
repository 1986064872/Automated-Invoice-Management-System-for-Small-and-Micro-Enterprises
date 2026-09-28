import { useNavigate } from 'react-router-dom'
import {
  AlertOctagon,
  ArrowRight,
  ClipboardCheck,
  FileStack,
  RefreshCw,
  ShieldCheck,
  Wallet,
} from 'lucide-react'
import { api } from '../api'
import { Badge, Button, Card, CardTitle, EmptyState, ProgressBar, Spinner } from '../components/ui'
import { useAsync } from '../lib/hooks'
import {
  JOB_STATUS_CLASS,
  JOB_STATUS_TEXT,
  currentMonth,
  dateTimeText,
  money,
} from '../lib/format'
import { useState } from 'react'

function StatCard({
  label,
  value,
  sub,
  icon,
  tone,
  onClick,
}: {
  label: string
  value: string
  sub?: string
  icon: React.ReactNode
  tone: 'brand' | 'amber' | 'red' | 'emerald'
  onClick?: () => void
}) {
  const tones = {
    brand: 'bg-brand-50 text-brand-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-red-50 text-red-600',
    emerald: 'bg-emerald-50 text-emerald-600',
  }
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className="group flex w-full items-start gap-3.5 rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-brand-300 hover:shadow-md disabled:hover:border-slate-200 disabled:hover:shadow-sm"
    >
      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${tones[tone]}`}>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-slate-500">{label}</span>
        <span className="mt-1 block text-xl font-semibold tracking-tight text-slate-900">{value}</span>
        <span className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-400">
          {sub}
          {onClick ? (
            <ArrowRight size={11} className="opacity-0 transition group-hover:opacity-100" />
          ) : null}
        </span>
      </span>
    </button>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [month, setMonth] = useState(currentMonth())
  const { data, loading, error, reload } = useAsync(() => api.dashboard(month), [month])

  const maxCategory = Math.max(1, ...(data?.by_category ?? []).map((c) => c.total_amount))

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">首页概览</h1>
          <p className="mt-1 text-sm text-slate-500">
            上传 → 识别 → 校验 → 复核 → 入账 → 导出，一眼看清本月进度
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
          <Button size="sm" variant="ghost" onClick={reload} title="刷新">
            <RefreshCw size={14} />
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="mb-5 border-red-200 bg-red-50">
          <div className="text-sm text-red-700">
            加载失败：{error}
            <div className="mt-1 text-xs text-red-500">
              请确认后端服务已启动（默认 http://127.0.0.1:8000）
            </div>
          </div>
        </Card>
      ) : null}

      {loading && !data ? (
        <Spinner />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label={`${month} 票据数`}
              value={`${data?.invoice_count ?? 0} 张`}
              sub="点击查看账本"
              icon={<FileStack size={19} />}
              tone="brand"
              onClick={() => navigate(`/ledger?month=${month}`)}
            />
            <StatCard
              label="本月总金额（价税合计）"
              value={money(data?.total_amount ?? 0)}
              sub="点击查看账本"
              icon={<Wallet size={19} />}
              tone="emerald"
              onClick={() => navigate(`/ledger?month=${month}`)}
            />
            <StatCard
              label="待复核"
              value={`${data?.pending_review ?? 0} 张`}
              sub="需要人工确认"
              icon={<ClipboardCheck size={19} />}
              tone="amber"
              onClick={() => navigate('/review')}
            />
            <StatCard
              label="异常（红色错误）"
              value={`${data?.abnormal ?? 0} 张`}
              sub="阻止入账，必须先修"
              icon={<AlertOctagon size={19} />}
              tone="red"
              onClick={() => navigate('/ledger?status=all&abnormal=1')}
            />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-3">
              <CardTitle
                title="费用分类分布"
                subtitle={`${month} 按价税合计汇总`}
                right={<Badge>{data?.by_category?.length ?? 0} 个分类</Badge>}
              />
              {data && data.by_category.length > 0 ? (
                <ul className="space-y-3">
                  {data.by_category.map((item) => (
                    <li key={item.category}>
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-700">{item.category}</span>
                        <span className="text-slate-500">
                          {item.count} 张 · {money(item.total_amount)}
                        </span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-brand-500"
                          style={{ width: `${(item.total_amount / maxCategory) * 100}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="本月还没有票据"
                  description="去「票据中心」上传一批电子发票试试"
                  action={
                    <Button variant="primary" size="sm" onClick={() => navigate('/tickets')}>
                      去上传
                    </Button>
                  }
                />
              )}
            </Card>

            <div className="space-y-4 lg:col-span-2">
              <Card>
                <CardTitle title="识别能力" subtitle="OCR 供应商状态" />
                <ul className="space-y-2.5 text-sm">
                  <li className="flex items-center justify-between">
                    <span className="flex items-center gap-2 text-slate-600">
                      <ShieldCheck size={15} className="text-brand-500" />
                      当前启用的供应商
                    </span>
                    <Badge className="bg-brand-50 text-brand-700 ring-brand-200">
                      {data?.provider.configured === 'baidu' ? '百度智能云' : '本地识别'}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span className="text-slate-600">PDF 文本层解析</span>
                    <Badge className="bg-emerald-50 text-emerald-700 ring-emerald-200">可用</Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span className="text-slate-600">图片 OCR（PaddleOCR）</span>
                    <Badge
                      className={
                        data?.provider.paddle_ready
                          ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
                          : 'bg-slate-100 text-slate-500 ring-slate-200'
                      }
                      title={data?.provider.paddle_detail}
                    >
                      {data?.provider.paddle_ready ? 'GPU 就绪' : '未检测到'}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span className="text-slate-600">百度 OCR 密钥</span>
                    <Badge
                      className={
                        data?.provider.baidu_ready
                          ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
                          : 'bg-slate-100 text-slate-500 ring-slate-200'
                      }
                    >
                      {data?.provider.baidu_ready ? '已配置' : '未配置'}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span className="text-slate-600">今日云调用额度</span>
                    <span className="text-xs text-slate-500">
                      {data?.provider.daily_ocr_used ?? 0} / {data?.provider.daily_ocr_limit ?? 0}
                    </span>
                  </li>
                </ul>
              </Card>

              <Card>
                <CardTitle title="处理进度" subtitle="本月已入账占比" />
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-2xl font-semibold text-slate-900">
                    {data?.confirmed ?? 0}
                    <span className="ml-1 text-sm font-normal text-slate-400">
                      / {data?.invoice_count ?? 0} 张
                    </span>
                  </span>
                  <span className="text-xs text-slate-500">
                    {data && data.invoice_count
                      ? Math.round((data.confirmed / data.invoice_count) * 100)
                      : 0}
                    %
                  </span>
                </div>
                <ProgressBar
                  value={data && data.invoice_count ? data.confirmed / data.invoice_count : 0}
                />
                {data && data.failed > 0 ? (
                  <p className="mt-3 text-xs text-red-600">
                    有 {data.failed} 个文件处理失败，去「票据中心」查看原因并重试
                  </p>
                ) : null}
              </Card>
            </div>
          </div>

          <Card className="mt-4">
            <CardTitle title="最近上传批次" subtitle="每批最多 50 个文件" />
            {data && data.recent_jobs.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                      <th className="py-2 pr-4 font-medium">批次</th>
                      <th className="py-2 pr-4 font-medium">总文件</th>
                      <th className="py-2 pr-4 font-medium">成功</th>
                      <th className="py-2 pr-4 font-medium">失败</th>
                      <th className="py-2 pr-4 font-medium">状态</th>
                      <th className="py-2 pr-4 font-medium">上传时间</th>
                      <th className="py-2 font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_jobs.map((job) => (
                      <tr key={job.id} className="border-b border-slate-100 last:border-0">
                        <td className="py-2.5 pr-4 font-mono text-xs text-slate-500">
                          {job.id.slice(0, 8)}
                        </td>
                        <td className="py-2.5 pr-4 text-slate-700">{job.total}</td>
                        <td className="py-2.5 pr-4 text-emerald-700">{job.succeeded}</td>
                        <td className="py-2.5 pr-4 text-red-600">{job.failed}</td>
                        <td className="py-2.5 pr-4">
                          <Badge className={JOB_STATUS_CLASS[job.status]}>
                            {JOB_STATUS_TEXT[job.status] ?? job.status}
                          </Badge>
                        </td>
                        <td className="py-2.5 pr-4 text-xs text-slate-500">
                          {dateTimeText(job.created_at)}
                        </td>
                        <td className="py-2.5">
                          <button
                            onClick={() => navigate(`/tickets?job=${job.id}`)}
                            className="text-xs font-medium text-brand-600 hover:underline"
                          >
                            查看详情
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="还没有上传记录" description="上传第一批票据后，这里会显示批次进度" />
            )}
          </Card>
        </>
      )}
    </div>
  )
}
