'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Parse Meta + Extract Title reads the Gemini meta response from $input and the
// prior config (incl. articleHtml) from $('Build P5 Request'). The post title
// must come from <h1> (the real title), NOT the first <h2> section, and the
// <h1> must be removed from the body to avoid duplicating the WP post title.

function metaResponse(text) {
  return { candidates: [{ content: { parts: [{ text }] } }] };
}

const ARTICLE = [
  '<h1>アジングデートは成立するか？初心者と楽しむ成功の秘訣</h1>',
  '<p>導入文です。</p>',
  '<h2>アジングデートは成立するか？研究的視点での検証</h2>',
  '<p>本文です。</p>',
].join('\n');

const CONFIG = { KEYWORD: 'アジング デート', articleHtml: ARTICLE };

describe('Parse Meta + Extract Title — タイトルはh1から', () => {
  test('投稿タイトルは<h1>（セクションの最初のh2ではない）', async () => {
    const out = await runNode('Parse Meta + Extract Title', [metaResponse('メタ説明文')], {
      nodeRefs: { 'Build P5 Request': CONFIG },
    });
    expect(out[0].articleTitle).toBe('アジングデートは成立するか？初心者と楽しむ成功の秘訣');
    // 最初のh2（セクション見出し）をタイトルにしていないこと
    expect(out[0].articleTitle).not.toContain('研究的視点での検証');
  });

  test('本文から<h1>が削除される（WP投稿タイトルとの重複防止）', async () => {
    const out = await runNode('Parse Meta + Extract Title', [metaResponse('メタ説明文')], {
      nodeRefs: { 'Build P5 Request': CONFIG },
    });
    expect(out[0].articleHtml).not.toContain('<h1');
    // 本文（h2以降）は残る
    expect(out[0].articleHtml).toContain('<h2>アジングデートは成立するか？研究的視点での検証</h2>');
    expect(out[0].metaDescription).toBe('メタ説明文');
  });

  test('h1が無い場合は最初のh2にフォールバック', async () => {
    const cfg = { KEYWORD: 'k', articleHtml: '<h2>フォールバック見出し</h2><p>x</p>' };
    const out = await runNode('Parse Meta + Extract Title', [metaResponse('m')], {
      nodeRefs: { 'Build P5 Request': cfg },
    });
    expect(out[0].articleTitle).toBe('フォールバック見出し');
  });
});

describe('Parse Meta + Extract Title — 文字数の注記を除去', () => {
  // Real run 1294 published a meta description ending with「（133文字）」because
  // P5 asks for a strict 120–140 characters and the model appended the count.
  async function meta(text) {
    const out = await runNode('Parse Meta + Extract Title', [metaResponse(text)], {
      nodeRefs: { 'Build P5 Request': CONFIG },
    });
    return out[0].metaDescription;
  }

  test.each([
    ['全角括弧', '秋のアジングを解説します。（133文字）'],
    ['半角括弧', '秋のアジングを解説します。(133文字)'],
    ['隅付き括弧', '秋のアジングを解説します。【133文字】'],
    ['文字数：の表記', '秋のアジングを解説します。（文字数：133）'],
    ['改行のあとに注記', '秋のアジングを解説します。\n（133文字）'],
    ['空白と注記', '秋のアジングを解説します。 133文字'],
  ])('%s を末尾から除去する', async (_label, text) => {
    expect(await meta(text)).toBe('秋のアジングを解説します。');
  });

  test('文中の数値や括弧は残す', async () => {
    const text = '水温が15℃（目安）を下回る晩秋に、20cm前後のアジを狙うコツを解説します。';
    expect(await meta(text)).toBe(text);
  });
});
