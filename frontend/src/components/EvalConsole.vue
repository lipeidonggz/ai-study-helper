<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { evalApi, type EvalCase, type EvalRun } from '../api/client'
import { navigate } from '../router'

const tab = ref<'cases' | 'runs'>('cases')

// —— 用例管理 ——
const CATEGORIES = ['tool_call', 'boundary', 'combined', 'multi_turn', 'kb_qa']
const CRITERIA_OPTIONS = [
  'answer_correct',
  'tool_used',
  'tool_not_used',
  'refusal',
  'stream_complete',
  'latency_budget',
  'no_prompt_leak',
  'citation_correct',
  'context_consistent'
]
const MODES = ['general', 'kb_priority', 'tool_enhanced']

// —— 字段说明元数据（编辑界面提示用） ——
const CATEGORY_META: Record<string, string> = {
  tool_call: '验证工具调用行为（调不调、调得对不对）',
  boundary: '边界/安全：拒答、提示注入、幻觉、隐私等',
  combined: '多步组合：工具调用 + 推理/保存/验证串联',
  multi_turn: '多轮一致性（阶段 3 启用）',
  kb_qa: '知识库问答（阶段 2 启用）'
}
const MODE_META: Record<string, string> = {
  general: '通用助手，中文回答',
  kb_priority: '优先个人知识库并给出引用（阶段 2）',
  tool_enhanced: '适合时调用工具获取准确结果'
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

const criteriaConflict = computed(
  () => criteriaSel.value.includes('tool_used') && criteriaSel.value.includes('tool_not_used')
)

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
    tags: [],
    compare: false,
    notes: '',
    enabled: true,
    admin_note: '',
    updated_at: '',
    updated_by: '',
    annotation: {
      golden_answer: '',
      reference_answer: '',
      note: '',
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
  tagsText.value = ''
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
  tagsText.value = c.tags.join(', ')
}

function closeEditor() {
  editing.value = null
  goldenCopyMsg.value = ''
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

async function saveCase() {
  if (!editing.value) return
  caseError.value = ''
  if (criteriaConflict.value) {
    caseError.value = '验收维度互斥：tool_used 与 tool_not_used 不能同时选择'
    return
  }
  try {
    const body: EvalCase = JSON.parse(JSON.stringify(editing.value))
    body.input.messages = parseMessages()
    body.expected.criteria = criteriaSel.value
    if (toolCallsText.value.trim()) {
      body.expected.tool_calls = JSON.parse(toolCallsText.value)
    } else {
      body.expected.tool_calls = []
    }
    body.tags = tagsText.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    if (isNew.value) {
      await evalApi.createCase(body)
    } else {
      await evalApi.updateCase(body.id, body)
    }
    caseMsg.value = '已保存'
    editing.value = null
    await loadCases()
  } catch (err) {
    caseError.value = `保存失败：${err}`
  }
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
const startForm = ref({
  name: '',
  llm: 'real' as 'real' | 'fake',
  concurrency: 2,
  retries: 1,
  repeat: 1,
  filterType: 'all' as 'all' | 'categories' | 'ids',
  categories: [] as string[],
  ids: ''
})
let pollTimer: number | undefined

async function loadRuns() {
  runs.value = await evalApi.listRuns()
  syncPolling()
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
      concurrency: startForm.value.concurrency,
      retries: startForm.value.retries,
      repeat: startForm.value.repeat,
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
  if (typeof s.avg_elapsed_ms === 'number') parts.push(`均 ${s.avg_elapsed_ms}ms`)
  if (typeof s.total_tokens === 'number') parts.push(`${s.total_tokens} token`)
  if (s.status && typeof s.status === 'object') {
    parts.push(JSON.stringify(s.status))
  }
  return parts.join(' · ') || '—'
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
        <p v-if="caseError" class="ui-error">{{ caseError }}</p>
        <p v-if="caseMsg" class="ui-ok">{{ caseMsg }}</p>
        <table class="ui-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>类别</th>
              <th>标题</th>
              <th>验收维度</th>
              <th>状态</th>
              <th>金标准</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in cases" :key="c.id">
              <td class="ui-mono">{{ c.id }}</td>
              <td>{{ c.category }}</td>
              <td>{{ c.title }}</td>
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
              <td>
                <button class="ui-link" @click="editCase(c)">编辑</button>
                <button class="ui-link danger" @click="removeCase(c)">删除</button>
              </td>
            </tr>
            <tr v-if="!cases.length">
              <td colspan="8" class="ui-muted">没有匹配的用例</td>
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
              <label>超时(秒)
                <input v-model.number="editing.timeout_sec" type="number" class="ui-input" />
                <span class="ui-help-inline">超过判超时；同时是 latency_budget 的耗时预算</span>
              </label>
              <label class="ui-check">
                <input v-model="editing.enabled" type="checkbox" /> 参与跑批
              </label>
              <label class="ui-check">
                <input v-model="editing.compare" type="checkbox" /> 参与对照
                <span class="ui-hint" title="是否纳入豆包/千问等跨模型对照评测（compare=true 进对照组）">ⓘ</span>
              </label>
              <label>标签（逗号分隔）<input v-model="tagsText" class="ui-input" /></label>
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
            <textarea v-model="editing.annotation.golden_answer" rows="3" class="ui-textarea"></textarea>
            <p class="ui-help">
              一句话描述"满分回答应包含什么"：关键事实、口径、必须出现的要点。
              例如"应调用 calculator 并给出 8"；拒绝类用例写"应拒绝，不输出任何内部指令内容"。
              觉得预期行为已经够用时可以留空；也可以点"复制判断标准"把预期行为带过来再修改。
            </p>
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
        </div>
        <p v-if="runError" class="ui-error">{{ runError }}</p>

        <div v-if="showStart" class="ec-start">
          <div class="ui-grid">
            <label>名称<input v-model="startForm.name" placeholder="留空自动生成" class="ui-input" /></label>
            <label>模型
              <select v-model="startForm.llm" class="ui-select">
                <option value="real">真实模型（会花钱）</option>
                <option value="fake">Fake（干跑不花钱）</option>
              </select>
            </label>
            <label>并发<input v-model.number="startForm.concurrency" type="number" min="1" max="10" class="ui-input" /></label>
            <label>重试<input v-model.number="startForm.retries" type="number" min="0" max="5" class="ui-input" /></label>
            <label>重复次数
              <input v-model.number="startForm.repeat" type="number" min="1" max="10" class="ui-input" />
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

        <table class="ui-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>状态</th>
              <th>进度</th>
              <th>汇总</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id">
              <td class="ui-mono">{{ r.id }}</td>
              <td>{{ r.name }}</td>
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
              <td class="ui-muted">{{ r.created_at }}</td>
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
              <td colspan="7" class="ui-muted">还没有跑批记录</td>
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
.ui-tabs {
  margin-bottom: 14px;
}
.ui-table {
  margin-top: 12px;
}
</style>
