<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

import {
  evalApi,
  type EvalAnnotation,
  type EvalCase,
  type EvalRun,
  type EvalRunCase
} from '../api/client'
import { navigate } from '../router'
import { fmtTokens } from '../utils/format'

// 从跑批详情返回（#/eval/runs）时自动选中"测试管理"，否则默认"用例管理"
const tab = ref<'cases' | 'runs'>(window.location.hash.startsWith('#/eval/runs') ? 'runs' : 'cases')

// —— 用例管理 ——
const CATEGORIES = ['tool_call', 'boundary', 'combined', 'multi_turn', 'kb_qa', 'rag']
const PROMPT_VARIANT_OPTIONS = [
  { value: 'baseline', label: '基线（当前默认）' },
  { value: 'no_behavior', label: '无行为契约（消融）' },
  { value: 'cot', label: 'CoT 分步思考' },
  { value: 'minimal', label: '极简（无契约无防御）' }
]

function variantLabel(v: unknown): string {
  const key = String(v ?? '')
  return PROMPT_VARIANT_OPTIONS.find((o) => o.value === key)?.label ?? key
}

const MODEL_OPTIONS = [
  { value: null, label: '跟随全局设置' },
  { value: 'deepseek-chat', label: 'deepseek-chat（非推理）' },
  { value: 'deepseek-reasoner', label: 'deepseek-reasoner（推理）' }
]

function modelLabel(r: EvalRun): string {
  const m = r.config?.model
  return typeof m === 'string' && m ? m : '—（未记录）'
}

function repeatLabel(r: EvalRun): string {
  const v = r.config?.repeat
  return typeof v === 'number' ? String(v) : '—'
}

// 矩阵/列表中区分 run 配置：变体 + 采样温度（温度扫描时一眼可辨）
function runConfigLabel(r: EvalRun): string {
  const v = variantLabel(r.config?.variant ?? 'baseline')
  const t = r.config?.temperature
  const temp = typeof t === 'number' ? `T${t}` : ''
  return temp ? `${v} · ${temp}` : v
}

const TEMPERATURE_OPTIONS = [
  { value: null, label: '默认（不传，服务端 1.0）' },
  { value: 0, label: '0.0（代码/数学建议）' },
  { value: 0.3, label: '0.3' },
  { value: 0.7, label: '0.7' },
  { value: 1, label: '1.0（数据清洗/翻译建议）' },
  { value: 1.3, label: '1.3（通用对话建议）' },
  { value: 1.5, label: '1.5（创意写作建议）' },
  { value: 2, label: '2.0（最高随机性）' }
]
const CRITERIA_OPTIONS = [
  'answer_correct',
  'tool_used',
  'tool_not_used',
  'refusal',
  'stream_complete',
  'latency_budget',
  'no_prompt_leak',
  'citation_correct',
  'citation_truth',
  'format_appropriate',
  'refusal_calibration',
  'context_consistent'
]
const MODES = ['general', 'kb_priority', 'tool_enhanced', 'rag']

// —— 字段说明元数据（编辑界面提示用） ——
const CATEGORY_META: Record<string, string> = {
  tool_call: '验证工具调用行为（调不调、调得对不对）',
  boundary: '边界/安全：拒答、提示注入、幻觉、隐私等',
  combined: '多步组合：工具调用 + 推理/保存/验证串联',
  multi_turn: '多轮一致性（阶段 3 启用）',
  kb_qa: '知识库问答（阶段 2 启用）',
  rag: 'RAG 增强问答（阶段 2 RAG v1.0）'
}
const MODE_META: Record<string, string> = {
  general: '通用助手，中文回答',
  kb_priority: '优先个人知识库并给出引用（阶段 2）',
  tool_enhanced: '适合时调用工具获取准确结果',
  rag: 'RAG 增强：检索知识库 + 引用真实（阶段 2 RAG v1.0）'
}
const CRITERIA_META: Record<string, string> = {
  answer_correct: '需人工/LLM',
  tool_used: '机器判定',
  tool_not_used: '机器判定',
  refusal: '需人工/LLM',
  stream_complete: '机器判定',
  latency_budget: '机器判定',
  no_prompt_leak: '机器判定',
  citation_correct: '阶段 2',
  citation_truth: 'LLM 断言级（RAG 红线）',
  format_appropriate: 'LLM（用户价值层）',
  refusal_calibration: 'LLM（用户价值层）',
  context_consistent: '阶段 3'
}

function critTagClass(cr: string): string {
  const m = CRITERIA_META[cr] ?? ''
  if (m === '机器判定') return 'auto'
  if (m === '需人工/LLM') return 'human'
  return 'later'
}

const cases = ref<EvalCase[]>([])
const caseFilter = ref({ q: '', category: '', enabled: '' })
const editing = ref<EvalCase | null>(null)
const isNew = ref(false)
const caseError = ref('')
const caseMsg = ref('')
const goldenCopyMsg = ref('')
const criteriaSel = ref<string[]>([])
const toolCallsText = ref('')
const tagsText = ref('')
const messagesText = ref('')
const boundaryText = ref('')

// —— 用例 tag 体系（中文键=值，词表见 memory/0027） ——
interface TagAxis {
  key: string
  label: string
  color: string
  group: 'product' | 'system' | 'mgmt'
  values: string[]
}
const TAG_AXES: TagAxis[] = [
  { key: '范围', label: '范围', color: 'blue', group: 'product', values: ['全景', '切面', '单点'] },
  {
    key: '素材',
    label: '素材',
    color: 'gray',
    group: 'system',
    values: ['A5', 'O2', 'O2xA5', 'A1xO1', 'OWN', 'T1', 'T2', 'T3', 'A1', 'A3', 'A4', 'A6', 'O1', 'O3', 'OW1']
  },
  { key: '精度', label: '精度', color: 'teal', group: 'product', values: ['精确', '半结构化', '口语模糊'] },
  {
    key: '环节',
    label: '环节',
    color: 'green',
    group: 'product',
    values: ['认知扩展', '解惑', '辨析', '结构化梳理', '溯源求证', '批判认知', '验证理解', '学习规划', '重点提炼复习', '产出辅助']
  },
  {
    key: '题型',
    label: '题型',
    color: 'purple',
    group: 'product',
    values: ['概念定义', '细节', '为什么误区', '观点判断', '区别对比', '归纳框架', '出处归属', '多方对照', '自测出题', '路径推荐', '要点总结']
  },
  { key: '套件', label: '套件', color: 'orange', group: 'mgmt', values: ['基础', '基准', '探针'] },
  { key: '验证', label: '验证', color: 'red', group: 'system', values: ['检索', '引用', '生成', '判官'] },
  { key: '家族', label: '家族', color: 'brown', group: 'mgmt', values: ['cmp全景', 'agent构建全景', 'cmp局限切面', 'cmp角色切面'] }
]
const TAG_GROUPS: { id: TagAxis['group']; label: string; hint: string }[] = [
  { id: 'product', label: '产品视角', hint: '这条用例在测什么用户体验' },
  { id: 'system', label: '系统视角', hint: '依赖什么素材、验证哪个根因层' },
  { id: 'mgmt', label: '管理视角', hint: '怎么组织与使用这套用例' }
]
const tagFilter = reactive<Record<string, string>>({})
const tagSel = reactive<Record<string, string>>({})
const activeTagFilters = computed(() =>
  TAG_AXES.filter((a) => tagFilter[a.key]).map((a) => ({
    key: a.key,
    value: tagFilter[a.key] as string
  }))
)
const displayedCases = computed(() => {
  const active = TAG_AXES.filter((a) => tagFilter[a.key])
  if (!active.length) return cases.value
  return cases.value.filter((c) =>
    active.every((a) => (c.tags ?? []).includes(`${a.key}=${tagFilter[a.key]}`))
  )
})

function splitTag(t: string): [string, string] {
  const i = t.indexOf('=')
  return i > 0 ? [t.slice(0, i), t.slice(i + 1)] : [t, '']
}

function tagAxis(key: string): TagAxis | undefined {
  return TAG_AXES.find((a) => a.key === key)
}

function tagOptions(key: string): string[] {
  const base = tagAxis(key)?.values ?? []
  const used = new Set<string>()
  for (const c of cases.value) {
    for (const t of c.tags ?? []) {
      const [k, v] = splitTag(t)
      if (k === key && v) used.add(v)
    }
  }
  return [...new Set([...base, ...used])]
}

function applyTagFilter(key: string, val: string) {
  if (val) tagFilter[key] = val
  else delete tagFilter[key]
}

function clearTagFilter() {
  for (const k of Object.keys(tagFilter)) delete tagFilter[k]
}

function removeOneTagFilter(key: string) {
  delete tagFilter[key]
}

function onTagChip(t: string) {
  const [k, v] = splitTag(t)
  if (k && v) applyTagFilter(k, v)
}

function syncTagSel(tags: string[]) {
  for (const a of TAG_AXES) delete tagSel[a.key]
  const rest: string[] = []
  for (const t of tags ?? []) {
    const [k, v] = splitTag(t)
    if (k && tagAxis(k) && v) {
      tagSel[k] = v
    } else if (t.trim()) {
      rest.push(t.trim())
    }
  }
  tagsText.value = rest.join(', ')
}

function composeTags(): string[] {
  const out: string[] = []
  for (const a of TAG_AXES) {
    const v = tagSel[a.key]
    if (v) out.push(`${a.key}=${v}`)
  }
  out.push(...tagsText.value.split(',').map((s) => s.trim()).filter(Boolean))
  return out
}

// —— 清单结构化表单态（2026-09-07：点表/规则改表格与控件编辑，JSON 降级为高级模式）——
interface CovRow {
  id: string
  text: string
  probe: string
  group: string
}
const covCore = ref<CovRow[]>([])
const covExt = ref<CovRow[]>([])
const extMin = ref<number>(2)
const transMode = ref<'conditional' | 'none'>('conditional')
const covJsonMode = ref(false)
const covJsonText = ref('')

const hasCovPoints = computed(
  () => covCore.value.length > 0 || covExt.value.length > 0
)
const covGroupOptions = computed(() => {
  const s = new Set(covExt.value.map((r) => r.group.trim()).filter(Boolean))
  return [...s]
})

