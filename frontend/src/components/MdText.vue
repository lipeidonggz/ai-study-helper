<script setup lang="ts">
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ text?: string }>()
const raw = ref(false)

const hasText = computed(() => Boolean((props.text ?? '').trim()))
const html = computed(() => {
  const t = props.text ?? ''
  if (!t.trim()) return ''
  return DOMPurify.sanitize(marked.parse(t) as string)
})
</script>

<template>
  <div class="mdtext">
    <div v-if="hasText" class="mdtext-bar">
      <button type="button" class="mdtext-toggle" :class="{ on: !raw }" @click="raw = false">
        渲染
      </button>
      <button type="button" class="mdtext-toggle" :class="{ on: raw }" @click="raw = true">
        原文
      </button>
    </div>
    <div v-if="!raw && html" class="md-body" v-html="html"></div>
    <pre v-else-if="raw && text" class="mdtext-raw">{{ text }}</pre>
    <span v-else class="ui-muted">（无输出）</span>
  </div>
</template>

<style scoped>
.mdtext {
  margin-top: 6px;
}
.mdtext-bar {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-bottom: 4px;
}
.mdtext-toggle {
  border: 1px solid var(--ui-border-2, #d0d7de);
  background: #fff;
  color: var(--ui-text-2, #57606a);
  border-radius: 10px;
  padding: 1px 10px;
  font-size: 0.76em;
  cursor: pointer;
}
.mdtext-toggle.on {
  background: #0969da;
  border-color: #0969da;
  color: #fff;
}
.md-body,
.mdtext-raw {
  background: #f6f8fa;
  border: 1px solid #eef1f4;
  border-radius: 8px;
  padding: 10px;
  font-size: 0.85em;
  line-height: 1.6;
  color: #1f2328;
  max-height: 260px;
  overflow-y: auto;
  word-break: break-word;
}
.mdtext-raw {
  white-space: pre-wrap;
  margin: 0;
  font-family: Consolas, 'Courier New', monospace;
}
.md-body > :first-child {
  margin-top: 0;
}
.md-body > :last-child {
  margin-bottom: 0;
}
.md-body p {
  margin: 0.4em 0;
}
.md-body ul,
.md-body ol {
  margin: 0.4em 0;
  padding-left: 1.5em;
}
.md-body li {
  margin: 0.2em 0;
}
.md-body table {
  border-collapse: collapse;
  margin: 0.5em 0;
  width: 100%;
}
.md-body th,
.md-body td {
  border: 1px solid #d7dce1;
  padding: 4px 8px;
  text-align: left;
}
.md-body th {
  background: #eef1f4;
}
.md-body pre {
  background: #0d1117;
  color: #e6edf3;
  border-radius: 6px;
  padding: 8px 10px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.md-body code {
  font-family: Consolas, 'Courier New', monospace;
}
.md-body :not(pre) > code {
  background: #eaeef2;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.92em;
}
.md-body blockquote {
  margin: 0.5em 0;
  padding-left: 10px;
  border-left: 3px solid #d0d7de;
  color: #57606a;
}
.md-body a {
  color: #0969da;
  text-decoration: none;
}
.md-body hr {
  border: none;
  border-top: 1px solid #d7dce1;
  margin: 0.8em 0;
}
</style>
