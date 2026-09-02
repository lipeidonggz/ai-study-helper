<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { kbApi, type KbDocument, type KbSearchHit } from '../api/client'

const docs = ref<KbDocument[]>([])
const loading = ref(false)
const busyIds = ref<Set<string>>(new Set())
const msg = ref('')

// 检索调试台
const showSearch = ref(false)
const query = ref('')
const topK = ref(8)
const sourceFilter = ref('')
const searching = ref(false)
const hits = ref<KbSearchHit[]>([])

// 筛选下拉框
const categoryFilter = ref('全部')
const statusFilter = ref('全部')
const categories = computed(() => ['全部', ...new Set(docs.value.map((d) => d.category))])
const statuses = ['全部', '未入库', 'ready', 'indexing', 'failed']
const filteredDocs = computed(() =>
  docs.value.filter(
    (d) =>
      (categoryFilter.value === '全部' || d.category === categoryFilter.value) &&
      (statusFilter.value === '全部' || d.status === statusFilter.value)
  )
)

const statusColor = (s: string): string => {
  if (s === 'ready') return 'ok'
  if (s === 'failed') return 'bad'
  if (s === 'indexing') return 'run'
  return 'idle'
}

async function load() {
  loading.value = true
  try {
    docs.value = await kbApi.listDocuments()
  } catch (err) {
    msg.value = `加载失败：${err}`
  } finally {
    loading.value = false
  }
}

function pollWhileIndexing() {
  const timer = window.setInterval(async () => {
    try {
      docs.value = await kbApi.listDocuments()
      if (!docs.value.some((d) => d.status === 'indexing')) {
        window.clearInterval(timer)
        msg.value = '入库完成'
      }
    } catch {
      window.clearInterval(timer)
    }
  }, 2000)
}

async function indexOne(id: string) {
  busyIds.value = new Set([...busyIds.value, id])
  msg.value = ''
  try {
    const r = await kbApi.indexDocument(id)
    msg.value = `${id} 入库完成：${r.chunk_count} 块`
    await load()
  } catch (err) {
    msg.value = `${id} 入库失败：${err}`
    await load()
  } finally {
    busyIds.value = new Set([...busyIds.value].filter((x) => x !== id))
  }
}

async function indexAll() {
  msg.value = ''
  try {
    const r = await kbApi.indexAll()
    msg.value = `已提交 ${r.accepted} 篇全量入库，后台执行中`
    pollWhileIndexing()
  } catch (err) {
    msg.value = `全量入库失败：${err}`
  }
}

async function remove(id: string) {
  if (!confirm(`确定删除 ${id} 的入库结果？（素材文件不动）`)) return
  try {
    await kbApi.deleteDocument(id)
    msg.value = `${id} 已删除`
    await load()
  } catch (err) {
    msg.value = `删除失败：${err}`
  }
}

function openChunks(id: string) {
  window.open('#/kb/chunks/' + id, '_blank')
}

async function runSearch() {
  if (!query.value.trim()) return
  searching.value = true
  msg.value = ''
  try {
    const filters: Record<string, unknown> = {}
    const ids = sourceFilter.value
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (ids.length) filters.source_id = ids
    hits.value = await kbApi.search({ query: query.value, top_k: topK.value, filters })
  } catch (err) {
    msg.value = `检索失败：${err}`
  } finally {
    searching.value = false
  }
}

const totalChunks = computed(() => docs.value.reduce((n, d) => n + d.chunk_count, 0))

onMounted(load)
</script>

