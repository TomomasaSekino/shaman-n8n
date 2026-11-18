'use strict';
// P6：参考画像の読み込み → 画像のリクエストの組み立て → 結果の集約と作り直し → 画像の枚数の判定。
// HTTP は HTTP Request ノード（Credential で認証）が送る。ここでは Code ノードの判定だけを試す。
const { loadWorkflow, runCode } = require('../helpers/code-node');

const WF = loadWorkflow();
const j = (json, binary) => (binary ? { json, binary } : { json });
const CFG = { KEYWORD: 'k', imgFolder: 'site-a', MODEL_IMAGE_OPENAI: 'gpt-image-2', IMAGE_QUALITY: 'medium' };
const REFS = [{ b64: Buffer.from('ref-a').toString('base64'), mime: 'image/png', file: 'a.png' },
  { b64: Buffer.from('ref-b').toString('base64'), mime: 'image/jpeg', file: 'b.jpg' }];
const sections = (n) => Array.from({ length: n }, (_, i) => j({
  ...CFG, sectionIndex: i + 1, imageDescription: `desc ${i + 1}`, altText: `alt ${i + 1}`, insertionContext: `ctx ${i + 1}`,
  scene: i === 0 ? { location: '公園', mood: '明るい' } : null,
}));
const ok = (b64) => j({ statusCode: 200, body: { data: [{ b64_json: b64 }] } });
const status = (code, error) => j({ statusCode: code, body: { error } });

describe('Read Reference Images（Google Drive からダウンロードした画像）', () => {
  const refs = { 'Check P6 Mode': [j(CFG)],
    'P6: Check Image Folder': [j({folderId:'F1',folderName:'site-a',imageReferenceNote:''})] };
  const helpers = { getBinaryDataBuffer: async (i) => Buffer.from(`img-${i}`) };

  test('バイナリを referenceImages にまとめ、ファイル名の順に並べる', async () => {
    const items = [j({ id: '2' }, { data: { mimeType: 'image/jpeg', fileName: 'b.jpg' } }),
      j({ id: '1' }, { data: { mimeType: 'image/png', fileName: 'a.png' } })];
    const [out] = await runCode(WF, 'Read Reference Images', items, refs, helpers);
    expect(out.json.KEYWORD).toBe('k');
    expect(out.json.referenceImages).toEqual([
      { b64: Buffer.from('img-1').toString('base64'), mime: 'image/png', file: 'a.png' },
      { b64: Buffer.from('img-0').toString('base64'), mime: 'image/jpeg', file: 'b.jpg' },
    ]);
  });

  test('参考画像の合計が上限（10MB）を超えたら止める', async () => {
    const big = { getBinaryDataBuffer: async () => Buffer.alloc(6 * 1024 * 1024) };
    const items = [j({ id: '1' }, { data: { mimeType: 'image/png', fileName: 'a.png' } }),
      j({ id: '2' }, { data: { mimeType: 'image/png', fileName: 'b.png' } })];
    await expect(runCode(WF, 'Read Reference Images', items, refs, big)).rejects.toThrow('参考画像の合計が 12MB');
  });

  test('空フォルダーは参照なしで続行、取得した画像が未対応形式なら停止', async () => {
    const items = [j({ id: '1' }, { data: { mimeType: 'image/gif', fileName: 'x.gif' } })];
    const noImages = await runCode(WF, 'Read Reference Images', [j({})], refs, helpers);
    expect(noImages[0].json.referenceImages).toEqual([]);
    const downloaded = { ...refs, 'P6: Check Reference Images': [j({ hasReferenceImages: 'yes' })] };
    await expect(runCode(WF, 'Read Reference Images', items, downloaded, helpers))
      .rejects.toThrow('参照画像のダウンロード結果が空です');
  });
});

describe('P6: Check Image Folder / P6: Check Reference Images', () => {
  test('フォルダーなしは参照なしの分岐へ進める', async () => {
    const [missing] = await runCode(WF, 'P6: Check Image Folder', [j({})], { 'Check P6 Mode': [j(CFG)] });
    expect(missing.json).toEqual({ folderId: '', folderName: 'site-a', imageReferenceNote: '参照画像なし（フォルダ無し）' });
    const [out] = await runCode(WF, 'P6: Check Image Folder', [j({ id: 'F1', name: 'site-a' })], { 'Check P6 Mode': [j(CFG)] });
    expect(out.json).toEqual({ folderId: 'F1', folderName: 'site-a', imageReferenceNote: '' });
  });

  test('画像なしは生成へ進み、画像ありはダウンロードへ進む', async () => {
    const refs = { 'P6: Check Image Folder': [j({ folderId: 'F1', folderName: 'site-a' })] };
    const [missing] = await runCode(WF, 'P6: Check Reference Images', [j({})], refs);
    expect(missing.json).toEqual({ hasReferenceImages: '', referenceImages: [], imageReferenceNote: '参照画像なし（0枚）' });
    const out = await runCode(WF, 'P6: Check Reference Images', [j({ id: 'a' }), j({}), j({ id: 'b' })], refs);
    expect(out.map((it) => it.json.id)).toEqual(['a', 'b']);
    expect(out.every((it) => it.json.hasReferenceImages === 'yes')).toBe(true);
  });
});

