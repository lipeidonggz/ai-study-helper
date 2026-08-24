/** 轻量 hash 路由：不引入 vue-router，满足三个页面（聊天 / 评测台 / 跑批详情）。 */

export type Route =
  | { name: 'chat' }
  | { name: 'eval' }
  | { name: 'run'; runId: number }

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#/, '').split('/').filter(Boolean)
  if (parts[0] === 'eval') {
    if (parts[1] === 'runs' && parts[2]) {
      const id = Number(parts[2])
      if (Number.isFinite(id)) return { name: 'run', runId: id }
    }
    return { name: 'eval' }
  }
  return { name: 'chat' }
}

export function navigate(hash: string): void {
  window.location.hash = hash
}
