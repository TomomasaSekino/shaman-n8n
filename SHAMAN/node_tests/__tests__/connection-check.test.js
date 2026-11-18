'use strict';
// 接続確認 Workflow の判定（Code ノード）を、生成した JSON から読み込んで動かす。
// 外部への接続はしない。各ノードの出力（成功・失敗）を、$('ノード名') の値として与える。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JSON_PATH = path.join(__dirname, '../../workflows/SHAMAN_Connection_Check.json');
const WF = JSON.parse(fs.readFileSync(JSON_PATH, 'utf8'));
const codeOf = (name) => WF.nodes.find((n) => n.name === name).parameters.jsCode;

const KEY = 'KEYWORD\n（必須）\nフォーカスキーワード';
const RUN = '実行ID\n【自動】';
const EXEC = '77';

// refs: { ノード名: [item, ...] }。item は { json, binary }。refs にないノードは「未実行」（n8n では例外）。
async function run(name, items, refs = {}) {
  const ctx = {
    $input: { first: () => items[0], all: () => items }, // 暗黙の items は渡さない（n8n 2.35.7 と同じ）
    $: (node) => {
      if (!(node in refs)) throw new Error(`Node '${node}' hasn't been executed`);
      return { first: () => refs[node][0], all: (_branchIndex, runIndex) => runIndex === undefined ? refs[node] : (refs[node][runIndex] || []) };
    },
    $execution: { id: EXEC },
    JSON, Object, String, Number, Error, Array,
  };
  const fn = vm.runInContext(`(async function () {\n${codeOf(name)}\n})`, vm.createContext(ctx));
  return (await fn.call(ctx)).map((it) => it.json);
}
const j = (json, binary) => (binary ? { json, binary } : { json });
const fail = (message) => j({ error: { message } });

describe('Conn: Sheets Pick Row / Conn: Sheets Check', () => {
  const rows = [j({ row_number: 4, [KEY]: '__SHAMAN_AUTO_E2E__', [RUN]: '' }), j({ row_number: 5, [KEY]: '別' })];

  test('予約行を 1 件選び、この実行の印を作る', async () => {
    expect(await run('Conn: Sheets Pick Row', rows)).toEqual([{ ok: true, row: 4, mark: 'CONN-77' }]);
  });

  test('予約行が 0 件・2 件、読み込みの失敗は NG', async () => {
    expect((await run('Conn: Sheets Pick Row', [rows[1]]))[0]).toMatchObject({ ok: false, detail: expect.stringContaining('0 件') });
    expect((await run('Conn: Sheets Pick Row', [rows[0], rows[0]]))[0].detail).toContain('2 件');
    expect((await run('Conn: Sheets Pick Row', [fail('403 PERMISSION_DENIED')]))[0].detail).toContain('403');
  });

  // IF「Conn: Sheets Row Found?」は $json.ok が true のときだけ Write へ進める（false は Check へ直接）。
  const gate = WF.nodes.find((n) => n.name === 'Conn: Sheets Row Found?');
  const outs = WF.connections['Conn: Sheets Row Found?'].main.map((o) => o.map((t) => t.node));

  test('予約行を特定できなければ Write に進まず、Sheets を NG にして診断を続ける', async () => {
    expect(gate.parameters.conditions.conditions[0].leftValue).toBe('={{ $json.ok }}');
    expect(outs).toEqual([['Conn: Sheets Write'], ['Conn: Sheets Check']]);
    for (const input of [[rows[1]], [rows[0], rows[0]], [fail('403 PERMISSION_DENIED')], [j({})]]) {
      const picked = await run('Conn: Sheets Pick Row', input);
      expect(picked[0].ok).toBe(false); // IF は false → Write は呼ばれない
      // Write を通らないので、refs に Write はない（参照すれば「未実行」で例外になる）。
      const out = await run('Conn: Sheets Check', [j(picked[0])], { 'Conn: Sheets Pick Row': [j(picked[0])] });
      expect(out[0]).toMatchObject({ ok: false, detail: picked[0].detail });
    }
    expect(WF.connections['Conn: Sheets Check'].main[0][0].node).toBe('Conn: Gemini Models');
  });

  const pick = { 'Conn: Sheets Pick Row': [j({ ok: true, row: 4, mark: 'CONN-77' })], 'Conn: Sheets Write': [j({})] };

  test('書いた印を読み直せれば OK', async () => {
    const out = await run('Conn: Sheets Check', [j({ row_number: 4, [RUN]: 'CONN-77' })], pick);
    expect(out[0].ok).toBe(true);
  });

  test('読み直した値が違う・書き込みが失敗したら NG', async () => {
    expect((await run('Conn: Sheets Check', [j({ row_number: 4, [RUN]: 'CONN-1' })], pick))[0]).toMatchObject({ ok: false });
    const out = await run('Conn: Sheets Check', [j({ row_number: 4, [RUN]: '' })],
      { ...pick, 'Conn: Sheets Write': [fail('The caller does not have permission')] });
    expect(out[0]).toMatchObject({ ok: false, detail: expect.stringContaining('書き込めません') });
  });
});