<template>
  <main class="kb-page">
    <header class="kb-head">
      <h1>知识库管理</h1>
      <span class="hint">已入库 {{ totalChunks }} 块</span>
      <button :disabled="loading" @click="load">刷新</button>
      <button :disabled="loading" @click="indexAll">全量入库</button>
      <button @click="showSearch = !showSearch">
        {{ showSearch ? '收起检索调试' : '检索调试台' }}
      </button>
    </header>
    <p v-if="msg" class="kb-msg">{{ msg }}</p>

    <section v-if="showSearch" class="card kb-search">
      <h2>检索调试台（当前为稠密检索，混合检索待问答侧接入）</h2>
      <div class="row">
        <input v-model="query" class="grow" placeholder="输入 query，回车检索" @keyup.enter="runSearch" />
        <label>top-k <input v-model.number="topK" type="number" min="1" max="20" style="width: 64px" /></label>
        <label>来源过滤（逗号分隔）<input v-model="sourceFilter" placeholder="O2,A5" /></label>
        <button :disabled="searching" @click="runSearch">检索</button>
      </div>
      <ol v-if="hits.length" class="hit-list">
        <li v-for="h in hits" :key="h.source_id + h.section_path + h.score">
          <span class="badge score">{{ h.score.toFixed(4) }}</span>
          <span class="badge">{{ h.source_id }}</span>
          <span class="hint">{{ h.decay_class }}</span>
          <strong>{{ h.section_path }}</strong>
          <p>{{ h.text }}</p>
        </li>
      </ol>
      <p v-else-if="searching" class="hint">检索中…</p>
    </section>

    <section class="card">
      <div class="filters">
        <label>
          类别
          <select v-model="categoryFilter">
            <option v-for="c in categories" :key="c">{{ c }}</option>
          </select>
        </label>
        <label>
          入库状态
          <select v-model="statusFilter">
            <option v-for="s in statuses" :key="s">{{ s }}</option>
          </select>
        </label>
        <span class="hint">显示 {{ filteredDocs.length }} / {{ docs.length }} 条</span>
      </div>
      <table class="kb-table">
        <thead>
          <tr>
            <th>编号</th><th>名称</th><th>类别</th><th>载体</th><th>采集</th>
            <th>入库状态</th><th>chunk 数</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in filteredDocs" :key="d.source_id">
            <td>{{ d.source_id }}</td>
            <td class="name" :title="d.error">{{ d.name }}</td>
            <td>{{ d.category }}</td>
            <td>{{ d.carrier }}</td>
            <td>{{ d.collected ? '✅' : '⬜' }}</td>
            <td>
              <span class="badge" :class="statusColor(d.status)">{{ d.status }}</span>
              <span v-if="d.indexed_at" class="hint"> {{ d.indexed_at }}</span>
            </td>
            <td>{{ d.chunk_count || '' }}</td>
            <td class="ops">
              <button :disabled="!d.collected || busyIds.has(d.source_id)" @click="indexOne(d.source_id)">
                {{ d.status === 'ready' ? '重入库' : '入库' }}
              </button>
              <button :disabled="d.status !== 'ready'" @click="openChunks(d.source_id)">chunks</button>
              <button :disabled="d.status !== 'ready'" @click="remove(d.source_id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

  </main>
</template>

<style scoped>
.kb-page {
  padding: 16px;
}
.kb-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.kb-head h1 {
  margin: 0 12px 0 0;
  font-size: 1.4em;
}
.kb-head button,
.kb-search button,
.ops button {
  padding: 5px 12px;
  border: 1px solid #ccc;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.kb-head button:disabled,
.ops button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.kb-msg {
  color: #9a6700;
  background: #fff8c5;
  padding: 6px 10px;
  border-radius: 6px;
}
.filters {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.filters select {
  padding: 5px 8px;
  border: 1px solid #ccc;
  border-radius: 6px;
  margin-left: 6px;
}
.card {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 12px;
}
.kb-search .row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.kb-search input {
  padding: 6px 10px;
  border: 1px solid #ccc;
  border-radius: 6px;
}
.grow {
  flex: 1;
  min-width: 240px;
}
.kb-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
}
.kb-table th,
.kb-table td {
  border-bottom: 1px solid #eee;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
.kb-table .name {
  max-width: 360px;
}
.ops {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.badge {
  display: inline-block;
  font-size: 0.78em;
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef2f7;
  color: #444;
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
.badge.score {
  background: #dbeafe;
  color: #0969da;
}
.hint {
  color: #888;
  font-size: 0.82em;
}
.hit-list {
  padding-left: 0;
  list-style: none;
}
.hit-list li {
  border-bottom: 1px solid #f0f0f0;
  padding: 8px 0;
}
.hit-list pre {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 8px;
  font-size: 0.82em;
  white-space: pre-wrap;
  margin: 6px 0 0;
}
</style>
