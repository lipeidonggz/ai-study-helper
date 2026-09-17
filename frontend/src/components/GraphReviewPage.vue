<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { compileApi, type CompileClaim, type CompileGraph, type GraphEdge } from '../api/client'
import { navigate } from '../router'

const props = defineProps<{ sourceId: string }>()

const graph = ref<CompileGraph | null>(null)
const claims = ref<CompileClaim[]>([])
const focusName = ref('')
const error = ref('')
const loading = ref(true)
const entLimit = ref(30)
const picked = ref<GraphEdge | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    graph.value = await compileApi.graph(props.sourceId)
    claims.value = await compileApi.claims(props.sourceId) // 顺序与 claim_idx 一致
    focusName.value = mainEntities.value[0]?.name || graph.value.entities[0]?.name || ''
  } catch (err) {
    error.value = `读取图产物失败：${err}（先在这篇上点"抽取/重抽"跑一遍）`
  } finally {
    loading.value = false
  }
}

/** 每个实体的三个量：跨节数 / 度 / 关系族数（与离线复核页同口径） */
const entMeta = computed(() => {
  const m = new Map<string, { degree: number; families: Set<string>; sections: Set<string> }>()
  const g = graph.value
  if (!g) return m
  const bump = (name: string, pred: string, chunkId: string | null | undefined) => {
    if (!name) return
    if (!m.has(name)) m.set(name, { degree: 0, families: new Set(), sections: new Set() })
    const e = m.get(name)!
    e.degree += 1
    e.families.add(pred)
    e.sections.add(g.chunk_sections?.[chunkId || ''] || '(未定位)')
  }
  for (const ed of g.edges) {
    bump(ed.from, ed.predicate, ed.chunk_id)
    if (ed.to) bump(ed.to, ed.predicate, ed.chunk_id)
  }
  return m
})

const entities = computed(() => {
  // 按名字归并：**S5 刻意不去重**（同一实体在多批里会各声明一次，去重是 S6 的活），
  // 但界面上必须合成一行，否则主要实体卡会出现六个 Codex。
  const byName = new Map<string, { name: string; type: string; aliases: string[] }>()
  for (const e of graph.value?.entities || []) {
    const hit = byName.get(e.name)
    if (!hit) byName.set(e.name, { name: e.name, type: e.type, aliases: [...(e.aliases || [])] })
    else for (const a of e.aliases || []) if (!hit.aliases.includes(a)) hit.aliases.push(a)
  }
  return [...byName.values()]
    .map((e) => {
      const meta = entMeta.value.get(e.name)
      return { ...e, degree: meta?.degree || 0, families: meta?.families.size || 0, sections: meta?.sections.size || 0 }
    })
    // 排序：跨节 → 度 → 关系族（贯穿全文的更像主题级概念）
    .sort((a, b) => b.sections - a.sections || b.degree - a.degree || b.families - a.families || a.name.localeCompare(b.name))
})

const mainEntities = computed(() => entities.value.slice(0, 12))
const shownEntities = computed(() => entities.value.slice(0, entLimit.value))

/** 边清单排序：点列头切 from / to，再点一次切升降序。一元边（to 空）永远排最后。 */
const sortKey = ref<'from' | 'to'>('from')
const sortDir = ref<1 | -1>(1)
function toggleSort(k: 'from' | 'to') {
  if (sortKey.value === k) sortDir.value = sortDir.value === 1 ? -1 : 1
  else {
    sortKey.value = k
    sortDir.value = 1
  }
}
const sortMark = (k: 'from' | 'to') => (sortKey.value === k ? (sortDir.value === 1 ? '▲' : '▼') : '⇅')
const sortedEdges = computed(() => {
  const k = sortKey.value
  const key = (e: GraphEdge) => (k === 'from' ? e.from : e.to || '')
  return [...(graph.value?.edges || [])].sort((a, b) => {
    const ea = k === 'to' && !a.to ? 1 : 0
    const eb = k === 'to' && !b.to ? 1 : 0
    if (ea !== eb) return ea - eb                       // 一元边垫底，不受升降序影响
    return (
      sortDir.value * (key(a).localeCompare(key(b)) || a.predicate.localeCompare(b.predicate)) ||
      a.from.localeCompare(b.from) ||
      (a.to || '').localeCompare(b.to || '')
    )
  })
})

/** 合并重复边后的边（同 from|pred|to → 一条 + 出现次数 n） */
type MergedEdge = GraphEdge & { n: number }

