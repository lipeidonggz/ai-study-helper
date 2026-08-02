<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  getLLMSettings,
  saveLLMSettings,
  streamChat,
  type SessionMode
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
</style>