describe('P6: IMGルートの共有と参照画像なしの警告', () => {
  const findConfig = { 'Check P6 Mode': [j(CFG)] };

  test('IMGルートのIDが見えない場合はフォルダー不在として続行せず止める', async () => {
    const expected = WF.nodes.find(n => n.name === 'P6: Verify IMG Root').parameters.jsCode
      .match(/var expected = "([^"]+)"/)[1];
    await expect(runCode(WF, 'P6: Verify IMG Root', [j({})], findConfig))
      .rejects.toThrow('SHAMAN_DRIVE_IMG_ACCESS_LOST');
    await expect(runCode(WF, 'P6: Verify IMG Root', [j({id:'another-folder'})], findConfig))
      .rejects.toThrow('SHAMAN_DRIVE_IMG_ACCESS_LOST');
    const out = await runCode(WF, 'P6: Verify IMG Root', [j({id:expected})], findConfig);
    expect(out[0].json).toEqual(CFG);
  });

  test('参照画像なしのプロンプトは添付・同一人物を前提にしない', async () => {
    const inputs = sections(1);
    for (const refImages of [[], REFS]) {
      const result = await runCode(WF, 'Build Image Requests', inputs,
        { 'Read Reference Images': [j({referenceImages:refImages, imageReferenceNote: refImages.length ? '' : '参照画像なし（0枚）'})] });
      const text = result[0].json.requestBody.prompt;
      if (refImages.length === 0) {
        expect(text).not.toContain('添付された参照画像');
        expect(text).not.toContain('完全に同一人物');
        expect(result[0].json.imageReferenceNote).toBe('参照画像なし（0枚）');
      } else {
        expect(text).toContain('添付された参照画像');
      }
    }
  });
});

describe('Build Image Requests（OpenAI images/edits の JSON の本文を組み立てる）', () => {
  const refs = { 'Read Reference Images': [j({ referenceImages: REFS })] };

  test('節ごとに 1 件。本文は JSON で、参考画像は data: URL で images に入る（バイナリにしない）', async () => {
    const out = await runCode(WF, 'Build Image Requests', sections(5), refs);
    expect(out).toHaveLength(5);
    // multipart のバイナリを HTTP Request ノードに渡すと、n8n 2.x が 1 バイトずつフォームの項目に作り直して止まる（実行 11）
    expect(out[0].binary).toBeUndefined();
    const body = out[0].json.requestBody;
    expect(Object.keys(body).sort()).toEqual(['images', 'model', 'n', 'output_compression', 'output_format', 'prompt', 'quality', 'size']);
    expect(body).toMatchObject({ model: 'gpt-image-2', size: '1536x864', quality: 'medium', output_format: 'jpeg',
      output_compression: 80, n: 1 });
    expect(body.images).toEqual([
      { image_url: 'data:image/png;base64,' + Buffer.from('ref-a').toString('base64') },
      { image_url: 'data:image/jpeg;base64,' + Buffer.from('ref-b').toString('base64') },
    ]);
    expect(out[0].json.p6Attempt).toBe(1);
    expect(out[0].json.sectionIndex).toBe(1);
  });

  test('scene は項目名付きで、ない節は imageDescription で prompt に入る', async () => {
    const out = await runCode(WF, 'Build Image Requests', sections(2), refs);
    expect(out[0].json.requestBody.prompt).toContain('場所: 公園\n演出・雰囲気: 明るい');
    expect(out[1].json.requestBody.prompt).toContain('desc 2');
  });

  test('作り直しでは、作り直す節だけを、次の回として組み立てる', async () => {
    const results = [
      { ...CFG, sectionIndex: 1, imageB64: 'x', imageIndex: 1 },
      { ...CFG, sectionIndex: 2, imageError: 'api_error_section_2: [429]', imageIndex: 2, imageRetryable: true },
      { ...CFG, sectionIndex: 3, imageError: 'policy_or_bad_request_section_3: [400]', imageIndex: 3, imageRetryable: false },
    ];
    const out = await runCode(WF, 'Build Image Requests', [j({ p6Results: results, p6Attempt: 1 })], refs);
    expect(out.map((it) => it.json.sectionIndex)).toEqual([2]);
    expect(out[0].json.p6Attempt).toBe(2);
    expect(out[0].json.imageError).toBeUndefined();
  });
});

