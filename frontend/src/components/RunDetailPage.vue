<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import {
  evalApi,
  type EvalRun,
  type EvalRunCase,
  type EvalRunAttempt,
  type ExecTraceEvent
} from '../api/client'
import { fmtTokens } from '../utils/format'

const props = defineProps<{ runId: number }>()

const data = ref<{ run: EvalRun; cases: EvalRunCase[]; active: boolean } | null>(null)
const error = ref('')
const saving = ref<string>('') // 正在保存标注的 case_id
const saveMsg = ref('')
const goldenSaving = ref<string>('') // 正在保存金标准的 case_id
const goldenMsg = ref('')
const goldenEdits = ref<Record<string, string>>({})
const rerunning = ref<string>('') // 正在重跑的 case_id
const rerunMsg = ref('')
const renaming = ref(false) // 正在保存跑批名称
const editingName = ref('') // 标题行内编辑的输入值（空=非编辑态）
const verifying = ref(false)
const verifyMsg = ref('')
const expanded = ref<Set<string>>(new Set())
const allExpanded = ref(false)
// 轻量轮询下，展开过的用例全量数据缓存（repeat_results / trace），轮询刷新后合并回行内
const detailCache = ref<Record<string, { repeat_results: EvalRunAttempt[]; trace: ExecTraceEvent[] }>>({})
const filter = ref({ category: '', status: '', verdict: '', q: '' })
let pollTimer: number | undefined

// —— 用例级结论（后端未填时按状态/判定推导，兼容旧跑批） ——
function rowVerdict(row: EvalRunCase): string {
  if (row.verdict) return row.verdict
  if (row.status !== 'ok') return 'exec_error'
  if (row.pending_human?.length) return 'pending'
  if (Object.values(row.judgments).includes('fail')) return 'fail'
  return 'pass'
}

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

// —— 执行轨迹渲染：把后端落盘的 exec_trace 转成可读文本（失败回放/绕圈分析用） ——
function traceText(trace: any[] | undefined): string {
  if (!trace?.length) return '（无执行轨迹）'
  return trace
    .map((t) => {
      if (t.type === 'round') return `── 第 ${t.round} 轮 ──`
      if (t.type === 'text') {
        const text = String(t.text ?? '').replace(/\s+/g, ' ').slice(0, 100)
        return `  文本: ${text}`
      }
      if (t.type === 'tool_exec') {
        const args = JSON.stringify(t.arguments ?? {})
        const result = String(t.result ?? '').replace(/\s+/g, ' ').slice(0, 100)
        const err = t.error ? ` [错误: ${t.error}]` : ''
        return `  工具调用: ${t.name}(${args}) → ${result}${err}`
      }
      if (t.type === 'guardrail') return `  ⛔ 护栏拦截: ${t.action}`
      if (t.type === 'done') return `  ✓ 结束: ${t.end_reason}`
      return `  ${t.type}: ${JSON.stringify(t)}`
    })
    .join('\n')
}

const verdictCounts = computed(() => {
  const c = { pass: 0, fail: 0, pending: 0, exec_error: 0 }
  for (const row of data.value?.cases ?? []) {
    const v = rowVerdict(row)
    if (v in c) c[v as keyof typeof c]++
  }
  return c
})

const pendingCount = computed(() => {
  // 待人工 = 任一 attempt 需要人工标注的用例（判官 uncertain / 未判定），
  // 旧跑批没有 pending_attempts 列时回退看行级 pending_human
  const rows = data.value?.cases ?? []
  return rows.filter(
    (row) => (row.pending_attempts ?? 0) > 0 || (row.pending_human?.length ?? 0) > 0
  ).length
})

const categories = computed(() => {
  const set = new Set((data.value?.cases ?? []).map((r) => r.category))
  return [...set]
})

const filteredCases = computed(() => {
  const rows = data.value?.cases ?? []
  const f = filter.value
  return rows.filter((r) => {
    if (f.category && r.category !== f.category) return false
    if (f.status && r.status !== f.status) return false
    if (f.verdict && rowVerdict(r) !== f.verdict) return false
    if (f.q) {
      const q = f.q.trim().toLowerCase()
      if (q && !r.case_id.toLowerCase().includes(q) && !(r.title ?? '').toLowerCase().includes(q)) {
        return false
      }
    }
    return true
  })
})

