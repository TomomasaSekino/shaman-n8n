'use strict';
// API使用量（2026-11-09。DOCS/API_USAGE_LOG.md）：API の応答から、呼び出し 1 回ごとのトークン数を 1 行にする。
const { loadWorkflow, runCode } = require('../helpers/code-node');

const WF = loadWorkflow();
const j = (json) => ({ json });
const MV = { 'Map Variables': [j({ rowIndex: 31, siteId: 'kuro-aji-lab.com', KEYWORD: 'アジング コツ 夜' })] };

describe('Usage: Row（応答からトークン数を読む）', () => {
  test('Gemini（usageMetadata）', async () => {
    const res = { candidates: [{ finishReason: 'STOP' }], modelVersion: 'gemini-3.1-pro-preview',
      usageMetadata: { promptTokenCount: 100, candidatesTokenCount: 200, thoughtsTokenCount: 50, totalTokenCount: 350 } };
    const [row] = await runCode(WF, 'Usage: Row P1', [j(res)],
      { ...MV, 'Usage: Stamp P1': [j({ geminiModel: 'gemini-3.1-pro-preview', apiRequestedAt: Date.now() - 1000 })] },
      { executionId: '12' });
    expect(row.json).toMatchObject({ '実行ID': '12', 'キューの行': 31, SITE_ID: 'kuro-aji-lab.com', KEYWORD: 'アジング コツ 夜',
      '工程': 'P1', 'モデル': 'gemini-3.1-pro-preview', 'HTTPの状態': 200, '入力トークン': 100, '出力トークン': 200,
      '思考トークン': 50, '合計トークン': 350, '停止理由': 'STOP' });
    expect(row.json['所要時間ms']).toBeGreaterThanOrEqual(1000);
    expect(JSON.parse(row.json.usage)).toEqual(res.usageMetadata);
  });

  test('Claude（usage。キャッシュの読み込み・作成）', async () => {
    const res = { model: 'claude-opus-5-5', stop_reason: 'end_turn',
      usage: { input_tokens: 10684, output_tokens: 44569, cache_read_input_tokens: 5, cache_creation_input_tokens: 7 } };
    const [row] = await runCode(WF, 'Usage: Row P4', [j(res)], { ...MV, 'Usage: Stamp P4': [j({ claudeModel: 'claude-opus-5-5' })] });
    expect(row.json).toMatchObject({ '工程': 'P4', 'モデル': 'claude-opus-5-5', '入力トークン': 10684, '出力トークン': 44569,
      'キャッシュ読み込みトークン': 5, 'キャッシュ作成トークン': 7, '停止理由': 'end_turn' });
  });

  test('OpenAI の画像（HTTP の応答そのもの。節ごとに 1 行。失敗の状態コードも残す）', async () => {
    const ok = { statusCode: 200, body: { data: [{ b64_json: 'x' }],
      usage: { input_tokens: 3000, output_tokens: 4000, total_tokens: 7000, input_tokens_details: { image_tokens: 2900, text_tokens: 100 } } } };
    const bad = { statusCode: 400, body: { error: { code: 'moderation_blocked' } } };
    const rows = await runCode(WF, 'Usage: Row P6', [j(ok), j(bad), j({ error: { message: 'timeout' } })],
      { ...MV, 'Usage: Stamp P6': [j({ requestBody: { model: 'gpt-image-2' } }), j({ requestBody: { model: 'gpt-image-2' } }), j({})] });
    expect(rows).toHaveLength(3);
    expect(rows[0].json).toMatchObject({ '工程': 'P6', 'モデル': 'gpt-image-2', 'HTTPの状態': 200, '入力トークン': 3000,
      '出力トークン': 4000, '合計トークン': 7000 });
    expect(rows[1].json).toMatchObject({ 'HTTPの状態': 400, '入力トークン': '', '出力トークン': '' });
    expect(rows[2].json).toMatchObject({ 'HTTPの状態': 0, '停止理由': 'error', usage: 'timeout' });
  });

  test('P6 は、前の工程から残った geminiModel（P6A）ではなく、リクエストのモデルを書く（本番の実行 43）', async () => {
    const ok = { statusCode: 200, body: { data: [{ b64_json: 'x' }], usage: { input_tokens: 12514, output_tokens: 1078 } } };
    const stamp = { geminiModel: 'gemini-3.8-flash', requestBody: JSON.stringify({ model: 'gpt-image-2', prompt: 'p' }) };
    const [row] = await runCode(WF, 'Usage: Row P6', [j(ok)], { ...MV, 'Usage: Stamp P6': [j(stamp)] });
    expect(row.json['モデル']).toBe('gpt-image-2');
  });

  test('呼び直し（P2 の 2 回目）では、2 回目の呼び出しの記録を読む', async () => {
    const res = { candidates: [{ finishReason: 'STOP' }], usageMetadata: { promptTokenCount: 3691, candidatesTokenCount: 162 } };
    const [row] = await runCode(WF, 'Usage: Row P2', [j(res)], { ...MV,
      'Usage: Stamp P2': { runs: [[j({ geminiModel: 'old', apiRequestedAt: 1 })], [j({ geminiModel: 'gemini-3.5-flash-lite', apiRequestedAt: Date.now() })]] } });
    expect(row.json['モデル']).toBe('gemini-3.5-flash-lite');
    expect(row.json['所要時間ms']).toBeLessThan(60000);
  });

  test('読めないときも止めずに、理由を書いた 1 行にする', async () => {
    const [row] = await runCode(WF, 'Usage: Row P2', [j({ usageMetadata: { promptTokenCount: 'x' } })], {});
    expect(row.json['工程']).toBe('P2');
    expect(row.json['入力トークン']).toBe('');
  });
});

describe('Usage: Pass / Usage: Sheet Ready', () => {
  test('Usage: Pass は API の出力をそのまま渡す（Parse が受け取る item は変わらない）', async () => {
    const api = [j({ candidates: [1] }), j({ candidates: [2] })];
    const out = await runCode(WF, 'Usage: Pass P3', [j({ written: true })], { 'Gemini: Generate Content': api });
    expect(out.map((o) => o.json)).toEqual(api.map((o) => o.json));
  });

  test('Usage: Sheet Ready は、シートを作ったときだけ true', async () => {
    const [made] = await runCode(WF, 'Usage: Sheet Ready', [j({ sheetId: 1, title: 'API使用量' })]);
    expect(made.json.usageSheetCreated).toBe(true);
    const [none] = await runCode(WF, 'Usage: Sheet Ready', [j({})]);
    expect(none.json.usageSheetCreated).toBe(false);
    // シートを作れなかった（エラーの item）ときも false にし、見出しを書かずにキューへ進む
    const [failed] = await runCode(WF, 'Usage: Sheet Ready', [j({ error: { message: 'The caller does not have permission' } })]);
    expect(failed.json.usageSheetCreated).toBe(false);
  });
});
