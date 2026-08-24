<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

import { evalApi, type EvalRun, type EvalRunCase } from '../api/client'

const props = defineProps<{ runId: number }>()

const data = ref<{ run: EvalRun; cases: EvalRunCase[]; active: boolean } | null>(null)
const error = ref('')
const saving = ref<string>('') // 正在保存标注的 case_id
const saveMsg = ref('')
const adopting = ref<string>('') // 正在沉淀金标准的 case_id
const adoptMsg = ref('')
const expanded = ref<Set<string>>(new Set())
let pollTimer: number | undefined

const allExpanded = ref(false)

async function refresh() {
  try {
    data.value = await evalApi.getRun(props.runId)
    stopPolling()
    if (data.value.active) {
      pollTimer = window.setInterval(refresh, 1500)
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
  }
  expanded.value = next
  allExpanded.value =
    !!data.value && data.value.cases.length > 0 && next.size === data.value.cases.length
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

async function adoptAnnotation(row: EvalRunCase) {
  if (!data.value) return
  adopting.value = row.case_id
  adoptMsg.value = ''
  try {
    await evalApi.adoptAnnotation(row.case_id, {
      answer_correct: row.answer_correct,
      refusal: row.refusal,
      note: row.annotate_note
    })
    adoptMsg.value = `已沉淀到用例：${row.case_id}`
  } catch (err) {
    error.value = `沉淀失败：${err}`
  } finally {
    adopting.value = ''
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
    <header class="rd-bar">
      <a class="rd-back" href="#/eval">← 返回评测台</a>
      <h1 v-if="data" class="rd-title">
        Run #{{ data.run.id }} · {{ data.run.name }}
        <span class="rd-badge" :class="data.run.status">{{ statusLabel(data.run.status) }}</span>
      </h1>
      <div class="rd-actions">
        <button v-if="data?.active" class="rd-btn danger" @click="cancelCurrent">取消跑批</button>
        <a v-if="data" class="rd-btn link" :href="evalApi.exportUrl(data.run.id)" target="_blank">
          导出 JSON
        </a>
      </div>
    </header>

    <p v-if="error" class="rd-error">{{ error }}</p>

    <template v-if="data">
      <p v-if="data.run.error" class="rd-error">错误：{{ data.run.error }}</p>
      <p class="rd-hint">
        进度 {{ data.run.progress }}/{{ data.run.total }} · 汇总：{{ summaryText(data.run) }}
      </p>

      <div class="rd-card">
        <div class="rd-toolbar">
          <span class="rd-count">{{ data.cases.length }} 条用例</span>
          <button class="rd-btn" @click="toggleAll">
            {{ allExpanded ? '全部收起' : '全部展开' }}
          </button>
        </div>

        <table class="rd-table">
          <thead>
            <tr>
              <th class="rd-th-expand"></th>
              <th>用例</th>
              <th>类别</th>
              <th>状态</th>
              <th>耗时</th>
              <th>轮数</th>
              <th>工具调用</th>
              <th>自动判定</th>
              <th>答案正确</th>
              <th>拒答合理</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in data.cases" :key="row.case_id">
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
                <td>
                  <div class="rd-case-id">{{ row.case_id }}</div>
                  <div class="rd-case-title">{{ row.title }}</div>
                </td>
                <td>{{ row.category }}</td>
                <td>
                  <span class="rd-badge" :class="row.status">{{ statusLabel(row.status) }}</span>
                </td>
                <td class="rd-mono">{{ row.elapsed_ms }}ms</td>
                <td>{{ row.rounds }}</td>
                <td class="rd-mono">{{ row.tool_calls.join(', ') || '—' }}</td>
                <td class="rd-mono">{{ judgmentsText(row.judgments) || '—' }}</td>
                <td>
                  <span v-if="row.answer_correct" class="rd-badge" :class="row.answer_correct === '对' ? 'ok' : row.answer_correct === '错' ? 'error' : 'warn'">
                    {{ row.answer_correct }}
                  </span>
                  <span v-else class="rd-muted">—</span>
                </td>
                <td>
                  <span v-if="row.refusal" class="rd-badge" :class="row.refusal === '合理' ? 'ok' : row.refusal === '不合理' ? 'error' : 'warn'">
                    {{ row.refusal }}
                  </span>
                  <span v-else class="rd-muted">—</span>
                </td>
              </tr>
              <tr v-if="expanded.has(row.case_id)" class="rd-detail-row">
                <td colspan="10">
                  <div class="rd-detail">
                    <div class="rd-grid2">
                      <div class="rd-block">
                        <strong>输入</strong>
                        <pre>{{ row.input }}</pre>
                      </div>
                      <div class="rd-block">
                        <strong>输出</strong>
                        <pre>{{ row.output || '（无输出）' }}</pre>
                      </div>
                    </div>
                    <p v-if="row.error" class="rd-error">错误：{{ row.error }}</p>

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
                      <button class="rd-btn primary" :disabled="saving === row.case_id" @click="saveAnnotation(row)">
                        {{ saving === row.case_id ? '保存中…' : '保存标注' }}
                      </button>
                      <span v-if="saveMsg" class="rd-ok">{{ saveMsg }}</span>
                      <button
                        class="rd-btn"
                        :disabled="adopting === row.case_id || !row.answer_correct && !row.refusal"
                        title="把本次标注结论写入用例文件，成为后续跑批的金标准"
                        @click="adoptAnnotation(row)"
                      >
                        {{ adopting === row.case_id ? '沉淀中…' : '沉淀为金标准' }}
                      </button>
                      <span v-if="adoptMsg" class="rd-ok">{{ adoptMsg }}</span>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!data.cases.length">
              <td colspan="10" class="rd-muted">暂无结果（跑批进行中或刚启动）</td>
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
.rd-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.rd-back {
  color: #0969da;
  text-decoration: none;
  font-size: 0.92em;
  white-space: nowrap;
}
.rd-back:hover {
  text-decoration: underline;
}
.rd-title {
  font-size: 1.2em;
  margin: 0;
  flex: 1;
}
.rd-actions {
  display: flex;
  gap: 8px;
}
.rd-card {
  background: #fff;
  border: 1px solid #e2e5e9;
  border-radius: 10px;
  padding: 12px 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.rd-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.rd-count {
  color: #57606a;
  font-size: 0.88em;
}
.rd-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
}
.rd-table th,
.rd-table td {
  border-bottom: 1px solid #eef1f4;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
.rd-table th {
  background: #f6f8fa;
  font-weight: 600;
  white-space: nowrap;
  position: sticky;
  top: 0;
}
.rd-th-expand {
  width: 34px;
}
.rd-row:hover td {
  background: #f8fafc;
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
.rd-badge {
  display: inline-block;
  font-size: 0.76em;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.rd-badge.done,
.rd-badge.ok {
  background: #dafbe1;
  color: #1a7f37;
}
.rd-badge.error,
.rd-badge.timeout {
  background: #ffeef0;
  color: #cf222e;
}
.rd-badge.running,
.rd-badge.queued {
  background: #ddf4ff;
  color: #0969da;
}
.rd-badge.canceled {
  background: #e6edf3;
  color: #57606a;
}
.rd-badge.warn {
  background: #fff8c5;
  color: #9a6700;
}
.rd-mono {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.82em;
}
.rd-muted {
  color: #888;
  font-size: 0.85em;
}
.rd-hint {
  color: #57606a;
  font-size: 0.88em;
  margin: 0 0 10px;
}
.rd-error {
  color: #cf222e;
  font-size: 0.88em;
}
.rd-ok {
  color: #1a7f37;
  font-size: 0.85em;
}
.rd-btn {
  padding: 6px 14px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: #24292f;
  cursor: pointer;
  font-size: 0.88em;
  text-decoration: none;
  display: inline-block;
}
.rd-btn:hover {
  background: #f6f8fa;
}
.rd-btn.primary {
  background: #0969da;
  border-color: #0969da;
  color: #fff;
}
.rd-btn.primary:hover {
  background: #0a5fc2;
}
.rd-btn.primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.rd-btn.danger {
  color: #cf222e;
}
.rd-btn.link {
  color: #0969da;
}
.rd-detail-row td {
  background: #fafbfc;
  padding: 0;
}
.rd-detail {
  padding: 14px 16px;
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
.rd-annotate select,
.rd-annotate input {
  padding: 5px 8px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  font-size: 0.92em;
  min-width: 110px;
}
.rd-note {
  flex: 1;
  min-width: 180px;
}
.rd-note input {
  width: 100%;
}
@media (max-width: 900px) {
  .rd-grid2 {
    grid-template-columns: 1fr;
  }
}
</style>
