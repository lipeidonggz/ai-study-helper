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
  /** checklist 形态的点表：{"core": [{id,text,probe?,group?}], "ext": [...],
   *  "transparency"?: {...}} */
  checklist_points?: Record<string, unknown>
  /** checklist 规则形状：{"ext_min_per_group": int, "transparency":
   *  "conditional"|"none"} */
  checklist_rule?: Record<string, unknown>
  /** 事实边界声明（有地基才判的自动检查项）：
   *  [{id, claim, violation_example?}] */
  boundary_claims?: { id: string; claim: string; violation_example?: string }[]
  /** 表达期望（format 判官输入；空串 = 只用内置通用形态口径） */
  format_expected?: string
  note: string
  judge_shape: string
  annotated_at: string
  annotated_by: string
}

export interface ChecklistPoint {
  id: string
  text: string
  probe?: string
  /** 扩展主题所属分组（源族，如 O2 / A5），core 点无此字段 */
  group?: string
}

export interface ChecklistPoints {
  core?: ChecklistPoint[]
  ext?: ChecklistPoint[]
  transparency?: unknown
}

export interface ChecklistRule {
  ext_min_per_group?: number
  transparency?: string
  [key: string]: unknown
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
  diagnostics?: string
  verdict: string
  repeat_count: number
  pass_count: number
  repeat_results: EvalRunAttempt[]
  answer_correct: string
  refusal: string
  annotate_note: string
  golden_answer: string
  behavior: string
  judge_shape?: string
  checklist_points?: ChecklistPoints
  checklist_rule?: ChecklistRule
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
  diagnostics?: Record<string, unknown>
  citation_pairs?: { groups?: CitationGroup[]; pairs?: CitationPair[] }
  coverage_points?: CoveragePoints
  coverage_rejudge?: CoverageRejudgeRecord
  citation_rejudge?: CitationRejudgeRecord
  citation_stability?: CitationStabilityRecord
  verdict: string
  trace?: ExecTraceEvent[]
}

export interface CoveragePointVerdict {
  v: string
  evidence?: string
  declared?: boolean
  named_items?: string[]
  matched_uncovered?: string[]
  gate_note?: string
}

export interface CoverageBoundary {
  v: string
  violations?: { claim_id?: string; quote?: string }[]
  reason?: string
}

export interface CoveragePoints {
  core: Record<string, CoveragePointVerdict>
  ext: Record<string, CoveragePointVerdict>
  transparency?: CoveragePointVerdict
  boundary?: CoverageBoundary | null
}

export interface CitationPair {
  idx: number
  claim: string
  ref: string
  block_no?: number
  source_id: string
  source_label?: string
  section_path?: string
  content_match: string
  attribution_ok: string
  reason: string
}

/** 声明组核对（2026-09-05 起：判定单元从对子升级为声明组）。 */
export interface CitationBlockRef {
  block_no?: number
  ref?: string
  source_id: string
  source_label?: string
  section_path?: string
}

export interface CitationViolation {
  block_no?: number | null
  component?: string
  issue?: string
}

export interface CitationGroup {
  gid: number
  claim: string
  refs?: string[]
  kind?: 'block' | 'source' | 'mixed' | string
  blocks?: CitationBlockRef[]
  content_supported: string
  attribution_ok: string
  supporting_blocks?: number[]
  violations?: CitationViolation[]
  reason: string
}

export interface CoverageRejudgeRecord {
  created_at: string
  repeats: number
  point_ids: string[]
  points_meta?: Record<string, { text: string; probe?: string }>
  reps: Array<{
    ok: boolean
    points: Record<string, { v: string; evidence?: string }>
    error?: string
  }>
  summary: Record<string, Record<string, number>>
  errors: number
}

export interface CitationRejudgeRecord {
  created_at: string
  verdict: string | null
  reason: string
  detail: { groups?: CitationGroup[] }
}

export interface CitationStabilityRecord {
  created_at: string
  repeats: number
  verdicts: Array<string | null>
  matrix: Array<{
    gid: number
    claim: string
    refs: string[]
    cells: string[]
  }>
  summary: Record<string, Record<string, number>>
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
  coverageRejudge(
    runId: number,
    caseId: string,
    body: { attempt: number; repeats: number }
  ): Promise<{ ok: boolean; key: string; record: CoverageRejudgeRecord }> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}/coverage-rejudge`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  citationRejudge(
    runId: number,
    caseId: string,
    body: { attempt: number }
  ): Promise<{ ok: boolean; key: string; record: CitationRejudgeRecord }> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}/citation-rejudge`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
  },
  citationStability(
    runId: number,
    caseId: string,
    body: { attempt: number; repeats: number }
  ): Promise<{ ok: boolean; key: string; record: CitationStabilityRecord }> {
    return http(`/api/eval/runs/${runId}/cases/${caseId}/citation-stability`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body)
    })
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

