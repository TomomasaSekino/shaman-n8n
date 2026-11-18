'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Parse Strengthened reads the Claude (Anthropic Messages API) response from
// $input and the prior config from $('Build P4 Request'). It must strip CSS
// that would override the site's stylesheet, while keeping class/id attributes
// that the site CSS targets (e.g. wp-block-paragraph).

const CONFIG = {
  KEYWORD: 'AI活用',
  htmlContent: '<p>old</p>',
  outlineText: 'outline',
  researchText: 'research',
};

function claudeResponse(html) {
  return { content: [{ type: 'text', text: html }] };
}

describe('Parse Strengthened — CSS除去', () => {
  test('<style> ブロックが除去される', async () => {
    const html = '<h2>見出し</h2><style>.x{color:red}</style><p>本文</p>';
    const out = await runNode('Parse Strengthened', [claudeResponse(html)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].articleHtml).not.toContain('<style');
    expect(out[0].articleHtml).not.toContain('color:red');
    expect(out[0].articleHtml).toContain('<p>本文</p>');
  });

  test('インライン style= 属性が除去される', async () => {
    const html = '<h2 style="margin:0">見出し</h2><p style=\'color:blue\'>本文</p>';
    const out = await runNode('Parse Strengthened', [claudeResponse(html)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].articleHtml).not.toMatch(/style\s*=/i);
    expect(out[0].articleHtml).toContain('<h2>見出し</h2>');
  });

  test('class 属性は保持される（サイトCSSが利用するため）', async () => {
    const html = '<h2>見出し</h2><p class="wp-block-paragraph" style="color:red">本文</p>';
    const out = await runNode('Parse Strengthened', [claudeResponse(html)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].articleHtml).toContain('class="wp-block-paragraph"');
    expect(out[0].articleHtml).not.toMatch(/style\s*=/i);
  });

  test('コードフェンス内のHTMLでもCSS除去が効く', async () => {
    const html = '```html\n<h2>見出し</h2><p style="x">本文</p>\n```';
    const out = await runNode('Parse Strengthened', [claudeResponse(html)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].articleHtml).not.toMatch(/style\s*=/i);
    expect(out[0].articleHtml).toContain('<h2>見出し</h2>');
  });

  test('大きな中間フィールドは削除される', async () => {
    const html = '<h2>見出し</h2><p>本文</p>';
    const out = await runNode('Parse Strengthened', [claudeResponse(html)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].htmlContent).toBeUndefined();
    expect(out[0].outlineText).toBeUndefined();
    expect(out[0].researchText).toBeUndefined();
    expect(out[0].KEYWORD).toBe('AI活用');
  });
});

describe('Parse Strengthened — 完全HTML文書の殻除去', () => {
  const FULL_DOC = [
    '```html',
    '<!DOCTYPE html>',
    '<html lang="ja">',
    '<head>',
    '<meta charset="UTF-8">',
    '<title>記事タイトル</title>',
    '<style>body{max-width:860px}</style>',
    '</head>',
    '<body>',
    '<h1>記事タイトル</h1>',
    '<h2>見出し</h2>',
    '<div class="box-point" style="color:red"><p>本文</p></div>',
    '</body>',
    '</html>',
    '```',
  ].join('\n');

  test('body内だけ抽出し、doctype/head/meta/title/styleを除去（h1とclassは残す）', async () => {
    const out = await runNode('Parse Strengthened', [claudeResponse(FULL_DOC)], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    const h = out[0].articleHtml;
    expect(h).not.toMatch(/<!DOCTYPE/i);
    expect(h).not.toContain('<html');
    expect(h).not.toContain('<head');
    expect(h).not.toContain('<meta');
    expect(h).not.toContain('<title');
    expect(h).not.toContain('<style');
    expect(h).not.toContain('<body');
    expect(h).not.toMatch(/style\s*=/i);
    // 本文・class・h1 は保持（h1 削除は後段の Parse Meta が担当）
    expect(h).toContain('<h2>見出し</h2>');
    expect(h).toContain('class="box-point"');
    expect(h).toContain('<h1>記事タイトル</h1>');
  });
});

describe('Parse Strengthened — Claude Sonnet 5 (adaptive thinking)', () => {
  test('text ブロックの前に thinking ブロックがあっても本文を取り出す', async () => {
    const response = {
      stop_reason: 'end_turn',
      content: [
        { type: 'thinking', thinking: '', signature: 'sig' },
        { type: 'text', text: '<h2>見出し</h2><p>本文</p>' },
      ],
    };
    const out = await runNode('Parse Strengthened', [response], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    });
    expect(out[0].articleHtml).toBe('<h2>見出し</h2><p>本文</p>');
  });

  test('stop_reason=max_tokens は途中切れとしてエラーにする', async () => {
    const response = {
      stop_reason: 'max_tokens',
      content: [{ type: 'text', text: '<h2>見出し</h2><p>途中で切れ' }],
    };
    await expect(runNode('Parse Strengthened', [response], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    })).rejects.toThrow('max_tokens');
  });

  test('thinking だけで上限に達した場合も max_tokens としてエラーにする', async () => {
    const response = {
      stop_reason: 'max_tokens',
      content: [{ type: 'thinking', thinking: '', signature: 'sig' }],
    };
    await expect(runNode('Parse Strengthened', [response], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    })).rejects.toThrow('max_tokens');
  });

  test('stop_reason=refusal はエラーにする', async () => {
    const response = { stop_reason: 'refusal', content: [] };
    await expect(runNode('Parse Strengthened', [response], {
      nodeRefs: { 'Build P4 Request': CONFIG },
    })).rejects.toThrow('refusal');
  });
});
