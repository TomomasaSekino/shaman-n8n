'use strict';
const { runNode } = require('../helpers/n8n-mock');

const BASE = {
  rowIndex: 3,
  siteId: 'site-a',
  KEYWORD: 'テストキーワード',
  OPTION1: '',
  articleHtml: '<p>本文</p>',
  articleTitle: 'テスト記事',
  metaDescription: 'メタ説明',
  slug: 'test-article',
  wpUrl: 'https://example.com',
  AUTHOR: 'author',
  SITENAME: 'サイト名',
  BASEURL: 'https://example.com',
  FIRSTPERSON: '私',
  IMAGE_TRIGGER: 'TRUE',
  imgPath: '/path/to/imgs',
  p6Status: 'image_api_error',
};

// P6: Save Article は中身（draftJson）とパス（draftFile）だけを作る。書き込みは P6: Write Draft（標準ノード）。
describe('P6: Save Article', () => {
  test('article_draft_{rowIndex}.json のパスと、記事の中身を作る', async () => {
    const [out] = await runNode('P6: Save Article', [BASE]);
    expect(out.draftFile).toMatch(/\/article_draft_3\.json$/);
    const saved = JSON.parse(out.draftJson);
    expect(saved.articleHtml).toBe('<p>本文</p>');
    expect(saved.articleTitle).toBe('テスト記事');
    expect(saved.KEYWORD).toBe('テストキーワード');
    expect(saved.siteId).toBe('site-a');
  });

  test('まだ「画像エラー」にしない（保存できたかは P6: Write Draft の後で決める）', async () => {
    const [out] = await runNode('P6: Save Article', [BASE]);
    expect(out.p6Status).toBe('image_api_error');
  });

  test('行番号がなければ止める（どの行の下書きか分からない）', async () => {
    await expect(runNode('P6: Save Article', [{ ...BASE, rowIndex: null }])).rejects.toThrow('行番号');
  });
});

describe('P6: Draft Saved', () => {
  test('保存できたら image_error にし、wpPostId を null にする', async () => {
    const built = { ...BASE, draftFile: '/d/article_draft_3.json', draftJson: '{}' };
    const [out] = await runNode('P6: Draft Saved', [{}], { nodeRefs: { 'P6: Save Article': built } });
    expect(out.p6Status).toBe('image_error');
    expect(out.wpPostId).toBeNull();
    expect(out.articleDraftFile).toBe('/d/article_draft_3.json');
    expect(out).not.toHaveProperty('draftJson');
  });
});

describe('P6: Draft Save Failed', () => {
  test('保存できなければ「画像エラー」にせず、理由を wpError に残す', async () => {
    const built = { ...BASE, draftFile: '/d/article_draft_3.json', draftJson: '{}' };
    const [out] = await runNode('P6: Draft Save Failed', [{ error: { message: 'EACCES: permission denied' } }], {
      nodeRefs: { 'P6: Save Article': built },
    });
    expect(out.p6Status).toBe('draft_save_error');
    expect(out.wpPostId).toBeNull();
    expect(out.wpError).toContain('EACCES');
    expect(out.wpError).toContain('image_api_error');
    expect(out).not.toHaveProperty('draftJson');
  });
});