async function refresh() {
  try {
    // 轻量模式：列表/轮询不携带 repeat_results 与 trace（repeat 20 时是 MB 级）
    data.value = await evalApi.getRun(props.runId, true)
    for (const row of data.value.cases) {
      // 判定参考草稿：金标准优先，无则用预期行为；已有草稿则保留（避免轮询覆盖）
      goldenEdits.value[row.case_id] ??= row.golden_answer || row.behavior
      // 把已加载的展开明细合并回轻量行，避免轮询把展开区"清空"
      const cached = detailCache.value[row.case_id]
      if (cached) {
        row.repeat_results = cached.repeat_results
        row.trace = cached.trace
      }
    }
    stopPolling()
    if (data.value.active) {
      pollTimer = window.setInterval(refresh, 5000)
    }
  } catch (err) {
    error.value = `加载失败：${err}`
  }
}

function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function toggleCase(caseId: string) {
  const next = new Set(expanded.value)
  if (next.has(caseId)) {
    next.delete(caseId)
  } else {
    next.add(caseId)
    void ensureCaseDetail(caseId) // 展开时按需拉全量（repeat_results / trace）
  }
  expanded.value = next
  allExpanded.value =
    !!data.value && data.value.cases.length > 0 && next.size === data.value.cases.length
}

async function ensureCaseDetail(caseId: string) {
  if (!data.value || detailCache.value[caseId]) return
  const row = data.value.cases.find((r) => r.case_id === caseId)
  if (!row) return
  try {
    const full = await evalApi.getRunCase(data.value.run.id, caseId)
    detailCache.value[caseId] = {
      repeat_results: full.repeat_results,
      trace: full.trace ?? []
    }
    row.repeat_results = full.repeat_results
    row.trace = full.trace ?? []
  } catch (err) {
    error.value = `加载用例明细失败：${err}`
  }
}

function toggleAll() {
  if (!data.value) return
  const allIds = data.value.cases.map((c) => c.case_id)
  const willCollapse = allExpanded.value || expanded.value.size === allIds.length
  expanded.value = willCollapse ? new Set() : new Set(allIds)
  allExpanded.value = !willCollapse
}

async function cancelCurrent() {
  if (!data.value) return
  try {
    await evalApi.cancelRun(data.value.run.id)
    await refresh()
  } catch (err) {
    error.value = `取消失败：${err}`
  }
}

async function toggleVerified() {
  if (!data.value) return
  verifying.value = true
  verifyMsg.value = ''
  try {
    if (data.value.run.verified) {
      await evalApi.unverifyRun(data.value.run.id)
      verifyMsg.value = '已取消核验'
    } else {
      await evalApi.verifyRun(data.value.run.id)
      verifyMsg.value = '已标记核验'
    }
    await refresh()
  } catch (err) {
    error.value = `操作失败：${err}`
  } finally {
    verifying.value = false
  }
}

async function saveAnnotation(row: EvalRunCase) {
  if (!data.value) return
  saving.value = row.case_id
  saveMsg.value = ''
  try {
    await evalApi.annotate(data.value.run.id, row.case_id, {
      answer_correct: row.answer_correct,
      refusal: row.refusal,
      note: row.annotate_note
    })
    saveMsg.value = '已保存'
  } catch (err) {
    error.value = `标注保存失败：${err}`
  } finally {
    saving.value = ''
  }
}

async function saveGolden(row: EvalRunCase) {
  if (!data.value) return
  goldenSaving.value = row.case_id
  goldenMsg.value = ''
  try {
    await evalApi.updateGoldenAnswer(row.case_id, {
      golden_answer: goldenEdits.value[row.case_id] ?? ''
    })
    row.golden_answer = goldenEdits.value[row.case_id] ?? ''
    goldenMsg.value = `已保存到用例 ${row.case_id}，后续跑批生效`
  } catch (err) {
    error.value = `保存金标准失败：${err}`
  } finally {
    goldenSaving.value = ''
  }
}

