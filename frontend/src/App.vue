<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  getLLMSettings,
  saveLLMSettings,
  streamChat,
  type SessionMode,
  type TraceStep
} from './api/client'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

const messages = ref<ChatMsg[]>([])
const input = ref('')
const mode = ref<SessionMode>('general')
const streaming = ref(false)

const showSettings = ref(false)
const settings = ref({ model: 'deepseek-chat', apiKey: '', masked: '', hasKey: false })
const saving = ref(false)
const saveMsg = ref('')

// —— 处理过程面板（展示 Agent 内部步骤） ——
const traceSteps = ref<TraceStep[]>([])
const showTrace = ref(true)
// —— 原始流开关：默认关闭，打开后显示每个原始 chunk ——
const showRaw = ref(false)
const visibleTraceSteps = computed(() =>
  showRaw.value ? traceSteps.value : traceSteps.value.filter((s) => s.type !== 'raw_chunk')
)
const rawCount = computed(() => traceSteps.value.filter((s) => s.type === 'raw_chunk').length)

onMounted(async () => {
  try {
    await loadSettings()
  } catch {
    saveMsg.value = '设置读取失败'
  }
})

async function loadSettings() {
  const s = await getLLMSettings()
  settings.value = { model: s.model, apiKey: '', masked: s.api_key_masked, hasKey: s.has_key }
}

