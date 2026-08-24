<script setup lang="ts">
import EvalConsole from './EvalConsole.vue'
</script>

<template>
  <div class="ep">
    <header class="ep-bar">
      <h1>评测台</h1>
      <a class="ep-back" href="#/">← 返回聊天</a>
    </header>
    <EvalConsole />
  </div>
</template>

<style scoped>
.ep {
  padding: 8px 0 24px;
}
.ep-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.ep-bar h1 {
  font-size: 1.35em;
  margin: 0;
}
.ep-back {
  color: #0969da;
  text-decoration: none;
  font-size: 0.92em;
}
.ep-back:hover {
  text-decoration: underline;
}
</style>
