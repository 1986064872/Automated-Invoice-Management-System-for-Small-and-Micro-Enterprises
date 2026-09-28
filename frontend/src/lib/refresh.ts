import { useEffect, useState } from 'react'

/**
 * 极简的「数据变了」广播。
 *
 * 解决什么问题：侧边栏的待复核角标、首页概览这类**全局数据**，
 * 如果只在应用启动时拉一次，用户确认/删除票据之后角标就停在旧值
 * （实测：已经 0 张待复核了，角标还挂着一个「1」）。
 *
 * 用法：
 *   写操作成功后广播 → notifyDataChanged()（已在 api.ts 里统一调用，不用手写）
 *   订阅方 → const v = useDataVersion(); useAsync(fn, [v])
 */
type Listener = () => void

const listeners = new Set<Listener>()

/** 广播「数据变了」，让所有订阅者重新拉取。 */
export function notifyDataChanged(): void {
  // 复制一份再遍历：监听器回调里可能会触发增删
  for (const listener of [...listeners]) {
    listener()
  }
}

/** 订阅数据版本号。把它放进 useAsync 的依赖数组即可自动刷新。 */
export function useDataVersion(): number {
  const [version, setVersion] = useState(0)

  useEffect(() => {
    const listener = () => setVersion((v) => v + 1)
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }, [])

  return version
}