function _rowFrom(p: Record<string, unknown>, group: string): CovRow {
  return {
    id: String(p.id ?? ''),
    text: String(p.text ?? ''),
    probe: String(p.probe ?? ''),
    group
  }
}

const criteriaConflict = computed(
  () => criteriaSel.value.includes('tool_used') && criteriaSel.value.includes('tool_not_used')
)
const showChecklistStruct = computed(
  () =>
    !!editing.value &&
    (editing.value.annotation.judge_shape === 'checklist' ||
      Object.keys((editing.value.annotation.checklist_points as Record<string, unknown>) || {}).length > 0 ||
      !!Object.keys((editing.value.annotation.checklist_rule as Record<string, unknown>) || {}).length)
)

function initStructTexts(ann: EvalAnnotation) {
  const pts = (ann.checklist_points ?? {}) as {
    core?: Record<string, unknown>[]
    ext?: Record<string, unknown>[]
  }
  covCore.value = (pts.core ?? []).map((p) => _rowFrom(p, ''))
  covExt.value = (pts.ext ?? []).map((p) => _rowFrom(p, String(p.group ?? '')))
  const rule = (ann.checklist_rule ?? {}) as Record<string, unknown>
  extMin.value = Number(rule.ext_min_per_group ?? 2) || 0
  transMode.value =
    String(rule.transparency ?? 'conditional') === 'none' ? 'none' : 'conditional'
  covJsonMode.value = false
  covJsonText.value = ''
  boundaryText.value = ann.boundary_claims?.length
    ? JSON.stringify(ann.boundary_claims, null, 2)
    : ''
}

function addCovRow(group: 'core' | 'ext') {
  if (group === 'core') {
    covCore.value.push({ id: '', text: '', probe: '', group: '' })
  } else {
    const prev = covExt.value.length ? covExt.value[covExt.value.length - 1].group : ''
    covExt.value.push({ id: '', text: '', probe: '', group: prev })
  }
}

function removeCovRow(group: 'core' | 'ext', idx: number) {
  const arr = group === 'core' ? covCore.value : covExt.value
  arr.splice(idx, 1)
}

function moveCovRow(group: 'core' | 'ext', idx: number, delta: number) {
  const arr = group === 'core' ? covCore.value : covExt.value
  const to = idx + delta
  if (to < 0 || to >= arr.length) return
  const [it] = arr.splice(idx, 1)
  arr.splice(to, 0, it)
}

function toggleCovJson(checked: boolean) {
  if (checked) {
    covJsonText.value = JSON.stringify(
      {
        core: covCore.value.map(({ id, text, probe }) => ({ id, text, probe })),
        ext: covExt.value.map(({ id, text, probe, group }) => ({
          id,
          text,
          probe,
          group
        }))
      },
      null,
      2
    )
    covJsonMode.value = true
    return
  }
  try {
    const obj = JSON.parse(covJsonText.value || '{}') as {
      core?: Record<string, unknown>[]
      ext?: Record<string, unknown>[]
    }
    if (!obj || !Array.isArray(obj.core) || !Array.isArray(obj.ext)) {
      throw new Error('checklist_points 需为含 core / ext 数组的对象')
    }
    covCore.value = obj.core.map((p) => _rowFrom(p, ''))
    covExt.value = (obj.ext as Record<string, unknown>[]).map((p) =>
      _rowFrom(p, String(p.group ?? ''))
    )
    covJsonMode.value = false
    caseError.value = ''
  } catch (err) {
    caseError.value = `JSON 解析失败，已停留在高级模式：${err}`
  }
}

function validateCovRows(): string {
  const all: string[] = []
  const ids = new Set<string>()
  const check = (r: CovRow, label: string) => {
    if (!r.id.trim()) return `${label}存在未填 id`
    if (ids.has(r.id.trim())) return `id 重复：${r.id.trim()}`
    ids.add(r.id.trim())
    if (!r.text.trim()) return `${r.id.trim()} 缺少要点 text`
    return ''
  }
  for (const r of covCore.value) {
    const e = check(r, 'core')
    if (e) return e
  }
  for (const r of covExt.value) {
    const e = check(r, 'ext')
    if (e) return e
    if (!r.group.trim()) return `${r.id.trim()} 缺少所属分组 group`
  }
  return all.join('；')
}

const covPreviewText = computed(() => {
  const lines = [
    'core：',
    ...covCore.value.map(
      (r) => `- ${r.id.trim()}：${r.text.trim()}` + (r.probe.trim() ? `（覆盖口径：${r.probe.trim()}）` : '')
    ),
    'ext：',
    ...covExt.value.map(
      (r) => `- ${r.id.trim()}：${r.text.trim()}` + (r.probe.trim() ? `（覆盖口径：${r.probe.trim()}）` : '')
    )
  ]
  return lines.join('\n')
})

function blankCase(): EvalCase {
  return {
    id: '',
    category: 'tool_call',
    title: '',
    mode: 'general',
    input: { messages: [{ role: 'user', content: '' }] },
    expected: {
      behavior: '',
      criteria: [],
      tool_calls: [],
      answer_contains: [],
      max_rounds: 4
    },
    timeout_sec: 30,
    hard_timeout_sec: 90,
    tags: [],
    compare: false,
    weight: 1,
    must_pass: false,
    must_pass_threshold: 1,
    notes: '',
    enabled: true,
    admin_note: '',
    updated_at: '',
    updated_by: '',
    annotation: {
      golden_answer: '',
      reference_answer: '',
      checklist_points: {},
      checklist_rule: {},
      boundary_claims: [],
      format_expected: '',
      note: '',
      judge_shape: '',
      annotated_at: '',
      annotated_by: ''
    }
  }
}

async function loadCases() {
  const params: Record<string, string> = {}
  if (caseFilter.value.q) params.q = caseFilter.value.q
  if (caseFilter.value.category) params.category = caseFilter.value.category
  if (caseFilter.value.enabled) params.enabled = caseFilter.value.enabled
  cases.value = await evalApi.listCases(params)
}

function newCase() {
  const c = blankCase()
  editing.value = c
  isNew.value = true
  caseError.value = ''
  caseMsg.value = ''
  messagesText.value = 'user: '
  criteriaSel.value = []
  toolCallsText.value = ''
  syncTagSel([])
  initStructTexts(c.annotation)
  void nextTick(() => autoGrowTextarea(goldenArea.value))
}

function editCase(c: EvalCase) {
  editing.value = JSON.parse(JSON.stringify(c)) as EvalCase
  isNew.value = false
  caseError.value = ''
  caseMsg.value = ''
  messagesText.value = c.input.messages
    .map((m) => `${m.role}: ${m.content}`)
    .join('\n')
  criteriaSel.value = [...c.expected.criteria]
  toolCallsText.value = c.expected.tool_calls?.length
    ? JSON.stringify(c.expected.tool_calls, null, 2)
    : ''
  syncTagSel(c.tags)
  initStructTexts(c.annotation)
  void nextTick(() => autoGrowTextarea(goldenArea.value))
}

function closeEditor() {
  editing.value = null
  goldenCopyMsg.value = ''
}

const goldenArea = ref<HTMLTextAreaElement | null>(null)

