<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

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
  'citation_correct',
  'context_consistent'
]
const MODES = ['general', 'kb_priority', 'tool_enhanced']

const cases = ref<EvalCase[]>([])
const caseFilter = ref({ q: '', category: '', enabled: '' })
const editing = ref<EvalCase | null>(null)
const isNew = ref(false)
const caseError = ref('')
const caseMsg = ref('')
const criteriaSel = ref<string[]>([])
const toolCallsText = ref('')
const tagsText = ref('')
const messagesText = ref('')

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
      answer_correct: '',
      refusal: '',
      note: '',
      golden_answer: '',
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
  try {
    await Promise.all([loadCases(), loadRuns()])
  } catch (err) {
    runError.value = `加载失败：${err}`
  }
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="ec">
    <header class="ec-tabs">
      <button class="ec-tab" :class="{ on: tab === 'cases' }" @click="tab = 'cases'">
        用例管理
      </button>
      <button class="ec-tab" :class="{ on: tab === 'runs' }" @click="tab = 'runs'">
        测试管理
      </button>
    </header>

    <!-- ========== 用例管理 ========== -->
    <section v-if="tab === 'cases'">
      <div class="ec-card">
        <div class="ec-toolbar">
          <input v-model="caseFilter.q" placeholder="搜索 id / 标题" @keyup.enter="loadCases" />
          <select v-model="caseFilter.category">
            <option value="">全部类别</option>
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <select v-model="caseFilter.enabled">
            <option value="">全部状态</option>
            <option value="true">启用</option>
            <option value="false">停用</option>
          </select>
          <button class="ec-btn" @click="loadCases">筛选</button>
          <button class="ec-btn primary" @click="newCase">新建用例</button>
        </div>
        <p v-if="caseError" class="ec-error">{{ caseError }}</p>
        <p v-if="caseMsg" class="ec-ok">{{ caseMsg }}</p>
        <table class="ec-table">
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
              <td class="ec-mono">{{ c.id }}</td>
              <td>{{ c.category }}</td>
              <td>{{ c.title }}</td>
              <td class="ec-criteria">
                <span v-for="cr in c.expected.criteria" :key="cr" class="ec-chip">{{ cr }}</span>
              </td>
              <td>
                <span class="ec-badge" :class="c.enabled ? 'ok' : 'off'">
                  {{ c.enabled ? '启用' : '停用' }}
                </span>
              </td>
              <td>
                <span
                  class="ec-badge"
                  :class="c.annotation.answer_correct || c.annotation.golden_answer ? 'ok' : 'muted'"
                >
                  {{ c.annotation.answer_correct || c.annotation.golden_answer ? '已标' : '未标' }}
                </span>
              </td>
              <td class="ec-muted">{{ c.updated_at || '—' }}</td>
              <td>
                <button class="ec-link" @click="editCase(c)">编辑</button>
                <button class="ec-link danger" @click="removeCase(c)">删除</button>
              </td>
            </tr>
            <tr v-if="!cases.length">
              <td colspan="8" class="ec-muted">没有匹配的用例</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="editing" class="ec-card ec-editor">
        <h3>{{ isNew ? '新建用例' : `编辑 ${editing.id}` }}</h3>
        <div class="ec-grid">
          <label>ID<input v-model="editing.id" :disabled="!isNew" /></label>
          <label>类别
            <select v-model="editing.category">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
            </select>
          </label>
          <label>标题<input v-model="editing.title" /></label>
          <label>模式
            <select v-model="editing.mode">
              <option v-for="m in MODES" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>
          <label>超时(秒)<input v-model.number="editing.timeout_sec" type="number" /></label>
          <label class="ec-check">
            <input v-model="editing.enabled" type="checkbox" /> 参与跑批
          </label>
          <label class="ec-check">
            <input v-model="editing.compare" type="checkbox" /> 参与对照
          </label>
          <label>标签（逗号分隔）<input v-model="tagsText" /></label>
        </div>
        <label class="ec-field">输入消息（每行 "role: content"，支持多轮）</label>
        <textarea v-model="messagesText" rows="4"></textarea>
        <label class="ec-field">预期行为</label>
        <textarea v-model="editing.expected.behavior" rows="2"></textarea>
        <label class="ec-field">验收维度（可多选）</label>
        <div class="ec-checks">
          <label v-for="cr in CRITERIA_OPTIONS" :key="cr" class="ec-check">
            <input v-model="criteriaSel" type="checkbox" :value="cr" /> {{ cr }}
          </label>
        </div>
        <label class="ec-field">预期工具调用（JSON 数组，可选）</label>
        <textarea v-model="toolCallsText" rows="3" class="ec-mono"></textarea>
        <label class="ec-field">设计说明（notes）</label>
        <textarea v-model="editing.notes" rows="2"></textarea>
        <label class="ec-field">管理备注（admin_note）</label>
        <textarea v-model="editing.admin_note" rows="2"></textarea>

        <h3 class="ec-sub">金标准（人工标注结论）</h3>
        <label class="ec-field">金标准答案要点（供自动判定与人工对照）</label>
        <textarea v-model="editing.annotation.golden_answer" rows="3"></textarea>
        <div class="ec-grid">
          <label>答案正确
            <select v-model="editing.annotation.answer_correct">
              <option value="">未标</option>
              <option value="对">对</option>
              <option value="错">错</option>
              <option value="存疑">存疑</option>
            </select>
          </label>
          <label>拒答合理
            <select v-model="editing.annotation.refusal">
              <option value="">未标</option>
              <option value="合理">合理</option>
              <option value="不合理">不合理</option>
              <option value="不适用">不适用</option>
            </select>
          </label>
        </div>
        <label class="ec-field">标注备注 / 依据</label>
        <textarea v-model="editing.annotation.note" rows="2"></textarea>

        <div class="ec-toolbar">
          <button class="ec-btn primary" @click="saveCase">保存</button>
          <button class="ec-btn" @click="closeEditor">取消</button>
        </div>
      </div>
    </section>

    <!-- ========== 测试管理 ========== -->
    <section v-else>
      <div class="ec-card">
        <div class="ec-toolbar">
          <h3 class="ec-title">历史跑批</h3>
          <button class="ec-btn primary" @click="showStart = !showStart">
            {{ showStart ? '收起' : '新建跑批' }}
          </button>
        </div>
        <p v-if="runError" class="ec-error">{{ runError }}</p>

        <div v-if="showStart" class="ec-start">
          <div class="ec-grid">
            <label>名称<input v-model="startForm.name" placeholder="留空自动生成" /></label>
            <label>模型
              <select v-model="startForm.llm">
                <option value="real">真实模型（会花钱）</option>
                <option value="fake">Fake（干跑不花钱）</option>
              </select>
            </label>
            <label>并发<input v-model.number="startForm.concurrency" type="number" min="1" max="10" /></label>
            <label>重试<input v-model.number="startForm.retries" type="number" min="0" max="5" /></label>
            <label>范围
              <select v-model="startForm.filterType">
                <option value="all">全部启用用例</option>
                <option value="categories">按类别</option>
                <option value="ids">按 ID</option>
              </select>
            </label>
          </div>
          <div v-if="startForm.filterType === 'categories'" class="ec-checks">
            <label v-for="c in CATEGORIES" :key="c" class="ec-check">
              <input v-model="startForm.categories" type="checkbox" :value="c" /> {{ c }}
            </label>
          </div>
          <label v-if="startForm.filterType === 'ids'" class="ec-field">
            ID 列表（每行一个）
            <textarea v-model="startForm.ids" rows="4" class="ec-mono"></textarea>
          </label>
          <div class="ec-toolbar">
            <button class="ec-btn primary" @click="startRun">启动跑批</button>
          </div>
          <p class="ec-hint">
            {{ startForm.llm === 'real' ? '真实模型跑批会消耗 API 额度；参考经验：并发 2 + 重试 1 更稳。' : 'Fake 干跑用于验证管道。' }}
          </p>
        </div>

        <table class="ec-table">
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
              <td class="ec-mono">{{ r.id }}</td>
              <td>{{ r.name }}</td>
              <td>
                <span class="ec-badge" :class="r.status">{{ statusLabel(r.status) }}</span>
              </td>
              <td>
                <div class="ec-progress">
                  <div
                    class="ec-progress-inner"
                    :style="{ width: (r.total ? (r.progress / r.total) * 100 : 0) + '%' }"
                  ></div>
                </div>
                <span class="ec-muted">{{ r.progress }}/{{ r.total }}</span>
              </td>
              <td class="ec-muted">{{ summaryText(r) }}</td>
              <td class="ec-muted">{{ r.created_at }}</td>
              <td>
                <button class="ec-link" @click="navigate(`#/eval/runs/${r.id}`)">查看</button>
                <button
                  class="ec-link danger"
                  :disabled="r.status === 'queued' || r.status === 'running'"
                  :title="r.status === 'queued' || r.status === 'running' ? '运行中的跑批请先取消再删除' : '删除该跑批'"
                  @click="removeRun(r)"
                >
                  删除
                </button>
              </td>
            </tr>
            <tr v-if="!runs.length">
              <td colspan="7" class="ec-muted">还没有跑批记录</td>
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
.ec-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 14px;
}
.ec-tab {
  padding: 8px 22px;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  background: #fff;
  color: #57606a;
  cursor: pointer;
  font-size: 0.92em;
}
.ec-tab:hover {
  background: #f6f8fa;
}
.ec-tab.on {
  background: #0969da;
  color: #fff;
  border-color: #0969da;
}
.ec-card {
  background: #fff;
  border: 1px solid #e2e5e9;
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 14px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.ec-title {
  margin: 0;
}
.ec-sub {
  margin: 18px 0 2px;
  font-size: 1em;
  border-top: 1px solid #eef1f4;
  padding-top: 12px;
}
.ec-toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.ec-toolbar input,
.ec-toolbar select,
.ec-editor input,
.ec-editor select,
.ec-start input,
.ec-start select {
  padding: 6px 10px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  font-size: 0.92em;
}
.ec-btn {
  padding: 6px 16px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: #24292f;
  cursor: pointer;
  font-size: 0.9em;
}
.ec-btn:hover {
  background: #f6f8fa;
}
.ec-btn.primary {
  background: #0969da;
  border-color: #0969da;
  color: #fff;
}
.ec-btn.primary:hover {
  background: #0a5fc2;
}
.ec-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
  font-size: 0.9em;
}
.ec-table th,
.ec-table td {
  border-bottom: 1px solid #eef1f4;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
.ec-table th {
  background: #f6f8fa;
  font-weight: 600;
  white-space: nowrap;
}
.ec-table tbody tr:hover td {
  background: #f8fafc;
}
.ec-criteria {
  max-width: 240px;
}
.ec-chip {
  display: inline-block;
  font-size: 0.75em;
  background: #eef2f7;
  color: #444;
  border-radius: 999px;
  padding: 1px 8px;
  margin: 1px 2px 1px 0;
}
.ec-badge {
  display: inline-block;
  font-size: 0.78em;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.ec-badge.ok,
.ec-badge.done {
  background: #dafbe1;
  color: #1a7f37;
}
.ec-badge.off,
.ec-badge.error,
.ec-badge.timeout {
  background: #ffeef0;
  color: #cf222e;
}
.ec-badge.running,
.ec-badge.queued {
  background: #ddf4ff;
  color: #0969da;
}
.ec-badge.canceled {
  background: #e6edf3;
  color: #57606a;
}
.ec-badge.muted {
  background: #e6edf3;
  color: #57606a;
}
.ec-link {
  background: none;
  border: none;
  color: #0969da;
  cursor: pointer;
  padding: 0 6px 0 0;
  font-size: 0.9em;
}
.ec-link.danger {
  color: #cf222e;
}
.ec-link:disabled {
  color: #999;
  cursor: not-allowed;
}
.ec-error {
  color: #cf222e;
  font-size: 0.88em;
}
.ec-ok {
  color: #1a7f37;
  font-size: 0.88em;
}
.ec-muted {
  color: #888;
  font-size: 0.85em;
}
.ec-mono {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.85em;
}
.ec-field {
  display: block;
  margin-top: 12px;
  font-size: 0.88em;
  color: #444;
}
.ec-editor textarea,
.ec-start textarea {
  width: 100%;
  padding: 8px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  font-family: inherit;
  font-size: 0.92em;
  margin-top: 4px;
}
.ec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 4px;
}
.ec-grid label,
.ec-start label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.86em;
  color: #57606a;
}
.ec-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 6px;
}
.ec-check {
  flex-direction: row !important;
  align-items: center;
  gap: 4px !important;
}
.ec-progress {
  width: 90px;
  height: 6px;
  background: #e6edf3;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 2px;
}
.ec-progress-inner {
  height: 100%;
  background: #0969da;
}
.ec-start {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fafbfc;
  border: 1px dashed #d0d7de;
  border-radius: 8px;
  padding: 14px 16px;
  margin-top: 12px;
}
.ec-hint {
  color: #888;
  font-size: 0.85em;
  margin: 4px 0 0;
}
</style>
