'use strict';
const { runNode } = require('../helpers/n8n-mock');

const BASE = {
  rowIndex: 4,
  wpPostId: '123',
  wpPostLink: 'https://example.com/?p=1',
  p6Status: null,
  uploadError: null,
  wpError: null,
  KEYWORD: 'テスト',
  articleTitle: 'テスト記事',
};
const MAP_VARS = { rowIndex: 4, siteId: 'site-a', KEYWORD: 'テスト' };

// Cleanup は書き戻す値を queueValues にして、Queue: Check Final → Queue: Update へ渡す。
async function runCleanup(data, mapVars = MAP_VARS) {
  const [out] = await runNode('Cleanup', [data], { nodeRefs: { 'Map Variables': mapVars } });
  return out;
}

describe('Cleanup — excelStatus 決定ロジック', () => {
  test('wpPostId あり → 完了', async () => {
    expect((await runCleanup({ ...BASE, wpPostId: '123' })).queueValues.STATUS).toBe('完了');
  });

  test('wpPostId なし + p6Status=image_error → 画像エラー', async () => {
    expect((await runCleanup({ ...BASE, wpPostId: null, p6Status: 'image_error' })).queueValues.STATUS).toBe('画像エラー');
  });

  test('wpPostId なし + p6Status=null → エラー', async () => {
    expect((await runCleanup({ ...BASE, wpPostId: null, p6Status: null })).queueValues.STATUS).toBe('エラー');
  });

  test('wpPostId なし + p6Status=skipped → エラー（skippedは画像エラーにならない）', async () => {
    expect((await runCleanup({ ...BASE, wpPostId: null, p6Status: 'skipped' })).queueValues.STATUS).toBe('エラー');
  });
});

describe('Cleanup — 書き戻す値', () => {
  test('書き戻すのは決めた 6 列だけで、値はすべて文字列', async () => {
    const { queueValues } = await runCleanup(BASE);
    expect(Object.keys(queueValues).sort()).toEqual(
      ['QUALITY', 'STATUS', 'WP_POST_ID', 'エラー内容', '完了日時', '開始日時'].sort());
    Object.values(queueValues).forEach((v) => expect(typeof v).toBe('string'));
  });

  test('長いエラー内容は 2000 文字で切る', async () => {
    const { queueValues } = await runCleanup({ ...BASE, wpError: 'x'.repeat(5000) });
    expect(queueValues['エラー内容']).toHaveLength(2000);
  });

  test('制御文字は除く', async () => {
    const { queueValues } = await runCleanup({ ...BASE, wpError: 'a\u0001b\u001fc\nd' });
    expect(queueValues['エラー内容']).toBe('abc\nd');
  });

  test('上限で切って片方だけ残ったサロゲートは除く', async () => {
    const { queueValues } = await runCleanup({ ...BASE, wpError: 'x'.repeat(1999) + '😀' });
    expect(queueValues['エラー内容']).toBe('x'.repeat(1999));
  });

  test('値のない列も空として送る（Queue: Update はいつも 6 列を書く）', async () => {
    const { queueValues } = await runCleanup({ ...BASE, wpPostId: null, wpError: null });
    expect(queueValues.WP_POST_ID).toBe('');
    expect(queueValues['エラー内容']).toBe('');
  });

  test('行番号がなければ書き戻さずに止める', async () => {
    await expect(runCleanup(BASE, { ...MAP_VARS, rowIndex: null })).rejects.toThrow('行番号');
  });

  test('次のノードへ記事の結果をそのまま渡す', async () => {
    const out = await runCleanup(BASE);
    expect(out.wpPostLink).toBe(BASE.wpPostLink);
    expect(out.updatedRowIndex).toBe(4);
    expect(out.cleanup).toBe('success');
  });
});
