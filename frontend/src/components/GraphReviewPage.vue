<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { compileApi, type CompileClaim, type CompileGraph, type GraphEdge, type StatementsPayload } from '../api/client'
import { navigate } from '../router'

const props = defineProps<{ sourceId: string }>()

const graph = ref<CompileGraph | null>(null)
const s7 = ref<StatementsPayload | null>(null)
const claims = ref<CompileClaim[]>([])
const focusName = ref('')
const error = ref('')
const loading = ref(true)
const entLimit = ref(30)
const picked = ref<GraphEdge | null>(null)
/** s5 = 图组装原始产物；s6 = S6-A 确定性形合并后的产物（实体带 id、边端点换 id） */
const stage = ref<'s5' | 's6'>('s5')
/** S6 产物里 id → 名字（界面一律按名字工作；边端点先映射回名字再展示） */
const idToName = ref<Record<string, string>>({})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const g = await compileApi.graph(props.sourceId, stage.value)
    // S6 产物：边端点存的是实体 id → 先映射回名字，界面其余逻辑（按名字索引）不变
    const map: Record<string, string> = {}
    for (const e of g.entities) if (e.id) map[e.id] = e.name
    idToName.value = map
    if (stage.value === 's6' && Object.keys(map).length) {
      for (const ed of g.edges) {
        ed.from = map[ed.from] || ed.from
        if (ed.to) ed.to = map[ed.to] || ed.to
      }
    }
    graph.value = g
    claims.value = await compileApi.claims(props.sourceId) // 顺序与 claim_idx 一致
    try {
      s7.value = await compileApi.statements(props.sourceId)   // S7 未跑过则 404 → 保持 null
    } catch {
      s7.value = null
    }
    focusName.value = mainEntities.value[0]?.name || graph.value.entities[0]?.name || ''
  } catch (err) {
    error.value = `读取图产物失败：${err}（先在这篇上点"抽取/重抽"跑一遍）`
  } finally {
    loading.value = false
  }
}

