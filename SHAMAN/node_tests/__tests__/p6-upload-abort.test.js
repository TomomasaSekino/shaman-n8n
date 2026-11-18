'use strict';
const { runNode } = require('../helpers/n8n-mock');

const BASE = {
  KEYWORD: 'テスト',
  OPTION1: '',
  articleHtml: '<p>テスト記事本文</p>',
  articleTitle: 'テスト記事',
  metaDescription: 'テスト説明',
  slug: 'test-article',
  wpUrl: 'https://example.com',
  AUTHOR: 'テスト著者',
  SITENAME: 'テストサイト',
  BASEURL: 'https://example.com',
  FIRSTPERSON: 'テスト',
  IMAGE_TRIGGER: false,
  imgPath: '/path/to/images',
  rowIndex: 1,
  uploadError: 'upload_failed [index=1, status=500]: WP upload failed [500]: Internal Server Error',
};

// P6: Upload Abort は途中保存の中身を作り、保存の後で P6: Abort Draft Saved / Failed が throw する。
describe('P6: Upload Abort', () => {
  test('途中保存の中身（p6Status=upload_error）とパスを作る', async () => {
    const [out] = await runNode('P6: Upload Abort', [BASE]);
    expect(out.draftFile).toMatch(/\/article_draft_1\.json$/);
    const saved = JSON.parse(out.draftJson);
    expect(saved.KEYWORD).toBe('テスト');
    expect(saved.articleHtml).toBe('<p>テスト記事本文</p>');
    expect(saved.p6Status).toBe('upload_error');
  });

  test('止めるときのメッセージに [P6]・KEYWORD・uploadError を入れる', async () => {
    const [out] = await runNode('P6: Upload Abort', [BASE]);
    expect(out.abortMessage).toContain('[P6]');
    expect(out.abortMessage).toContain('テスト');
    expect(out.abortMessage).toContain('upload_failed');
  });
});

describe('P6: Abort Draft Saved / Failed', () => {
  const built = { abortMessage: '[P6] WP image upload failed for "テスト" | upload_failed', draftFile: '/d/article_draft_1.json' };

  test('保存できたら、途中保存のパスを付けて throw する（ハードストップ）', async () => {
    await expect(runNode('P6: Abort Draft Saved', [{}], { nodeRefs: { 'P6: Upload Abort': built } }))
      .rejects.toThrow(/\[P6\].*article_draft_1\.json/);
  });

  test('保存に失敗しても throw する（保存失敗で握り潰さない）', async () => {
    const err = await runNode('P6: Abort Draft Failed', [{ error: { message: 'disk full' } }], {
      nodeRefs: { 'P6: Upload Abort': built },
    }).catch((e) => e);
    expect(err.message).toContain('[P6]');
    expect(err.message).toContain('disk full');
  });
});