async function saveSettings() {
  saving.value = true
  saveMsg.value = ''
  try {
    const s = await saveLLMSettings({
      model: settings.value.model,
      apiKey: settings.value.apiKey
    })
    settings.value.masked = s.api_key_masked
    settings.value.hasKey = s.has_key
    settings.value.apiKey = ''
    saveMsg.value = '已保存'
  } catch (err) {
    saveMsg.value = `保存失败：${err}`
  } finally {
    saving.value = false
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  traceSteps.value = [] // 新请求开始：清空上一次的处理过程
  messages.value.push({ role: 'user', content: text })
  input.value = ''
  messages.value.push({ role: 'assistant', content: '' })
  // 关键：从响应式数组中取回代理对象，后续 content 拼接才会触发渲染
  const reply = messages.value[messages.value.length - 1]
  streaming.value = true
  try {
    await streamChat(text, mode.value, (event) => {
      if (event.event === 'delta' && event.text) reply.content += event.text
      else if (event.event === 'error' && event.message) reply.content = event.message
      else if (event.event === 'trace' && event.trace) traceSteps.value.push(event.trace)
    })
  } catch (err) {
    reply.content = `（请求失败：${err}）`
  } finally {
    streaming.value = false
  }
}
</script>

<template>
  <main class="page">
    <header class="bar">
      <h1>AI 助手</h1>
      <select v-model="mode" :disabled="streaming">
        <option value="general">通用模式</option>
        <option value="kb_priority">知识库优先</option>
        <option value="tool_enhanced">工具增强</option>
      </select>
      <button class="link-btn" @click="showSettings = !showSettings">
        {{ showSettings ? '收起' : '设置' }}
      </button>
    </header>

    <div v-if="showSettings" class="settings">
      <label>
        模型
        <select v-model="settings.model">
          <option value="deepseek-chat">deepseek-chat</option>
          <option value="deepseek-reasoner">deepseek-reasoner</option>
        </select>
      </label>
      <label>
        API Key
        <input
          v-model="settings.apiKey"
          type="password"
          :placeholder="settings.hasKey ? '已配置，留空保持不变' : 'sk-...'"
        />
      </label>
      <button :disabled="saving" @click="saveSettings">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <span class="hint">
        {{ settings.hasKey ? `当前：${settings.masked}` : '未配置' }} {{ saveMsg }}
      </span>
    </div>

    <section class="chat">
      <div v-for="(msg, i) in messages" :key="i" class="msg" :class="msg.role">
        <pre>{{ msg.content }}</pre>
      </div>
      <p v-if="streaming" class="hint">正在生成…</p>
    </section>

    <section class="trace">
      <div class="trace-head">
        <strong>处理过程</strong>
        <label class="trace-toggle">
          <input v-model="showRaw" type="checkbox" /> 原始流
        </label>
        <span class="hint">
          {{ visibleTraceSteps.length }} 个步骤
          <template v-if="!showRaw && rawCount">（+{{ rawCount }} 原始流）</template>
        </span>
        <button class="link-btn" @click="showTrace = !showTrace">
          {{ showTrace ? '收起' : '展开' }}
        </button>
      </div>
      <ol v-if="showTrace" class="trace-list">
        <li v-for="step in visibleTraceSteps" :key="step.seq">
          <span class="trace-badge" :class="`type-${step.type}`">{{ step.type }}</span>
          <span class="trace-ms">{{ step.elapsed_ms }}ms</span>
          <details open>
            <summary>详情</summary>
            <pre>{{ JSON.stringify(step.data, null, 2) }}</pre>
          </details>
        </li>
        <li v-if="!traceSteps.length" class="trace-empty">发送消息后，这里会逐步展示内部处理过程</li>
      </ol>
    </section>

    <footer class="bar">
      <input
        v-model="input"
        placeholder="输入消息，回车发送"
        :disabled="streaming"
        @keyup.enter="send"
      />
      <button :disabled="streaming" @click="send">发送</button>
    </footer>
  </main>
</template>

<style>
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: #f5f6f8;
}
.page {
  max-width: 760px;
  margin: 0 auto;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 16px;
}
.bar {
  display: flex;
  gap: 12px;
  align-items: center;
}
.bar h1 {
  font-size: 1.2em;
  margin: 0;
}
.chat {
  flex: 1;
  overflow-y: auto;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  margin: 12px 0;
}
.msg {
  margin-bottom: 10px;
}
.msg pre {
  white-space: pre-wrap;
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: #eef2f7;
}
.msg.user pre {
  background: #dbeafe;
}
.hint {
  color: #888;
  font-size: 0.85em;
}
footer.bar input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #ccc;
  border-radius: 6px;
}
footer.bar button {
  padding: 8px 18px;
  border: none;
  border-radius: 6px;
  background: #0969da;
  color: #fff;
  cursor: pointer;
}
footer.bar button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.link-btn {
  margin-left: auto;
  padding: 6px 14px;
  border: 1px solid #ccc;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.settings {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 12px;
}
.settings label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.9em;
}
.settings input,
.settings select {
  padding: 6px 10px;
  border: 1px solid #ccc;
  border-radius: 6px;
}
.settings button {
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  background: #0969da;
  color: #fff;
  cursor: pointer;
}
.trace {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.trace-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.trace-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85em;
  color: #57606a;
  cursor: pointer;
}
.trace-badge.type-raw_chunk {
  background: #e6edf3;
  color: #57606a;
  font-family: Consolas, 'Courier New', monospace;
}
.trace-badge.type-usage {
  background: #fef2e0;
  color: #9a6700;
}
.trace-list {
  margin: 10px 0 0;
  padding-left: 0;
  list-style: none;
  max-height: 320px;
  overflow-y: auto;
  border-top: 1px solid #eee;
}
.trace-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid #f0f0f0;
}
.trace-badge {
  font-size: 0.78em;
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef2f7;
  color: #444;
  white-space: nowrap;
}
.trace-badge.type-tool_exec,
.trace-badge.type-tool_call {
  background: #dbeafe;
  color: #0969da;
}
.trace-badge.type-error {
  background: #ffeef0;
  color: #cf222e;
}
.trace-badge.type-done {
  background: #dafbe1;
  color: #1a7f37;
}
.trace-ms {
  font-size: 0.8em;
  color: #888;
}
.trace-list details {
  flex: 1;
}
.trace-list summary {
  cursor: pointer;
  font-size: 0.85em;
  color: #57606a;
}
.trace-list pre {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 8px;
  font-size: 0.8em;
  overflow-x: auto;
  white-space: pre-wrap;
}
.trace-empty {
  color: #999;
  font-size: 0.85em;
}
</style>