async function switchStage(next: 's5' | 's6') {
  if (stage.value === next) return
  stage.value = next
  picked.value = null
  await load()
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

/** 边清单排序：点列头切 from / 谓词 / to，再点一次切升降序。按 to 排序时一元边（to 空）永远垫底。 */
type SortKey = 'from' | 'predicate' | 'to'
const sortKey = ref<SortKey>('from')
const sortDir = ref<1 | -1>(1)
function toggleSort(k: SortKey) {
  if (sortKey.value === k) sortDir.value = sortDir.value === 1 ? -1 : 1
  else {
    sortKey.value = k
    sortDir.value = 1
  }
}
const sortMark = (k: SortKey) => (sortKey.value === k ? (sortDir.value === 1 ? '▲' : '▼') : '⇅')
const sortedEdges = computed(() => {
  const k = sortKey.value
  const dir = sortDir.value
  const tail = (a: GraphEdge, b: GraphEdge) =>
    a.from.localeCompare(b.from) || (a.to || '').localeCompare(b.to || '') || a.claim_idx - b.claim_idx
  return [...(graph.value?.edges || [])].sort((a, b) => {
    if (k === 'to') {
      const ea = a.to ? 0 : 1
      const eb = b.to ? 0 : 1
      if (ea !== eb) return ea - eb                     // 一元边垫底，不受升降序影响
      return dir * (a.to || '').localeCompare(b.to || '') || a.predicate.localeCompare(b.predicate) || tail(a, b)
    }
    if (k === 'predicate') {
      // 同谓词内部固定按 from → to → claim 排，方便一眼看"这个谓词都连了谁"
      return dir * a.predicate.localeCompare(b.predicate) || tail(a, b)
    }
    return dir * a.from.localeCompare(b.from) || a.predicate.localeCompare(b.predicate) || tail(a, b)
  })
})

/** 谓词计数：按谓词排序时在表头下方给出分布，方便判断哪个关系被用得最多 */
const predicateCounts = computed(() => {
  const m = new Map<string, number>()
  for (const e of graph.value?.edges || []) m.set(e.predicate, (m.get(e.predicate) || 0) + 1)
  return [...m.entries()].sort((a, b) => b[1] - a[1])
})

/** 护栏命中数（程序侧拒掉的端点）：未建边总数里，减去"模型判 null"和"谓词待定"的部分 */
const guardHits = computed(() => {
  const cats = graph.value?.skip_categories
  if (!cats) return 0            // 老产物没有分类字段 → 不显示（避免把"全部未建边"误报成护栏命中）
  const notGuard = (cats['取不出可点名概念'] || 0) + (cats['其他'] || 0) + (cats['谓词待定'] || 0)
  return Math.max(0, (graph.value?.skipped?.length || 0) - notGuard)
})

/** 端点规范化映射（S5 审计）：默认只看带 derived_from 的（概括/溯源），其余折叠 */
/** S7 断言视图：按 claim_type 过滤（局限 / 展望 / 未标） */
const s7Filter = ref<'all' | 'limitation' | 'outlook' | 'none'>('all')
const s7Limit = ref(60)
const s7Rows = computed(() => {
  const rows = s7.value?.statements || []
  if (s7Filter.value === 'all') return rows
  if (s7Filter.value === 'limitation') return rows.filter((r) => r.claim_type === 'LimitationStatement')
  if (s7Filter.value === 'outlook') return rows.filter((r) => r.claim_type === 'OutlookStatement')
  return rows.filter((r) => !r.claim_type)
})

const normLimit = ref(60)
const normOnlyDerived = ref(false)
const normOnlyGeneralized = ref(false)
const normalizeRows = computed(() => {
  let rows = graph.value?.normalize_map || []
  if (normOnlyGeneralized.value) rows = rows.filter((r) => r.tier === 'generalized')
  else if (normOnlyDerived.value) rows = rows.filter((r) => (r.derived_from || []).length > 0)
  return rows
})
const derivedCount = computed(() => (graph.value?.normalize_map || []).filter((r) => (r.derived_from || []).length > 0).length)

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
      <h1>{{ sourceId }} · 概念图复核（S5 原始 / S6 形合并）</h1>
      <span class="stage-switch">
        <button :class="{ on: stage === 's5' }" @click="switchStage('s5')">S5 原始</button>
        <button :class="{ on: stage === 's6' }" @click="switchStage('s6')">S6 形合并</button>
      </span>
      <button :disabled="loading" @click="load">刷新</button>
      <button @click="navigate('#/compile/' + sourceId)">← 体检报告</button>
      <button @click="navigate('#/kb')">知识库</button>
      <span v-if="graph" class="hint">
        <template v-if="stage === 's5'">
          claim {{ graph.stats.claims_in }} → 建边 <strong>{{ graph.stats.claims_with_edge }}</strong>
          （未建边 {{ graph.stats.claims_skipped }}，均带原因）｜ 实体 <strong>{{ graph.stats.entities }}</strong>
          ｜ 端点规范化 {{ graph.stats.normalize_targets }} 个 / {{ graph.stats.normalize_batches }} 批
          <template v-if="graph.stats.bleed_retried">
            ｜ <b>串味修正</b> {{ graph.stats.bleed_fixed }}/{{ graph.stats.bleed_retried }}
            <template v-if="graph.stats.bleed_remaining">（仍串味 {{ graph.stats.bleed_remaining }}）</template>
          </template>
        </template>
        <template v-else>
          实体 <strong>{{ graph.stats.entities_in }} → {{ graph.stats.entities_out }}</strong>
          （合并 {{ graph.stats.form_merged_groups }} 组 / {{ graph.stats.form_merged_names }} 个名字）｜
          边 {{ graph.stats.edges_in }} → {{ graph.stats.edges_out }}（自环丢 {{ graph.stats.self_loops_dropped }}）｜
          别名 {{ graph.stats.aliases_total }}｜ 悬挂 {{ graph.stats.dangling_endpoints }}｜
          低置信待抽检 {{ graph.stats.low_confidence_merges }}
        </template>
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
            <summary>边清单（{{ graph.edges.length }}）— <b>点列头切换排序</b>（from / 谓词 / to），点 from / to 切焦点</summary>
            <p v-if="sortKey === 'predicate'" class="hint pred-dist">
              <b>谓词分布</b>（{{ predicateCounts.length }} 种）：
              <span v-for="([p, n], i) in predicateCounts" :key="'pc' + p">
                <code>{{ p }}</code> {{ n }}<template v-if="i < predicateCounts.length - 1"> · </template>
              </span>
            </p>
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
                  <th class="sortable" @click="toggleSort('predicate')">谓词 {{ sortMark('predicate') }}</th>
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

          <details v-if="graph.normalize_map?.length">
            <summary>
              端点规范化映射（{{ graph.normalize_map.length }}）— S5 把每个端点短语规范成了什么
              <template v-if="derivedCount">｜ 带溯源词 {{ derivedCount }}</template>
              <template v-if="(graph.generalized || []).length">｜ C 档待抽检 {{ (graph.generalized || []).length }}</template>
            </summary>
            <p class="hint">
              <b>raw → concept</b>：概念名是怎么从原文端点来的。带 <code>derived_from</code> 的是"概括/名词化"
              （概念是新词，但列出了取自 raw 的来源词）——这是可回溯的关键：名字不是原文措辞时，
              必须能说出它由哪些原文词而来。
              <b>档位</b>：<code>ok</code>＝原文措辞｜<code>derived</code>＝词形派生｜<code>generalized</code>＝语义概括（待抽检）。
              <span v-if="graph.name_tiers" style="margin-left:6px">
                （本图：ok {{ graph.name_tiers.ok || 0 }} · derived {{ graph.name_tiers.derived || 0 }} ·
                generalized {{ graph.name_tiers.generalized || 0 }}）
              </span>
              <label style="margin-left:8px"><input v-model="normOnlyDerived" type="checkbox" @change="normOnlyGeneralized = false" /> 只看带溯源词的</label>
              <label style="margin-left:8px"><input v-model="normOnlyGeneralized" type="checkbox" @change="normOnlyDerived = false" /> 只看 C 档（语义概括）</label>
            </p>
            <table class="tbl">
              <thead><tr><th>idx</th><th>raw（原文端点）</th><th>concept（规范化结果）</th><th>档位</th><th>derived_from</th></tr></thead>
              <tbody>
                <tr v-for="r in normalizeRows.slice(0, normLimit)" :key="'nm' + r.idx">
                  <td><small>{{ r.idx }}</small></td>
                  <td>{{ r.raw }}</td>
                  <td><b v-if="r.concept">{{ r.concept }}</b><span v-else style="color:#8c959f">null（取不出）</span></td>
                  <td>
                    <small v-if="r.tier === 'generalized'" class="badge">概括·待抽检</small>
                    <small v-else-if="r.tier === 'derived'" class="badge">词形派生</small>
                    <small v-else-if="r.tier">原文</small>
                  </td>
                  <td><small>{{ (r.derived_from || []).join(' / ') || '—' }}</small></td>
                </tr>
              </tbody>
            </table>
            <button v-if="normalizeRows.length > normLimit" @click="normLimit = normalizeRows.length">
              展开全部 {{ normalizeRows.length }} 条（还有 {{ normalizeRows.length - normLimit }} 条）
            </button>
          </details>

          <details>
            <summary>
              未建边的 claim（{{ graph.skipped.length }}）
              <template v-if="guardHits">
                — 其中 <b>护栏命中 {{ guardHits }}</b>（端点名不合格/自造/串味等，程序侧拒）
              </template>
            </summary>
            <p class="hint">
              每条 claim 的去向都可对账：建边 {{ graph.stats.claims_with_edge }} 条、未建边 {{ graph.skipped.length }} 条（原因在下面）。
              <b>组图由程序做</b>（LLM 只负责把端点规范成可点名的概念），所以数量关系是确定的。
            </p>
            <p v-if="graph.skip_categories" class="hint">
              <b>护栏命中分类</b>（全在程序侧）：
              <span v-for="(n, k, i) in graph.skip_categories" :key="'sc' + k">
                <code>{{ k }}</code> {{ n }}<template v-if="i < Object.keys(graph.skip_categories).length - 1"> · </template>
              </span>
            </p>
            <table class="tbl">
              <thead><tr><th>claim（三件套）</th><th>原因</th></tr></thead>
              <tbody>
                <tr v-for="s in graph.skipped" :key="'s' + s.claim_idx">
                  <td>
                    <small>c{{ s.claim_idx }}</small>
                    <a class="lnk" @click="focusName = claimOf(s.claim_idx)?.subject || ''">{{ claimOf(s.claim_idx)?.subject }}</a>
                    <code>{{ claimOf(s.claim_idx)?.predicate_normalized || claimOf(s.claim_idx)?.predicate }}</code>
                    <span v-if="claimOf(s.claim_idx)?.object">{{ claimOf(s.claim_idx)?.object }}</span>
                    <span v-else style="color: #8c959f">∅</span>
                  </td>
                  <td>{{ s.reason }}</td>
                </tr>
              </tbody>
            </table>
          </details>

          <details v-if="stage === 's6'">
            <summary>
              S6 形合并记录（{{ (graph.merge_log || []).length }} 组合并
              <template v-if="(graph.review_log || []).length">，{{ (graph.review_log || []).length }} 组低置信待抽检</template>）
            </summary>
            <p class="hint">
              <b>确定性合并</b>（不调 LLM）：同一形态 key 下出现多种写法才合并——单形不改写（改名＝造名）；
              原名一律进 <code>aliases</code>，检索两个方向都能命中。<b>置信度</b>＝两形在原文里的出处是否共现
              （程序从原文算，不靠模型自报）：相交＝high；都有出处但不交＝low（合并照做，进待抽检）；只有一形有出处＝unknown。
            </p>
            <table class="tbl">
              <thead><tr><th>canonical</th><th>合并进来的写法（次数）</th><th>置信度</th></tr></thead>
              <tbody>
                <tr v-for="m in graph.merge_log || []" :key="'ml' + m.canonical">
                  <td><a class="lnk" @click="focusName = m.canonical">{{ m.canonical }}</a></td>
                  <td>
                    <span v-for="(n, v) in m.variants" :key="'v' + v">
                      <template v-if="v !== m.canonical">{{ v }}<small>（{{ n }}）</small>　</template>
                    </span>
                  </td>
                  <td>{{ m.confidence || '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="(graph.self_loops || []).length" class="hint">
              <b>被丢弃的自环 {{ (graph.self_loops || []).length }} 条</b>（合并前是"同一物的两种写法"之间的关系，
              合并后变成自己指向自己——<u>不是</u>凭空丢边，明细如下）：
              <span v-for="(s, i) in graph.self_loops" :key="'sl' + i">
                <code>c{{ s.claim_idx }}</code> {{ s.from }} —{{ s.predicate }}→ {{ s.to }}<template v-if="i < (graph.self_loops || []).length - 1">；</template>
              </span>
            </p>
            <p v-if="(graph.isolated_dropped || []).length" class="hint">
              <b>无边移除 {{ (graph.isolated_dropped || []).length }} 个实体</b>（合并 + 丢自环后它一条边都不剩——
              保持"实体表＝边端点集合"的不变式；明细可回溯）：
              <span v-for="(d, i) in graph.isolated_dropped" :key="'iso' + i">
                <code>{{ d.name }}</code>{{ d.aliases?.length ? '（别名 ' + d.aliases.join(' / ') + '）' : '' }}<template v-if="i < (graph.isolated_dropped || []).length - 1">；</template>
              </span>
            </p>
          </details>

          <details v-if="s7">
            <summary>
              S7 断言（{{ s7.stats.statements }}）— 每条 claim 一个 statement
              ｜局限 {{ s7.stats.limitation || 0 }} / 展望 {{ s7.stats.outlook || 0 }} / 其他 {{ s7.stats.null || 0 }}
              <template v-if="(s7.claim_type_demoted || []).length">｜依据护栏降级 {{ (s7.claim_type_demoted || []).length }}</template>
            </summary>
            <p class="hint">
              <b>statement = 可独立寻址的断言</b>：一条 claim 一个（394 条 claim → 394 个 statement，一一对应），
              挂 <code>doc —asserts→</code>、有概念端点的另挂 <code>concept —hasStatement→</code>，
              并 <code>groundedIn</code> 回 chunk。<b>claim_type</b> 只有两类（局限 / 展望），其余为空；
              口径是"<b>宁多勿漏</b>"——「局限」标签实际是"风险 / 代价分析 + 作者自述局限"的合集，
              下游按需过滤（这是有意为之，不是判错）。
              <span style="margin-left:8px">
                筛选：
                <button :class="{ on: s7Filter === 'all' }" @click="s7Filter = 'all'">全部</button>
                <button :class="{ on: s7Filter === 'limitation' }" @click="s7Filter = 'limitation'">只看局限</button>
                <button :class="{ on: s7Filter === 'outlook' }" @click="s7Filter = 'outlook'">只看展望</button>
                <button :class="{ on: s7Filter === 'none' }" @click="s7Filter = 'none'">只看未标</button>
              </span>
            </p>
            <table class="tbl">
              <thead><tr><th>#</th><th>断言（原文三件套）</th><th>类型</th><th>依据（逐字）</th></tr></thead>
              <tbody>
                <tr v-for="st in s7Rows.slice(0, s7Limit)" :key="'st' + st.id">
                  <td><small>{{ st.id }}</small></td>
                  <td>
                    <a class="lnk" @click="focusName = claimOf(st.claim_idx)?.subject || ''">{{ claimOf(st.claim_idx)?.subject }}</a>
                    <code>{{ claimOf(st.claim_idx)?.predicate }}</code>
                    <span v-if="claimOf(st.claim_idx)?.object">{{ claimOf(st.claim_idx)?.object }}</span>
                    <span v-else style="color: #8c959f">∅</span>
                  </td>
                  <td>
                    <span v-if="st.claim_type === 'LimitationStatement'" class="badge">局限</span>
                    <span v-else-if="st.claim_type === 'OutlookStatement'" class="badge ok">展望</span>
                    <span v-else style="color: #8c959f">—</span>
                  </td>
                  <td><small>{{ st.claim_type_evidence }}</small></td>
                </tr>
              </tbody>
            </table>
            <button v-if="s7Rows.length > s7Limit" @click="s7Limit = s7Rows.length">
              展开全部 {{ s7Rows.length }} 条（还有 {{ s7Rows.length - s7Limit }} 条）
            </button>
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
.stage-switch button { font-size: 12px; padding: 2px 8px; }
.stage-switch button.on { background: #0969da; color: #fff; border-color: #0969da; }
.gr-lists button.on { background: #0969da; color: #fff; border-color: #0969da; }
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
.pred-dist { margin: 6px 0 2px; line-height: 1.9; }
.pred-dist code { background: #eef4ff; border-radius: 4px; padding: 0 3px; }
</style>
