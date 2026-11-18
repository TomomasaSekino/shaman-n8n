'use strict';
const { runNode } = require('../helpers/n8n-mock');

// 途中保存（article_draft_<row>.json）は行番号で探すので、別の行の古い下書きを拾わないよう SITE_ID・KEYWORD を照合する。
// 中身は Read Draft → Draft From File（標準ノード）が読み、json.data に文字列で入る。
const ROW = { rowIndex: 4, siteId: 'site-a', KEYWORD: 'キーワードA', STATUS: '画像エラー' };
const load = (text) => runNode('Load Saved Article', [{ data: text }], { nodeRefs: { 'Map Variables': ROW } });

describe('Load Saved Article', () => {
  test('SITE_ID と KEYWORD が行と一致する下書きを読み込む', async () => {
    const [out] = await load(JSON.stringify({ siteId: 'site-a', KEYWORD: ' キーワードA ', articleHtml: '<p>本文</p>' }));
    expect(out.articleHtml).toBe('<p>本文</p>');
    expect(out.IMAGE_TRIGGER).toBe('TRUE');
    expect(out.rowIndex).toBe(4);
  });

  test.each([
    ['KEYWORD が違う', { siteId: 'site-a', KEYWORD: '別のキーワード' }],
    ['SITE_ID が違う', { siteId: 'site-b', KEYWORD: 'キーワードA' }],
    ['SITE_ID のない古い下書き', { KEYWORD: 'キーワードA' }],
  ])('%s下書きは使わずに止める', async (_name, draft) => {
    await expect(load(JSON.stringify(draft))).rejects.toThrow('一致しません');
  });

  test('中身が空・JSON でないなら止める（Draft Unusable へ）', async () => {
    await expect(load('')).rejects.toThrow('空です');
    await expect(load(undefined)).rejects.toThrow('空です');
    await expect(load('{broken')).rejects.toThrow('JSON');
  });
});

describe('Draft Unusable', () => {
  test('画像だけの再開をやめ、STATUS を「エラー」にする値を Cleanup へ渡す', async () => {
    const [out] = await runNode('Draft Unusable', [{ error: { message: 'No binary data exists on item' } }], {
      nodeRefs: { 'Map Variables': ROW },
    });
    expect(out.rowIndex).toBe(4);
    expect(out.p6Status).toBe('draft_load_error');
    expect(out.wpPostId).toBeNull();
    expect(out.wpError).toContain('No binary data');
  });

  test('Cleanup は「エラー」として書き戻す', async () => {
    const [unusable] = await runNode('Draft Unusable', [{ error: 'x' }], { nodeRefs: { 'Map Variables': ROW } });
    const [out] = await runNode('Cleanup', [unusable], { nodeRefs: { 'Map Variables': ROW } });
    expect(out.queueValues.STATUS).toBe('エラー');
  });
});

describe('P6: Draft Saved → Cleanup', () => {
  test('保存できたときだけ「画像エラー」になる', async () => {
    const [saved] = await runNode('P6: Draft Saved', [{}], { nodeRefs: { 'P6: Save Article': { ...ROW, p6Status: 'x' } } });
    const [out] = await runNode('Cleanup', [saved], { nodeRefs: { 'Map Variables': ROW } });
    expect(out.queueValues.STATUS).toBe('画像エラー');
  });
});