describe('Collect Generated Images（応答の判定と、作り直す節の選び出し）', () => {
  const reqsFor = (n, attempt = 1) => sections(n).map((it) => j({ ...it.json, p6Attempt: attempt, requestBody: 'big' }));

  test('すべて成功：画像を付け、作り直しはない', async () => {
    const refs = { 'Build Image Requests': reqsFor(5) };
    const [out] = await runCode(WF, 'Collect Generated Images', [1, 2, 3, 4, 5].map((i) => ok('b64-' + i)), refs);
    expect(out.json.p6RetryNeeded).toBe('');
    expect(out.json.p6Results.map((r) => [r.imageIndex, r.imageB64, r.imageMime, r.imageFilename])).toEqual(
      [1, 2, 3, 4, 5].map((i) => [i, 'b64-' + i, 'image/jpeg', `article-image-${i}.jpg`]));
    expect(out.json.p6Results[0].requestBody).toBeUndefined();
  });

  test('429・5xx・通信エラーは作り直す。そのほかの 4xx は作り直さない', async () => {
    const refs = { 'Build Image Requests': reqsFor(4) };
    const res = [status(429, { code: 'rate_limit_exceeded' }), status(503, {}), j({ error: { message: 'ECONNRESET' } }),
      status(400, { code: 'moderation_blocked', message: 'blocked' })];
    const [out] = await runCode(WF, 'Collect Generated Images', res, refs);
    const r = out.json.p6Results;
    expect(out.json.p6RetryNeeded).toBe('yes');
    expect(r.map((x) => x.imageRetryable)).toEqual([true, true, true, false]);
    expect(r[0].imageError).toMatch(/^api_error_section_1: \[429\/rate_limit_exceeded\]/);
    expect(r[2].imageError).toContain('ECONNRESET');
    expect(r[3].imageError).toMatch(/^policy_or_bad_request_section_4: \[400\/moderation_blocked\]/);
  });

  test('2 回目は前の結果に重ね、3 回目で失敗したら、もう作り直さない', async () => {
    const prev = [
      { ...CFG, sectionIndex: 1, imageB64: 'p1', imageIndex: 1 },
      { ...CFG, sectionIndex: 2, imageError: 'api_error_section_2: x', imageIndex: 2, imageRetryable: true },
    ];
    const second = await runCode(WF, 'Collect Generated Images', [ok('new-2')],
      { 'Build Image Requests': [j({ ...CFG, sectionIndex: 2, p6Attempt: 2 })], 'P6: Retry Wait': [j({ p6Results: prev })] });
    expect(second[0].json.p6Results.map((x) => x.imageB64)).toEqual(['p1', 'new-2']);
    const third = await runCode(WF, 'Collect Generated Images', [status(500, {})],
      { 'Build Image Requests': [j({ ...CFG, sectionIndex: 2, p6Attempt: 3 })], 'P6: Retry Wait': [j({ p6Results: prev })] });
    expect(third[0].json.p6Results[1].imageRetryable).toBe(false);
    expect(third[0].json.p6RetryNeeded).toBe('');
  });

  test('成功の応答に画像がなければ no_image', async () => {
    const [out] = await runCode(WF, 'Collect Generated Images', [j({ statusCode: 200, body: { data: [] } })],
      { 'Build Image Requests': reqsFor(1) });
    expect(out.json.p6Results[0].imageError).toBe('no_image_section_1');
  });

  test('エラー文の API キーは伏せる', async () => {
    const [out] = await runCode(WF, 'Collect Generated Images', [j({ error: { message: 'GET https://x?key=SECRET123 failed' } })],
      { 'Build Image Requests': reqsFor(1) });
    expect(out.json.p6Results[0].imageError).not.toContain('SECRET123');
  });
});

describe('P6: Images Ready（画像の枚数の判定）', () => {
  const results = (n) => Array.from({ length: 7 }, (_, i) => (i < n
    ? { sectionIndex: i + 1, imageB64: 'x', imageIndex: i + 1, imageRetryable: false }
    : { sectionIndex: i + 1, imageError: 'e', imageIndex: i + 1, imageRetryable: false }));

  test('5 枚以上なら、節ごとの item に戻す', async () => {
    const out = await runCode(WF, 'P6: Images Ready', [j({ p6Results: results(5) })]);
    expect(out).toHaveLength(7);
    expect(out.every((it) => it.json.p6Status === undefined && it.json.imageRetryable === undefined)).toBe(true);
  });

  test('5 枚未満なら、すべての item に insufficient_images_N', async () => {
    const out = await runCode(WF, 'P6: Images Ready', [j({ p6Results: results(4) })]);
    expect(out.every((it) => it.json.p6Status === 'insufficient_images_4')).toBe(true);
  });
});