async function rerunCase(row: EvalRunCase) {
  if (!data.value) return
  if (!confirm(`确认重跑 ${row.case_id}？将覆盖该条结果并清空其人工标注。`)) return
  rerunning.value = row.case_id
  rerunMsg.value = ''
  try {
    await evalApi.rerunCase(data.value.run.id, row.case_id)
    rerunMsg.value = `已重跑：${row.case_id}，结果已更新`
    delete detailCache.value[row.case_id] // 结果变了，旧缓存作废
    await refresh()
    if (expanded.value.has(row.case_id)) {
      await ensureCaseDetail(row.case_id) // 展开中则拉新全量
    }
  } catch (err) {
    error.value = `重跑失败：${err}`
  } finally {
    rerunning.value = ''
  }
}

function startEditName() {
  editingName.value = data.value?.run.name ?? ''
}

async function saveName() {
  if (!data.value) return
  const name = editingName.value.trim()
  if (!name) return
  renaming.value = true
  try {
    const res = await evalApi.renameRun(data.value.run.id, name)
    data.value.run.name = res.name
    editingName.value = ''
  } catch (err) {
    error.value = `重命名失败：${err}`
  } finally {
    renaming.value = false
  }
}

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    done: '完成',
    canceled: '已取消',
    error: '出错',
    ok: '通过',
    timeout: '超时'
  }
  return map[s] ?? s
}

function execStatusClass(s: string): string {
  const map: Record<string, string> = { ok: 'ok', timeout: 'warn', error: 'error' }
  return map[s] ?? 'neutral'
}

function summaryText(run: EvalRun): string {
  const s = run.summary as Record<string, unknown> | undefined
  if (!s) return '—'
  const parts: string[] = []
  if (typeof s.total === 'number') parts.push(`共 ${s.total} 条`)
  if (typeof s.avg_elapsed_ms === 'number') parts.push(`均 ${s.avg_elapsed_ms}ms`)
  if (typeof s.total_tokens === 'number') parts.push(`${fmtTokens(s.total_tokens)} token`)
  return parts.join(' · ') || '—'
}

function compositeText(run: EvalRun): string {
  const s = run.summary as Record<string, unknown> | undefined
  const cs = s?.composite_score
  if (typeof cs !== 'number') return '—'
  const lo = s?.composite_ci_low
  const hi = s?.composite_ci_high
  const ci =
    typeof lo === 'number' && typeof hi === 'number'
      ? ` [${(lo * 100).toFixed(1)}~${(hi * 100).toFixed(1)}%]`
      : ''
  return `${(cs * 100).toFixed(2)}%${ci}`
}

interface RedLineViolation {
  case_id: string
  pass_rate: number
  threshold: number
}

