/** 极简数据获取 hook：避免引入 react-query 这类额外依赖。 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | undefined
  loading: boolean
  error: string
  reload: () => void
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let alive = true
    setLoading(true)
    fnRef.current()
      .then((result) => {
        if (alive) {
          setData(result)
          setError('')
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((n) => n + 1), [])
  return { data, loading, error, reload }
}

/** 批次进度轮询：status 进入终态就自动停下 */
export function usePoll(fn: () => Promise<boolean>, active: boolean, intervalMs = 800) {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (!active) return
    let stopped = false
    let timer: number | undefined

    const tick = async () => {
      if (stopped) return
      try {
        const keepGoing = await fnRef.current()
        if (!keepGoing) {
          stopped = true
          return
        }
      } catch {
        /* 轮询失败不打断界面 */
      }
      if (!stopped) timer = window.setTimeout(tick, intervalMs)
    }

    tick()
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
    }
  }, [active, intervalMs])
}
