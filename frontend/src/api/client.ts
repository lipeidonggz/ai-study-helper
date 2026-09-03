/** 后端 API 客户端：骨架版只实现 SSE 流式对话。 */

export type SessionMode = 'general' | 'kb_priority' | 'tool_enhanced' | 'rag'

export interface ChatEvent {
  event: 'start' | 'delta' | 'done' | 'error' | 'trace'
  text?: string
  message?: string
  trace?: TraceStep
  session_id?: string | null
  mode?: SessionMode
}

/** Agent 内部处理过程的一个步骤（来自后端 trace 事件）。 */
export interface TraceStep {
  seq: number
  type: string // context | round | llm_call | event | tool_exec | done
  data: Record<string, unknown>
  elapsed_ms: number
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
      onEvent({
        event: eventName as ChatEvent['event'],
        ...data,
        // trace 事件的 data 就是步骤对象本身，规整到 trace 字段供面板使用
        ...(eventName === 'trace' ? { trace: data as unknown as TraceStep } : {})
      })
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

// —— 评测台（0017）：用例管理 + 跑批管理 ——

export interface EvalMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export interface EvalCase {
  id: string
  category: string
  title: string
  mode: SessionMode
  input: { messages: EvalMessage[] }
  expected: {
    behavior: string
    criteria: string[]
    tool_calls?: { name: string; arguments: Record<string, unknown> }[]
    answer_contains?: string[]
    max_rounds?: number
  }
  timeout_sec: number
  hard_timeout_sec: number
  tags: string[]
  compare: boolean
  weight: number
  must_pass: boolean
  must_pass_threshold: number
  notes: string
  enabled: boolean
  admin_note: string
  updated_at: string
  updated_by: string
  annotation: EvalAnnotation
}

export interface EvalAnnotation {
  golden_answer: string
  reference_answer: string
  note: string
  annotated_at: string
  annotated_by: string
}

export type RunStatus = 'queued' | 'running' | 'done' | 'canceled' | 'error'

export interface EvalRun {
  id: number
  name: string
  status: RunStatus
  progress: number
  total: number
  error?: string
  created_at: string
  started_at?: string
  finished_at?: string
  summary?: Record<string, unknown>
  verified: string
  verified_by: string
  config?: Record<string, unknown>
}

export interface EvalRunCase {
  run_id: number
  case_id: string
  category: string
  title: string
  input: string
  status: string
  elapsed_ms: number
  rounds: number
  tool_calls: string[]
  output: string
  error: string
  judgments: Record<string, string>
  pending_human: string[]
  pending_attempts?: number
  judge_reasons: Record<string, string>
  verdict: string
  repeat_count: number
  pass_count: number
  repeat_results: EvalRunAttempt[]
  answer_correct: string
  refusal: string
  annotate_note: string
  golden_answer: string
  behavior: string
  trace?: ExecTraceEvent[]
}

export interface EvalRunAttempt {
  status: string
  elapsed_ms: number
  rounds: number
  tool_calls: string[]
  output: string
  error: string
  judgments: Record<string, string>
  pending_human: string[]
  judge_reasons: Record<string, string>
  verdict: string
  trace?: ExecTraceEvent[]
}

/** 执行轨迹事件（后端 exec_trace：round / text / tool_exec / guardrail / done）。 */
export interface ExecTraceEvent {
  type: string
  [key: string]: unknown
}

async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    let detail = ''
    try {
      const json = await resp.json()
      detail = fmtDetail(json.detail)
    } catch {
      // 非 JSON 错误体，忽略
    }
    throw new Error(`${resp.status}${detail ? ' ' + detail : ''}`)
  }
  return resp.json() as Promise<T>
}