/** 一跳邻居：入边（谁→它）/ 出边（它→谁）/ 双向 */
const ego = computed<{ inc: MergedEdge[]; out: MergedEdge[]; unary: MergedEdge[] }>(() => {
  const g = graph.value
  const name = focusName.value
  if (!g || !name) return { inc: [], out: [], unary: [] }
  const merge = (arr: GraphEdge[]): (GraphEdge & { n: number })[] => {
    const m = new Map<string, GraphEdge & { n: number }>()
    for (const e of arr) {
      const k = `${e.from}|${e.predicate}|${e.to}`
      const hit = m.get(k)
      if (hit) hit.n += 1
      else m.set(k, { ...e, n: 1 })
    }
    return [...m.values()]
  }
  const out = merge(g.edges.filter((e) => e.from === name && e.to && e.to !== name))
  const inc = merge(g.edges.filter((e) => e.to === name && e.from !== name))
  const unary = merge(g.edges.filter((e) => e.from === name && !e.to))
  return { inc, out, unary }
})

const bothNames = computed(() => {
  const outs = new Set(ego.value.out.map((e) => e.to))
  return new Set(ego.value.inc.map((e) => e.from).filter((n) => outs.has(n)))
})

/** SVG 几何：确定性两列布局（左入边 / 中焦点 / 右出边），不搞力导向 */
const layout = computed(() => {
  const W = 1000
  const H = 640
  const cx = W / 2
  const cy = H / 2
  const cap = Math.floor((H - 90) / 26)
  const rows = (list: (GraphEdge & { n: number })[], side: -1 | 1) => {
    const shown = list.slice(0, cap)
    return shown.map((e, i) => {
      const y = cy + (i - (shown.length - 1) / 2) * 26
      const x = side < 0 ? W * 0.2 : W * 0.8
      const other = (side < 0 ? e.from : e.to) as string
      const color = bothNames.value.has(other) ? '#8250df' : side < 0 ? '#bc4c00' : '#1a7f37'
      // 谓词标签放**节点的内侧**（名字在外侧，两者分开就不会重叠）
      const lx = side < 0 ? x + 12 : x - 12
      return {
        x, y, other, color, side,
        x1: side < 0 ? x : cx, y1: side < 0 ? y : cy,
        x2: side < 0 ? cx : x, y2: side < 0 ? cy : y,
        label: e.predicate + (e.n > 1 ? ` ×${e.n}` : ''),
        lx,
        ly: y + 3.5,
        lane: side < 0 ? 'start' : 'end',
        // 名字在**外侧**（入边在左、出边在右），与内侧的谓词标签分居两侧 → 不会重叠
        nx: side < 0 ? x - 10 : x + 10,
        nAnchor: side < 0 ? 'end' : 'start',
      }
    })
  }
  return {
    W, H, cx, cy,
    inc: rows(ego.value.inc, -1),
    out: rows(ego.value.out, 1),
    more: { inc: Math.max(0, ego.value.inc.length - cap), out: Math.max(0, ego.value.out.length - cap) },
  }
})

function claimOf(idx: number | null | undefined): CompileClaim | undefined {
  return typeof idx === 'number' && idx >= 0 ? claims.value[idx] : undefined
}

onMounted(load)
</script>

