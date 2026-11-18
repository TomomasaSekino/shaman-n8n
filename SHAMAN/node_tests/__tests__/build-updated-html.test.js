'use strict';
const { runNode } = require('../helpers/n8n-mock');

const BASE = {
  articleHtml: '<p>AIの活用が重要です。</p><p>次の手順で実施します。</p>',
  KEYWORD: 'AI活用',
  mediaUrl: 'https://example.com/image.png',
  mediaId: 10,
  insertionContext: 'AIの活用が重要です',
  altText: 'AI活用イメージ',
  imageIndex: 1,
};

describe('Build Updated HTML', () => {
  test('insertionContext に一致する箇所に画像タグが挿入される', async () => {
    const out = await runNode('Build Updated HTML', [BASE]);
    expect(out[0].updatedHtml).toContain('<figure');
    expect(out[0].updatedHtml).toContain('https://example.com/image.png');
    expect(out[0].imagesInserted).toBe(1);
  });

  test('insertionContext が空の場合は記事末尾に追記される', async () => {
    // insertionContext が空でも mediaUrl があれば末尾追記（アップロード済み画像を捨てない）
    const out = await runNode('Build Updated HTML', [{ ...BASE, insertionContext: '' }]);
    expect(out[0].updatedHtml).toContain('<figure');
    expect(out[0].imagesInserted).toBe(1);
  });

  test('mediaUrl が空の場合は挿入されない', async () => {
    const out = await runNode('Build Updated HTML', [{ ...BASE, mediaUrl: '' }]);
    expect(out[0].updatedHtml).not.toContain('<figure');
    expect(out[0].imagesInserted).toBe(0);
  });

  test('複数画像: 各 insertionContext に挿入される', async () => {
    const items = [
      { ...BASE, insertionContext: 'AIの活用が重要です', mediaUrl: 'https://example.com/img1.png' },
      { ...BASE, insertionContext: '次の手順で実施します', mediaUrl: 'https://example.com/img2.png', imageIndex: 2 },
    ];
    const out = await runNode('Build Updated HTML', items);
    expect(out[0].imagesInserted).toBe(2);
    expect(out[0].updatedHtml).toContain('img1.png');
    expect(out[0].updatedHtml).toContain('img2.png');
  });

  test('insertionContext がマッチしない画像は末尾に追記され imagesInserted にカウントされる', async () => {
    const items = [
      { ...BASE, insertionContext: 'AIの活用が重要です', mediaUrl: 'https://example.com/img1.png' },
      { ...BASE, insertionContext: '存在しないテキスト', mediaUrl: 'https://example.com/img2.png', imageIndex: 2 },
    ];
    const out = await runNode('Build Updated HTML', items);
    // 2枚目はマッチしないが末尾に追記されるため、合計2枚挿入
    expect(out[0].imagesInserted).toBe(2);
    expect(out[0].updatedHtml).toContain('img2.png');
  });

  test('insertionContext にHTMLタグが含まれる場合でもタグを除去して正しい位置に挿入される', async () => {
    const item = { ...BASE,
      insertionContext: '<p>AIの活用が重要です</p>',
      articleHtml: '<p class="wp-block-paragraph">AIの活用が重要です</p><p>本文テキスト</p>',
    };
    const out = await runNode('Build Updated HTML', [item]);
    expect(out[0].imagesInserted).toBe(1);
    // 画像は「AIの活用が重要です」の直後に挿入されること（末尾追記ではなく正しい位置）
    const html = out[0].updatedHtml;
    const figurePos = html.indexOf('<figure');
    const nextParaPos = html.indexOf('<p>本文テキスト</p>');
    // figureが「本文テキスト」段落より前に現れること
    expect(figurePos).toBeGreaterThan(-1);
    expect(figurePos).toBeLessThan(nextParaPos);
  });

  test('全角スペース差異がある insertionContext でも正規化マッチで挿入される', async () => {
    // articleHtml に全角スペース入り見出し
    const html = '<h3>AIの活用が\u3000重要です</h3><p>本文です</p>';
    // insertionContext は半角スペース
    const item = { ...BASE, articleHtml: html, insertionContext: 'AIの活用が 重要です' };
    const out = await runNode('Build Updated HTML', [item]);
    expect(out[0].imagesInserted).toBe(1);
    expect(out[0].updatedHtml).toContain('<figure');
  });

  test('uploadError は出力に保持される', async () => {
    const out = await runNode('Build Updated HTML', [{ ...BASE, uploadError: 'upload_failed [index=1, status=500]: ...' }]);
    expect(out[0].uploadError).toContain('upload_failed');
  });

  test('insertionContext に ASCII 正規表現特殊文字が含まれる場合でもクラッシュせず末尾追記される', async () => {
    // ASCII "(", "[", "+" は RegExp 特殊文字 — safeContext でエスケープされなければ SyntaxError
    const item = { ...BASE,
      insertionContext: 'AIの活用 (see [below]) が重要です',
      articleHtml: '<p>AIの活用が重要です</p>',
    };
    // クラッシュせず imagesInserted === 1 になることを確認（末尾フォールバック）
    const out = await runNode('Build Updated HTML', [item]);
    expect(out[0].imagesInserted).toBe(1);
    expect(out[0].updatedHtml).toContain('<figure');
  });

  test('mediaUrl が http/https 以外（javascript:）の場合は src が空になる', async () => {
    const item = { ...BASE, mediaUrl: 'javascript:alert(1)' };
    const out = await runNode('Build Updated HTML', [item]);
    expect(out[0].updatedHtml).not.toContain('javascript:');
    // src="" か src 属性が空文字になること
    expect(out[0].updatedHtml).toMatch(/src=""/);
  });

  test('altText にシングルクォートが含まれる場合は &#39; にエスケープされる', async () => {
    const item = { ...BASE, altText: "AI's article" };
    const out = await runNode('Build Updated HTML', [item]);
    expect(out[0].updatedHtml).toContain('&#39;');
    expect(out[0].updatedHtml).not.toContain("AI's");
  });

  test('insertionContext が見出しの場合、画像は </h3> の後ろに入る（見出しの中に入らない）', async () => {
    const item = { ...BASE,
      insertionContext: 'アジングが初心者には難しい理由',
      articleHtml: '<h3>アジングが初心者には難しい理由</h3><p>本文テキスト</p>',
    };
    const out = await runNode('Build Updated HTML', [item]);
    const html = out[0].updatedHtml;
    expect(out[0].imagesInserted).toBe(1);
    // figure は </h3> の直後にあること（見出しタグの内側ではない）
    expect(html).toMatch(/<\/h3>\s*<figure/);
    // figure が </h3> より前（＝見出し内）に現れないこと
    expect(html.indexOf('<figure')).toBeGreaterThan(html.indexOf('</h3>'));
  });

  test('全角スペース差異がある見出しでも </h3> の後ろに入る', async () => {
    const item = { ...BASE,
      insertionContext: 'アジングが 重要です',
      articleHtml: '<h3>アジングが　重要です</h3><p>本文</p>',
    };
    const out = await runNode('Build Updated HTML', [item]);
    const html = out[0].updatedHtml;
    expect(out[0].imagesInserted).toBe(1);
    expect(html).toMatch(/<\/h3>\s*<figure/);
  });
});
