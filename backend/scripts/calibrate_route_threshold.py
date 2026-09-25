"""L2 的 margin 阈值标定：用示例话语自己做留一法（LOO）。

做法：把每条示例话语当 query，与**两个类质心**各算一次余弦，两者之差就是
"同类样本的典型 margin"。阈值取这个分布的低分位——含义是"只有比同类样本还更像，
才算判得明确"（沛东 2026-09-20 定的方向：宁可漏到 L3，不可错判）。

顺带对比三种 L2 实现的自分类正确数（最近单条话语 / 类质心 / 类描述句），
当前实现取"类质心"（实测 15/15）。

只加载 embedder，不碰 Qdrant（后端可以继续跑）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.rag.route_decision import UTTERANCES  # noqa: E402
from app.storage.fastembed_store import FastEmbedEmbedder  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cache = settings.embedding_cache_dir or str(Path("data/models").resolve())
    emb = FastEmbedEmbedder(model_name=settings.embedding_model, cache_dir=cache)

    labels: list[str] = []
    texts: list[str] = []
    for label, group in UTTERANCES.items():
        for u in group:
            labels.append(label)
            texts.append(u)

    m = np.array(emb.embed(texts, is_query=True), dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    # 三种候选实现的自分类正确数（对照用）
    desc_labels = list(UTTERANCES.keys())          # 与 protos 同序，供 C 方案比对
    desc = np.array(emb.embed([
        "答案是一处内容，把那一处讲清楚就成立",
        "答案是两个事物之间的连接",
        "答案是一片内容的全部",
    ], is_query=True), dtype=np.float32)
    desc /= np.linalg.norm(desc, axis=1, keepdims=True) + 1e-9

    def centroid(idx_list: list[int]) -> np.ndarray:
        c = m[idx_list].mean(axis=0)
        return c / (np.linalg.norm(c) + 1e-9)

    protos = {k: centroid([i for i, l in enumerate(labels) if l == k]) for k in UTTERANCES}
    ok_a = ok_b = ok_c = 0
    rows: list[tuple[str, str, float, float, float]] = []
    for i, (label, text) in enumerate(zip(labels, texts)):
        # A 最近单条话语（本类用其余条，避免自己跟自己比）
        own_a = max(m[i] @ m[j] for j, l in enumerate(labels) if l == label and j != i)
        oth_a = max(m[i] @ m[j] for j, l in enumerate(labels) if l != label)
        ok_a += int(own_a > oth_a)
        # B 类质心
        sb = {k: float(m[i] @ p) for k, p in protos.items()}
        ok_b += int(max(sb, key=lambda k: sb[k]) == label)
        # C 类描述句
        sc = {k: float(m[i] @ desc[j]) for j, k in enumerate(desc_labels)}
        ok_c += int(max(sc, key=lambda k: sc[k]) == label)
        # 阈值标定用 B：最高分质心与次高分之差（2026-09-21 起 L2 是三分类）
        ranked_b = sorted(sb.values(), reverse=True)
        rows.append((label, text, ranked_b[0], ranked_b[1], ranked_b[0] - ranked_b[1]))

    print(f"三种实现的留一法自分类：最近单条话语 {ok_a}/{len(rows)}｜"
          f"类质心 {ok_b}/{len(rows)}｜类描述句 {ok_c}/{len(rows)}（当前实现＝类质心）")

    print("每条示例话语的留一法结果（own＝最高分质心，other＝次高分质心）")
    print(f"{'类':<6}{'own':<8}{'other':<8}{'margin':<9} 话语")
    for label, text, own, other, margin in sorted(rows, key=lambda r: r[4]):
        print(f"{label:<6}{own:<8.3f}{other:<8.3f}{margin:<9.3f} {text}")

    margins = np.array([r[4] for r in rows])
    print("\nmargin 分布：")
    for q in (5, 10, 20, 25, 50, 75, 100):
        print(f"  p{q:<3}= {np.percentile(margins, q):.4f}")
    print(f"  min={margins.min():.4f} max={margins.max():.4f} 均值={margins.mean():.4f}")

    print("\n分位数越低越保守（更多 query 会降级到 L3）：")
    for q in (5, 10, 20):
        v = float(np.percentile(margins, q))
        print(f"  取 p{q} = {v:.4f} → 约 {100 - q}% 的同类样本能直接判（其余降级 L3）")

    # 两类各自的分位（看是否有某一类整体更难分）
    for label in UTTERANCES:
        sub = np.array([r[4] for r in rows if r[0] == label])
        print(f"  {label:<6} 类内 margin：min={sub.min():.4f} p25={np.percentile(sub, 25):.4f} "
              f"中位={np.median(sub):.4f}")


if __name__ == "__main__":
    main()