function redLineViolations(run: EvalRun): RedLineViolation[] {
  const s = run.summary as Record<string, unknown> | undefined
  const r = s?.red_line as Record<string, unknown> | undefined
  const v = r?.violations
  if (!Array.isArray(v)) return []
  return v
    .filter((x): x is RedLineViolation => {
      const o = x as Record<string, unknown>
      return typeof o?.case_id === 'string' && typeof o?.pass_rate === 'number'
    })
    .map((o) => ({ case_id: o.case_id, pass_rate: o.pass_rate, threshold: o.threshold ?? 1 }))
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

function judgmentsText(j: Record<string, string>): string {
  return Object.entries(j)
    .map(([k, v]) => `${k}:${v === 'pass' ? '过' : '挂'}`)
    .join('  ')
}

onMounted(refresh)
onUnmounted(stopPolling)
</script>

<template>
  <div class="rd">
    <header class="ui-bar rd-head">
      <a class="ui-link" href="#/eval/runs">← 返回评测台</a>
      <h1 v-if="data" class="rd-title">
        Run #{{ data.run.id }} ·
        <template v-if="editingName !== ''">
          <input
            v-model="editingName"
            class="ui-input rd-name-input"
            :disabled="renaming"
            @keyup.enter="saveName"
          />
          <button class="ui-btn sm" :disabled="renaming" @click="saveName">
            {{ renaming ? '保存中…' : '保存' }}
          </button>
          <button class="ui-btn sm" @click="editingName = ''">取消</button>
        </template>
        <template v-else>
          {{ data.run.name }}
          <button class="ui-link" title="重命名跑批" @click="startEditName">✎</button>
        </template>
        <span class="ui-badge" :class="data.run.status === 'done' ? 'ok' : 'neutral'">
          {{ statusLabel(data.run.status) }}
        </span>
        <span v-if="data.run.verified" class="ui-badge ok">已核验</span>
      </h1>
      <div class="rd-actions">
        <button v-if="data?.active" class="ui-btn danger" @click="cancelCurrent">取消跑批</button>
        <button
          v-if="data && !data.active"
          class="ui-btn"
          :disabled="verifying"
          :title="pendingCount ? `还有 ${pendingCount} 条待人工，仍可标记核验` : '标记该跑批结果已人工核验'"
          @click="toggleVerified"
        >
          {{ verifying ? '处理中…' : data.run.verified ? '取消核验' : '标记已核验' }}
        </button>
        <a v-if="data" class="ui-btn link" :href="evalApi.exportUrl(data.run.id)" target="_blank">
          导出 JSON
        </a>
      </div>
    </header>

    <p v-if="error" class="ui-error">{{ error }}</p>

    <template v-if="data">
      <p v-if="data.run.error" class="ui-error">错误：{{ data.run.error }}</p>
      <p class="ui-help">
        进度 {{ data.run.progress }}/{{ data.run.total }} · 汇总：{{ summaryText(data.run) }}
      </p>
      <p class="ui-help">
        模型 {{ data.run.config?.model ?? '—（未记录）' }} · Repeat {{ data.run.config?.repeat ?? '—' }}
      </p>
      <p class="ui-help">
        开始 {{ fmtTime(data.run.started_at) }} · 结束 {{ fmtTime(data.run.finished_at) }} · 耗时 {{ fmtDuration(data.run.started_at, data.run.finished_at) }}
      </p>

      <div class="ui-stats">
        <span class="ui-stat ok">通过 <span class="num">{{ verdictCounts.pass }}</span></span>
        <span class="ui-stat error">未通过 <span class="num">{{ verdictCounts.fail }}</span></span>
        <span class="ui-stat warn">待人工 <span class="num">{{ pendingCount }}</span></span>
        <span class="ui-stat neutral">执行失败 <span class="num">{{ verdictCounts.exec_error }}</span></span>
        <span class="ui-stat plain">
          通过率 {{ verdictCounts.pass }}/{{ data.cases.length }}
          <template v-if="data.cases.length">
            （{{ Math.round((verdictCounts.pass / data.cases.length) * 100) }}%）
          </template>
        </span>
        <span class="ui-stat plain">复合分 <span class="num">{{ compositeText(data.run) }}</span></span>
      </div>
      <div
        v-if="redLineViolations(data.run).length"
        class="ui-error"
        style="margin: 8px 0"
      >
        <strong>红线闸门未通过：</strong>
        <span v-for="v in redLineViolations(data.run)" :key="v.case_id">
          {{ v.case_id }}（通过率 {{ (v.pass_rate * 100).toFixed(0) }}% / 阈值
          {{ (v.threshold * 100).toFixed(0) }}%）
        </span>
      </div>
      <p v-if="verifyMsg" class="ui-ok">{{ verifyMsg }}</p>
      <p v-if="rerunMsg" class="ui-ok">{{ rerunMsg }}</p>
      <p v-if="pendingCount" class="ui-help">
        有 {{ pendingCount }} 条待人工：判官 uncertain 或未判定，可在展开区人工标注。
      </p>

      <div class="ui-card">
        <div class="ui-toolbar rd-filters">
          <select v-model="filter.category" class="ui-select">
            <option value="">全部类别</option>
            <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
          </select>
          <select v-model="filter.status" class="ui-select">
            <option value="">全部执行状态</option>
            <option value="ok">执行通过</option>
            <option value="timeout">超时</option>
            <option value="error">出错</option>
          </select>
          <select v-model="filter.verdict" class="ui-select">
            <option value="">全部判定结果</option>
            <option value="pass">通过</option>
            <option value="fail">未通过</option>
            <option value="pending">待人工</option>
            <option value="unstable">不稳定</option>
            <option value="exec_error">执行失败</option>
          </select>
          <input v-model="filter.q" placeholder="搜索 id / 标题" class="ui-input" />
          <span class="ui-muted rd-filter-count">{{ filteredCases.length }}/{{ data.cases.length }} 条</span>
          <button class="ui-btn sm" @click="toggleAll">
            {{ allExpanded ? '全部收起' : '全部展开' }}
          </button>
        </div>

        <table class="ui-table">
          <thead>
            <tr>
              <th class="rd-th-expand"></th>
              <th>序号</th>
              <th>用例</th>
              <th>类别</th>
              <th>执行状态</th>
              <th>判定结果</th>
              <th>耗时</th>
              <th>轮数</th>
              <th>工具调用</th>
              <th>答案正确</th>
              <th>拒答合理</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in filteredCases" :key="row.case_id">
              <tr class="rd-row">
                <td>
                  <button
                    class="rd-expand"
                    :class="{ on: expanded.has(row.case_id) }"
                    :title="expanded.has(row.case_id) ? '收起' : '展开输入输出'"
                    @click="toggleCase(row.case_id)"
                  >
                    ▸
                  </button>
                </td>
                <td class="ui-mono">{{ data?.cases.indexOf(row) + 1 }}</td>
                <td>
                  <div class="rd-case-id">{{ row.case_id }}</div>
                  <div class="rd-case-title">{{ row.title }}</div>
                </td>
                <td>{{ row.category }}</td>
                <td>
                  <span class="ui-badge" :class="execStatusClass(row.status)">
                    {{ statusLabel(row.status) }}
                  </span>
                </td>
                <td>
                  <span class="ui-badge" :class="verdictClass(rowVerdict(row))">
                    {{ verdictLabel(rowVerdict(row)) }}
                  </span>
                  <span
                    v-if="(row.repeat_count ?? 1) > 1"
                    class="ui-mono rd-pass-rate"
                    :title="`${row.repeat_count} 次执行中通过 ${row.pass_count} 次`"
                  >
                    通过 {{ row.pass_count }}/{{ row.repeat_count }}
                  </span>
                </td>
                <td class="ui-mono">{{ row.elapsed_ms }}ms</td>
                <td>{{ row.rounds }}</td>
                <td class="ui-mono">{{ row.tool_calls.join(', ') || '—' }}</td>
                <td>
                  <span
                    v-if="row.answer_correct"
                    class="ui-badge"
                    :class="row.answer_correct === '对' ? 'ok' : row.answer_correct === '错' ? 'error' : 'warn'"
                  >
                    {{ row.answer_correct }}
                  </span>
                  <span v-else class="ui-muted">—</span>
                </td>
                <td>
                  <span
                    v-if="row.refusal"
                    class="ui-badge"
                    :class="row.refusal === '合理' ? 'ok' : row.refusal === '不合理' ? 'error' : 'warn'"
                  >
                    {{ row.refusal }}
                  </span>
                  <span v-else class="ui-muted">—</span>
                </td>
                <td>
                  <button
                    class="ui-btn sm"
                    :disabled="rerunning === row.case_id || !!data.active"
                    :title="data.active ? '跑批运行中，暂不能重跑' : '重跑该用例（覆盖结果并清空标注）'"
                    @click="rerunCase(row)"
                  >
                    {{ rerunning === row.case_id ? '重跑中…' : '重跑' }}
                  </button>
                </td>
              </tr>
              <tr v-if="expanded.has(row.case_id)" class="rd-detail-row">
                <td colspan="12">
                  <div class="rd-detail">
                    <div class="rd-ref">
                      <div class="rd-ref-head">
                        <strong>判定参考</strong>
                        <span class="ui-badge" :class="row.golden_answer ? 'ok' : 'warn'">
                          {{ row.golden_answer ? '金标准' : '预期行为（无金标准）' }}
                        </span>
                        <span class="ui-help-inline">判官以此为标准评判本条输出</span>
                      </div>
                      <textarea
                        v-model="goldenEdits[row.case_id]"
                        rows="2"
                        class="ui-textarea"
                        placeholder="留空则后续以预期行为为参考"
                      ></textarea>
                      <div class="rd-ref-actions">
                        <button
                          class="ui-btn sm"
                          :disabled="goldenSaving === row.case_id"
                          @click="saveGolden(row)"
                        >
                          {{ goldenSaving === row.case_id ? '保存中…' : '保存为金标准' }}
                        </button>
                        <span v-if="goldenMsg" class="ui-ok">{{ goldenMsg }}</span>
                        <span class="ui-help-inline">修改对后续跑批生效；当前记录判定不变，可点"重跑"验证。</span>
                      </div>
                    </div>

                    <div class="rd-grid2">
                      <div class="rd-block">
                        <strong>输入</strong>
                        <pre>{{ row.input }}</pre>
                      </div>
                      <div class="rd-block">
                        <strong>
                          输出
                          <span
                            v-if="rowVerdict(row) === 'fail'"
                            class="ui-badge error rd-fail-tag"
                          >
                            判官判定未通过
                          </span>
                        </strong>
                        <pre
                          :class="{ 'rd-output-fail': rowVerdict(row) === 'fail' }"
                        >{{ row.output || '（无输出）' }}</pre>
                        <p v-if="rowVerdict(row) === 'fail'" class="ui-help">
                          请对照上方"判定参考"检查输出：是否缺要点、答错内容或拒绝不合理。
                          若判官误判，可调整参考后点"重跑"。
                        </p>
                      </div>
                    </div>
                    <p v-if="row.error" class="ui-error">错误：{{ row.error }}</p>
                    <p v-if="Object.keys(row.judgments).length" class="ui-muted rd-judgments">
                      自动判定明细：{{ judgmentsText(row.judgments) }}
                    </p>
                    <div v-if="Object.keys(row.judge_reasons ?? {}).length" class="rd-reasons">
                      <div
                        v-for="(reason, cr) in row.judge_reasons"
                        :key="cr"
                        class="rd-reason"
                        :class="{ fail: row.judgments[cr] === 'fail' }"
                      >
                        <span
                          class="ui-badge"
                          :class="row.judgments[cr] === 'pass' ? 'ok' : row.judgments[cr] === 'fail' ? 'error' : 'warn'"
                        >
                          {{ cr }}
                        </span>
                        <span class="rd-reason-text">{{ reason }}</span>
                      </div>
                    </div>
                    <details v-if="(row.trace ?? []).length" class="rd-trace">
                      <summary>执行轨迹（{{ (row.trace ?? []).length }} 步）</summary>
                      <pre>{{ traceText(row.trace) }}</pre>
                    </details>
                    <div
                      v-if="(row.repeat_count ?? 1) > 1 && row.repeat_results?.length"
                      class="rd-attempts"
                    >
                      <div class="rd-attempts-head">
                        <strong>本次重复执行明细（{{ row.repeat_results.length }} 次）</strong>
                      </div>
                      <div
                        v-for="(att, ai) in row.repeat_results"
                        :key="ai"
                        class="rd-attempt"
                        :class="{ fail: att.verdict === 'fail' }"
                      >
                        <div class="rd-attempt-head">
                          <span class="rd-attempt-no">#{{ ai + 1 }}</span>
                          <span class="ui-badge" :class="verdictClass(att.verdict)">
                            {{ verdictLabel(att.verdict) }}
                          </span>
                          <span class="ui-muted">
                            {{ att.elapsed_ms }}ms · {{ att.tool_calls.join(', ') || '无工具' }}
                            <template v-if="att.error"> · {{ att.error }}</template>
                          </span>
                        </div>
                        <div
                          v-for="(reason, cr) in att.judge_reasons ?? {}"
                          :key="cr"
                          class="rd-attempt-reason"
                        >
                          <span class="ui-badge" :class="att.judgments[cr] === 'pass' ? 'ok' : att.judgments[cr] === 'fail' ? 'error' : 'warn'">
                            {{ cr }}
                          </span>
                          {{ reason }}
                        </div>
                        <details class="rd-attempt-output">
                          <summary>查看本次输出</summary>
                          <pre>{{ att.output || '（无输出）' }}</pre>
                        </details>
                        <details v-if="(att.trace ?? []).length" class="rd-trace">
                          <summary>执行轨迹</summary>
                          <pre>{{ traceText(att.trace) }}</pre>
                        </details>
                      </div>
                    </div>

                    <div class="rd-annotate">
                      <label>
                        答案正确
                        <select v-model="row.answer_correct">
                          <option value="">—</option>
                          <option value="对">对</option>
                          <option value="错">错</option>
                          <option value="存疑">存疑</option>
                        </select>
                      </label>
                      <label>
                        拒答合理
                        <select v-model="row.refusal">
                          <option value="">—</option>
                          <option value="合理">合理</option>
                          <option value="不合理">不合理</option>
                          <option value="不适用">不适用</option>
                        </select>
                      </label>
                      <label class="rd-note">
                        标注备注
                        <input v-model="row.annotate_note" placeholder="可选" />
                      </label>
                      <button class="ui-btn primary" :disabled="saving === row.case_id" @click="saveAnnotation(row)">
                        {{ saving === row.case_id ? '保存中…' : '保存标注' }}
                      </button>
                      <span v-if="saveMsg" class="ui-ok">{{ saveMsg }}</span>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!filteredCases.length">
              <td colspan="12" class="ui-muted">
                {{ data.cases.length ? '没有匹配筛选条件的用例' : '暂无结果（跑批进行中或刚启动）' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.rd {
  padding: 8px 0 24px;
}
.rd-head {
  margin-bottom: 10px;
}
.rd-title {
  font-size: 1.2em;
  margin: 0;
  flex: 1;
}
.rd-name-input {
  width: 280px;
  margin: 0 6px;
}
.rd-actions {
  display: flex;
  gap: 8px;
}
.rd-filters {
  margin-bottom: 10px;
}
.rd-filters .ui-input {
  width: 170px;
}
.rd-filter-count {
  margin-left: auto;
}
.rd-th-expand {
  width: 34px;
}
.rd-expand {
  width: 26px;
  height: 26px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: #57606a;
  cursor: pointer;
  font-size: 0.8em;
  line-height: 1;
  transition: transform 0.12s ease;
}
.rd-expand.on {
  transform: rotate(90deg);
  background: #eef7ff;
  border-color: #0969da;
  color: #0969da;
}
.rd-case-id {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.85em;
}
.rd-case-title {
  color: #57606a;
  font-size: 0.82em;
  margin-top: 2px;
}
.rd-judgments {
  margin: 8px 0 0;
}
.rd-reasons {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.rd-reason {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: #f6f8fa;
  border: 1px solid var(--ui-border);
  border-left: 3px solid var(--ui-border-2);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.84em;
}
.rd-reason.fail {
  background: #fff7f7;
  border-left-color: var(--ui-danger-fg);
}
.rd-reason-text {
  color: var(--ui-text);
  line-height: 1.5;
}
.rd-pass-rate {
  display: block;
  margin-top: 3px;
  font-size: 0.78em;
}
.rd-attempts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--ui-border);
}
.rd-attempts-head {
  font-size: 0.86em;
  color: var(--ui-text-2);
}
.rd-attempt {
  border: 1px solid var(--ui-border);
  border-left: 3px solid var(--ui-border-2);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fafbfc;
  font-size: 0.84em;
}
.rd-attempt.fail {
  border-left-color: var(--ui-danger-fg);
  background: #fff7f7;
}
.rd-attempt-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rd-attempt-no {
  font-family: Consolas, 'Courier New', monospace;
  color: var(--ui-text-2);
}
.rd-attempt-reason {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 6px;
  color: var(--ui-text);
  line-height: 1.5;
}
.rd-attempt-output {
  margin-top: 6px;
}
.rd-attempt-output summary {
  cursor: pointer;
  color: var(--ui-primary);
  font-size: 0.84em;
}
.rd-attempt-output pre {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 8px;
  font-size: 0.85em;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 160px;
  overflow-y: auto;
  margin: 6px 0 0;
}
.rd-detail-row td {
  background: #fafbfc;
  padding: 0;
}
.rd-detail {
  padding: 14px 16px;
}
.rd-ref {
  background: #fff;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 10px 12px;
  margin-bottom: 14px;
}
.rd-ref-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.rd-ref-head strong {
  font-size: 0.86em;
}
.rd-ref-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.rd-output-fail {
  border-color: #f0b7bc !important;
  background: #fff7f7 !important;
}
.rd-fail-tag {
  margin-left: 6px;
  vertical-align: middle;
}
.rd-grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.rd-block strong {
  font-size: 0.84em;
  color: #57606a;
}
.rd-block pre {
  background: #f6f8fa;
  border: 1px solid #eef1f4;
  border-radius: 8px;
  padding: 10px;
  font-size: 0.85em;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
  margin: 6px 0 0;
}
.rd-annotate {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e2e5e9;
}
.rd-annotate label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.82em;
  color: #57606a;
}
.rd-note {
  flex: 1;
  min-width: 180px;
}
.rd-note input {
  width: 100%;
}
.rd-annotate select {
  min-width: 110px;
}
@media (max-width: 900px) {
  .rd-grid2 {
    grid-template-columns: 1fr;
  }
}
</style>
