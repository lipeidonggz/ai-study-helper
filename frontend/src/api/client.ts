/** 后端 API 客户端：骨架版只实现 SSE 流式对话。 */

export type SessionMode = 'general' | 'kb_priority' | 'tool_enhanced'

export interface ChatEvent {
  event: 'start' | 'delta' | 'done' | 'error'
  text?: string
  message?: string
  session_id?: string | null
  mode?: SessionMode
}

export interface LLMSettingsView {
  provider: string
  model: string
  api_key_masked: string
  has_key: boolean
  models: string[]
}

/** POST /api/chat 并解析 SSE 流，逐事件回调。 */
export async function streamChat(
  message: string,
  mode: SessionMode,
  onEvent: (event: ChatEvent) => void
): Promise<void> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, mode })
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`chat failed: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep = buffer.indexOf('\n\n')
    while (sep >= 0) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let eventName = 'message'
      let data: Record<string, unknown> = {}
      for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) {
          eventName = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            data = JSON.parse(line.slice(6))
          } catch {
            // 非 JSON 数据行（如 [DONE]），忽略
          }
        }
      }
      onEvent({ event: eventName as ChatEvent['event'], ...data })
      sep = buffer.indexOf('\n\n')
    }
  }
}

export async function getLLMSettings(): Promise<LLMSettingsView> {
  const resp = await fetch('/api/settings/llm')
  if (!resp.ok) throw new Error(`get settings failed: ${resp.status}`)
  return resp.json()
}

export async function saveLLMSettings(body: {
  provider?: string
  model: string
  apiKey: string
}): Promise<LLMSettingsView> {
  const resp = await fetch('/api/settings/llm', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider: body.provider ?? 'deepseek',
      model: body.model,
      api_key: body.apiKey
    })
  })
  if (!resp.ok) {
    let detail = ''
    try {
      const json = await resp.json()
      detail = json.detail?.[0]?.msg ?? ''
    } catch {
      // 非 JSON 错误体，忽略
    }
    throw new Error(`${resp.status}${detail ? ' ' + detail : ''}`)
  }
  return resp.json()
}
