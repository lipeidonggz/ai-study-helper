/** 展示格式化工具。 */

/** token 数自适应：小于 1 万显示原数，1 万以上用「万」，1 亿以上用「亿」。 */
export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return '—'
  if (n < 10000) return String(n)
  if (n < 100000000) return `${trimZero((n / 10000).toFixed(1))}万`
  return `${trimZero((n / 100000000).toFixed(2))}亿`
}

/** 去掉小数末尾无意义的 0（如 522.0 → 522、1.50 → 1.5）。 */
function trimZero(s: string): string {
  return s.replace(/\.?0+$/, '')
}
