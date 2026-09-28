import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertCircle,
  CloudUpload,
  ExternalLink,
  FileImage,
  FileText,
  PlayCircle,
  RefreshCw,
  Trash2,
  Upload,
} from 'lucide-react'
import { api } from '../api'
import { useToast } from '../components/Toast'
import { Alert, Badge, Button, Card, CardTitle, EmptyState, ProgressBar, Spinner } from '../components/ui'
import { useAsync, usePoll } from '../lib/hooks'
import {
  FILE_STATUS_CLASS,
  FILE_STATUS_TEXT,
  JOB_STATUS_CLASS,
  JOB_STATUS_TEXT,
  dateTimeText,
  fileSize,
} from '../lib/format'
import type { Job, UploadResult } from '../types'

const ACCEPT = '.pdf,.jpg,.jpeg,.png'

export default function TicketCenter() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()

  const [picked, setPicked] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [rejected, setRejected] = useState<UploadResult['rejected']>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(params.get('job'))
  const [job, setJob] = useState<Job | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: config } = useAsync(() => api.config(), [])
  const { data: jobs, reload: reloadJobs } = useAsync(() => api.listJobs(15), [])
  const maxSizeMb = Number(config?.max_file_size_mb ?? 20)
  const maxFiles = Number(config?.max_batch_files ?? 50)

  const totalSize = useMemo(() => picked.reduce((sum, f) => sum + f.size, 0), [picked])

  // ---------- 批次进度轮询 ----------
  const pollTick = useCallback(async () => {
    if (!activeJobId) return false
    try {
      const latest = await api.getJob(activeJobId)
      setJob(latest)
      if (latest.status === 'done' || latest.status === 'partial' || latest.status === 'failed') {
        reloadJobs()
        return false
      }
    } catch {
      return false
    }
    return true
  }, [activeJobId, reloadJobs])

  usePoll(pollTick, Boolean(activeJobId), 900)

  useEffect(() => {
    if (activeJobId) void pollTick()
  }, [activeJobId, pollTick])

  // ---------- 选文件 ----------
  const addFiles = useCallback(
    (incoming: FileList | File[]) => {
      const list = Array.from(incoming)
      const fresh: File[] = []
      const localRejected: UploadResult['rejected'] = []

      for (const file of list) {
        const ext = '.' + (file.name.split('.').pop() ?? '').toLowerCase()
        if (!ACCEPT.includes(ext)) {
          localRejected.push({ name: file.name, reason: `格式不支持（${ext || '无扩展名'}）` })
          continue
        }
        if (file.size > maxSizeMb * 1024 * 1024) {
          localRejected.push({
            name: file.name,
            reason: `超过 ${maxSizeMb}MB 上限（${fileSize(file.size)}）`,
          })
          continue
        }
        fresh.push(file)
      }

      setReasonIfAny(localRejected)
      setPicked((prev) => {
        const merged = [...prev, ...fresh]
        if (merged.length > maxFiles) {
          setReasonIfAny([
            ...localRejected,
            { name: '（已截断）', reason: `单批最多 ${maxFiles} 个文件，多出的未加入` },
          ])
          return merged.slice(0, maxFiles)
        }
        return merged
      })
    },
    [maxFiles, maxSizeMb],
  )

  function setReasonIfAny(list: UploadResult['rejected']) {
    if (list.length) setRejected(list)
  }

  const removeAt = (index: number) => setPicked((prev) => prev.filter((_, i) => i !== index))

  // ---------- 上传 ----------
  const doUpload = async () => {
    if (!picked.length) return
    setUploading(true)
    setProgress(0)
    setRejected([])
    try {
      const result = await api.upload(picked, setProgress)
      setRejected(result.rejected)
      toast(
        result.rejected.length ? 'info' : 'success',
        `${result.message}${
          result.rejected.length
            ? '\n未通过：' + result.rejected.map((r) => `${r.name}（${r.reason}）`).join('；')
            : ''
        }`,
      )
      setPicked([])
      if (result.job_id) {
        setActiveJobId(result.job_id)
        setParams({ job: result.job_id })
        reloadJobs()
      }
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
      setProgress(0)
    }
  }

  const retryFile = async (invoiceId: string | null | undefined, fileName: string) => {
    if (!invoiceId) {
      toast('info', `「${fileName}」没有可重试的票据记录，请删除后重新上传。`)
      return
    }
    try {
      await api.retryInvoice(invoiceId)
      toast('success', `「${fileName}」已重新识别`)
      if (activeJobId) void pollTick()
    } catch (err) {
      toast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const running = job ? ['queued', 'processing'].includes(job.status) : false

  return (
    <div>
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-slate-900">票据中心</h1>
        <p className="mt-1 text-sm text-slate-500">
          批量上传 PDF / JPG / JPEG / PNG，系统会逐个识别并标出失败原因
        </p>
      </div>

      {/* ---------------- 上传区 ---------------- */}
      <Card className="mb-4">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files)
          }}
          onClick={() => inputRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 transition ${
            dragging ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-slate-50/60 hover:border-brand-400'
          }`}
        >
          <CloudUpload size={30} className={dragging ? 'text-brand-600' : 'text-slate-400'} />
          <div className="text-sm font-medium text-slate-700">
            把发票拖到这里，或点击选择文件
          </div>
          <div className="text-xs text-slate-400">
            支持 PDF / JPG / JPEG / PNG · 单文件 ≤ {maxSizeMb}MB · 单批 ≤ {maxFiles} 个
          </div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files)
              e.target.value = ''
            }}
          />
        </div>

        {picked.length > 0 ? (
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
              <span>
                已选 {picked.length} 个文件 · 共 {fileSize(totalSize)}
              </span>
              <button className="text-slate-500 hover:text-red-600" onClick={() => setPicked([])}>
                清空
              </button>
            </div>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {picked.map((file, index) => (
                <li key={`${file.name}-${index}`} className="flex items-center gap-3 px-3 py-2 text-sm">
                  {file.type.includes('pdf') ? (
                    <FileText size={15} className="text-red-500" />
                  ) : (
                    <FileImage size={15} className="text-blue-500" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-slate-700">{file.name}</span>
                  <span className="text-xs text-slate-400">{fileSize(file.size)}</span>
                  <button
                    onClick={() => removeAt(index)}
                    className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>

            {uploading ? (
              <div className="mt-4">
                <div className="mb-1 flex justify-between text-xs text-slate-500">
                  <span>正在上传…</span>
                  <span>{progress}%</span>
                </div>
                <ProgressBar value={progress / 100} />
              </div>
            ) : (
              <div className="mt-4 flex justify-end">
                <Button variant="primary" onClick={doUpload} disabled={uploading}>
                  <Upload size={15} />
                  开始上传并识别（{picked.length}）
                </Button>
              </div>
            )}
          </div>
        ) : null}

        {rejected.length > 0 ? (
          <div className="mt-4 space-y-2">
            {rejected.map((item, i) => (
              <Alert key={i} level="warning" title={`未上传：${item.name}`}>
                {item.reason}
              </Alert>
            ))}
          </div>
        ) : null}
      </Card>

      {/* ---------------- 当前批次 ---------------- */}
      {job ? (
        <Card className="mb-4">
          <CardTitle
            title={`当前批次 ${job.id.slice(0, 8)}`}
            subtitle={`共 ${job.total} 个文件 · 成功 ${job.succeeded} · 失败 ${job.failed}`}
            right={
              <div className="flex items-center gap-2">
                <Badge className={JOB_STATUS_CLASS[job.status]}>
                  {JOB_STATUS_TEXT[job.status] ?? job.status}
                </Badge>
                {running ? <RefreshCw size={14} className="animate-spin text-brand-500" /> : null}
                <Button size="sm" variant="ghost" onClick={() => void pollTick()}>
                  刷新
                </Button>
              </div>
            }
          />
          <div className="mb-4">
            <ProgressBar value={job.progress} />
            <div className="mt-1 text-right text-xs text-slate-400">
              {Math.round(job.progress * 100)}%
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                  <th className="py-2 pr-3 font-medium">文件</th>
                  <th className="py-2 pr-3 font-medium">类型</th>
                  <th className="py-2 pr-3 font-medium">页数</th>
                  <th className="py-2 pr-3 font-medium">状态</th>
                  <th className="py-2 pr-3 font-medium">说明</th>
                  <th className="py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {job.files.map((file) => (
                  <tr key={file.id} className="border-b border-slate-100 last:border-0 align-top">
                    <td className="max-w-[22rem] py-2.5 pr-3">
                      <span className="line-clamp-2 text-slate-700">{file.original_name}</span>
                      <span className="text-[11px] text-slate-400">
                        {fileSize(file.file_size)} · {dateTimeText(file.created_at)}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-xs uppercase text-slate-500">{file.file_type}</td>
                    <td className="py-2.5 pr-3 text-slate-600">{file.page_count}</td>
                    <td className="py-2.5 pr-3">
                      <Badge className={FILE_STATUS_CLASS[file.status]}>
                        {FILE_STATUS_TEXT[file.status] ?? file.status}
                      </Badge>
                    </td>
                    <td className="max-w-[24rem] py-2.5 pr-3 text-xs text-slate-500">
                      {file.error_message ? (
                        <span className={file.status === 'failed' ? 'text-red-600' : 'text-amber-700'}>
                          {file.error_message}
                        </span>
                      ) : file.ocr_provider ? (
                        <span className="text-slate-400">识别引擎：{file.ocr_provider}</span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <a
                          href={file.raw_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-brand-600"
                        >
                          <ExternalLink size={12} />
                          看原票
                        </a>
                        {file.status === 'failed' ? (
                          <button
                            onClick={() => retryFile(file.invoice_id, file.original_name)}
                            className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline"
                          >
                            <PlayCircle size={12} />
                            重新识别
                          </button>
                        ) : null}
                        {file.invoice_id && file.status !== 'failed' ? (
                          <button
                            onClick={() => navigate(`/review?id=${file.invoice_id}`)}
                            className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 hover:underline"
                          >
                            去复核
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {job.error_message ? (
            <div className="mt-3">
              <Alert level="error" title="批次异常">
                {job.error_message}
              </Alert>
            </div>
          ) : null}
        </Card>
      ) : null}

      {/* ---------------- 历史批次 ---------------- */}
      <Card>
        <CardTitle
          title="历史批次"
          subtitle="点击查看某一批的文件明细"
          right={
            <Button size="sm" variant="ghost" onClick={reloadJobs}>
              <RefreshCw size={13} />
              刷新
            </Button>
          }
        />
        {!jobs ? (
          <Spinner />
        ) : jobs.length === 0 ? (
          <EmptyState title="还没有批次" description="上传第一批票据后出现" />
        ) : (
          <ul className="divide-y divide-slate-100">
            {jobs.map((item) => (
              <li key={item.id}>
                <button
                  onClick={() => {
                    setActiveJobId(item.id)
                    setParams({ job: item.id })
                  }}
                  className={`flex w-full items-center gap-4 px-1 py-3 text-left text-sm transition hover:bg-slate-50 ${
                    activeJobId === item.id ? 'bg-brand-50/60' : ''
                  }`}
                >
                  <span className="font-mono text-xs text-slate-500">{item.id.slice(0, 8)}</span>
                  <Badge className={JOB_STATUS_CLASS[item.status]}>
                    {JOB_STATUS_TEXT[item.status] ?? item.status}
                  </Badge>
                  <span className="text-xs text-slate-500">
                    {item.total} 个文件 · 成功 {item.succeeded} · 失败 {item.failed} · 待复核{' '}
                    {item.pending_review}
                  </span>
                  <span className="ml-auto text-xs text-slate-400">
                    {dateTimeText(item.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="mt-4">
        <Alert level="info">
          识别规则：电子发票 PDF 直接读文本层，字段最准、零成本；<b>图片票与扫描件</b>交给本机
          GPU 上的 PaddleOCR，票据不出本机。云 OCR（百度）是可选方案，配了密钥才走。
          {config ? (
            <>
              {' '}
              当前配置：<b>{String(config.ocr_provider)}</b>
            </>
          ) : null}
        </Alert>
      </div>

      {jobs === undefined && !activeJobId ? (
        <div className="mt-4 flex items-center gap-2 text-xs text-slate-400">
          <AlertCircle size={13} /> 新上传的票据会在后台排队识别，可离开本页再回来查看
        </div>
      ) : null}
    </div>
  )
}