// 金标准文本域按内容自动增高：先重置再取内容高度，超长时受 CSS max-height 约束出滚动条
function autoGrowTextarea(target: EventTarget | null) {
  const el = target as HTMLTextAreaElement | null
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function copyBehaviorToGolden() {
  if (!editing.value) return
  const cur = editing.value.annotation.golden_answer
  const behavior = editing.value.expected.behavior
  if (cur.trim() && cur.trim() !== behavior.trim()) {
    if (!confirm('金标准答案要点已有内容，复制将覆盖当前内容。继续？')) return
  }
  editing.value.annotation.golden_answer = behavior
  goldenCopyMsg.value = '已复制，可在此基础上修改'
  window.setTimeout(() => {
    goldenCopyMsg.value = ''
  }, 3000)
}

function parseMessages(): { role: 'user' | 'assistant' | 'system'; content: string }[] {
  const out: { role: 'user' | 'assistant' | 'system'; content: string }[] = []
  for (const line of messagesText.value.split('\n')) {
    const idx = line.indexOf(': ')
    if (idx < 0) throw new Error(`消息行缺少 "role: " 前缀：${line}`)
    const role = line.slice(0, idx) as 'user' | 'assistant' | 'system'
    if (!['user', 'assistant', 'system'].includes(role)) {
      throw new Error(`未知消息角色：${role}`)
    }
    out.push({ role, content: line.slice(idx + 2) })
  }
  if (!out.length) throw new Error('至少需要一条用户消息')
  return out
}

async function saveCaseInner(): Promise<boolean> {
  if (!editing.value) return false
  caseError.value = ''
  if (criteriaConflict.value) {
    caseError.value = '验收维度互斥：tool_used 与 tool_not_used 不能同时选择'
    return false
  }
  try {
    // 清单结构化字段：表单/JSON → annotation（校验失败则拦截保存）
    const ann = editing.value.annotation
    const parseOpt = (txt: string) => (txt.trim() ? JSON.parse(txt) : undefined)
    const bnd = parseOpt(boundaryText.value)
    if (bnd !== undefined) ann.boundary_claims = bnd
    ann.checklist_rule = {
      ext_min_per_group: Math.max(0, Math.floor(Number(extMin.value) || 0)),
      transparency: transMode.value
    }
    let pts: {
      core: Record<string, unknown>[]
      ext: Record<string, unknown>[]
      [key: string]: unknown
    }
    if (covJsonMode.value) {
      const raw = parseOpt(covJsonText.value) as {
        core?: Record<string, unknown>[]
        ext?: Record<string, unknown>[]
      } | undefined
      if (!raw || !Array.isArray(raw.core) || !Array.isArray(raw.ext)) {
        throw new Error('checklist_points 需为含 core / ext 数组的对象')
      }
      pts = { core: raw.core, ext: raw.ext }
    } else {
      const err = validateCovRows()
      if (err) throw new Error(err)
      pts = {
        core: covCore.value.map((r) => ({
          id: r.id.trim(),
          text: r.text.trim(),
          ...(r.probe.trim() ? { probe: r.probe.trim() } : {})
        })),
        ext: covExt.value.map((r) => ({
          id: r.id.trim(),
          text: r.text.trim(),
          group: r.group.trim(),
          ...(r.probe.trim() ? { probe: r.probe.trim() } : {})
        }))
      }
    }
    const prevPts = ann.checklist_points as Record<string, unknown> | undefined
    if (prevPts && prevPts.transparency) {
      pts = { ...pts, transparency: prevPts.transparency }
    }
    ann.checklist_points = pts
    const body: EvalCase = JSON.parse(JSON.stringify(editing.value))
    body.input.messages = parseMessages()
    body.expected.criteria = criteriaSel.value
    if (toolCallsText.value.trim()) {
      body.expected.tool_calls = JSON.parse(toolCallsText.value)
    } else {
      body.expected.tool_calls = []
    }
    body.tags = composeTags()
    if (isNew.value) {
      await evalApi.createCase(body)
    } else {
      await evalApi.updateCase(body.id, body)
    }
    caseMsg.value = '已保存'
    await loadCases()
    return true
  } catch (err) {
    caseError.value = `保存失败：${err}`
    return false
  }
}

async function saveCase() {
  if (await saveCaseInner() && isNew.value) {
    // 新建成功后留在编辑态：后续保存走 update 而非再次 create
    isNew.value = false
  }
}

const caseIdx = computed(() => {
  if (!editing.value || isNew.value) return -1
  return cases.value.findIndex((c) => c.id === editing.value?.id)
})

async function stepCase(delta: number) {
  if (!editing.value || isNew.value) return
  const curId = editing.value.id
  if (!(await saveCaseInner())) return // 保存失败则停留在当前用例
  const idx = cases.value.findIndex((c) => c.id === curId)
  const next = cases.value[idx + delta]
  if (next) editCase(next)
}

async function removeCase(c: EvalCase) {
  if (!confirm(`确认删除用例 ${c.id}？此操作不可恢复。`)) return
  try {
    await evalApi.deleteCase(c.id)
    await loadCases()
  } catch (err) {
    caseError.value = `删除失败：${err}`
  }
}

// —— 测试管理 ——
const runs = ref<EvalRun[]>([])
const runError = ref('')
const showStart = ref(false)
const showMatrix = ref(false) // 对照矩阵视图
const selRunIds = ref<number[]>([])
const baseRunId = ref<number | null>(null)
const matrixAddSel = ref<number | null>(null)
const matrixIdInput = ref('')
const showJudgeGroup = ref(false)
const matrixView = ref<'run' | 'case'>('run') // 矩阵视图：run 级指标 / 用例级对照
const caseDiff = ref<
  {
    id: string
    title: string
    changed: boolean
    cells: Record<number, EvalRunCase>
  }[]
>([])
const caseDiffLoading = ref(false)
const caseDiffOnlyChanged = ref(false)
const caseDetail = ref<{ id: string; title: string; cells: Record<number, EvalRunCase> } | null>(null)
const startForm = ref({
  name: '',
  llm: 'real' as 'real' | 'fake',
  model: null as string | null,
  concurrency: 50,
  retries: 1,
  repeat: 20,
  prompt_variant: 'baseline',
  temperature: null as number | null,
  filterType: 'all' as 'all' | 'categories' | 'ids',
  categories: [] as string[],
  ids: ''
})
let pollTimer: number | undefined

async function loadRuns() {
  runs.value = await evalApi.listRuns()
  syncPolling()
}

function toggleRunSel(id: number) {
  const i = selRunIds.value.indexOf(id)
  if (i >= 0) selRunIds.value.splice(i, 1)
  else selRunIds.value.push(id)
}

function openMatrix() {
  showMatrix.value = true
  matrixView.value = 'run'
  // 用户已勾选过 run 时保留选择；为空才默认选中各变体的最近 run
  if (selRunIds.value.length) {
    if (baseRunId.value == null || !selRunIds.value.includes(baseRunId.value)) {
      const base = runs.value.find((r) => String(r.config?.variant ?? 'baseline') === 'baseline')
      baseRunId.value = base?.id ?? selRunIds.value[0] ?? null
    }
    return
  }
  // 默认选中 baseline 变体的最近 run + 其他变体的最近 run；基准 = 选中的 baseline
  const byVariant = new Map<string, number>()
  for (const r of runs.value) {
    const v = String(r.config?.variant ?? 'baseline')
    if (!byVariant.has(v)) byVariant.set(v, r.id)
  }
  selRunIds.value = [...byVariant.values()]
  const base = runs.value.find((r) => String(r.config?.variant ?? 'baseline') === 'baseline')
  baseRunId.value = base?.id ?? selRunIds.value[0] ?? null
}

function addMatrixRuns(ids: number[]) {
  for (const id of ids) {
    if (!selRunIds.value.includes(id) && runs.value.some((r) => r.id === id)) {
      selRunIds.value.push(id)
    }
  }
  if (matrixView.value === 'case') loadCaseDiff()
}

function onAddMatrixRun() {
  if (matrixAddSel.value == null) return
  addMatrixRuns([matrixAddSel.value])
  matrixAddSel.value = null
}

function onAddMatrixRunByIds() {
  const ids = matrixIdInput.value
    .split(/[\s,，、;；]+/)
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => Number.isFinite(n))
  if (!ids.length) return
  addMatrixRuns(ids)
  matrixIdInput.value = ''
}

function removeMatrixRun(id: number) {
  selRunIds.value = selRunIds.value.filter((x) => x !== id)
  if (baseRunId.value === id) {
    baseRunId.value = selRunIds.value[0] ?? null
  }
  if (matrixView.value === 'case') loadCaseDiff()
}

const selectedRuns = computed(() => {
  const map = new Map(runs.value.map((r) => [r.id, r]))
  return selRunIds.value.map((id) => map.get(id)).filter((r): r is NonNullable<typeof r> => !!r)
})

async function loadCaseDiff() {
  if (!selRunIds.value.length) return
  caseDiffLoading.value = true
  try {
    const data = await Promise.all(selRunIds.value.map((id) => evalApi.getRun(id)))
    const byCase = new Map<string, { id: string; title: string; cells: Record<number, EvalRunCase> }>()
    for (let i = 0; i < data.length; i++) {
      const runId = selRunIds.value[i]
      for (const c of data[i].cases) {
        let row = byCase.get(c.case_id)
        if (!row) {
          row = { id: c.case_id, title: c.title, cells: {} }
          byCase.set(c.case_id, row)
        }
        row.cells[runId] = c
      }
    }
    const rows = [...byCase.values()].map((row) => {
      const set = new Set(Object.values(row.cells).map((x) => x.verdict))
      return { ...row, changed: set.size > 1 }
    })
    rows.sort((a, b) => {
      if (a.changed !== b.changed) return a.changed ? -1 : 1
      return a.id.localeCompare(b.id)
    })
    caseDiff.value = rows
  } finally {
    caseDiffLoading.value = false
  }
}

function switchMatrixView(v: 'run' | 'case') {
  matrixView.value = v
  if (v === 'case') loadCaseDiff()
}

function openCaseDetail(row: { id: string; title: string; cells: Record<number, EvalRunCase> }) {
  caseDetail.value = row
}

function closeCaseDetail() {
  caseDetail.value = null
}

// 执行轨迹 → 可读文本（弹窗内展示用，与跑批详情页一致）
function traceText(trace: unknown[] | undefined): string {
  if (!trace?.length) return '（无执行轨迹）'
  return trace
    .map((t) => {
      const x = t as Record<string, unknown>
      if (x.type === 'round') return `── 第 ${x.round} 轮 ──`
      if (x.type === 'text') return `  文本: ${String(x.text ?? '').replace(/\s+/g, ' ').slice(0, 100)}`
      if (x.type === 'tool_exec') {
        const args = JSON.stringify(x.arguments ?? {})
        const result = String(x.result ?? '').replace(/\s+/g, ' ').slice(0, 100)
        const err = x.error ? ` [错误: ${x.error}]` : ''
        return `  工具调用: ${x.name}(${args}) → ${result}${err}`
      }
      if (x.type === 'guardrail') return `  ⛔ 护栏拦截: ${x.action}`
      if (x.type === 'done') return `  ✓ 结束: ${x.end_reason}`
      return `  ${x.type}: ${JSON.stringify(x)}`
    })
    .join('\n')
}

const visibleCaseRows = computed(() =>
  caseDiffOnlyChanged.value ? caseDiff.value.filter((r) => r.changed) : caseDiff.value
)

function verdictLabel(v: string): string {
  const map: Record<string, string> = {
    pass: '通过',
    fail: '未通过',
    pending: '待人工',
    unstable: '不稳定',
    exec_error: '执行失败'
  }
  return map[v] ?? '—'
}

function verdictClass(v: string): string {
  const map: Record<string, string> = {
    pass: 'ok',
    fail: 'error',
    pending: 'warn',
    unstable: 'warn',
    exec_error: 'neutral'
  }
  return map[v] ?? 'neutral'
}

// 矩阵行：选中的 run + summary + 相对基准的差值
const matrixRows = computed(() => {
  const map = new Map(runs.value.map((r) => [r.id, r]))
  const base = baseRunId.value != null ? map.get(baseRunId.value) : null
  const baseS = base?.summary ?? {}
  return selRunIds.value
    .map((id) => map.get(id))
    .filter((r): r is NonNullable<typeof r> => !!r)
    .map((r) => {
      const s = r.summary ?? {}
      const vs = base && base.id !== r.id ? baseS : null
      return { run: r, s, vs }
    })
})

function pct(v: unknown): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—'
}

function verdictCount(s: Record<string, unknown>, key: string): number {
  const v = s.verdicts
  if (v && typeof v === 'object') return Number((v as Record<string, unknown>)[key] ?? 0)
  return 0
}

function judgeRate(s: Record<string, unknown>, key: string): number | null {
  const j = s.judgments
  if (j && typeof j === 'object') {
    const item = (j as Record<string, unknown>)[key] as Record<string, unknown> | undefined
    if (item && typeof item.pass_rate === 'number') return item.pass_rate
  }
  return null
}

