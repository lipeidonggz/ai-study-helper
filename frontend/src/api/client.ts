/** 后端 API 客户端：骨架版只实现 SSE 流式对话。 */

export type SessionMode = 'general' | 'kb_priority' | 'tool_enhanced'

export interface ChatEvent {
  event: 'start' | 'delta' | 'done'
  text?: string
  session_id?: string | null
  mode?: SessionMode
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
      for (const line of raw.split('\n')) {
        if (line.startsWith('data: ')) {
          onEvent(JSON.parse(line.slice(6)))
        }
      }
      sep = buffer.indexOf('\n\n')
    }
  }
}