<template>
  <main class="gr-page">
    <header class="gr-head">
      <h1>{{ sourceId }} · 概念图复核（S5）</h1>
      <button :disabled="loading" @click="load">刷新</button>
      <button @click="navigate('#/compile/' + sourceId)">← 体检报告</button>
      <button @click="navigate('#/kb')">知识库</button>
      <span v-if="graph" class="hint">
        claim {{ graph.stats.claims_in }} → 建边 <strong>{{ graph.stats.claims_with_edge }}</strong>
        （未建边 {{ graph.stats.claims_skipped }}，均带原因）｜ 实体 <strong>{{ graph.stats.entities }}</strong>
        ｜ 端点规范化 {{ graph.stats.normalize_targets }} 个 / {{ graph.stats.normalize_batches }} 批
      </span>
    </header>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-else-if="loading" class="hint">加载中…</p>

    <template v-else-if="graph">
      <!-- 主要实体卡：常驻（sticky），一屏看到文章骨架 -->
      <div class="chips">
        <span class="chips-title">主要实体</span>
        <span
          v-for="e in mainEntities"
          :key="e.name"
          class="chip"
          :class="{ active: e.name === focusName }"
          @click="focusName = e.name"
        >
          {{ e.name }} <small>跨{{ e.sections }}节·{{ e.families }}族·{{ e.degree }}边</small>
        </span>
        <span class="chips-note">排序＝跨节 → 度 → 关系族；点一下看它的一跳关系</span>
      </div>

      <div class="gr-body">
        <section class="card gr-graph">
          <svg :viewBox="`0 0 ${layout.W} ${layout.H}`" preserveAspectRatio="xMidYMid meet">
            <defs>
              <marker id="ah-o" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#1a7f37" />
              </marker>
              <marker id="ah-i" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#bc4c00" />
              </marker>
            </defs>
            <text x="500" y="24" font-size="12.5" text-anchor="middle" fill="#57606a">
              入边 {{ ego.inc.length }} · 出边 {{ ego.out.length }}<template v-if="ego.unary.length"> · 一元 {{ ego.unary.length }}</template>
            </text>
            <g v-for="(r, i) in [...layout.inc, ...layout.out]" :key="'e' + i">
              <line
                :x1="r.x1" :y1="r.y1" :x2="r.x2" :y2="r.y2"
                :stroke="r.color" stroke-width="1.5"
                :marker-end="r.x1 < r.x2 ? 'url(#ah-o)' : 'url(#ah-i)'"
              />
              <text
                :x="r.lx" :y="r.ly" font-size="10.5" :fill="r.color" :text-anchor="r.lane"
                paint-order="stroke" stroke="#fff" stroke-width="3"
              >{{ r.label }}</text>
              <circle :cx="r.x" :cy="r.y" r="6" :fill="r.color" stroke="#fff" stroke-width="1.2"
                      style="cursor: pointer" @click="focusName = r.other" />
              <text
                :x="r.nx" :y="r.y + 3.5" font-size="11.5" :fill="r.color"
                :text-anchor="r.nAnchor" style="cursor: pointer"
                @click="focusName = r.other"
              >{{ r.other.slice(0, 30) }}</text>
            </g>
            <circle :cx="layout.cx" :cy="layout.cy" r="11" fill="#0969da" stroke="#fff" stroke-width="2" />
            <text :x="layout.cx" :y="layout.cy + 32" font-size="13" font-weight="700" text-anchor="middle">{{ focusName }}</text>
            <text v-if="layout.more.inc || layout.more.out" x="500" :y="layout.H - 8" font-size="11" text-anchor="middle" fill="#8c959f">
              还有 {{ layout.more.inc + layout.more.out }} 条未画出（见右侧边清单）
            </text>
          </svg>
          <p class="hint">
            颜色分地位：<span style="color: #bc4c00">左＝入边（谁→它）</span> ·
            <span style="color: #1a7f37">右＝出边（它→谁）</span> ·
            <span style="color: #8250df">紫＝双向</span> · 点节点可继续跳
          </p>
        </section>

        <section class="card gr-lists">
          <details open>
            <summary>边清单（{{ graph.edges.length }}）— <b>点列头切换排序</b>（from / to），点 from / to 切焦点</summary>
            <div v-if="picked" class="edge-detail">
              <b>{{ picked.from }}</b> —<code>{{ picked.predicate }}</code>→ <b>{{ picked.to || '∅' }}</b>
              <button class="x" @click="picked = null">×</button>
              <div class="hint">
                claim c{{ picked.claim_idx }}：原谓词「{{ claimOf(picked.claim_idx)?.predicate }}」
                <template v-if="claimOf(picked.claim_idx)?.polarity"> · polarity {{ claimOf(picked.claim_idx)?.polarity }}</template>
                <template v-if="picked.passive_flipped"> · <b>被动翻转</b>（原句是受事，建边时交换了两端）</template>
              </div>
              <div class="ev">{{ claimOf(picked.claim_idx)?.anchor }}</div>
            </div>
            <table class="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th class="sortable" @click="toggleSort('from')">from {{ sortMark('from') }}</th>
                  <th>谓词</th>
                  <th class="sortable" @click="toggleSort('to')">to {{ sortMark('to') }}</th>
                  <th>标记</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(e, i) in sortedEdges" :key="'ed' + i" style="cursor: pointer" @click="picked = e">
                  <td>{{ i + 1 }}</td>
                  <td><a class="lnk" @click="focusName = e.from">{{ e.from }}</a></td>
                  <td><code>{{ e.predicate }}</code><br /><small>{{ claimOf(e.claim_idx)?.predicate }}</small></td>
                  <td>
                    <a v-if="e.to" class="lnk" @click="focusName = e.to!">{{ e.to }}</a>
                    <span v-else style="color: #8c959f">∅</span>
                  </td>
                  <td>
                    <span v-if="e.passive_flipped" class="badge ok">被动翻转</span>
                    <span v-if="!e.to" class="badge">一元</span>
                    <br /><small>c{{ e.claim_idx }}</small>
                  </td>
                </tr>
              </tbody>
            </table>
          </details>

          <details>
            <summary>实体清单（{{ entities.length }}）— 按跨节→度→关系族排序</summary>
            <table class="tbl">
              <thead><tr><th>实体</th><th>类型</th><th>跨节</th><th>度</th><th>关系族</th><th>别名</th></tr></thead>
              <tbody>
                <tr v-for="e in shownEntities" :key="'en' + e.name" :style="e.degree === 0 ? 'background:#fff8c5' : ''">
                  <td><a class="lnk" @click="focusName = e.name">{{ e.name }}</a></td>
                  <td>{{ e.type }}</td>
                  <td>{{ e.sections }}</td>
                  <td>{{ e.degree }}</td>
                  <td>{{ e.families }}</td>
                  <td><small>{{ (e.aliases || []).join(' / ') }}</small></td>
                </tr>
              </tbody>
            </table>
            <button v-if="entities.length > entLimit" @click="entLimit = entities.length">
              展开全部 {{ entities.length }} 个实体（还有 {{ entities.length - entLimit }} 个）
            </button>
          </details>

          <details>
            <summary>未建边的 claim（{{ graph.skipped.length }}）</summary>
            <p class="hint">
              每条 claim 的去向都可对账：建边 {{ graph.stats.claims_with_edge }} 条、未建边 {{ graph.skipped.length }} 条（原因在下面）。
              <b>组图由程序做</b>（LLM 只负责把端点规范成可点名的概念），所以数量关系是确定的。
            </p>
            <table class="tbl">
              <thead><tr><th>claim</th><th>原因</th></tr></thead>
              <tbody><tr v-for="s in graph.skipped" :key="'s' + s.claim_idx"><td>c{{ s.claim_idx }}</td><td>{{ s.reason }}</td></tr></tbody>
            </table>
          </details>

        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.gr-page { padding: 12px 16px 24px; }