describe('Conn: Summarize / Conn: Result', () => {
  const geminiModels = codeOf('Conn: Summarize').match(/var missing = (\[[^\]]*\])/)[1];
  const models = JSON.parse(geminiModels).map((m) => ({ name: 'models/' + m }));
  const anthropicId = codeOf('Conn: Summarize').match(/\['Anthropic', 'Conn: Anthropic Model', "([^"]+)"\]/)[1];
  const openaiId = codeOf('Conn: Summarize').match(/\['OpenAI', 'Conn: OpenAI Model', "([^"]+)"\]/)[1];
  const ok = {
    'Conn: Sheets Check': [j({ ok: true, detail: '行 4' })],
    'Conn: Gemini Models': [j({ models })],
    'Conn: Anthropic Model': [j({ id: anthropicId })],
    'Conn: OpenAI Model': [j({ id: openaiId })],
  };
  const summarize = (refs) => run('Conn: Summarize', [j({})], refs);
  const status = (checks) => Object.fromEntries(checks.map((c) => [c.service, c.ok]));

  // 共有 Conn: WordPress はサイトごとに1回実行され、runIndexで応答を取得する。
  const SITES = Object.keys(JSON.parse(fs.readFileSync(path.join(__dirname, '../../config/sites.json'), 'utf8')));
  const wpOk = { 'Conn: WordPress': SITES.map((_, i) => [j({ id: i + 1 })]) };
  // Google Drive：サイトごとのフォルダー（Conn: Drive Folder 1, 2, … も sites.json の順）。
  const driveOk = Object.fromEntries(SITES.flatMap((id, i) => [
    ['Conn: Drive Folder ' + (i + 1), [j({ id: 'f' + i, name: id })]],
    ['Conn: Drive Image ' + (i + 1), [j({ id: 'i' + i, name: 'a.png' })]],
    ['Conn: Drive Download ' + (i + 1), [j({ id: 'i' + i }, { data: { mimeType: 'image/png' } })]],
  ]));
  Object.assign(ok, driveOk);
  const base = { 'Google Sheets': true, Gemini: true, Anthropic: true, OpenAI: true };

  test('全部成功なら OK（WordPress はサイトごと）', async () => {
    const [s] = await summarize({ ...ok, ...wpOk });
    const wp = SITES.length ? Object.fromEntries(SITES.map((id) => ['WordPress ' + id, true])) : { WordPress: false };
    const drive = SITES.length ? Object.fromEntries(SITES.map((id) => ['Google Drive ' + id, true])) : { 'Google Drive': false };
    expect(status(s.checks)).toEqual({ ...base, ...wp, ...drive });
    expect(s.message).toContain('実行ID 77');
  });

  test('WordPress のノードが実行されていなければ、そのサイトは「未実行」で NG', async () => {
    const [s] = await summarize(ok);
    for (const id of SITES) {
      expect(s.checks.find((x) => x.service === 'WordPress ' + id)).toMatchObject({ ok: false, detail: '未実行' });
    }
  });

  test.each([
    ['Gemini の認証エラー', { 'Conn: Gemini Models': [fail('400 API key not valid')] }, 'Gemini', 'API key not valid'],
    ['本番のモデルが一覧にない', { 'Conn: Gemini Models': [j({ models: [] })] }, 'Gemini', 'モデルが一覧にない'],
    ['Anthropic の認証エラー', { 'Conn: Anthropic Model': [fail('401 invalid x-api-key')] }, 'Anthropic', '401'],
    ['OpenAI のモデル情報がない', { 'Conn: OpenAI Model': [j({})] }, 'OpenAI', 'モデル情報がない'],
    ['サイトのフォルダーがない', { 'Conn: Drive Folder 1': [j({})] }, 'Google Drive ' + SITES[0], 'IMG にフォルダー「' + SITES[0] + '」がない'],
    ['画像がない', { 'Conn: Drive Image 1': [j({})] }, 'Google Drive ' + SITES[0], '画像がない'],
    ['ダウンロードの失敗', { 'Conn: Drive Download 1': [fail('404 File not found')] }, 'Google Drive ' + SITES[0], '404'],
    ['中身のないダウンロード', { 'Conn: Drive Download 1': [j({ id: 'i1' })] }, 'Google Drive ' + SITES[0], '中身'],
  ])('%s は NG', async (_n, over, service, detail) => {
    const [s] = await summarize({ ...ok, ...over });
    const c = s.checks.find((x) => x.service === service);
    expect(c.ok).toBe(false);
    expect(c.detail).toContain(detail);
  });

  test('別名のモデルが正式な ID で返っても OK', async () => {
    const [s] = await summarize({ ...ok, 'Conn: Anthropic Model': [j({ id: anthropicId + '-20260101' })] });
    expect(s.checks.find((x) => x.service === 'Anthropic').ok).toBe(true);
  });

  test('Discord に送るエラー文では、API キーの値を伏せる', async () => {
    const [s] = await summarize({ ...ok, 'Conn: Gemini Models': [fail('400 https://x/models?key=AIzaSECRET123&pageSize=1')] });
    expect(s.message).not.toContain('AIzaSECRET123');
    expect(s.message).toContain('key=***');
  });

  test('実行されなかったノードは「未実行」で NG', async () => {
    const refs = { ...ok };
    delete refs['Conn: Anthropic Model'];
    const [s] = await summarize(refs);
    expect(s.checks.find((x) => x.service === 'Anthropic')).toMatchObject({ ok: false, detail: '未実行' });
  });

  const result = (checks, discord = j({})) =>
    run('Conn: Result', [discord], { 'Conn: Summarize': [j({ checks, message: '' })] });

  test('全部 OK で Discord にも送れたら成功', async () => {
    const [r] = await result([{ service: 'Gemini', ok: true, detail: 'OK' }]);
    expect(r.result).toBe('SHAMAN_CONNECTION_CHECK_OK');
    expect(r.checks.map((c) => c.service)).toEqual(['Gemini', 'Discord']);
    // Actions（run-shaman-n8n.yml）は shamanRun.ok で成否を決める。
    expect(r.shamanRun).toMatchObject({ ok: true, workflow: 'connection' });
  });

  test('NG が 1 つでもあれば実行を失敗させ、理由を出す', async () => {
    // 例外にせず、NG の理由を shamanRun で返す（Webhook の応答に載せ、Actions に出す）。
    const [a] = await result([{ service: 'WordPress', ok: false, detail: '未確認' }]);
    expect(a.result).toBe('SHAMAN_CONNECTION_CHECK_FAILED: WordPress（未確認）');
    expect(a.shamanRun).toMatchObject({ ok: false, workflow: 'connection', reason: 'WordPress（未確認）' });
    const [b] = await result([{ service: 'Gemini', ok: true, detail: 'OK' }], fail('Unknown Webhook'));
    expect(b.shamanRun.ok).toBe(false);
    expect(b.shamanRun.reason).toContain('Discord（Unknown Webhook）');
  });
});

describe('Run: Check（NG なら例外にして、n8n の実行を Error にする）', () => {
  test('ok なら、そのまま返す', async () => {
    const out = await run('Run: Check', [j({ shamanRun: { ok: true, workflow: 'connection' } })]);
    expect(out[0].shamanRun.ok).toBe(true);
  });

  test('NG なら、理由を例外の文にする', async () => {
    await expect(run('Run: Check', [j({ shamanRun: { ok: false, reason: 'Gemini（HTTP 403）' } })]))
      .rejects.toThrow('SHAMAN_RUN_NG: Gemini（HTTP 403）');
    await expect(run('Run: Check', [j({ shamanRun: { ok: false, reason: '1 / 1 行が完了していません', rows: [{ row: 5 }] } })]))
      .rejects.toThrow('行: [{"row":5}]');
  });

  test('結果が無ければ例外', async () => {
    await expect(run('Run: Check', [j({})])).rejects.toThrow('SHAMAN_RUN_NG: 実行結果（shamanRun）がありません');
    await expect(run('Run: Check', [])).rejects.toThrow('SHAMAN_RUN_NG');
  });
});
