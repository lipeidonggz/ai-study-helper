<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { kbApi, type KbChunk } from '../api/client'
import { navigate } from '../router'

const props = defineProps<{ sourceId: string }>()

const chunks = ref<KbChunk[]>([])
const loading = ref(true)
const error = ref('')
const maxTokens = ref(0)

onMounted(async () => {
  try {
    chunks.value = await kbApi.listChunks(props.sourceId)
    maxTokens.value = chunks.value.reduce((m, c) => Math.max(m, c.tokens), 0)
  } catch (err) {
    error.value = String(err)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="preview-page">
    <header class="preview-head">
      <h1>{{ sourceId }} · chunk 预览</h1>
      <button @click="navigate('#/kb')">← 返回知识库管理</button>
      <span class="hint">{{ chunks.length }} 块 · 最长 {{ maxTokens }} tokens</span>
    </header>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-else-if="loading" class="hint">加载中…</p>
    <section v-else class="card">
      <ol class="chunk-list">
        <li v-for="c in chunks" :key="c.id">
          <div class="chunk-head">
            <strong>{{ c.section_path }}</strong>
            <span class="badge" :class="{ over: c.tokens > 450 }">{{ c.tokens }} tokens</span>
          </div>
          <pre>{{ c.text }}</pre>
        </li>
      </ol>
    </section>
  </main>
</template>

<style scoped>
.preview-page {
  padding: 16px;
}
.preview-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.preview-head h1 {
  margin: 0 12px 0 0;
  font-size: 1.4em;
}
.preview-head button {
  padding: 5px 12px;
  border: 1px solid #ccc;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.card {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 12px;
}
.chunk-list {
  padding-left: 0;
  list-style: none;
  margin: 0;
}
.chunk-list li {
  border-bottom: 1px solid #f0f0f0;
  padding: 10px 0;
}
.chunk-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.chunk-list pre {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 10px;
  font-size: 0.85em;
  white-space: pre-wrap;
  overflow-x: auto;
  margin: 6px 0 0;
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
.badge.over {
  background: #ffeef0;
  color: #cf222e;
}
.hint {
  color: #888;
  font-size: 0.82em;
}
.err {
  color: #cf222e;
  background: #ffeef0;
  padding: 6px 10px;
  border-radius: 6px;
}
</style>
