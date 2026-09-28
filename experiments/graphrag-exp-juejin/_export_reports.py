"""把 community_reports.parquet 导成可读版。

2026-09-25 第二版（沛东要求）：
- 索引表加 **parent 列**；
- 顶部工具栏支持 **按 level / parent 筛选** ＋ 标题/社区号搜索，筛选同时作用于索引表与报告正文；
- 报告只给结构化字段（title / summary / rating_explanation / findings）；
  findings[].explanation 是带引用标记的文本，full_content 只是它的渲染版 ⇒ 不再重复渲染
  （需要渲染版时另出 reports-rendered.*）；
- 同时列出"有社区但没报告"的社区，便于对账。

只读 parquet，不联网。
"""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def esc(v: object) -> str:
    return html.escape(str(v))


def findings_html(findings) -> str:
    if not hasattr(findings, "__len__") or len(findings) == 0:
        return "<p class='muted'>（无 findings）</p>"
    items = []
    for f in findings:
        if isinstance(f, dict):
            s = esc(f.get("summary", ""))
            e = esc(f.get("explanation", ""))
            items.append(f"<li><div class='fsum'>{s}</div><div class='fexp'>{e}</div></li>")
        else:
            items.append(f"<li><div class='fexp'>{esc(f)}</div></li>")
    return "<ol class='findings'>" + "\n".join(items) + "</ol>"