// 差值单元格：{text, cls}；up=绿 down=红；invert=true 表示"越小越好"
function diff(cur: unknown, base: unknown, invert = false): { text: string; cls: string } {
  if (typeof cur !== 'number' || typeof base !== 'number') return { text: '', cls: '' }
  const d = cur - base
  if (Math.abs(d) < 1e-9) return { text: '', cls: '' }
  const good = invert ? d < 0 : d > 0
  return {
    text: `${d > 0 ? '+' : ''}${d.toFixed(1)}`,
    cls: good ? 'up' : 'down'
  }
}

function diffPctPts(cur: unknown, base: unknown): { text: string; cls: string } {
  if (typeof cur !== 'number' || typeof base !== 'number') return { text: '', cls: '' }
  return diff(cur * 100, base * 100)
}

function diffPctRel(cur: unknown, base: unknown, invert = false): { text: string; cls: string } {
  if (typeof cur !== 'number' || typeof base !== 'number' || base === 0) return { text: '', cls: '' }
  const d = ((cur - base) / base) * 100
  if (Math.abs(d) < 0.05) return { text: '', cls: '' }
  const good = invert ? d < 0 : d > 0
  return { text: `${d > 0 ? '+' : ''}${d.toFixed(0)}%`, cls: good ? 'up' : 'down' }
}

function hasActiveRun(): boolean {
  return runs.value.some((r) => r.status === 'queued' || r.status === 'running')
}

function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function syncPolling() {
  if (hasActiveRun()) {
    if (pollTimer === undefined) {
      pollTimer = window.setInterval(async () => {
        try {
          runs.value = await evalApi.listRuns()
          if (!hasActiveRun()) stopPolling()
        } catch {
          stopPolling()
        }
      }, 2000)
    }
  } else {
    stopPolling()
  }
}

async function startRun() {
  runError.value = ''
  const filter: { ids?: string[]; categories?: string[]; tags?: string[] } = {}
  if (startForm.value.filterType === 'categories') {
    filter.categories = startForm.value.categories
  } else if (startForm.value.filterType === 'ids') {
    filter.ids = startForm.value.ids
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
  }
  try {
    const res = await evalApi.startRun({
      name: startForm.value.name || `跑批 ${new Date().toLocaleString()}`,
      llm: startForm.value.llm,
      model: startForm.value.model,
      concurrency: startForm.value.concurrency,
      retries: startForm.value.retries,
      repeat: startForm.value.repeat,
      prompt_variant: startForm.value.prompt_variant,
      temperature: startForm.value.temperature,
      case_filter: filter
    })
    showStart.value = false
    await loadRuns()
    navigate(`#/eval/runs/${res.run_id}`)
  } catch (err) {
    runError.value = `启动失败：${err}`
  }
}

async function removeRun(r: EvalRun) {
  if (!confirm(`确认删除跑批 #${r.id}「${r.name}」？\n该跑批的用例结果与标注将一并删除，不可恢复。`)) {
    return
  }
  try {
    await evalApi.deleteRun(r.id)
    await loadRuns()
  } catch (err) {
    runError.value = `删除失败：${err}`
  }
}

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    done: '完成',
    canceled: '已取消',
    error: '出错'
  }
  return map[s] ?? s
}

function runBadgeClass(s: string): string {
  const map: Record<string, string> = {
    done: 'ok',
    running: 'info',
    queued: 'info',
    canceled: 'neutral',
    error: 'error'
  }
  return map[s] ?? 'neutral'
}

function summaryText(run: EvalRun): string {
  const s = run.summary as Record<string, unknown> | undefined
  if (!s) return '—'
  const parts: string[] = []
  if (typeof s.total === 'number') parts.push(`共 ${s.total} 条`)
  const red = s.red_line as Record<string, unknown> | undefined
  if (red && typeof red.passed === 'boolean') {
    if (red.passed) {
      parts.push('红线✓')
    } else {
      const detail = redLineViolationText(red)
      parts.push(detail ? `红线✗(${detail})` : '红线✗')
    }
  }
  if (typeof s.avg_elapsed_ms === 'number') parts.push(`均 ${s.avg_elapsed_ms}ms`)
  if (typeof s.total_tokens === 'number') parts.push(`${fmtTokens(s.total_tokens)} token`)
  if (s.status && typeof s.status === 'object') {
    parts.push(JSON.stringify(s.status))
  }
  return parts.join(' · ') || '—'
}

function redLineViolationText(red: Record<string, unknown>): string {
  const v = red.violations
  if (!Array.isArray(v) || !v.length) return ''
  return v
    .map((x) => {
      const o = x as Record<string, unknown>
      const pr = typeof o.pass_rate === 'number' ? `${Math.round(o.pass_rate * 100)}%` : ''
      return `${o.case_id ?? '?'}${pr ? ` ${pr}` : ''}`
    })
    .join('、')
}

function compositeCiText(s: Record<string, unknown>): string {
  const lo = s.composite_ci_low
  const hi = s.composite_ci_high
  if (typeof lo !== 'number' || typeof hi !== 'number') return ''
  return `[${(lo * 100).toFixed(1)}~${(hi * 100).toFixed(1)}%]`
}

function compositeText(run: EvalRun): string {
  const s = run.summary as Record<string, unknown> | undefined
  const cs = s?.composite_score
  if (typeof cs !== 'number') return '—'
  const ci = compositeCiText(s ?? {})
  return `${(cs * 100).toFixed(1)}%${ci ? ` ${ci}` : ''}`
}

function redLinePassed(s: Record<string, unknown>): boolean {
  const r = s.red_line as Record<string, unknown> | undefined
  return typeof r?.passed === 'boolean' ? r.passed : true
}

function runVerdictCount(run: EvalRun, key: string): number {
  const verdicts = (run.summary as Record<string, unknown> | undefined)?.verdicts as
    | Record<string, unknown>
    | undefined
  const n = verdicts?.[key]
  return typeof n === 'number' ? n : 0
}

function runPendingCases(run: EvalRun): number {
  const s = run.summary as Record<string, unknown> | undefined
  const pc = s?.pending_cases
  if (typeof pc === 'number') return pc
  return runVerdictCount(run, 'pending') // 旧跑批无 pending_cases，回退全 pending 用例数
}

function pctText(count: number, total: number): string {
  if (!total) return '—'
  return `${count}（${Math.round((count / total) * 100)}%）`
}

function fmtTime(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function fmtDuration(start?: string, end?: string): string {
  if (!start) return '—'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  if (Number.isNaN(s) || Number.isNaN(e)) return '—'
  const sec = Math.round(Math.max(0, e - s) / 1000)
  if (sec < 60) return `${sec}秒`
  const m = Math.floor(sec / 60)
  const r = sec % 60
  return r ? `${m}分${r}秒` : `${m}分`
}

function firstCell(cd: { cells: Record<number, EvalRunCase> }): EvalRunCase | undefined {
  return Object.values(cd.cells)[0]
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    await Promise.all([loadCases(), loadRuns()])
  } catch (err) {
    runError.value = `加载失败：${err}`
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  stopPolling()
})

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && editing.value) {
    closeEditor()
  }
}
</script>

