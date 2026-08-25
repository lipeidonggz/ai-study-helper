<script setup lang="ts">
import EvalConsole from './EvalConsole.vue'
</script>

<template>
  <div class="ep">
    <header class="ui-bar ep-head">
      <h1>评测台</h1>
      <a class="ui-link" href="#/">← 返回聊天</a>
    </header>
    <EvalConsole />
  </div>
</template>

<style scoped>
.ep {
  padding: 8px 0 24px;
}
.ep-head {
  justify-content: space-between;
  margin-bottom: 14px;
}
.ep-head h1 {
  font-size: 1.35em;
  margin: 0;
}
</style>
