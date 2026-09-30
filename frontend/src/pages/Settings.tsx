import { useEffect, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Beaker,
  CheckCircle2,
  Cog,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  XCircle,
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
  Select,
  Spinner,
  TextArea,
  TextInput,
} from '../components/ui'
import { useAsync } from '../lib/hooks'
import type { CategoryRule } from '../types'

const EMPTY_FORM = {
  id: '',
  match_field: 'item_name',
  keyword: '',
  expense_category: '',
  account_subject: '',
  priority: 10,
  enabled: true,
  note: '',
}

export default function SettingsPage() {
  const toast = useToast()
  const [tab, setTab] = useState<'company' | 'rules' | 'ocr'>('company')
  const [typeFilter, setTypeFilter] = useState('')
  const [search, setSearch] = useState('')
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editing, setEditing] = useState(false)
  const [testSeller, setTestSeller] = useState('')
  const [testItem, setTestItem] = useState('*电子工业设备*LED液晶电视机')
  const [testResult, setTestResult] = useState<{ expense_category: string; rule_source: string } | null>(null)
  const [deepCheck, setDeepCheck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [companyName, setCompanyName] = useState('')
  const [companyTaxId, setCompanyTaxId] = useState('')
  const [companyAliases, setCompanyAliases] = useState('')
  const [savingCompany, setSavingCompany] = useState(false)

  const {
    data: rules,
    loading,
    reload,
  } = useAsync(
    () => api.listRules({ rule_type: typeFilter || undefined, q: search || undefined }),
    [typeFilter, search],
  )
  const { data: options } = useAsync(() => api.ruleOptions(), [])
  const { data: providers, reload: reloadProviders } = useAsync(() => api.providers(false), [deepCheck])
  const { data: company, reload: reloadCompany } = useAsync(() => api.getCompany(), [])

  useEffect(() => {
    if (!company) return
    setCompanyName(company.name || '')
    setCompanyTaxId(company.tax_id || '')
    setCompanyAliases((company.aliases || []).join('\n'))
  }, [company])

  const openCreate = () => {
    setForm({ ...EMPTY_FORM })
    setEditing(true)
  }

  const openEdit = (rule: CategoryRule) => {
    setForm({
      id: rule.id,
      match_field: rule.match_field,
      keyword: rule.keyword,
      expense_category: rule.expense_category,
      account_subject: rule.account_subject ?? '',
      priority: rule.priority,
      enabled: rule.enabled,
      note: rule.note ?? '',
    })
    setEditing(true)
  }

  const submit = async () => {
    if (!form.keyword.trim() || !form.expense_category.trim()) {
      toast('error', '关键词和费用分类都要填')
      return
    }
    setBusy(true)
    try {
      const body = {
        rule_type: 'custom',
        match_field: form.match_field,
        keyword: form.keyword.trim(),
        expense_category: form.expense_category,
        account_subject: form.account_subject.trim() || null,
        priority: Number(form.priority) || 10,
        enabled: form.enabled,
        note: form.note.trim() || null,
      }
      if (form.id) {
        await api.updateRule(form.id, body)
        toast('success', '规则已更新')
      } else {
        await api.createRule(body)
        toast('success', '规则已新增，下张同类票据会自动命中')
      }
      setEditing(false)
      reload()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (rule: CategoryRule) => {
    try {
      await api.toggleRule(rule.id)
      reload()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const remove = async (rule: CategoryRule) => {
    try {
      const result = await api.deleteRule(rule.id)
      toast(result.disabled ? 'info' : 'success', result.message)
      reload()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const move = async (rule: CategoryRule, direction: 'up' | 'down') => {
    try {
      await api.moveRule(rule.id, direction)
      reload()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const runTest = async () => {
    try {
      setTestResult(await api.testRule({ seller_name: testSeller, item_name: testItem }))
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const saveCompany = async () => {
    if (!companyName.trim() && !companyTaxId.trim()) {
      toast('error', '企业名称和纳税人识别号至少填一个')
      return
    }
    setSavingCompany(true)
    try {
      await api.saveCompany({
        name: companyName.trim(),
        tax_id: companyTaxId.trim() || null,
        aliases: companyAliases
          .split(/\r?\n|[,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      toast('success', '企业档案已保存')
      reloadCompany()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    } finally {
      setSavingCompany(false)
    }
  }

  const customCount = (rules ?? []).filter((r) => r.rule_type === 'custom').length
  const systemCount = (rules ?? []).filter((r) => r.rule_type === 'system').length

  return (
    <div>
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-slate-900">设置</h1>
        <p className="mt-1 text-sm text-slate-500">
          企业档案、费用分类规则与 OCR 供应商
        </p>
      </div>

      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1">
        {(
          [
            ['company', '企业档案'],
            ['rules', '费用分类规则'],
            ['ocr', 'OCR 与识别'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === key ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'company' ? (
        <Card>
          <CardTitle
            title="当前企业档案"
            subtitle="用于判断进项发票和销项发票；保存后账本与导出都会使用这里的设置"
            right={
              <Button
                size="sm"
                variant="primary"
                onClick={() => void saveCompany()}
                disabled={savingCompany || (!companyName.trim() && !companyTaxId.trim())}
              >
                {savingCompany ? '保存中…' : '保存企业档案'}
              </Button>
            }
          />

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="当前企业名称" hint="公司全称，例如：南京轩海贸易有限公司">
              <TextInput
                value={companyName}
                placeholder="请填写公司全称"
                onChange={(e) => setCompanyName(e.target.value)}
              />
            </Field>
            <Field label="纳税人识别号" hint="有税号时优先按税号匹配，准确率更高">
              <TextInput
                value={companyTaxId}
                placeholder="统一社会信用代码 / 纳税人识别号"
                onChange={(e) => setCompanyTaxId(e.target.value)}
              />
            </Field>
          </div>

          <Field
            label="企业别名"
            hint="每行一个，用于兼容 OCR 偏差、历史名称或简称"
            className="mt-3"
          >
            <TextArea
              rows={4}
              value={companyAliases}
              placeholder="例如：轩海贸易"
              onChange={(e) => setCompanyAliases(e.target.value)}
            />
          </Field>

          <div className="mt-4">
            <Alert level="info">
              发票购买方匹配当前企业时归入进项，销售方匹配当前企业时归入销项。以后修改这里，
              已登记票据会按新档案重新判断。
            </Alert>
          </div>
        </Card>
      ) : tab === 'rules' ? (
        <>
          <Card className="mb-4">
            <CardTitle
              title="规则试跑"
              subtitle="输入销售方或项目名称，看看会命中哪条规则"
              right={
                <Badge className="bg-brand-50 text-brand-700 ring-brand-200">
                  <Beaker size={11} />
                  不写入命中次数
                </Badge>
              }
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="销售方名称">
                <TextInput
                  value={testSeller}
                  placeholder="如：阿里云计算有限公司"
                  onChange={(e) => setTestSeller(e.target.value)}
                />
              </Field>
              <Field label="项目名称">
                <TextInput
                  value={testItem}
                  placeholder="如：*信息技术服务*云服务"
                  onChange={(e) => setTestItem(e.target.value)}
                />
              </Field>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button size="sm" variant="primary" onClick={runTest}>
                试跑
              </Button>
              {testResult ? (
                <span className="text-sm">
                  命中结果：
                  <b className="text-slate-800">{testResult.expense_category}</b>
                  <span className="ml-2 text-xs text-slate-500">{testResult.rule_source}</span>
                </span>
              ) : null}
            </div>
          </Card>

          <Card>
            <CardTitle
              title="分类规则"
              subtitle={`自定义 ${customCount} 条 · 系统内置 ${systemCount} 条（当前筛选）`}
              right={
                <div className="flex items-center gap-2">
                  <Select
                    value={typeFilter}
                    onChange={(e) => setTypeFilter(e.target.value)}
                    className="!w-36 !py-1.5 !text-xs"
                  >
                    <option value="">全部类型</option>
                    <option value="custom">仅企业自定义</option>
                    <option value="system">仅系统内置</option>
                  </Select>
                  <TextInput
                    value={search}
                    placeholder="搜关键词 / 分类"
                    onChange={(e) => setSearch(e.target.value)}
                    className="!w-40 !py-1.5 !text-xs"
                  />
                  <Button size="sm" variant="ghost" onClick={reload}>
                    <RefreshCw size={13} />
                  </Button>
                  <Button size="sm" variant="primary" onClick={openCreate}>
                    <Plus size={13} />
                    新增规则
                  </Button>
                </div>
              }
            />

            {loading && !rules ? (
              <Spinner />
            ) : !rules || rules.length === 0 ? (
              <EmptyState title="没有匹配的规则" description="换个筛选条件，或新增一条自定义规则" />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                      <th className="py-2 pr-3 font-medium">优先级</th>
                      <th className="py-2 pr-3 font-medium">类型</th>
                      <th className="py-2 pr-3 font-medium">匹配字段</th>
                      <th className="py-2 pr-3 font-medium">关键词</th>
                      <th className="py-2 pr-3 font-medium">费用分类</th>
                      <th className="py-2 pr-3 font-medium">会计科目</th>
                      <th className="py-2 pr-3 font-medium">命中次数</th>
                      <th className="py-2 pr-3 font-medium">状态</th>
                      <th className="py-2 font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((rule) => (
                      <tr key={rule.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                        <td className="py-2.5 pr-3">
                          <div className="flex items-center gap-1">
                            <span className="w-8 tabular-nums text-slate-500">{rule.priority}</span>
                            <button
                              onClick={() => void move(rule, 'up')}
                              className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                            >
                              <ArrowUp size={12} />
                            </button>
                            <button
                              onClick={() => void move(rule, 'down')}
                              className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                            >
                              <ArrowDown size={12} />
                            </button>
                          </div>
                        </td>
                        <td className="py-2.5 pr-3">
                          {rule.rule_type === 'custom' ? (
                            <Badge className="bg-brand-50 text-brand-700 ring-brand-200">自定义</Badge>
                          ) : (
                            <Badge className="bg-slate-100 text-slate-500 ring-slate-200">系统</Badge>
                          )}
                        </td>
                        <td className="py-2.5 pr-3 text-xs text-slate-600">
                          {rule.match_field === 'item_name' ? '项目名称' : '销售方名称'}
                        </td>
                        <td className="py-2.5 pr-3 font-medium text-slate-800">{rule.keyword}</td>
                        <td className="py-2.5 pr-3 text-slate-700">{rule.expense_category}</td>
                        <td className="py-2.5 pr-3 text-xs text-slate-500">
                          {rule.account_subject || '—'}
                        </td>
                        <td className="py-2.5 pr-3 tabular-nums text-slate-500">{rule.hits}</td>
                        <td className="py-2.5 pr-3">
                          <button
                            onClick={() => void toggle(rule)}
                            className={`inline-flex items-center gap-1 text-xs font-medium ${
                              rule.enabled ? 'text-emerald-600' : 'text-slate-400'
                            }`}
                          >
                            {rule.enabled ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                            {rule.enabled ? '启用' : '停用'}
                          </button>
                        </td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2 text-xs">
                            <button
                              onClick={() => openEdit(rule)}
                              className="text-brand-600 hover:underline"
                            >
                              编辑
                            </button>
                            <button
                              onClick={() => void remove(rule)}
                              className="text-slate-400 hover:text-red-600"
                              title={
                                rule.rule_type === 'system'
                                  ? '系统规则只能停用，不能删除'
                                  : '删除规则'
                              }
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="mt-4">
              <Alert level="info">
                命中顺序：先按「企业自定义规则 → 系统内置规则」分两组，组内再按优先级数字从小到大。
                数字越小越优先，所以更具体的关键词应该给更小的数字。系统内置规则只能停用、不能删除，
                免得误删后无法回到基线。
              </Alert>
            </div>
          </Card>
        </>
      ) : (
        <>
          <Card className="mb-4">
            <CardTitle
              title="OCR 供应商"
              subtitle="云端结构化识别为主、本地解析为兜底，可随时切换"
              right={
                <Button
                  size="sm"
                  onClick={async () => {
                    setDeepCheck(true)
                    reloadProviders()
                  }}
                >
                  <RefreshCw size={13} className={deepCheck ? 'animate-spin' : ''} />
                  体检
                </Button>
              }
            />
            <ul className="space-y-3">
              {(providers?.providers ?? []).map((provider) => (
                <li
                  key={provider.name}
                  className="flex items-start gap-3 rounded-lg border border-slate-200 px-4 py-3"
                >
                  <span
                    className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                      provider.ready ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-400'
                    }`}
                  >
                    {provider.ready ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-800">{provider.display_name}</span>
                      {providers?.active === provider.name ? (
                        <Badge className="bg-brand-50 text-brand-700 ring-brand-200">当前启用</Badge>
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">{provider.detail ?? provider.note}</p>
                  </div>
                </li>
              ))}
              {!providers ? <Spinner /> : null}
            </ul>

            <div className="mt-4 space-y-3">
              <Alert level="warning" title="要启用百度智能云增值税发票识别（支持图片与扫描件）">
                <ol className="mt-1 list-decimal space-y-1 pl-4">
                  <li>
                    去{' '}
                    <a
                      className="underline"
                      href="https://console.bce.baidu.com/ai/#/ai/ocr/app/list"
                      target="_blank"
                      rel="noreferrer"
                    >
                      百度智能云控制台
                    </a>{' '}
                    完成实名认证并创建 OCR 应用，拿到 API Key 与 Secret Key
                  </li>
                  <li>
                    把 <code>backend/.env.example</code> 复制成 <code>backend/.env</code>
                  </li>
                  <li>
                    填写 <code>BAIDU_API_KEY</code> 与 <code>BAIDU_SECRET_KEY</code>，
                    把 <code>OCR_PROVIDER</code> 设为 <code>baidu</code>（或保留 <code>auto</code>
                    自动判断）
                  </li>
                  <li>重启后端服务，回到本页点「体检」确认鉴权通过</li>
                </ol>
                <p className="mt-2 text-xs">
                  密钥只存在后端环境变量里，不会出现在前端、不会写进日志。服务端还有每日调用上限
                  （当前 {providers?.providers ? '' : ''}默认 200 次）防止意外产生费用。
                </p>
              </Alert>

              <Alert level="info" title="图片票与扫描件：本地 PaddleOCR（GPU）">
                后端会自动扫描本机 Python 环境，找到装了 <code>paddleocr</code> 的那个就用它，
                通过<b>常驻子进程</b>调用（模型只加载一次，之后每张图只花推理时间）。
                票据全程不出本机，空闲一段时间后会自动退出子进程把显存还给你。
                <br />
                如果自动扫描没找到，可以在 <code>backend/.env</code> 里手动指定：
                <br />
                <code>PADDLE_PYTHON=C:\path\to\env\Scripts\python.exe</code>
                <br />
                还想要个更轻量的 CPU 备选，可以 <code>pip install rapidocr-onnxruntime</code>。
              </Alert>

              <Alert level="error" title="费用安全">
                云服务控制台请关闭或限制「自动后付费」，并设置用量告警。开发阶段请用测试发票，
                并对税号、公司名称等信息做脱敏。
              </Alert>
            </div>
          </Card>
        </>
      )}

      {/* ---------------- 规则编辑弹窗 ---------------- */}
      <Modal
        open={editing}
        title={form.id ? '编辑规则' : '新增自定义规则'}
        onClose={() => setEditing(false)}
        footer={
          <>
            <Button size="sm" onClick={() => setEditing(false)}>
              取消
            </Button>
            <Button size="sm" variant="primary" onClick={submit} disabled={busy}>
              <Cog size={13} />
              {form.id ? '保存修改' : '创建规则'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="匹配字段" hint="选「销售方名称」适合按供应商定分类">
              <Select
                value={form.match_field}
                onChange={(e) => setForm({ ...form, match_field: e.target.value })}
              >
                {options?.match_fields.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="优先级" hint="数字越小越优先，建议 10~99">
              <TextInput
                type="number"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
              />
            </Field>
          </div>
          <Field label="关键词" hint="包含匹配，不区分大小写；如「阿里云」「云服务」">
            <TextInput
              value={form.keyword}
              onChange={(e) => setForm({ ...form, keyword: e.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="费用分类">
              <Select
                value={form.expense_category}
                onChange={(e) => setForm({ ...form, expense_category: e.target.value })}
              >
                <option value="">（请选择）</option>
                {options?.categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="会计科目" hint="如：管理费用—差旅费">
              <TextInput
                value={form.account_subject}
                onChange={(e) => setForm({ ...form, account_subject: e.target.value })}
              />
            </Field>
          </div>
          <Field label="备注">
            <TextInput
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="h-3.5 w-3.5 rounded border-slate-300"
            />
            启用这条规则
          </label>
          {form.id ? (
            <p className="flex items-center gap-1 text-xs text-slate-400">
              <ShieldAlert size={12} />
              修改只影响之后的识别，已入库的票据需要手动复核修改
            </p>
          ) : null}
        </div>
      </Modal>
    </div>
  )
}
