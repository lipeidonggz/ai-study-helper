"""格式适配测试：编码探测（GB18030 页面不再乱码）+ 内容容器/叶子 div 兼容。"""

from app.kb.chunker import extract_sections_html
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
