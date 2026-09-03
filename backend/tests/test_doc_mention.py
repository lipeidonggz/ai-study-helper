"""文档点名检测测试：唯一点名定向、多篇点名不过滤、纯语义不过滤。"""

from app.rag.doc_mention import detect_named_source, single_mention


def test_single_mention_by_title():
    assert single_mention("Anthropic《How we contain Claude》讲了什么？") == "A5"
    assert single_mention("harness engineering 的核心观点？") == "O2"
    assert single_mention("AGENTS.md 应该怎么写？") == "OW1"


def test_multi_mention_returns_none():
    # cmp 场景：同时点名 O2 与 A5，过滤会砍掉跨文档对比 → 不过滤
    assert (
        single_mention(
            "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》对 harness 的理解有什么异同？"
        )
        is None
    )


def test_no_mention_returns_none():
    assert single_mention("A社讲怎么限制 Claude 的文章里 VM 起什么作用？") is None
    assert single_mention("你好") is None


def test_detect_dedupes():
    hits = detect_named_source("0022 里关于 harness engineering 的结论")
    assert hits.count("OW1") == 1 and "O2" in hits