// ---------------- 编译层（抽取：Pass 1 → 体检 → 谓词归一） ----------------

export interface CompileStatus {
  source_id: string
  status: 'idle' | 'running' | 'done' | 'failed'
  stage: string
  progress: string
  started_at: string
  finished_at: string
  error: string
  summary: Record<string, any>
}

export interface HealthDrop {
  idx: number
  subject: string
  predicate: string
  predicate_normalized?: string | null
  sign?: string | null
  polarity?: string | null
  object?: string | null
  anchor?: string
  reasons: string[]
  marks?: string[]
}

export interface CheckSpec {
  key: string
  name: string
  value: number
  threshold: number
  pass: boolean
  red_line: boolean
  action: string
  note: string
}

export interface AnaphoraRow {
  idx: number
  position: 'subject' | 'object'
  anaphor: string
  kind: 'bare' | 'phrase' | 'embedded'
  field_text: string
  subject: string
  predicate: string
  object: string
  status: 'resolved' | 'unresolved' | 'not_anaphora'
  resolution: string | null
  reason: 'summary' | 'recheck' | 'evidence' | null
  evidence: string
  evidence_verbatim: boolean
  window: string
}

export interface GraphEntity {
  name: string
  type: string
  aliases: string[]
}

export interface GraphEdge {
  from: string
  predicate: string
  to: string | null
  claim_idx: number
  passive_flipped?: boolean
  chunk_id?: string | null
  roles?: Record<string, string[]>
}

export interface CompileGraph {
  entities: GraphEntity[]
  edges: GraphEdge[]
  skipped: { claim_idx: number; reason: string }[]
  audit: { claim_idx: number; status: 'edge' | 'skipped'; reason: string }[]
  chunk_sections: Record<string, string>
  stats: {
    claims_in: number
    claims_with_edge: number
    claims_skipped: number
    entities: number
    edges: number
    normalize_targets: number
    normalize_batches: number
    normalize_failed_batches: number
  }
}

export interface HealthReport {
  source: string
  doc_subject: string
  claims_in: number
  claims_out: number
  checks: Record<string, Record<string, any>>
  check_spec: CheckSpec[]
  verdict: Record<string, { value: number; threshold: number; pass: boolean }>
  red_line_pass: boolean
  recall: Record<string, any>
  dropped: HealthDrop[]
  rewritten: Record<string, any>[]
  marked: Record<string, any>[]
  pending: Record<string, any>[]
  predicate_status: { mapped: number; forced: number; pending: number }
  predicate_clean: { targets: number; cleaned: number; rejected: number; noop: number }
  anaphora: { targets: number; resolved: number; unresolved: number; not_anaphora: number; demoted: number; evidence_verbatim: number; variant: string }
  anaphora_list: AnaphoraRow[]
}

export interface CompileClaim {
  subject: string
  predicate: string
  predicate_clean: string | null
  predicate_normalized: string | null
  predicate_status: 'mapped' | 'forced' | 'pending' | null
  predicate_invented: string | null
  sign: string | null
  polarity: string | null
  object: string | null
  marks: string[]
  anchor: string
}

export const compileApi = {
  list(): Promise<Record<string, { status: string; finished_at: string; claims_kept: number; red_line_pass: boolean; progress?: string; error?: string }>> {
    return http('/api/compile')
  },
  status(id: string): Promise<CompileStatus> {
    return http(`/api/compile/${id}`)
  },
  extract(id: string): Promise<CompileStatus> {
    return http(`/api/compile/${id}/extract`, { method: 'POST' })
  },
  report(id: string): Promise<HealthReport> {
    return http(`/api/compile/${id}/report`)
  },
  claims(id: string, limit = 0): Promise<CompileClaim[]> {
    return http(`/api/compile/${id}/claims?limit=${limit}`)
  },
  graph(id: string): Promise<CompileGraph> {
    return http(`/api/compile/${id}/graph`)
  }
}