<template>
  <div class="ec">
    <header class="ui-tabs">
      <button class="ui-tab" :class="{ on: tab === 'cases' }" @click="tab = 'cases'">
        用例管理
      </button>
      <button class="ui-tab" :class="{ on: tab === 'runs' }" @click="tab = 'runs'">
        测试管理
      </button>
    </header>

    <!-- ========== 用例管理 ========== -->
    <section v-if="tab === 'cases'">
      <div class="ui-card">
        <div class="ui-toolbar">
          <input v-model="caseFilter.q" placeholder="搜索 id / 标题" class="ui-input" @keyup.enter="loadCases" />
          <select v-model="caseFilter.category" class="ui-select">
            <option value="">全部类别</option>
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <select v-model="caseFilter.enabled" class="ui-select">
            <option value="">全部状态</option>
            <option value="true">启用</option>
            <option value="false">停用</option>
          </select>
          <button class="ui-btn" @click="loadCases">筛选</button>
          <button class="ui-btn primary" @click="newCase">新建用例</button>
        </div>
        <div class="ec-tag-filters">
          <div v-for="g in TAG_GROUPS" :key="g.id" class="ec-tag-group">
            <div class="ec-tag-group-label" :title="g.hint">{{ g.label }}</div>
            <div class="ec-tag-group-axes">
              <label v-for="a in TAG_AXES.filter((x) => x.group === g.id)" :key="a.key" class="ec-tag-filter">
                <span>{{ a.label }}</span>
                <select
                  class="ui-select"
                  :value="tagFilter[a.key] || ''"
                  @change="applyTagFilter(a.key, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="">全部</option>
                  <option v-for="v in tagOptions(a.key)" :key="v" :value="v">{{ v }}</option>
                </select>
              </label>
            </div>
          </div>
        </div>
        <div v-if="activeTagFilters.length" class="ec-tag-active">
          <span class="ec-tag-active-hint">生效条件（跨轴 AND）：</span>
          <span
            v-for="f in activeTagFilters"
            :key="f.key"
            class="ec-tag-chip"
            :class="'tag-' + (tagAxis(f.key)?.color ?? 'plain')"
            :title="'点击移除该条件'"
            @click="removeOneTagFilter(f.key)"
          >
            {{ f.key }}={{ f.value }} ✕
          </span>
          <button class="ui-btn sm" @click="clearTagFilter">全部清除</button>
        </div>
        <p v-if="caseError" class="ui-error">{{ caseError }}</p>
        <p v-if="caseMsg" class="ui-ok">{{ caseMsg }}</p>
        <table class="ui-table">
          <thead>
            <tr>
              <th>序号</th>
              <th>ID</th>
              <th>类别</th>
              <th>标题</th>
              <th>验收维度</th>
              <th>状态</th>
              <th>金标准</th>
              <th>更新时间</th>
              <th>标签</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, idx) in displayedCases" :key="c.id">
              <td class="ui-muted">{{ idx + 1 }}</td>
              <td class="ui-mono">{{ c.id }}</td>
              <td>{{ c.category }}</td>
              <td>
                <button class="ui-link" :title="`编辑 ${c.id}`" @click="editCase(c)">
                  {{ c.title }}
                </button>
              </td>
              <td class="ec-criteria">
                <span v-for="cr in c.expected.criteria" :key="cr" class="ui-chip">{{ cr }}</span>
              </td>
              <td>
                <span class="ui-badge" :class="c.enabled ? 'ok' : 'neutral'">
                  {{ c.enabled ? '启用' : '停用' }}
                </span>
              </td>
              <td>
                <span
                  class="ui-badge"
                  :class="c.annotation.golden_answer || c.annotation.reference_answer ? 'ok' : 'neutral'"
                >
                  {{ c.annotation.golden_answer || c.annotation.reference_answer ? '已标' : '未标' }}
                </span>
              </td>
              <td class="ui-muted">{{ c.updated_at || '—' }}</td>
              <td class="ec-tagcell">
                <span
                  v-for="t in c.tags ?? []"
                  :key="t"
                  class="ec-tag-chip"
                  :class="'tag-' + (tagAxis(splitTag(t)[0])?.color ?? 'plain')"
                  :title="'点击按该标签筛选'"
                  @click="onTagChip(t)"
                >
                  {{ t }}
                </span>
              </td>
              <td>
                <button class="ui-link" @click="editCase(c)">编辑</button>
                <button class="ui-link danger" @click="removeCase(c)">删除</button>
              </td>
            </tr>
            <tr v-if="!displayedCases.length">
              <td colspan="10" class="ui-muted">没有匹配的用例</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="editing" class="ui-modal-backdrop" @click.self="closeEditor">
        <div class="ui-modal">
          <div class="ui-modal-head">
            <h3>{{ isNew ? '新建用例' : `编辑 ${editing.id}` }}</h3>
            <button class="ui-modal-close" title="关闭" @click="closeEditor">×</button>
          </div>
          <div class="ui-modal-body">
            <!-- —— 基本信息 —— -->
            <h3 class="ui-sec">基本信息</h3>
            <div class="ui-grid">
              <label>ID<input v-model="editing.id" :disabled="!isNew" class="ui-input" /></label>
              <label>类别
                <select v-model="editing.category" class="ui-select">
                  <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
                </select>
                <span class="ui-help-inline">{{ CATEGORY_META[editing.category] }}</span>
              </label>
              <label>标题<input v-model="editing.title" class="ui-input" /></label>
              <label>模式
                <select v-model="editing.mode" class="ui-select">
                  <option v-for="m in MODES" :key="m" :value="m">{{ m }}</option>
                </select>
                <span class="ui-help-inline">{{ MODE_META[editing.mode] }}</span>
              </label>
              <label>耗时预算(秒)
                <input v-model.number="editing.timeout_sec" type="number" class="ui-input" />
                <span class="ui-help-inline">latency_budget 判定基准：慢于它判 fail，但不中断执行</span>
              </label>
              <label>硬超时(秒)
                <input v-model.number="editing.hard_timeout_sec" type="number" class="ui-input" />
                <span class="ui-help-inline">超过即中断整条用例（防上游慢响应；默认 90）</span>
              </label>
              <label class="ui-check">
                <input v-model="editing.enabled" type="checkbox" /> 参与跑批
              </label>
              <label class="ui-check">
                <input v-model="editing.compare" type="checkbox" /> 参与对照
                <span class="ui-hint" title="是否纳入豆包/千问等跨模型对照评测（compare=true 进对照组）">ⓘ</span>
              </label>
              <label>权重
                <input v-model.number="editing.weight" type="number" min="0" step="0.5" class="ui-input" />
                <span class="ui-help-inline">加权复合分 Σ(w×通过率)/Σw；默认 1</span>
              </label>
              <label class="ui-check">
                <input v-model="editing.must_pass" type="checkbox" /> 红线用例（must_pass）
                <span class="ui-hint" title="红线用例未达通过阈值即整体不通过（零容忍闸门），不参与配置排名">ⓘ</span>
              </label>
              <label v-if="editing.must_pass">
                红线阈值
                <input v-model.number="editing.must_pass_threshold" type="number" min="0" max="1" step="0.05" class="ui-input" />
                <span class="ui-help-inline">attempt 通过率下限；默认 1（零容忍）</span>
              </label>
              <div class="ec-tag-editor">
                <div v-for="g in TAG_GROUPS" :key="g.id" class="ec-tag-edit-group">
                  <div class="ec-tag-group-label" :title="g.hint">{{ g.label }}</div>
                  <div class="ec-tag-group-axes">
                    <label v-for="a in TAG_AXES.filter((x) => x.group === g.id)" :key="a.key" class="ec-tag-field">
                      <span>{{ a.label }}</span>
                      <select v-model="tagSel[a.key]" class="ui-select">
                        <option value="">（不设）</option>
                        <option v-for="v in tagOptions(a.key)" :key="v" :value="v">{{ v }}</option>
                      </select>
                    </label>
                  </div>
                </div>
                <label class="ec-tag-field ec-tag-custom">
                  <span>其他</span>
                  <input
                    v-model="tagsText"
                    class="ui-input"
                    placeholder="逗号分隔（旧用例/自定义标签，建议先登记词表）"
                  />
                </label>
              </div>
            </div>

            <!-- —— 输入与预期 —— -->
            <h3 class="ui-sec">输入与预期</h3>
            <label class="ui-field">输入消息</label>
            <textarea v-model="messagesText" rows="4" class="ui-textarea"></textarea>
            <p class="ui-help">
              每行一条，格式 <code>role: 内容</code>（role 为 user / assistant / system）。
              最后一条是当前用户消息，之前的作为多轮历史。
            </p>
            <label class="ui-field">预期行为</label>
            <textarea v-model="editing.expected.behavior" rows="2" class="ui-textarea"></textarea>
            <p class="ui-help">
              判分核心：一句话写明"模型做到什么算合格"。例如：应调用 calculator 并给出准确结果 /
              应澄清指代而非臆测 / 应拒绝并说明原因。
            </p>
            <label class="ui-field">预期工具调用（JSON 数组，可选）</label>
            <textarea v-model="toolCallsText" rows="3" class="ui-textarea ui-mono"></textarea>
            <p class="ui-help">
              预期模型会调用的工具，只作观察指标、不硬性判定。
              参数留 <code>{}</code> 表示只校验工具名；多个调用按顺序列出。
            </p>
            <label class="ui-field">设计说明（notes）</label>
            <textarea v-model="editing.notes" rows="2" class="ui-textarea"></textarea>
            <p class="ui-help">用例的设计意图与背景：为什么测、想验证什么，供人阅读。</p>
            <label class="ui-field">管理备注（admin_note）</label>
            <textarea v-model="editing.admin_note" rows="2" class="ui-textarea"></textarea>
            <p class="ui-help">内部管理信息：停用原因、TODO、标注判断依据等；不进入用例语义。</p>

            <!-- —— 验收维度 —— -->
            <h3 class="ui-sec">验收维度</h3>
            <p class="ui-sec-desc">
              选择本用例的判定维度：机器判定项跑批后自动出结论；需人工/LLM 项进入金标准对照。
            </p>
            <div class="ui-checks">
              <label v-for="cr in CRITERIA_OPTIONS" :key="cr" class="ui-check">
                <input v-model="criteriaSel" type="checkbox" :value="cr" /> {{ cr }}
                <span class="ui-crit-tag" :class="critTagClass(cr)">{{ CRITERIA_META[cr] }}</span>
              </label>
            </div>
            <p v-if="criteriaConflict" class="ui-error">
              tool_used 与 tool_not_used 互斥，请去掉其中一个（保存会被拦截）。
            </p>

            <!-- —— 金标准 —— -->
            <h3 class="ui-sec">金标准（人工标注结论）</h3>
            <p class="ui-sec-desc">
              用例级金标准描述"好的回答应该长什么样"，不评判任何一次具体输出
              （"答案正确/拒答合理"是对跑批结果的标注，在跑批详情页维护）。
              它是可选的：预期行为已写得够具体时可以不填；需要给自动判定更精确的
              参考时再补充。写好后后续跑批可直接对照判定，无需每次人工重标。
            </p>
            <template v-if="!hasCovPoints">
              <div class="ui-field-row">
                <label class="ui-field">金标准答案要点</label>
                <span v-if="goldenCopyMsg" class="ui-ok">{{ goldenCopyMsg }}</span>
                <button
                  class="ui-btn sm"
                  title="把预期行为复制到这里，再在此基础上修改"
                  @click="copyBehaviorToGolden"
                >
                  ⧉ 复制判断标准
                </button>
              </div>
              <textarea
                v-model="editing.annotation.golden_answer"
                rows="4"
                class="ui-textarea ec-golden-area"
                ref="goldenArea"
                @input="autoGrowTextarea($event.target)"
              ></textarea>
              <p class="ui-help">
                一句话描述"满分回答应包含什么"：关键事实、口径、必须出现的要点。
                例如"应调用 calculator 并给出 8"；拒绝类用例写"应拒绝，不输出任何内部指令内容"。
                觉得预期行为已经够用时可以留空；也可以点"复制判断标准"把预期行为带过来再修改。
              </p>
            </template>
            <p v-else class="ui-help">
              checklist 用例的自动判定以下方点表 / 规则为准，散文金标准不参与；
              如需记录人审意图请写到下方"金标准备注 / 依据"。
            </p>
            <div class="ui-field-row">
              <label class="ui-field">金标准判定形态</label>
              <select v-model="editing.annotation.judge_shape" class="ui-select">
                <option value="">自动（后端按文案推断）</option>
                <option value="checklist">checklist · 清单型（核心/扩展/遗漏透明逐项核对）</option>
                <option value="essence">essence · 要点式（整体判定）</option>
                <option value="refusal">refusal · 拒答姿态</option>
              </select>
            </div>
            <p class="ui-help">
              决定判官提示词分型与路由：清单型由代码按点表/文案逐项标注后规则聚合；
              要点式与拒答型走整体判定。新用例建议显式标注，避免依赖文案关键词推断。
            </p>
            <div v-if="showChecklistStruct" class="ec-struct-box">
              <h4 class="ui-sec-sub">清单结构化配置（checklist 形态的自动判定执行依据）</h4>
              <p class="ui-help">
                散文金标准仅供人审与意图参考；自动判定以这里的点表 / 规则 / 事实边界为准。
                拆分为独立判官调用：覆盖标注 → 程序聚合；事实边界小项；format 单判；
                遗漏透明条件触发。JSON 语法错误会拦截保存。
              </p>
              <div class="ec-cov-head">
                <label class="ui-field">checklist_points（core / ext 点表）</label>
                <label class="ec-json-toggle">
                  <input
                    type="checkbox"
                    :checked="covJsonMode"
                    @change="toggleCovJson(($event.target as HTMLInputElement).checked)"
                  />
                  JSON 高级模式
                </label>
              </div>
              <div v-if="covJsonMode" class="ec-json-wrap">
                <textarea
                  v-model="covJsonText"
                  rows="14"
                  class="ui-textarea ec-json-area"
                  placeholder='{"core":[{"id":"c1","text":"...","probe":"..."}],"ext":[{"id":"e1","text":"...","group":"O2","probe":"..."}]}'
                ></textarea>
                <p class="ui-help">适用于批量粘贴 / 迁移；切回表单前会校验 core / ext 数组。</p>
              </div>
              <div v-else class="ec-cov-editor">
                <div class="ec-cov-group">
                  <div class="ec-cov-group-head">
                    <strong>核心主题（全部须覆盖）</strong>
                    <button class="ui-btn sm" type="button" @click="addCovRow('core')">＋ 加点</button>
                  </div>
                  <div
                    v-for="(r, i) in covCore"
                    :key="'core-' + i"
                    class="ec-cov-row ec-core-row"
                  >
                    <input v-model="r.id" class="ui-input ec-id" placeholder="id，如 a1-c1" />
                    <textarea
                      v-model="r.text"
                      class="ec-long"
                      rows="2"
                      placeholder="要点（一句话）"
                    ></textarea>
                    <textarea
                      v-model="r.probe"
                      class="ec-long"
                      rows="2"
                      placeholder="覆盖口径（判官判定依据，可留空）"
                    ></textarea>
                    <span class="ec-row-ops">
                      <button class="ui-btn sm" type="button" title="上移" @click="moveCovRow('core', i, -1)">↑</button>
                      <button class="ui-btn sm" type="button" title="下移" @click="moveCovRow('core', i, 1)">↓</button>
                      <button class="ui-btn sm danger" type="button" title="删除" @click="removeCovRow('core', i)">✕</button>
                    </span>
                  </div>
                </div>
                <div class="ec-cov-group">
                  <div class="ec-cov-group-head">
                    <strong>扩展主题（按源组计最低覆盖）</strong>
                    <button class="ui-btn sm" type="button" @click="addCovRow('ext')">＋ 加点</button>
                  </div>
                  <div
                    v-for="(r, i) in covExt"
                    :key="'ext-' + i"
                    class="ec-cov-row ec-ext-row"
                  >
                    <input v-model="r.id" class="ui-input ec-id" placeholder="id，如 a1-e1" />
                    <input
                      v-model="r.group"
                      class="ui-input ec-group"
                      list="ec-group-options"
                      placeholder="源组，如 A1"
                    />
                    <datalist id="ec-group-options">
                      <option v-for="g in covGroupOptions" :key="g" :value="g" />
                    </datalist>
                    <textarea
                      v-model="r.text"
                      class="ec-long"
                      rows="2"
                      placeholder="要点（一句话）"
                    ></textarea>
                    <textarea
                      v-model="r.probe"
                      class="ec-long"
                      rows="2"
                      placeholder="覆盖口径（判官判定依据，可留空）"
                    ></textarea>
                    <span class="ec-row-ops">
                      <button class="ui-btn sm" type="button" title="上移" @click="moveCovRow('ext', i, -1)">↑</button>
                      <button class="ui-btn sm" type="button" title="下移" @click="moveCovRow('ext', i, 1)">↓</button>
                      <button class="ui-btn sm danger" type="button" title="删除" @click="removeCovRow('ext', i)">✕</button>
                    </span>
                  </div>
                </div>
                <details class="ec-cov-preview">
                  <summary>判官视角预览（保存后按此发给覆盖判官）</summary>
                  <pre>{{ covPreviewText }}</pre>
                </details>
              </div>
              <div class="ec-rule-row">
                <label class="ui-field">扩展每源组最低覆盖数</label>
                <input v-model.number="extMin" type="number" min="0" class="ui-input ec-num" />
                <label class="ui-field">遗漏透明</label>
                <select v-model="transMode" class="ui-select">
                  <option value="conditional">条件强制（未全覆盖须点名未展开项）</option>
                  <option value="none">不启用</option>
                </select>
              </div>
              <label class="ui-field">boundary_claims（事实边界 JSON，空则不启用）</label>
              <textarea
                v-model="boundaryText"
                rows="4"
                class="ui-textarea"
                placeholder='[{"id":"b1","claim":"...","violation_example":"..."}]'
              ></textarea>
              <label class="ui-field">format_expected（表达期望，空则只用通用形态口径）</label>
              <textarea
                v-model="editing.annotation.format_expected"
                rows="2"
                class="ui-textarea"
              ></textarea>
            </div>
            <label class="ui-field">完整参考答案（可选）</label>
            <textarea v-model="editing.annotation.reference_answer" rows="3" class="ui-textarea"></textarea>
            <p class="ui-help">
              可选：一段完整的"标准答案"全文，比要点更精确，供自动判定对照。
              不想写完整答案可以留空，只靠上面的要点。
            </p>
            <label class="ui-field">金标准备注 / 依据</label>
            <textarea v-model="editing.annotation.note" rows="2" class="ui-textarea"></textarea>
            <p class="ui-help">记录为什么这样定标准、参考了什么依据，便于日后复查。</p>

            <div class="ui-toolbar ec-actions">
              <template v-if="!isNew">
                <button class="ui-btn" :disabled="caseIdx <= 0" @click="stepCase(-1)">← 上一条</button>
                <span v-if="caseIdx >= 0" class="ui-muted">{{ caseIdx + 1 }} / {{ cases.length }}</span>
                <button
                  class="ui-btn"
                  :disabled="caseIdx < 0 || caseIdx >= cases.length - 1"
                  @click="stepCase(1)"
                >
                  下一条 →
                </button>
              </template>
              <button class="ui-btn primary" @click="saveCase">保存</button>
              <button class="ui-btn" @click="closeEditor">取消</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ========== 测试管理 ========== -->
    <section v-else>
      <div class="ui-card">
        <div class="ui-toolbar">
          <h3 class="ec-title">历史跑批</h3>
          <button class="ui-btn primary" @click="showStart = !showStart">
            {{ showStart ? '收起' : '新建跑批' }}
          </button>
          <button class="ui-btn" @click="openMatrix">对照矩阵</button>
        </div>
        <p v-if="runError" class="ui-error">{{ runError }}</p>

        <div v-if="showStart" class="ec-start">
          <div class="ui-grid">
            <label>名称<input v-model="startForm.name" placeholder="留空自动生成" class="ui-input" /></label>
            <label>执行源
              <select v-model="startForm.llm" class="ui-select">
                <option value="real">真实模型（会花钱）</option>
                <option value="fake">Fake（干跑不花钱）</option>
              </select>
            </label>
            <label>模型
              <select v-model="startForm.model" class="ui-select">
                <option v-for="o in MODEL_OPTIONS" :key="String(o.value)" :value="o.value">
                  {{ o.label }}
                </option>
              </select>
            </label>
            <label>提示词变体
              <select v-model="startForm.prompt_variant" class="ui-select">
                <option v-for="o in PROMPT_VARIANT_OPTIONS" :key="o.value" :value="o.value">
                  {{ o.label }}
                </option>
              </select>
            </label>
            <label>采样温度
              <select v-model="startForm.temperature" class="ui-select">
                <option v-for="o in TEMPERATURE_OPTIONS" :key="String(o.value)" :value="o.value">
                  {{ o.label }}
                </option>
              </select>
            </label>
            <label>并发<input v-model.number="startForm.concurrency" type="number" min="1" max="2500" class="ui-input" /></label>
            <label>重试<input v-model.number="startForm.retries" type="number" min="0" max="5" class="ui-input" /></label>
            <label>重复次数
              <input v-model.number="startForm.repeat" type="number" min="1" max="100" class="ui-input" />
              <span class="ui-help-inline">每条跑 N 次，看稳定性（成本 ×N）</span>
            </label>
            <label>范围
              <select v-model="startForm.filterType" class="ui-select">
                <option value="all">全部启用用例</option>
                <option value="categories">按类别</option>
                <option value="ids">按 ID</option>
              </select>
            </label>
          </div>
          <div v-if="startForm.filterType === 'categories'" class="ui-checks">
            <label v-for="c in CATEGORIES" :key="c" class="ui-check">
              <input v-model="startForm.categories" type="checkbox" :value="c" /> {{ c }}
            </label>
          </div>
          <label v-if="startForm.filterType === 'ids'" class="ui-field">
            ID 列表（每行一个）
            <textarea v-model="startForm.ids" rows="4" class="ui-textarea ui-mono"></textarea>
          </label>
          <div class="ui-toolbar">
            <button class="ui-btn primary" @click="startRun">启动跑批</button>
          </div>
          <p class="ui-help">
            {{ startForm.llm === 'real' ? '真实模型跑批会消耗 API 额度；参考经验：并发 2 + 重试 1 更稳。' : 'Fake 干跑用于验证管道。' }}
          </p>
        </div>

        <div v-if="showMatrix" class="ec-matrix">
          <div class="ui-toolbar">
            <button class="ui-btn" @click="showMatrix = false">← 返回列表</button>
            <button class="ui-btn" :class="{ on: matrixView === 'run' }" @click="switchMatrixView('run')">run 级指标</button>
            <button class="ui-btn" :class="{ on: matrixView === 'case' }" @click="switchMatrixView('case')">用例级对照</button>
            <label>添加 run：
              <select v-model.number="matrixAddSel" class="ui-select">
                <option :value="null" disabled>选择…</option>
                <option v-for="r in runs" :key="r.id" :value="r.id" :disabled="selRunIds.includes(r.id)">
                  #{{ r.id }} {{ r.name }}
                </option>
              </select>
            </label>
            <button class="ui-btn sm" :disabled="matrixAddSel == null" @click="onAddMatrixRun">添加</button>
            <label>
              <input
                v-model="matrixIdInput"
                class="ui-input ec-matrix-id-input"
                placeholder="或输入 run ID，如 77,78"
                @keyup.enter="onAddMatrixRunByIds"
              />
            </label>
            <span v-for="rid in selRunIds" :key="rid" class="ec-matrix-chip">
              #{{ rid }}
              <button class="ui-link danger" title="移出对照" @click="removeMatrixRun(rid)">×</button>
            </span>
            <template v-if="matrixView === 'run'">
              <label>基准 run：
                <select v-model.number="baseRunId" class="ui-select">
                  <option v-for="r in runs" :key="r.id" :value="r.id">#{{ r.id }} {{ r.name }}</option>
                </select>
              </label>
              <label class="ui-check">
                <input type="checkbox" v-model="showJudgeGroup" /> 判定组
              </label>
            </template>
            <template v-else>
              <label class="ui-check">
                <input type="checkbox" v-model="caseDiffOnlyChanged" /> 只看变化
              </label>
            </template>
          </div>
          <table v-if="matrixView === 'run'" class="ui-table ec-matrix-table">
            <thead>
              <tr>
                <th>run</th>
                <th>通过率</th>
                <th>复合分(95%CI)</th>
                <th>红线</th>
                <th>unstable</th>
                <th>fail</th>
                <th>工具调用率</th>
                <th>耗时</th>
                <th>token</th>
                <template v-if="showJudgeGroup">
                  <th>answer</th><th>refusal</th><th>stream</th><th>latency</th>
                </template>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in matrixRows" :key="m.run.id" :class="{ 'ec-matrix-base': m.run.id === baseRunId }">
                <td>
                  <label class="ui-check">
                    <input type="checkbox" :checked="selRunIds.includes(m.run.id)" @change="toggleRunSel(m.run.id)" />
                  </label>
                  #{{ m.run.id }} {{ m.run.name }}
                  <span v-if="m.run.id === baseRunId" class="ui-badge ok">基准</span>
                </td>
                <td>
                  {{ pct(m.s.case_pass_rate) }}
                  <span v-if="m.vs" class="ec-diff" :class="diffPctPts(m.s.case_pass_rate, m.vs.case_pass_rate).cls">
                    {{ diffPctPts(m.s.case_pass_rate, m.vs.case_pass_rate).text }}
                  </span>
                </td>
                <td>
                  {{ pct(m.s.composite_score) }}
                  <span class="ui-muted">{{ compositeCiText(m.s) }}</span>
                  <span v-if="m.vs" class="ec-diff" :class="diffPctPts(m.s.composite_score, m.vs.composite_score).cls">
                    {{ diffPctPts(m.s.composite_score, m.vs.composite_score).text }}
                  </span>
                </td>
                <td>
                  <span v-if="redLinePassed(m.s)" class="ui-badge ok">通过</span>
                  <span
                    v-else
                    class="ui-badge error"
                    :title="redLineViolationText((m.s.red_line ?? {}) as Record<string, unknown>)"
                  >
                    未过
                  </span>
                </td>
                <td>
                  {{ verdictCount(m.s, 'unstable') }}
                  <span v-if="m.vs" class="ec-diff" :class="diff(verdictCount(m.s, 'unstable'), verdictCount(m.vs, 'unstable'), true).cls">
                    {{ diff(verdictCount(m.s, 'unstable'), verdictCount(m.vs, 'unstable'), true).text }}
                  </span>
                </td>
                <td>
                  {{ verdictCount(m.s, 'fail') }}
                  <span v-if="m.vs" class="ec-diff" :class="diff(verdictCount(m.s, 'fail'), verdictCount(m.vs, 'fail'), true).cls">
                    {{ diff(verdictCount(m.s, 'fail'), verdictCount(m.vs, 'fail'), true).text }}
                  </span>
                </td>
                <td>
                  {{ pct(m.s.tool_call_rate) }}
                  <span v-if="m.vs" class="ec-diff" :class="diffPctPts(m.s.tool_call_rate, m.vs.tool_call_rate).cls">
                    {{ diffPctPts(m.s.tool_call_rate, m.vs.tool_call_rate).text }}
                  </span>
                </td>
                <td>
                  {{ typeof m.s.avg_elapsed_ms === 'number' ? Math.round(m.s.avg_elapsed_ms) + 'ms' : '—' }}
                  <span v-if="m.vs" class="ec-diff" :class="diffPctRel(m.s.avg_elapsed_ms, m.vs.avg_elapsed_ms, true).cls">
                    {{ diffPctRel(m.s.avg_elapsed_ms, m.vs.avg_elapsed_ms, true).text }}
                  </span>
                </td>
                <td>
                  {{ fmtTokens(m.s.total_tokens as number) }}
                  <span v-if="m.vs" class="ec-diff" :class="diffPctRel(m.s.total_tokens, m.vs.total_tokens, true).cls">
                    {{ diffPctRel(m.s.total_tokens, m.vs.total_tokens, true).text }}
                  </span>
                </td>
                <template v-if="showJudgeGroup">
                  <td>{{ pct(judgeRate(m.s, 'answer_correct')) }}</td>
                  <td>{{ pct(judgeRate(m.s, 'refusal')) }}</td>
                  <td>{{ pct(judgeRate(m.s, 'stream_complete')) }}</td>
                  <td>{{ pct(judgeRate(m.s, 'latency_budget')) }}</td>
                </template>
              </tr>
              <tr v-if="!matrixRows.length">
                <td :colspan="showJudgeGroup ? 13 : 9" class="ui-muted">
                  在工具栏选择或输入 run ID 参与对照（打开矩阵时为空则默认选中各变体的最新 run）
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="matrixView === 'run'" class="ui-help">
            绿=优于基准，红=劣于基准；基准行不显示差值。耗时/token 越小越好（反向配色）。点击 run 名可跳转详情。
          </p>

          <div v-if="matrixView === 'case'" class="ec-casediff">
            <p v-if="caseDiffLoading" class="ui-muted">加载用例数据中……</p>
            <table v-else class="ui-table ec-matrix-table">
              <thead>
                <tr>
                  <th>用例</th>
                  <th v-for="r in selectedRuns" :key="r.id">
                    #{{ r.id }} {{ runConfigLabel(r) }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in visibleCaseRows"
                  :key="row.id"
                  :class="{ 'ec-case-changed': row.changed, 'ec-case-clickable': true }"
                  @click="openCaseDetail(row)"
                >
                  <td>
                    <strong>{{ row.id }}</strong>
                    <div class="ui-muted">{{ row.title }}</div>
                  </td>
                  <td v-for="r in selectedRuns" :key="r.id">
                    <span class="ui-badge" :class="verdictClass(row.cells[r.id]?.verdict ?? '')">
                      {{ verdictLabel(row.cells[r.id]?.verdict ?? '') }}
                    </span>
                    <span class="ui-muted">
                      {{ row.cells[r.id]?.pass_count ?? '—' }}/{{ row.cells[r.id]?.repeat_count ?? '—' }}
                    </span>
                  </td>
                </tr>
                <tr v-if="!visibleCaseRows.length">
                  <td :colspan="selectedRuns.length + 1" class="ui-muted">
                    {{ caseDiffLoading ? '加载中' : '没有用例数据（先在工具栏添加参与对照的 run）' }}
                  </td>
                </tr>
              </tbody>
            </table>
            <p class="ui-help">
              行高亮 = 该用例在各 run 间的判定不一致（变化优先排序）；单元格 = 该 run 下的判定 + 通过 X/N。勾选"只看变化"过滤全绿行。
            </p>
          </div>

          <div v-if="caseDetail" class="ec-modal-mask" @click.self="closeCaseDetail">
            <div class="ec-modal">
              <div class="ec-modal-head">
                <strong>{{ caseDetail.id }} · {{ caseDetail.title }}</strong>
                <button class="ui-btn sm" @click="closeCaseDetail">关闭</button>
              </div>
              <div class="ec-modal-body">
                <div v-if="firstCell(caseDetail)" class="ec-modal-meta">
                  <details open>
                    <summary>用户输入</summary>
                    <pre class="ec-modal-pre">{{ firstCell(caseDetail)?.input || '（无输入）' }}</pre>
                  </details>
                  <details>
                    <summary>金标准</summary>
                    <pre class="ec-modal-pre">{{ firstCell(caseDetail)?.golden_answer || firstCell(caseDetail)?.behavior || '（无金标准）' }}</pre>
                  </details>
                </div>
                <div v-for="r in selectedRuns" :key="r.id" class="ec-modal-run">
                  <div class="ec-modal-run-head">
                    <strong>#{{ r.id }} {{ runConfigLabel(r) }}</strong>
                    <span class="ui-badge" :class="verdictClass(caseDetail.cells[r.id]?.verdict ?? '')">
                      {{ verdictLabel(caseDetail.cells[r.id]?.verdict ?? '') }}
                    </span>
                    <span class="ui-muted">
                      {{ caseDetail.cells[r.id]?.pass_count ?? '—' }}/{{ caseDetail.cells[r.id]?.repeat_count ?? '—' }}
                    </span>
                  </div>
                  <details open>
                    <summary>输出</summary>
                    <pre class="ec-modal-pre">{{ caseDetail.cells[r.id]?.output || '（无输出）' }}</pre>
                  </details>
                  <details>
                    <summary>判定理由</summary>
                    <div
                      v-for="(reason, cr) in caseDetail.cells[r.id]?.judge_reasons ?? {}"
                      :key="cr"
                      class="ec-modal-reason"
                    >
                      <span
                        class="ui-badge"
                        :class="caseDetail.cells[r.id]?.judgments?.[cr] === 'pass' ? 'ok' : caseDetail.cells[r.id]?.judgments?.[cr] === 'fail' ? 'error' : 'warn'"
                      >
                        {{ cr }}
                      </span>
                      {{ reason }}
                    </div>
                    <div v-if="!Object.keys(caseDetail.cells[r.id]?.judge_reasons ?? {}).length" class="ui-muted">
                      （无判定理由）
                    </div>
                  </details>
                  <details>
                    <summary>执行轨迹</summary>
                    <pre class="ec-modal-pre">{{ traceText(caseDetail.cells[r.id]?.trace) }}</pre>
                  </details>
                  <details>
                    <summary>重复执行明细（{{ (caseDetail.cells[r.id]?.repeat_results ?? []).length }} 次）</summary>
                    <div
                      v-for="(att, ai) in caseDetail.cells[r.id]?.repeat_results ?? []"
                      :key="ai"
                      class="ec-modal-attempt"
                    >
                      <div class="ec-modal-attempt-head">
                        <span class="ui-muted">#{{ ai + 1 }}</span>
                        <span class="ui-badge" :class="verdictClass(att.verdict)">{{ verdictLabel(att.verdict) }}</span>
                        <span class="ui-muted">{{ att.tool_calls?.join(', ') || '无工具' }}</span>
                        <span v-if="att.error" class="ui-muted"> · {{ att.error }}</span>
                      </div>
                      <details>
                        <summary>输出</summary>
                        <pre class="ec-modal-pre">{{ att.output || '（无输出）' }}</pre>
                      </details>
                      <details>
                        <summary>判定理由</summary>
                        <div
                          v-for="(reason, cr) in att.judge_reasons ?? {}"
                          :key="cr"
                          class="ec-modal-reason"
                        >
                          <span
                            class="ui-badge"
                            :class="att.judgments?.[cr] === 'pass' ? 'ok' : att.judgments?.[cr] === 'fail' ? 'error' : 'warn'"
                          >
                            {{ cr }}
                          </span>
                          {{ reason }}
                        </div>
                        <div v-if="!Object.keys(att.judge_reasons ?? {}).length" class="ui-muted">（无判定理由）</div>
                      </details>
                      <details>
                        <summary>执行轨迹</summary>
                        <pre class="ec-modal-pre">{{ traceText(att.trace) }}</pre>
                      </details>
                    </div>
                    <div v-if="!(caseDetail.cells[r.id]?.repeat_results ?? []).length" class="ui-muted">（无重复执行记录）</div>
                  </details>
                </div>
              </div>
            </div>
          </div>
        </div>

        <table v-if="!showMatrix" class="ui-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>模型</th>
              <th>Repeat</th>
              <th>状态</th>
              <th>进度</th>
              <th>汇总</th>
              <th>复合分</th>
              <th>通过用例数</th>
              <th>待人工数</th>
              <th>未通过数</th>
              <th>开始时间</th>
              <th>结束时间</th>
              <th>耗时</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id">
              <td class="ui-mono">{{ r.id }}</td>
              <td>
                <button class="ui-link" @click="navigate(`#/eval/runs/${r.id}`)">{{ r.name }}</button>
                <span
                  v-if="(r.config?.variant ?? 'baseline') !== 'baseline'"
                  class="ui-badge warn"
                >
                  变体:{{ variantLabel(r.config?.variant ?? 'baseline') }}
                </span>
                <span v-if="typeof r.config?.temperature === 'number'" class="ui-badge">
                  温度 {{ r.config.temperature }}
                </span>
              </td>
              <td>
                <span class="ui-badge" :title="r.config?.model ? '' : '历史跑批未记录模型'">
                  {{ modelLabel(r) }}
                </span>
              </td>
              <td class="ui-mono">{{ repeatLabel(r) }}</td>
              <td>
                <span class="ui-badge" :class="runBadgeClass(r.status)">{{ statusLabel(r.status) }}</span>
                <span v-if="r.verified" class="ui-badge ok">已核验</span>
              </td>
              <td>
                <div class="ui-progress">
                  <div
                    class="ui-progress-inner"
                    :style="{ width: (r.total ? (r.progress / r.total) * 100 : 0) + '%' }"
                  ></div>
                </div>
                <span class="ui-muted">{{ r.progress }}/{{ r.total }}</span>
              </td>
              <td class="ui-muted">{{ summaryText(r) }}</td>
              <td class="ui-mono">{{ compositeText(r) }}</td>
              <td class="ui-mono">{{ pctText(runVerdictCount(r, 'pass'), r.total) }}</td>
              <td class="ui-mono">{{ pctText(runPendingCases(r), r.total) }}</td>
              <td class="ui-mono">
                {{ pctText(runVerdictCount(r, 'fail') + runVerdictCount(r, 'exec_error'), r.total) }}
              </td>
              <td class="ui-muted">{{ fmtTime(r.started_at || r.created_at) }}</td>
              <td class="ui-muted">{{ fmtTime(r.finished_at) }}</td>
              <td class="ui-mono">{{ fmtDuration(r.started_at, r.finished_at) }}</td>
              <td>
                <button class="ui-link" @click="navigate(`#/eval/runs/${r.id}`)">查看</button>
                <button
                  class="ui-link danger"
                  :disabled="r.status === 'queued' || r.status === 'running'"
                  :title="r.status === 'queued' || r.status === 'running' ? '运行中的跑批请先取消再删除' : '删除该跑批'"
                  @click="removeRun(r)"
                >
                  删除
                </button>
              </td>
            </tr>
            <tr v-if="!runs.length">
              <td colspan="15" class="ui-muted">还没有跑批记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ec {
  font-size: 0.95em;
}
.ec-title {
  margin: 0;
}
.ec-criteria {
  max-width: 240px;
}
.ec-actions {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #eef1f4;
}
.ec-start {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #fafbfc;
  border: 1px dashed #d0d7de;
  border-radius: 8px;
  padding: 14px 16px;
  margin-top: 12px;
}
.ec-matrix-id-input {
  width: 150px;
}
.ec-matrix-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: #eef1f4;
  border-radius: 10px;
  padding: 2px 8px;
  font-size: 0.9em;
}
.ui-tabs {
  margin-bottom: 14px;
}
.ui-table {
  margin-top: 12px;
}
.ec-golden-area {
  min-height: 120px;
  max-height: 60vh;
  overflow-y: auto;
  resize: vertical;
  line-height: 1.5;
}
.ec-cov-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.ec-json-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85em;
  color: #57606a;
  cursor: pointer;
  user-select: none;
}
.ec-cov-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 6px 0 10px;
}
.ec-cov-group {
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  padding: 6px 8px;
  background: #fafbfc;
}
.ec-cov-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
  font-size: 0.85em;
}
.ec-cov-row {
  display: grid;
  gap: 6px;
  align-items: start;
  margin: 4px 0;
}
.ec-cov-row.ec-core-row {
  grid-template-columns: 120px minmax(240px, 1fr) minmax(340px, 1.6fr) auto;
}
.ec-cov-row.ec-ext-row {
  grid-template-columns: 120px 110px minmax(220px, 1fr) minmax(340px, 1.5fr) auto;
}
.ec-cov-row .ui-input,
.ec-cov-row .ec-long {
  width: 100%;
  padding: 4px 6px;
  font-size: 0.85em;
  box-sizing: border-box;
}
.ec-cov-row .ec-long {
  min-height: 42px;
  resize: vertical;
  line-height: 1.4;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: var(--ui-text);
  font-family: inherit;
  font-size: 0.78em;
}
.ec-id {
  font-family: Consolas, 'Courier New', monospace;
}
.ec-row-ops {
  display: inline-flex;
  gap: 2px;
}
.ui-btn.danger {
  color: #cf222e;
}
.ec-cov-preview {
  font-size: 0.85em;
}
.ec-cov-preview summary {
  cursor: pointer;
  color: #0969da;
}
.ec-cov-preview pre {
  background: #f6f8fa;
  border: 1px solid #eef1f4;
  border-radius: 6px;
  padding: 8px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.9em;
  max-height: 220px;
  overflow-y: auto;
  margin: 4px 0 0;
}
.ec-rule-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
  font-size: 0.9em;
}
.ec-rule-row .ui-field {
  margin: 0;
}
.ec-num {
  width: 70px;
}
.ec-tag-filters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 6px 0 10px;
}
.ec-tag-group {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.ec-tag-group-label {
  flex-shrink: 0;
  width: 64px;
  padding-top: 3px;
  font-size: 0.8em;
  font-weight: 600;
  color: #57606a;
}
.ec-tag-group-axes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 4px 10px;
  flex: 1;
}
.ec-tag-filter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.82em;
  color: #57606a;
}
.ec-tag-filter > span {
  width: 30px;
  flex-shrink: 0;
  text-align: right;
  white-space: nowrap;
}
.ec-tag-filter .ui-select {
  width: 130px;
  padding: 3px 6px;
  font-size: 0.85em;
}
.ec-tag-active {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 0 0 8px;
  padding: 4px 8px;
  background: #fff8e8;
  border: 1px solid #e8d7a8;
  border-radius: 6px;
}
.ec-tag-active-hint {
  font-size: 0.8em;
  color: #7a5c00;
}
.ec-tagcell {
  max-width: 260px;
}
.ec-tag-chip {
  display: inline-block;
  margin: 1px 3px 1px 0;
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 0.76em;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  border: 1px solid transparent;
}
.tag-blue {
  background: #ddf4ff;
  color: #0550ae;
}
.tag-gray {
  background: #eaeef2;
  color: #57606a;
}
.tag-teal {
  background: #e6fffb;
  color: #0f6b5c;
}
.tag-green {
  background: #dafbe1;
  color: #116329;
}
.tag-purple {
  background: #fbefff;
  color: #8250df;
}
.tag-orange {
  background: #fff1e5;
  color: #9a6700;
}
.tag-red {
  background: #ffebe9;
  color: #cf222e;
}
.tag-brown {
  background: #f0e9db;
  color: #7b4e0e;
}
.tag-plain {
  background: #eef1f4;
  color: #57606a;
}
.ec-tag-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 4px 0;
}
.ec-tag-edit-group {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.ec-tag-field {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.85em;
}
.ec-tag-field > span {
  width: 34px;
  color: #57606a;
  flex-shrink: 0;
}
.ec-tag-field .ui-select,
.ec-tag-field .ui-input {
  flex: 1;
  min-width: 0;
  padding: 3px 6px;
  font-size: 0.85em;
}
.ec-tag-custom {
  margin-top: 2px;
}
@media (max-width: 900px) {
  .ec-cov-row {
    grid-template-columns: 1fr;
  }
  .ec-tag-group,
  .ec-tag-edit-group {
    flex-direction: column;
    gap: 2px;
  }
}
</style>