.gr-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.gr-head h1 { font-size: 17px; margin: 0; }
.chips { position: sticky; top: 0; z-index: 3; background: #fff; border: 1px solid #d8dee4; border-radius: 10px; padding: 7px 10px; margin-bottom: 10px; }
.chips-title { font-weight: 700; font-size: 12.5px; margin-right: 6px; }
.chip { display: inline-block; font-size: 12px; background: #eef4ff; border: 1px solid #6b9aff; color: #0969da; border-radius: 14px; padding: 2px 9px; margin: 2px 4px 2px 0; cursor: pointer; }
.chip small { color: #57606a; font-size: 10.5px; }
.chip.active { background: #0969da; color: #fff; }
.chip.active small { color: #dbe9ff; }
.chips-note { color: #8c959f; font-size: 11.5px; margin-left: 6px; }
.gr-body { display: flex; gap: 12px; align-items: flex-start; }
.gr-graph { flex: 1.1; }
.gr-graph svg { width: 100%; height: 560px; display: block; }
.gr-lists { flex: 1; max-height: 76vh; overflow: auto; }
details { border: 1px solid #d8dee4; border-radius: 8px; margin-bottom: 8px; }
summary { cursor: pointer; padding: 6px 8px; font-weight: 600; background: #f6f8fa; border-radius: 8px; font-size: 12.5px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { border: 1px solid #e6e9ed; padding: 3px 6px; vertical-align: top; text-align: left; }
.tbl th { background: #f6f8fa; position: sticky; top: 0; }
.tbl th.sortable { cursor: pointer; user-select: none; color: #0969da; }
.tbl th.sortable:hover { background: #eef4ff; }
.lnk { color: #0969da; cursor: pointer; text-decoration: underline dotted; }
.edge-detail { position: relative; background: #f6f8fa; border-left: 3px solid #0969da; border-radius: 0 6px 6px 0; padding: 6px 26px 6px 9px; font-size: 12px; margin: 6px 0; }
.edge-detail .x { position: absolute; right: 4px; top: 2px; border: none; background: none; cursor: pointer; font-size: 14px; color: #8c959f; }
.edge-detail .ev { margin-top: 4px; color: #57606a; background: #fff; border-radius: 5px; padding: 4px 6px; }
.err { color: #cf222e; }
.hint { color: #57606a; font-size: 12px; }
</style>