def main() -> None:
    com = pd.read_parquet(OUT / "communities.parquet")
    rep = pd.read_parquet(OUT / "community_reports.parquet")
    ent = pd.read_parquet(OUT / "entities.parquet")
    rel = pd.read_parquet(OUT / "relationships.parquet")
    docs = pd.read_parquet(OUT / "documents.parquet")

    doc_title = str(docs.iloc[0].get("title", "?"))[:80] if len(docs) else "?"
    for txt in (ROOT / "input").glob("*.txt"):
        first = txt.read_text(encoding="utf-8", errors="ignore").strip().split("\n")[0]
        if first.startswith("# "):
            doc_title = first[2:].strip()[:90]
            break

    have = set(rep["community"])
    missing = sorted(set(com["community"]) - have)
    order = rep.sort_values(["level", "community"])

    levels = sorted(int(x) for x in order["level"].unique().tolist())
    parents = sorted(int(x) for x in order["parent"].dropna().unique().tolist())

    rows = []
    for _, r in order.iterrows():
        cid, lv, pa = int(r["community"]), int(r["level"]), int(r["parent"])
        n_f = len(r["findings"]) if hasattr(r["findings"], "__len__") else 0
        search = esc(f"{cid} {str(r['title']).lower()}")
        rows.append(
            f"<tr data-level='{lv}' data-parent='{pa}' data-community='{cid}' data-search='{search}'>"
            f"<td><a href='#c{cid}'>#{cid}</a></td><td>{lv}</td>"
            f"<td>{'—' if pa < 0 else '#' + str(pa)}</td>"
            f"<td>{int(r['size'])}</td><td>{r['rank']}</td><td>{n_f}</td>"
            f"<td>{len(str(r['full_content'])):,}</td><td>{esc(r['title'])}</td></tr>"
        )

    lv_opts = "".join(f"<option value='{lv}'>{lv}</option>" for lv in levels)
    pa_opts = "".join(
        f"<option value='{pa}'>{'—(顶层 -1)' if pa < 0 else '#' + str(pa)}</option>"
        for pa in parents
    )
    missing_html = (
        "、".join(
            f"#{int(x['community'])}(L{int(x['level'])}"
            f"{'' if int(x['parent']) < 0 else ', parent #' + str(int(x['parent']))}"
            f", size={int(x['size'])})"
            for _, x in com[com["community"].isin(missing)].iterrows()
        )
        if missing
        else "无"
    )

    sections = []
    for _, r in order.iterrows():
        cid, lv, pa = int(r["community"]), int(r["level"]), int(r["parent"])
        n_f = len(r["findings"]) if hasattr(r["findings"], "__len__") else 0
        search = esc(f"{cid} {str(r['title']).lower()}")
        sections.append(
            f"<section class='rep' id='c{cid}' data-level='{lv}' data-parent='{pa}' "
            f"data-community='{cid}' data-search='{search}'>"
            f"<h2><a class='cid' href='#idx'>#{cid}</a> {esc(r['title'])}</h2>"
            f"<p class='meta'>level <b>{lv}</b> · parent "
            f"<b>{'—' if pa < 0 else '#' + str(pa)}</b> · size <b>{int(r['size'])}</b> · "
            f"rank <b>{r['rank']}</b> · findings <b>{n_f}</b></p>"
            f"<div class='sec'><div class='lbl'>SUMMARY</div><p>{esc(r['summary'])}</p></div>"
            f"<div class='sec'><div class='lbl'>RATING EXPLANATION</div>"
            f"<p>{esc(r['rating_explanation'])}</p></div>"
            f"<div class='sec'><div class='lbl'>FINDINGS（解释里带引用）</div>"
            f"{findings_html(r['findings'])}</div></section>"
        )

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>GraphRAG 社区报告 —— __TITLE__</title>
<style>
  body { font: 15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         max-width: 1080px; margin: 20px auto; padding: 0 18px; color: #1f2328; }
  h1 { font-size: 22px; margin-bottom: 6px; }
  .meta { color: #57606a; font-size: 13px; }
  .tip { background: #f6f8fa; border-left: 3px solid #0969da; padding: 8px 12px;
         margin: 10px 0; font-size: 13.5px; }
  .tip p { margin: 4px 0; }
  .toolbar { position: sticky; top: 0; z-index: 9; background: #fff;
             border-bottom: 1px solid #d0d7de; padding: 8px 0; margin: 12px 0;
             display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: 14px; }
  .toolbar label { color: #57606a; }
  .toolbar select, .toolbar input { font: inherit; padding: 3px 6px;
             border: 1px solid #d0d7de; border-radius: 6px; }
  .toolbar button { font: inherit; padding: 3px 10px; border: 1px solid #d0d7de;
             border-radius: 6px; background: #f6f8fa; cursor: pointer; }
  #count { color: #1a7f37; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
  th, td { border: 1px solid #d8dee4; padding: 4px 7px; text-align: left; }
  th { background: #f6f8fa; }
  tbody tr:hover { background: #fff8c5; }
  section.rep { border-top: 2px solid #d0d7de; padding-top: 10px; margin: 26px 0; }
  section.rep h2 { font-size: 19px; margin: 0 0 4px; }
  a.cid { color: #57606a; text-decoration: none; font-weight: 400; }
  .sec { margin: 10px 0; }
  .lbl { font-size: 12px; letter-spacing: .06em; color: #0969da; font-weight: 700;
         text-transform: uppercase; margin-bottom: 2px; }
  ol.findings { margin: 4px 0 0; padding-left: 22px; }
  ol.findings li { margin: 8px 0; }
  .fsum { font-weight: 600; }
  .muted { color: #57606a; }
  a { color: #0969da; }
</style></head><body>
<h1>GraphRAG 社区报告 —— __TITLE__</h1>
<p class="meta">graphrag 3.2.0 · deepseek-chat（含本地 shim） · __COUNTS__</p>
<p class="meta">未生成报告的社区：__MISSING__</p>
<div class="tip">
  <p><b>这份文件只给结构化字段</b>（<code>title</code> / <code>summary</code> /
     <code>rating_explanation</code> / <code>findings</code>），<b>不含 full_content</b>——
     那只是把 findings 的解释拼成一篇正文。</p>
  <p><b>引用标记在哪</b>：在 <code>findings[].explanation</code> 里（全部带引用）；
     <code>summary</code>、<code>rating_explanation</code>、<code>findings[].summary</code> 都不带。</p>
</div>
<div class="toolbar" id="toolbar">
  <label>level <select id="fLevel"><option value="">全部</option>__LV__</select></label>
  <label>parent <select id="fParent"><option value="">全部</option>__PA__</select></label>
  <label>搜索 <input id="fText" type="search" placeholder="社区号 / 标题关键词" size="18"></label>
  <button id="btnReset" type="button">重置</button>
  <span id="count"></span>
</div>
<h2 id="idx">索引</h2>
<table id="idxTable">
  <thead><tr><th>社区</th><th>level</th><th>parent</th><th>size</th><th>rank</th>
    <th>findings</th><th>字符</th><th>标题</th></tr></thead>
  <tbody>
  __ROWS__
  </tbody>
</table>
__SECTIONS__
<script>
(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll('#idxTable tbody tr'));
  var reps = Array.prototype.slice.call(document.querySelectorAll('section.rep'));
  var elLevel = document.getElementById('fLevel');
  var elParent = document.getElementById('fParent');
  var elText = document.getElementById('fText');
  var elCount = document.getElementById('count');

  function match(el) {
    var lv = elLevel.value, pa = elParent.value;
    var q = elText.value.trim().toLowerCase();
    if (lv && el.dataset.level !== lv) return false;
    if (pa && el.dataset.parent !== pa) return false;
    if (q && el.dataset.search.indexOf(q) < 0) return false;
    return true;
  }

  function apply() {
    var shown = 0;
    rows.forEach(function (r) {
      var ok = match(r);
      r.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    reps.forEach(function (s) { s.style.display = match(s) ? '' : 'none'; });
    elCount.textContent = '显示 ' + shown + ' / ' + rows.length + ' 份';
  }

  [elLevel, elParent, elText].forEach(function (el) {
    el.addEventListener('input', apply);
    el.addEventListener('change', apply);
  });
  document.getElementById('btnReset').addEventListener('click', function () {
    elLevel.value = ''; elParent.value = ''; elText.value = ''; apply();
  });
  apply();
})();
</script>
</body></html>
"""
    doc = (
        doc.replace("__TITLE__", esc(doc_title))
        .replace("__COUNTS__", f"{len(ent)} 实体 / {len(rel)} 关系 / {len(com)} 社区 → <b>{len(rep)} 份报告</b>")
        .replace("__MISSING__", missing_html)
        .replace("__LV__", lv_opts)
        .replace("__PA__", pa_opts)
        .replace("__ROWS__", "\n".join(rows))
        .replace("__SECTIONS__", "\n".join(sections))
    )
    (ROOT / "reports.html").write_text(doc, encoding="utf-8")

    # -------- Markdown：加 parent 列 --------
    md = [f"# GraphRAG 社区报告 —— {doc_title}", ""]
    md.append("- 来源：`output/community_reports.parquet` · graphrag 3.2.0 · deepseek-chat（含本地 shim）")
    md.append(f"- {len(rep)} 份报告；未生成：{missing if missing else '无'}")
    md.append(
        "- **只给结构化字段**（title / summary / rating_explanation / findings）；不含 `full_content`。"
        "**引用标记在 `findings[].explanation` 里**（summary 与 rating_explanation 不带）。"
    )
    md.append("")
    md.append("| 社区 | level | parent | size | rank | findings | 字符 | 标题 |")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for _, r in order.iterrows():
        pa = int(r["parent"])
        n_f = len(r["findings"]) if hasattr(r["findings"], "__len__") else 0
        md.append(
            f"| #{int(r['community'])} | {int(r['level'])} | {'—' if pa < 0 else '#' + str(pa)} | "
            f"{int(r['size'])} | {r['rank']} | {n_f} | {len(str(r['full_content'])):,} | "
            f"{str(r['title']).replace('|', '/')} |"
        )
    md.append("")
    for _, r in order.iterrows():
        cid, pa = int(r["community"]), int(r["parent"])
        n_f = len(r["findings"]) if hasattr(r["findings"], "__len__") else 0
        md.append(f"\n---\n\n## 社区 #{cid} · {r['title']}\n")
        md.append(
            f"`level={int(r['level'])} · parent={'—' if pa < 0 else '#' + str(pa)} · "
            f"size={int(r['size'])} · rank={r['rank']} · findings={n_f}`\n"
        )
        md.append(f"**SUMMARY**\n\n{r['summary']}\n")
        md.append(f"**RATING EXPLANATION**\n\n{r['rating_explanation']}\n")
        md.append("**FINDINGS**（解释里带引用）\n")
        if hasattr(r["findings"], "__len__"):
            for i, f in enumerate(r["findings"], 1):
                if isinstance(f, dict):
                    md.append(f"{i}. **{f.get('summary','')}** — {f.get('explanation','')}")
        md.append("")
    (ROOT / "reports.md").write_text("\n".join(md), encoding="utf-8")

    # -------- 渲染版（可选） --------
    rmd = [
        "# GraphRAG 社区报告 · 渲染正文版（只含 full_content）",
        "",
        f"- 共 {len(rep)} 份；结构化字段版见 `reports.md`",
        "",
    ]
    for _, r in order.iterrows():
        rmd.append(f"\n---\n\n## 社区 #{int(r['community'])} · {r['title']}\n")
        rmd.append(str(r["full_content"]).strip() + "\n")
    (ROOT / "reports-rendered.md").write_text("\n".join(rmd), encoding="utf-8")

    rsec = []
    for _, r in order.iterrows():
        body = "".join(
            f"<h3>{esc(ln[3:])}</h3>" if ln.startswith("## ")
            else f"<h2>{esc(ln[2:])}</h2>" if ln.startswith("# ")
            else f"<p>{esc(ln)}</p>"
            for ln in str(r["full_content"]).split("\n")
            if ln.strip()
        )
        rsec.append(
            f"<section class='rep'><h2>社区 #{int(r['community'])} · "
            f"{esc(r['title'])}</h2>{body}</section><hr>"
        )
    rdoc = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
        "<title>GraphRAG 社区报告 · 渲染正文版</title><style>"
        "body{font:15px/1.7 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
        "max-width:1020px;margin:24px auto;padding:0 18px}"
        "h1{font-size:22px}h2{font-size:19px;margin-top:26px;border-bottom:1px solid #ddd;"
        "padding-bottom:6px}h3{font-size:16px;margin-top:16px}p{margin:8px 0}"
        "</style></head><body><h1>GraphRAG 社区报告 · 渲染正文版</h1>"
        f"<p>共 {len(rep)} 份；只含 full_content。</p>{''.join(rsec)}</body></html>"
    )
    (ROOT / "reports-rendered.html").write_text(rdoc, encoding="utf-8")

    print(f"写出 reports.html（{(ROOT / 'reports.html').stat().st_size:,} bytes）")
    print(f"写出 reports.md（{(ROOT / 'reports.md').stat().st_size:,} bytes）")
    print(f"写出 reports-rendered.html / .md")
    print(f"\n文档：{doc_title}")
    print(f"{len(rep)} 份报告；level 取值 {levels}；parent 取值 {parents}")
    print(f"未生成报告的社区：{missing if missing else '无'}")


if __name__ == "__main__":
    main()
