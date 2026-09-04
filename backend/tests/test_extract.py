"""格式适配测试：编码探测（GB18030 页面不再乱码）+ 内容容器/叶子 div 兼容。"""

from app.kb.chunker import extract_lake_content, extract_sections_html, extract_sections_html_auto
from app.kb.extract import read_text_auto


def test_read_utf8(tmp_path):
    p = tmp_path / "a.html"
    p.write_bytes("中国信通院可信AI。".encode("utf-8"))
    assert "中国信通院" in read_text_auto(p)


def test_read_gb18030(tmp_path):
    """模拟 D8：GB18030 编码的页面，探测后应得到可读中文而非替换符乱码。"""
    p = tmp_path / "b.html"
    text = "中国信通院可信AI智能体评估体系2.0。"
    p.write_bytes(text.encode("gb18030"))
    decoded = read_text_auto(p)
    assert "评估体系" in decoded
    assert "\ufffd" not in decoded


def test_leaf_div_content_with_hint():
    """老式新闻站：正文在 #left_cont 的叶子 div 里，侧栏列表不应混入。"""
    html = """
    <html><body>
      <div class="sidebar">
        <li>相关新闻：NucMind 2.0 发布</li>
        <li>相关新闻：NIST CSF 2.0 通过认证</li>
      </div>
      <div id="left_cont">
        <div class="title">信通院可信AI智能体评估体系2.0发布</div>
        <div>中国信息通信研究院人工智能研究所长期关注智能体发展态势。</div>
        <div>本次发布的评估体系覆盖可信、能力、可控等维度，是行业的重要参考。</div>
      </div>
    </body></html>
    """
    sections = extract_sections_html(html)
    paras = [p for s in sections for p in s.paragraphs]
    joined = "\n".join(paras)
    assert "评估体系覆盖" in joined
    assert "NucMind" not in joined and "NIST" not in joined


def test_main_root_keeps_hero_lede_and_drops_newsletter_cta():
    """main 根应保留 article 外的开场 lede，并过滤尾部 newsletter CTA。"""
    html = """
    <html><body>
      <main>
        <section class="hero">
          <p>As agents grow more capable, so does their potential blast radius.</p>
        </section>
        <article>
          <h1>Containment patterns</h1>
          <p>Twelve months ago, we rejected the idea of granting Claude broad access.</p>
        </article>
        <section class="cta">
          <h2>Get the developer newsletter</h2>
          <p>Sign up to receive updates from the team.</p>
          <p>Product updates, how-tos, community spotlights every month.</p>
        </section>
      </main>
    </body></html>
    """
    sections = extract_sections_html(html)
    paras = [p for s in sections for p in s.paragraphs]
    joined = "\n".join(paras)
    assert "As agents grow more capable" in joined
    assert "Twelve months ago" in joined
    assert "newsletter" not in joined and "Sign up" not in joined


def test_doc_header_h1_kept_but_site_header_excluded():
    """自有文档：<header class=page-head> 里的 <h1> 标题应成为路径根；
    站点级 header 在 <main> 外时应被根容器选择排除（不进内容）。"""
    own_doc = """
    <html><body>
      <header class="page-head"><h1>0022 · 主动式验证笔记</h1></header>
      <section class="card"><h2>会话元信息</h2><p>状态：温度档位扫描定档：1.0。</p></section>
    </body></html>
    """
    sections = extract_sections_html(own_doc)
    assert sections[0].path.startswith("0022 · 主动式验证笔记")
    assert "会话元信息" in sections[0].path
    assert "定档：1.0" in "".join(p for s in sections for p in s.paragraphs)

    article = """
    <html><body>
      <header>站点导航：不该进来</header>
      <main><h1>正文标题</h1><p>正文内容。</p></main>
    </body></html>
    """
    sections = extract_sections_html(article)
    joined = "".join(p for s in sections for p in s.paragraphs)
    assert "正文内容。" in joined and "站点导航" not in joined


def test_lake_content_restored_and_auto_extracts():
    """D6 型页面：正文以 JS 字面量（GLOBAL_CONFIG.larkContent）藏在 script 里，静态 DOM 只有导航。"""
    static_body = (
        '<article class="focus">关注阿里云公众号</article>'
        '<div class="nav">开发者社区</div>'
    )
    lake = (
        '<p data-lake-id="p0">\\u4F5C\\u8005\\uFF1A\\u674E\\u56FD\\u5F3A<\\/p>'
        '<h1>01 \\u4F01\\u4E1A\\u6784\\u5EFA Agent \\u65F6\\u7684\\u4E94\\u5927\\u75DB\\u70B9</h1>'
        '<p>\\u5F53\\u524D\\uFF0C\\u4F01\\u4E1A\\u6295\\u4EA7 Agent \\u7684\\u70ED\\u60C5\\u7A7A\\u524D\\u9AD8\\u6DA8\\u3002</p>'
    )
    html = (
        "<html><body>" + static_body +
        "<script>GLOBAL_CONFIG.larkContent = '" + lake + "';</script>" +
        "</body></html>"
    )
    restored = extract_lake_content(html)
    assert restored is not None
    assert "作者：李国强" in restored  # unicode 反转义
    assert "企业构建 Agent 时的五大痛点" in restored
    sections = extract_sections_html_auto(html)
    joined = "".join(p for s in sections for p in s.paragraphs)
    assert "当前，企业投产 Agent 的热情空前高涨。" in joined
    assert any("01 企业构建 Agent 时的五大痛点" == s.path for s in sections)
    assert "关注阿里云" not in joined  # 静态壳不进正文


def test_lake_absent_falls_back_to_normal():
    """无 larkContent 的页面走常规抽取，行为不变。"""
    html = "<html><body><main><h1>标题</h1><p>正文。</p></main></body></html>"
    assert extract_sections_html_auto(html) == extract_sections_html(html)
