import { NavLink, Outlet } from 'react-router-dom'
import { BookOpen, FileText, LayoutDashboard, Settings as SettingsIcon, Upload, Receipt } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../lib/hooks'
import { useDataVersion } from '../lib/refresh'

const NAV = [
  { to: '/', label: '首页', icon: LayoutDashboard, end: true, desc: '本月概览' },
  { to: '/tickets', label: '票据中心', icon: Upload, desc: '上传与批次' },
  { to: '/review', label: '复核工作台', icon: FileText, desc: '逐张确认' },
  { to: '/ledger', label: '账本', icon: BookOpen, desc: '查询与导出' },
  { to: '/settings', label: '设置', icon: SettingsIcon, desc: '分类规则' },
]

export default function Layout() {
  // 任何写操作成功后 api.ts 都会广播一次，版本号变了这里就重新拉概览 ——
  // 否则你确认完最后一张票，侧边栏角标还挂着旧数字。
  const version = useDataVersion()
  const { data: dash } = useAsync(() => api.dashboard(), [version])

  return (
    <div className="flex h-full min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Receipt size={18} />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-slate-800">票据记账助手</div>
            <div className="text-[11px] text-slate-400">企业智能票据 V1</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition ${
                  isActive
                    ? 'bg-brand-50 font-medium text-brand-700'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-800'
                }`
              }
            >
              <item.icon size={17} />
              <span className="flex-1">{item.label}</span>
              {item.to === '/review' && (dash?.pending_review_total ?? 0) > 0 ? (
                <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700">
                  {dash?.pending_review_total}
                </span>
              ) : null}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-100 px-4 py-3.5 text-[11px] leading-relaxed text-slate-400">
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                dash ? 'bg-emerald-500' : 'bg-slate-300'
              }`}
            />
            <span>
              OCR：{dash?.provider?.configured === 'baidu' ? '百度智能云' : '本地识别'}
            </span>
          </div>
          <div className="mt-1">
            {dash?.provider?.configured === 'baidu'
              ? `今日已用 ${dash.provider.daily_ocr_used}/${dash.provider.daily_ocr_limit} 次`
              : dash?.provider?.paddle_ready
                ? '含 GPU 图片识别 · 票据不出本机'
                : '零成本 · 票据不出本机'}
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-[1500px] px-7 py-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
