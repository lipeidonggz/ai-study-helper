<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { compileApi, type CompileClaim, type CompileStatus, type HealthReport } from '../api/client'
import { navigate } from '../router'

const props = defineProps<{ sourceId: string }>()

const status = ref<CompileStatus | null>(null)
const report = ref<HealthReport | null>(null)
const claims = ref<CompileClaim[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
let timer = 0

const pct = (v: number) => `${(v * 100).toFixed(1)}%`

async function loadStatus() {
  status.value = await compileApi.status(props.sourceId)
}

async function loadAll() {
  await loadStatus()
  // 红线不过时任务判 failed，但产物仍在——报告要能看（上方会亮失败横幅）
  if (status.value?.status === 'done' || status.value?.status === 'failed') {
    report.value = await compileApi.report(props.sourceId)
    claims.value = await compileApi.claims(props.sourceId) // limit=0 → 全部
  }
}

function poll() {
  window.clearInterval(timer)
  timer = window.setInterval(async () => {
    try {
      await loadStatus()
      if (status.value?.status !== 'running') {
        window.clearInterval(timer)
        busy.value = false
        await loadAll()   // 成功 / 红线不过 都重新载入报告；真失败（异常）由下面的 catch 接
      }
    } catch (err) {
      window.clearInterval(timer)
      busy.value = false
      error.value = String(err)
    }
  }, 2500)
}

async function reExtract() {
  if (!confirm(`重新抽取 ${props.sourceId}？（会覆盖现有产物）`)) return
  busy.value = true
  error.value = ''
  report.value = null
  claims.value = []
  try {
    status.value = await compileApi.extract(props.sourceId)
    poll()
  } catch (err) {
    busy.value = false
    error.value = String(err)
  }
}

const marked = computed(() => report.value?.marked || [])
const dropped = computed(() => report.value?.dropped || [])
const pending = computed(() => report.value?.pending || [])

/** 失败横幅：区分"体检红线不过（产物是本轮的，可看）"与"任务异常失败（报告可能是上一次的）" */
const failBanner = computed(() => {
  const s = status.value
  if (!s || s.status !== 'failed' || !s.error) return ''
  return s.error.startsWith('体检红线未通过')
    ? `⛔ ${s.error}`
    : `⛔ 本次任务失败：${s.error}（下方报告若有，可能来自上一次成功运行）`
})

onMounted(async () => {
  try {
    await loadAll()
    if (status.value?.status === 'running') {
      busy.value = true
      poll()
    }
  } catch (err) {
    error.value = String(err)
  } finally {
    loading.value = false
  }
})

onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <main class="cr-page">
    <header class="cr-head">
      <h1>{{ sourceId }} · 编译体检报告</h1>
      <span class="badge" :class="status?.status === 'done' ? 'ok' : status?.status === 'running' ? 'run' : status?.status === 'failed' ? 'bad' : 'idle'">
        {{ status?.status === 'done' ? '已抽取' : status?.status === 'running' ? '抽取中' : status?.status === 'failed' ? '抽取失败' : '未抽取' }}
      </span>
      <span v-if="status?.progress" class="hint">{{ status.progress }}</span>
      <button :disabled="busy" @click="reExtract">{{ status?.status === 'done' ? '重抽' : '开始抽取' }}</button>
      <button :disabled="busy" @click="loadAll">刷新</button>
      <button @click="navigate('#/kb')">← 返回知识库管理</button>
    </header>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-else-if="loading" class="hint">加载中…</p>
    <p v-else-if="status?.status === 'running'" class="hint">抽取中…（{{ status.progress }}）</p>

    <template v-else-if="report">
      <p v-if="failBanner" class="err">{{ failBanner }}</p>
      <p class="cr-summary">
        claim {{ report.claims_in }} 条 → 保留 <strong>{{ report.claims_out }}</strong> 条
        （丢弃 {{ dropped.length }}、改写 {{ report.rewritten.length }}、标记 {{ marked.length }}）
        ｜ 归一：mapped {{ report.predicate_status?.mapped ?? '—' }} · forced {{ report.predicate_status?.forced ?? '—' }}
        · <strong>待定 {{ report.predicate_status?.pending ?? 0 }}</strong>
        ｜ 谓词清洗：{{ report.predicate_clean?.cleaned ?? 0 }} / 目标 {{ report.predicate_clean?.targets ?? 0 }}
        ｜ 文档主体「{{ report.doc_subject }}」
        ｜ 召回：{{
          report.recall?.executed
            ? `${report.recall.must_hit}/${report.recall.must_total} must`
            : report.recall?.note
        }}
      </p>
      <p class="cr-verdict" :class="report.red_line_pass ? 'ok' : 'bad'">
        红线总判定：{{ report.red_line_pass ? '通过 ✅' : '不过 ❌（该篇编译失败，需人工介入）' }}
      </p>

      <section class="card">
        <h2>指标</h2>
        <table class="tbl">
          <thead>
            <tr><th>指标名称</th><th>取值</th><th>阈值</th><th>红线</th><th>动作</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in report.check_spec" :key="s.key">
              <td><strong>{{ s.name }}</strong><br /><code>{{ s.key }}</code></td>
              <td :class="s.pass ? 'ok-txt' : 'bad-txt'">{{ pct(s.value) }}</td>
              <td>{{ pct(s.threshold) }}</td>
              <td>{{ s.red_line ? '✅ 是' : '—' }}</td>
              <td>{{ s.action }}</td>
              <td class="note">{{ s.note }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h2>丢弃清单（{{ dropped.length }} 条）</h2>
        <p class="hint">丢弃 = 这条 claim 不可用（引文定位不到 / 主语是从句式），不再进入下一步。</p>
        <p v-if="!dropped.length" class="hint">（无）</p>
        <table v-else class="tbl">
          <thead>
            <tr><th>#</th><th>主语</th><th>谓词（原文）</th><th>归一化谓词</th><th>sign/极性</th><th>宾语</th><th>原因</th><th>锚定原文</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in dropped" :key="'d' + d.idx">
              <td>{{ d.idx }}</td>
              <td>{{ d.subject }}</td>
              <td>{{ d.predicate }}</td>
              <td><code>{{ d.predicate_normalized || '—（已丢弃，未归一）' }}</code></td>
              <td>{{ [d.sign, d.polarity].filter(Boolean).join(' / ') || '' }}</td>
              <td>{{ d.object || '' }}</td>
              <td><span class="badge bad">{{ d.reasons.join('；') }}</span></td>
              <td class="note">{{ d.anchor }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h2>待定清单（{{ pending.length }} 条）</h2>
        <p class="hint">
          待定 = 谓词<strong>归不进受控关系</strong>（表里确实没有对应概念）→ <strong>不生成边、不进图</strong>；
          内容仍在 chunk 里，由向量检索兜底。这些词会累积到 <code>backend/data/compile/_outside_words.json</code>，
          供定期收敛（补进关系表 或 确认丢弃）。若只是模型判错（表里有对应却造了新词），会被强制归入最接近的关系，不在此列。
        </p>
        <p v-if="!pending.length" class="hint">（无）</p>
        <table v-else class="tbl">
          <thead>
            <tr><th>#</th><th>主语</th><th>谓词（原文）</th><th>模型造的词</th><th>宾语</th><th>锚定原文</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in pending" :key="'p' + p.idx">
              <td>{{ p.idx }}</td>
              <td>{{ p.subject }}</td>
              <td>{{ p.predicate }}</td>
              <td><span class="badge bad">{{ p.invented }}</span></td>
              <td>{{ p.object || '' }}</td>
              <td class="note">{{ p.anchor }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h2>标记清单（{{ marked.length }} 条）</h2>
        <p class="hint">标记 = 仍进入下一步，但需要处理：<strong>谓词需清洗</strong>（交归一步剥修饰）、<strong>否定存疑</strong>、<strong>主语不在锚句</strong>（靠邻句）。</p>
        <p v-if="!marked.length" class="hint">（无）</p>
        <table v-else class="tbl">
          <thead>
            <tr><th>#</th><th>主语</th><th>谓词（原文）</th><th>归一化谓词</th><th>sign/极性</th><th>宾语</th><th>标记</th><th>锚定原文</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in marked" :key="'m' + m.idx">
              <td>{{ m.idx }}</td>
              <td>{{ m.subject }}</td>
              <td>{{ m.predicate }}</td>
              <td><code>{{ m.predicate_normalized || '—' }}</code></td>
              <td>{{ [m.sign, m.polarity].filter(Boolean).join(' / ') || '' }}</td>
              <td>{{ m.object || '' }}</td>
              <td><span class="badge run">{{ (m.marks || []).join('；') }}</span></td>
              <td class="note">{{ m.anchor }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h2>断言清单（{{ claims.length }} 条 · 全部）</h2>
        <p class="hint">每行 = 一条原子断言。谓词列是原文用词；归一化谓词=映射到受控关系表后的结果（<code>app/compile/relations.py</code>，38 条）。</p>
        <table class="tbl">
          <thead>
            <tr><th>#</th><th>主语</th><th>谓词（原文）</th><th>归一化谓词</th><th>sign/极性</th><th>宾语</th><th>标记</th><th>锚定原文</th></tr>
          </thead>
          <tbody>
            <tr v-for="(c, i) in claims" :key="'c' + i">
              <td>{{ i }}</td>
              <td>{{ c.subject }}</td>
              <td>
                {{ c.predicate }}
                <span v-if="c.predicate_clean" class="badge ok" :title="'清洗后用于归一：' + c.predicate_clean">⤳ {{ c.predicate_clean }}</span>
              </td>
              <td>
                <code>{{ c.predicate_normalized || '—' }}</code>
                <span v-if="c.predicate_status === 'forced'" class="badge run" :title="'模型造词：' + (c.predicate_invented || '')">强归</span>
                <span v-else-if="c.predicate_status === 'pending'" class="badge bad" :title="'待定（不进图）：' + (c.predicate_invented || '')">待定</span>
              </td>
              <td>{{ [c.sign, c.polarity].filter(Boolean).join(' / ') || '' }}</td>
              <td>{{ c.object || '' }}</td>
              <td><span v-if="c.marks.length" class="badge run">{{ c.marks.join('；') }}</span></td>
              <td class="note">{{ c.anchor }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <p v-else class="hint">还没有抽取产物。点"开始抽取"。</p>
  </main>
</template>

<style scoped>
.cr-page {
  padding: 16px;
  font-size: 14px;
}
.cr-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.cr-head h1 {
  margin: 0 8px 0 0;
  font-size: 1.4em;
}
.cr-head button {
  padding: 5px 12px;
  border: 1px solid #ccc;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.cr-head button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.cr-summary {
  margin: 6px 0;
}
.cr-verdict {
  padding: 6px 10px;
  border-radius: 6px;
  font-weight: 600;
  margin: 6px 0 12px;
}
.cr-verdict.ok {
  background: #dafbe1;
  color: #1a7f37;
}
.cr-verdict.bad {
  background: #ffeef0;
  color: #cf222e;
}
.card {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 12px;
}
.card h2 {
  font-size: 1.05em;
  margin: 0 0 6px;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86em;
}
.tbl th,
.tbl td {
  border: 1px solid #eee;
  padding: 5px 7px;
  text-align: left;
  vertical-align: top;
}
.tbl th {
  background: #f6f8fa;
  white-space: nowrap;
}
.tbl .note {
  color: #57606a;
  max-width: 380px;
}
.tbl code {
  background: #f6f8fa;
  padding: 1px 4px;
  border-radius: 4px;
}
.badge {
  display: inline-block;
  font-size: 0.8em;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.badge.ok {
  background: #dafbe1;
  color: #1a7f37;
}
.badge.bad {
  background: #ffeef0;
  color: #cf222e;
}
.badge.run {
  background: #fff8c5;
  color: #9a6700;
}
.badge.idle {
  background: #eef2f7;
  color: #57606a;
}
.ok-txt {
  color: #1a7f37;
  font-weight: 600;
}
.bad-txt {
  color: #cf222e;
  font-weight: 600;
}
.hint {
  color: #888;
  font-size: 0.86em;
}
.err {
  color: #cf222e;
}
</style>
