<script setup lang="ts">
import { ref } from 'vue'

import { streamChat, type SessionMode } from './api/client'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

const messages = ref<ChatMsg[]>([])
const input = ref('')
const mode = ref<SessionMode>('general')
const streaming = ref(false)

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  messages.value.push({ role: 'user', content: text })
  input.value = ''
  const reply: ChatMsg = { role: 'assistant', content: '' }
  messages.value.push(reply)
  streaming.value = true
  try {
    await streamChat(text, mode.value, (event) => {
      if (event.event === 'delta' && event.text) reply.content += event.text
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
    </header>

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
</style>