/** 把 FastAPI 错误体的 detail（可能是数组）格式化成可读文本，避免 [object Object]。 */
function fmtDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        d && typeof d === 'object' && 'msg' in d
          ? String((d as { msg: unknown }).msg)
          : JSON.stringify(d)
      )
      .join('；')
  }
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export const evalApi = {
  listCases(params: Record<string, string> = {}): Promise<EvalCase[]> {
    const qs = new URLSearchParams(params).toString()
    return http(`/api/eval/cases${qs ? '?' + qs : ''}`)
  },
  createCase(body: EvalCase): Promise<EvalCase> {
    return http('/api/eval/cases', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  updateCase(id: string, body: EvalCase): Promise<EvalCase> {
    return http(`/api/eval/cases/${id}`, {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  deleteCase(id: string): Promise<{ ok: boolean }> {
    return http(`/api/eval/cases/${id}`, { method: 'DELETE' })
  },
  updateGoldenAnswer(caseId: string, body: { golden_answer: string }): Promise<EvalCase> {
    return http(`/api/eval/cases/${caseId}/golden-answer`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  listRuns(): Promise<EvalRun[]> {
    return http('/api/eval/runs')
  },
  deleteRun(id: number): Promise<{ ok: boolean }> {
    return http(`/api/eval/runs/${id}`, { method: 'DELETE' })
  },
  renameRun(runId: number, name: string): Promise<{ ok: boolean; id: number; name: string }> {
    return http(`/api/eval/runs/${runId}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify({ name })
    })
  },
  getRun(id: number, light = false): Promise<{ run: EvalRun; cases: EvalRunCase[]; active: boolean }> {
    return http(`/api/eval/runs/${id}${light ? '?light=1' : ''}`)
  },
  getRunCase(runId: number, caseId: string): Promise<EvalRunCase> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}`)
  },
  startRun(body: {
    name: string
    llm: 'real' | 'fake'
    model: string | null
    concurrency: number
    retries: number
    repeat: number
    prompt_variant: string
    temperature: number | null
    case_filter: { ids?: string[]; categories?: string[]; tags?: string[] }
  }): Promise<{ run_id: number }> {
    return http('/api/eval/runs', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  cancelRun(id: number): Promise<{ ok: boolean; status: string }> {
    return http(`/api/eval/runs/${id}/cancel`, { method: 'POST' })
  },
  verifyRun(id: number): Promise<{ ok: boolean; verified: boolean }> {
    return http(`/api/eval/runs/${id}/verify`, { method: 'POST' })
  },
  unverifyRun(id: number): Promise<{ ok: boolean; verified: boolean }> {
    return http(`/api/eval/runs/${id}/unverify`, { method: 'POST' })
  },
  rerunCase(runId: number, caseId: string): Promise<{ ok: boolean; case: EvalRunCase }> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}/rerun`, { method: 'POST' })
  },
  annotate(
    runId: number,
    caseId: string,
    body: { answer_correct: string; refusal: string; note: string }
  ): Promise<EvalRunCase> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  exportUrl(runId: number): string {
    return `/api/eval/runs/${runId}/export`
  }
}

// —— 知识库管理（0025 步骤 2）：素材/入库状态 + chunk 预览 + 检索调试 ——

export interface KbDocument {
  source_id: string
  name: string
  category: string
  carrier: string
  collected: boolean
  knowledge_date: string
  decay_class: string
  status: string
  chunk_count: number
  error: string
  indexed_at: string
}

export interface KbChunk {
  id: string
  section_path: string
  tokens: number
  text: string
}

export interface KbSearchHit {
  score: number
  source_id: string
  section_path: string
  decay_class: string
  text: string
}

export const kbApi = {
  listDocuments(): Promise<KbDocument[]> {
    return http('/api/kb/documents')
  },
  indexDocument(id: string): Promise<{ source_id: string; status: string; chunk_count: number }> {
    return http(`/api/kb/documents/${id}/index`, { method: 'POST' })
  },
  indexAll(): Promise<{ accepted: number }> {
    return http('/api/kb/index-all', { method: 'POST' })
  },
  deleteDocument(id: string): Promise<{ ok: boolean }> {
    return http(`/api/kb/documents/${id}`, { method: 'DELETE' })
  },
  listChunks(id: string): Promise<KbChunk[]> {
    return http(`/api/kb/documents/${id}/chunks`)
  },
  search(body: { query: string; top_k?: number; filters?: Record<string, unknown> }): Promise<KbSearchHit[]> {
    return http('/api/kb/search', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  }
}
