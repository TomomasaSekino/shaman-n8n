'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Parse Content reads the P3 (Gemini) draft. P3 has no search tool, so any <a> it writes is invented
// (real run 1295: three internal links, all 404). Links are unwrapped here and only the anchor text is
// kept; real links are added later from verified candidates.

function gemini(text) {
  return { candidates: [{ content: { parts: [{ text }] } }] };
}
async function parse(html) {
  const out = await runNode('Parse Content', [gemini(html)], { nodeRefs: { 'Build P3 Request': { KEYWORD: 'k' } } });
  return out[0];
}

describe('Parse Content — P3 draft links', () => {
  test('内部リンクを外し、アンカー文字列は残す', async () => {
    const out = await parse('<h2>章</h2><p>詳しくは<a href="https://kuro-aji-lab.com/ester-line-merit/">エステルラインの記事</a>を読んでください。</p>');
    expect(out.htmlContent).not.toContain('<a');
    expect(out.htmlContent).toContain('詳しくはエステルラインの記事を読んでください。');
    expect(out.p3LinksRemoved).toBe(1);
  });

  test('属性付きの外部リンクも複数まとめて外す', async () => {
    const html = '<h2>章</h2><p><a href="https://a.example/" target="_blank" rel="noopener">A</a>と<a class="x" href="https://b.example/">B</a></p>';
    const out = await parse(html);
    expect(out.htmlContent).toBe('<h2>章</h2><p>AとB</p>');
    expect(out.p3LinksRemoved).toBe(2);
  });

  test('リンクが無ければ本文はそのまま', async () => {
    const html = '<h2>章</h2><h3>節</h3><p>本文です。</p>';
    const out = await parse(html);
    expect(out.htmlContent).toBe(html);
    expect(out.p3LinksRemoved).toBe(0);
  });
});
