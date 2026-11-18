'use strict';
// WordPress へのメディアのアップロードの前後（WP: Prepare Media → サイトごとの WP <n>: Upload Media → WP: Collect Uploads）。
// アップロードは HTTP Request ノード（サイトの WordPress の Credential）が送る。ここでは Code ノードの判定だけを試す。
const { loadWorkflow, runCode } = require('../helpers/code-node');

const WF = loadWorkflow();
const j = (json, binary) => (binary ? { json, binary } : { json });
const img = (i, extra = {}) => ({ KEYWORD: 'k', siteId: 's', imageIndex: i, sectionIndex: i, imageB64: Buffer.from('jpg-' + i).toString('base64'),
  imageMime: 'image/jpeg', imageFilename: `article-image-${i}.jpg`, ...extra });
const media = (id) => j({ statusCode: 201, body: { id, source_url: `https://s.example/m${id}.jpg` } });

describe('WP: Prepare Media', () => {
  test('画像のある節だけを、バイナリにして渡す。ファイル名は安全な文字にする', async () => {
    const items = [j(img(1, { imageFilename: 'a b"c.jpg' })), j({ KEYWORD: 'k', imageIndex: 2, imageError: 'e' }), j(img(3))];
    const out = await runCode(WF, 'WP: Prepare Media', items);
    expect(out.map((it) => it.json.imageIndex)).toEqual([1, 3]);
    expect(out[0].json.mediaFilename).toBe('a_b_c.jpg');
    expect(out[0].json.imageB64).toBeUndefined();
    expect(out[0].json.siteId).toBe('s');
    expect(out[0].binary.media).toEqual({ data: img(1).imageB64, mimeType: 'image/jpeg', fileName: 'a_b_c.jpg' });
  });

  test('アップロードする画像がなければ止める', async () => {
    await expect(runCode(WF, 'WP: Prepare Media', [j({ imageIndex: 1 })])).rejects.toThrow('アップロードする画像がありません');
  });
});

describe('WP: Collect Uploads', () => {
  const ready = [j(img(1)), j({ KEYWORD: 'k', imageIndex: 2, sectionIndex: 2, imageError: 'e' }), j(img(3))];
  const prepared = [j({ ...img(1), imageB64: undefined }), j({ ...img(3), imageB64: undefined })];
  const refs = { 'P6: Images Ready': ready, 'WP: Prepare Media': prepared };

  test('成功：mediaId と mediaUrl を付け、画像のない節は空にする。imageB64 は出さない', async () => {
    const out = (await runCode(WF, 'WP: Collect Uploads', [media(11), media(13)], refs)).map((it) => it.json);
    expect(out.map((r) => [r.imageIndex, r.mediaId, r.mediaUrl])).toEqual(
      [[1, 11, 'https://s.example/m11.jpg'], [2, null, ''], [3, 13, 'https://s.example/m13.jpg']]);
    expect(out.every((r) => r.imageB64 === undefined && r.batchUploadError === undefined)).toBe(true);
    expect(out[0].KEYWORD).toBe('k');
  });

  test('一部が失敗：失敗した節に uploadError、すべての item に batchUploadError', async () => {
    const res = [media(11), j({ statusCode: 500, body: { code: 'rest_upload_unknown_error', message: 'disk full' } })];
    const out = (await runCode(WF, 'WP: Collect Uploads', res, refs)).map((it) => it.json);
    expect(out[0].uploadError).toBeUndefined();
    expect(out[2].uploadError).toBe('upload_failed [index=3, status=500]: disk full');
    expect(out.every((r) => r.batchUploadError === 'upload_failed_1_of_3: disk full')).toBe(true);
  });

  test('通信エラー（応答なし）も uploadError にする', async () => {
    const out = (await runCode(WF, 'WP: Collect Uploads', [j({ error: { message: 'ETIMEDOUT' } }), media(13)], refs))
      .map((it) => it.json);
    expect(out[0].uploadError).toBe('upload_failed [index=1, status=0]: ETIMEDOUT');
    expect(out[0].mediaId).toBeNull();
  });
});
