'use strict';
const { runNode } = require('../helpers/n8n-mock');

const SITE_ID = 'SITE_ID\n（必須）\nサイト識別子';
const KEYWORD = 'KEYWORD\n（必須）\nフォーカスキーワード';
const STATUS = 'STATUS\n【自動】';
const RUN = '実行ID\n【自動】';
const EXEC = { id: 'exec-42' };

// Google Sheets ノード（読み込み）の出力：行ごとに 1 item。row_number はスプレッドシートの行番号。
function sheetRow(row, values) {
  return { row_number: row, [SITE_ID]: 'site-a', [KEYWORD]: 'キーワードA', [STATUS]: '待機中', [RUN]: '', ...values };
}
const MV = { rowIndex: 4, siteId: 'site-a', KEYWORD: 'キーワードA', pipelineStartedAt: '2026-11-01T00:00:00Z' };

describe('Queue: Parse', () => {
  const registry = [{ SITE_ID: 'kuro-aji-lab.com' }, { SITE_ID: 'kuromimi.online' }];
  const runParse = (queueRows, registered = registry, nodeRefs = {}) => runNode(
    'Queue: Parse', registered,
    { nodeRefs: { 'Queue: Read': { items: queueRows }, ...nodeRefs } }
  );

  test('登録サイトの行はrowIndexとURLが正しく設定される', async () => {
    const out = await runParse([
      sheetRow(4, { [SITE_ID]: 'kuro-aji-lab.com' }),
      sheetRow(7, { [SITE_ID]: 'kuromimi.online', [KEYWORD]: 'B' }),
    ]);
    expect(out.map((o) => o.rowIndex)).toEqual([4, 7]);
    expect(out[0]).not.toHaveProperty('row_number');
    expect(out[0].siteId).toBe('kuro-aji-lab.com');
    expect(out[0].siteConfig.WP_URL).toBe('https://kuro-aji-lab.com');
    expect(out[1].siteConfig.WP_URL).toBe('https://kuromimi.online');
  });

  test('未登録SITE_IDは正しいドメイン名でもsiteConfigがnullになり受付拒否される', async () => {
    const [out] = await runParse([sheetRow(4, { [SITE_ID]: 'unregistered.invalid' })]);
    expect(out.siteConfig).toBeNull();
  });

  test('登録シートが空なら、処理を進めず明示エラー', async () => {
    await expect(runParse([sheetRow(4)], [{}]))
      .rejects.toThrow('WP_SITE_REGISTRY_EMPTY');
  });

  test('空のキュー行は除外し、0件は手動実行でエラーになる', async () => {
    const out = await runParse([{}, { row_number: 5, [SITE_ID]: '', [KEYWORD]: 'A' }]);
    expect(out.map((o) => o.rowIndex)).toEqual([5]);
    await expect(runParse([{}, { row_number: 5, [SITE_ID]: '', [KEYWORD]: null }]))
      .rejects.toThrow('SHAMAN_RUN_NG: 「Queue: Parse」の出力が 0 件です');
  });
});

describe('Queue: Check Claim', () => {
  const run = (rows) => runNode('Queue: Check Claim', rows, { nodeRefs: { 'Map Variables': MV }, globals: { $execution: EXEC } });

  test.each(['待機中', '画像エラー'])('STATUS=%s の行を、この実行の ID で確保する', async (status) => {
    const [out] = await run([sheetRow(3, { [KEYWORD]: '別' }), sheetRow(4, { [STATUS]: status })]);
    expect(out).toEqual({ row: 4, runId: 'exec-42', startedAt: MV.pipelineStartedAt });
  });

  test.each(['処理中', '完了', 'エラー', ''])('STATUS=「%s」なら確保しない（重複を防ぐ）', async (status) => {
    await expect(run([sheetRow(4, { [STATUS]: status })])).rejects.toThrow('処理しません');
  });

  test('行が並べ替えられていたら止める（照合の失敗）', async () => {
    await expect(run([sheetRow(4, { [KEYWORD]: '別のキーワード' })])).rejects.toThrow('照合の失敗');
  });

  test('行が見つからなければ止める（行が 0 件のときの空の item を含む）', async () => {
    await expect(run([{}])).rejects.toThrow('見つかりません');
    await expect(run([sheetRow(5)])).rejects.toThrow('見つかりません');
  });
});

describe('Queue: Confirm Claim', () => {
  const run = (rows) => runNode('Queue: Confirm Claim', rows, { nodeRefs: { 'Map Variables': MV }, globals: { $execution: EXEC } });

  test('自分の実行 ID で処理中になっていれば、Map Variables を次へ渡す', async () => {
    const [out] = await run([sheetRow(4, { [STATUS]: '処理中', [RUN]: 'exec-42' })]);
    expect(out).toEqual(MV);
  });

  test('別の実行が確保していたら止める', async () => {
    await expect(run([sheetRow(4, { [STATUS]: '処理中', [RUN]: 'exec-41' })])).rejects.toThrow('確保できませんでした');
  });

  test('実行 ID が数値で返っても比べられる', async () => {
    const [out] = await runNode('Queue: Confirm Claim', [sheetRow(4, { [STATUS]: '処理中', [RUN]: 42 })], {
      nodeRefs: { 'Map Variables': MV }, globals: { $execution: { id: '42' } },
    });
    expect(out.rowIndex).toBe(4);
  });
});

describe('Queue: Check Final', () => {
  const CLEANUP = { wpPostId: 321, queueValues: { STATUS: '完了', WP_POST_ID: '321' } };
  const run = (rows) => runNode('Queue: Check Final', rows, {
    nodeRefs: { 'Map Variables': MV, Cleanup: CLEANUP }, globals: { $execution: EXEC },
  });

  test('まだ自分が確保している行にだけ、Cleanup の値を書き戻す', async () => {
    const [out] = await run([sheetRow(4, { [STATUS]: '処理中', [RUN]: 'exec-42' })]);
    expect(out).toEqual({ row: 4, values: CLEANUP.queueValues });
  });

  test.each([
    ['人が STATUS を変えた', { [STATUS]: '待機中', [RUN]: 'exec-42' }, '確保が外れて'],
    ['別の実行が確保した', { [STATUS]: '処理中', [RUN]: 'exec-99' }, '確保が外れて'],
    ['行が並べ替えられた', { [STATUS]: '処理中', [RUN]: 'exec-42', [KEYWORD]: '別' }, '照合の失敗'],
  ])('%s なら書き戻さず、WP 投稿 ID を出して止める', async (_name, values, message) => {
    const err = await run([sheetRow(4, values)]).catch((e) => e);
    expect(err.message).toContain(message);
    expect(err.message).toContain('WP 投稿 ID: 321');
  });
});

describe('Queue: Claim Check / Queue: Update Check（Sheets の update が 1 行を書いたか）', () => {
  // update は alwaysOutputData：行が見つからず 0 件なら、空の item が 1 つ来る。
  const check = (name, input) => runNode(name, input, { nodeRefs: { Cleanup: { wpPostId: 321 } } });

  test('1 行を書けたら、そのまま次へ', async () => {
    expect(await check('Queue: Claim Check', [{ row_number: 4, [STATUS]: '処理中' }])).toEqual([{ row_number: 4, [STATUS]: '処理中' }]);
  });

  test('行が見つからず 0 件なら止める（黙って止まり Succeeded にならない）', async () => {
    await expect(check('Queue: Claim Check', [{}])).rejects.toThrow('SHAMAN_RUN_NG: 「Queue: Claim」で書き込めた行が 0 件です');
    const err = await check('Queue: Update Check', [{}]).catch((e) => e);
    expect(err.message).toContain('「Queue: Update」で書き込めた行が 0 件です');
    expect(err.message).toContain('WP 投稿 ID: 321');
  });

  test('Sheets の書き込みに失敗したら、WP 投稿 ID を出して止める', async () => {
    for (const error of [{ message: 'The caller does not have permission' }, 'The caller does not have permission']) {
      const err = await check('Queue: Update Check', [{ error }]).catch((e) => e);
      expect(err.message).toContain('SHAMAN_RUN_NG: 「Queue: Update」に書き込めません: The caller does not have permission');
      expect(err.message).toContain('WP 投稿 ID: 321');
    }
  });

  test('2 行を書いたら（1 行のはず）止める', async () => {
    await expect(check('Queue: Claim Check', [{ row_number: 4 }, { row_number: 5 }])).rejects.toThrow('2 件です');
  });
});

describe('Queue: Done', () => {
  test('Cleanup の結果を Discord へ渡し、queueValues は落とす', async () => {
    const cleanup = { KEYWORD: 'テスト', wpPostLink: 'https://example.com/?p=1', queueValues: { STATUS: '完了' } };
    const [out] = await runNode('Queue: Done', [{ row_number: 4 }], { nodeRefs: { Cleanup: cleanup } });
    expect(out.wpPostLink).toBe(cleanup.wpPostLink);
    expect(out).not.toHaveProperty('queueValues');
  });
});

describe('Run Result（Actions が読む本番の実行結果。DOCS/RUN_FROM_ACTIONS.md）', () => {
  const ERR = 'エラー内容\n【自動】';
  const run = (rows) => runNode('Run Result', rows, { globals: { $execution: EXEC } });

  test('この実行が確保した行がすべて「完了」なら ok', async () => {
    const [out] = await run([sheetRow(4, { [STATUS]: '完了', [RUN]: 'exec-42' }), sheetRow(6, { [STATUS]: 'エラー', [RUN]: 'other' })]);
    expect(out.shamanRun).toMatchObject({ ok: true, workflow: 'production', executionId: 'exec-42', reason: '' });
    expect(out.shamanRun.rows.map((r) => r.row)).toEqual([4]);
  });

  test('1 行でも「画像エラー」「エラー」なら ng（HTTP 200 でも成功にしない）', async () => {
    const [out] = await run([
      sheetRow(4, { [STATUS]: '完了', [RUN]: 'exec-42' }),
      sheetRow(5, { [STATUS]: '画像エラー', [RUN]: 'exec-42', [ERR]: 'image_error' }),
    ]);
    expect(out.shamanRun.ok).toBe(false);
    expect(out.shamanRun.reason).toBe('1 / 2 行が完了していません');
    expect(out.shamanRun.rows[1]).toMatchObject({ row: 5, status: '画像エラー', error: 'image_error' });
  });

  test('この実行の行が 1 つも無ければ ng', async () => {
    const [out] = await run([sheetRow(4, { [STATUS]: '完了', [RUN]: 'other' })]);
    expect(out.shamanRun).toMatchObject({ ok: false, reason: 'この実行で処理したキューの行がありません', rows: [] });
  });
});

// 止まった行の再開（DOCS/RUN_FROM_ACTIONS.md「止まった行の再開」）。本番の実行 11 が P6 で異常終了し、
// 「記事キュー」31 行目が「処理中」・実行ID 11 のまま残った（2026-11-08）。Actions の recover_execution=11 で、
// run_shaman_workflow.py が安全を確かめてから Webhook の本文に recover.executionId を入れる。
describe('止まった行の再開（recover）', () => {
  const WP = 'WP_POST_ID\n【自動】';
  const stuck = (values = {}) => ({ [STATUS]: '処理中', [RUN]: '11', [WP]: '', ...values });
  const hook = (executionId) => ({ Webhook: { body: executionId === undefined ? {} : { recover: { executionId } } } });
  const claim = (rows, refs) => runNode('Queue: Check Claim', rows,
    { nodeRefs: { 'Map Variables': MV, ...refs }, globals: { $execution: EXEC } });
  const filter = (rows, refs) => runNode('Filter Rows', rows, { nodeRefs: refs });
  const parsed = (values) => ({ ...sheetRow(4, values), siteConfig: { WP_URL: 'x' }, siteId: 'site-a', rowIndex: 4 });

  test('指定の実行の「処理中」の行は、待機中と同じように拾って確保する', async () => {
    const [out] = await claim([sheetRow(4, stuck())], hook('11'));
    expect(out).toEqual({ row: 4, runId: 'exec-42', startedAt: MV.pipelineStartedAt });
    const rows = await filter([parsed(stuck())], hook('11'));
    expect(rows).toHaveLength(1);
  });

  // PR #90 レビュー B1：再開のときは、ほかの「待機中」「画像エラー」の行を処理しない（別の記事を投稿しない）。
  test('再開の対象と、待機中・画像エラーの行が同時にあれば、再開の対象だけを処理する', async () => {
    const rows = await filter([
      { ...parsed({ [STATUS]: '待機中' }), rowIndex: 30 },
      { ...parsed(stuck()), rowIndex: 31 },
      { ...parsed({ [STATUS]: '画像エラー' }), rowIndex: 32 },
    ], hook('11'));
    expect(rows.map((r) => r.rowIndex)).toEqual([31]);
    for (const status of ['待機中', '画像エラー']) {
      await expect(claim([sheetRow(4, { [STATUS]: status })], hook('11'))).rejects.toThrow('処理しません');
    }
  });

  test('再開の対象が無ければ、待機中の行があっても何も処理しない', async () => {
    await expect(filter([parsed({ [STATUS]: '待機中' }), parsed({ [STATUS]: '画像エラー' })], hook('11')))
      .rejects.toThrow(/処理対象が0件[\s\S]*再開の対象/);
  });

  test('再開でなければ、今までどおり待機中・画像エラーの行だけを処理する', async () => {
    const rows = await filter([
      { ...parsed({ [STATUS]: '待機中' }), rowIndex: 30 },
      { ...parsed(stuck()), rowIndex: 31 },
      { ...parsed({ [STATUS]: '画像エラー' }), rowIndex: 32 },
    ], hook(undefined));
    expect(rows.map((r) => r.rowIndex)).toEqual([30, 32]);
  });

  test('実行ID が数字で書かれていても拾う（シートの数値）', async () => {
    const [out] = await claim([sheetRow(4, stuck({ [RUN]: 11 }))], hook('11'));
    expect(out.row).toBe(4);
  });

  test.each([
    ['指定が無い', stuck(), undefined],
    ['別の実行の行', stuck({ [RUN]: '12' }), '11'],
    ['WP_POST_ID がある（投稿済みかもしれない）', stuck({ [WP]: '1886' }), '11'],
    ['STATUS が完了', stuck({ [STATUS]: '完了' }), '11'],
    ['実行 ID が数字でない', stuck(), '11; x'],
  ])('%s なら拾わない', async (_name, values, id) => {
    await expect(claim([sheetRow(4, values)], hook(id))).rejects.toThrow('処理しません');
    await expect(filter([parsed(values)], hook(id))).rejects.toThrow('処理対象が0件');
  });
});

describe('自動実行：日本時間6時に1記事だけ、対象なしは正常な無処理', () => {
  const parsed = (row, status) => ({
    ...sheetRow(row, { [STATUS]: status }),
    siteConfig: { WP_URL: 'x' },
    siteId: 'site-a', rowIndex: row,
  });
  const scheduled = { 'Daily Schedule': { isExecuted: true } };

  test('予定実行は最初の待機中・画像エラー行だけを処理する', async () => {
    const rows = await runNode('Filter Rows', [parsed(31, '待機中'), parsed(32, '画像エラー')], { nodeRefs: scheduled });
    expect(rows.map(r => r.rowIndex)).toEqual([31]);
  });

  test('シートが完全に空でも予定実行は異常停止しない', async () => {
    const parsed = await runNode('Queue: Parse', [{ SITE_ID: 'kuro-aji-lab.com' }],
      { nodeRefs: { ...scheduled, 'Queue: Read': { items: [{}] } } });
    expect(parsed).toEqual([{ shamanScheduledNoWork: true }]);
    const filtered = await runNode('Filter Rows', parsed, { nodeRefs: scheduled });
    expect(filtered).toEqual([{ shamanScheduledNoWork: true }]);
  });

  test('予定実行で処理対象がなくても例外にせず無処理にする', async () => {
    const rows = await runNode('Filter Rows', [parsed(31, '完了')], { nodeRefs: scheduled });
    expect(rows).toEqual([{ shamanScheduledNoWork: true }]);
  });

  test('手動Webhookの通常実行は既存どおり対象全件・0件ならエラー', async () => {
    const rows = await runNode('Filter Rows', [parsed(31, '待機中'), parsed(32, '画像エラー')]);
    expect(rows.map(r => r.rowIndex)).toEqual([31, 32]);
    await expect(runNode('Filter Rows', [parsed(31, '完了')]))
      .rejects.toThrow('処理対象が0件');
  });

  test('本番のSchedule Triggerは6時・Asia/Tokyo、無処理経路と処理経路が両方ある', () => {
    const wf = require('../../workflows/SHAMAN_Workflow.json');
    const node = wf.nodes.find(n => n.name === 'Daily Schedule');
    expect(node.parameters.rule.interval[0]).toMatchObject({ field: 'cronExpression', expression: '0 6 * * *' });
    expect(wf.settings.timezone).toBe('Asia/Tokyo');
    expect(wf.connections['Daily: Idle?'].main[0][0].node).toBe('Daily: No Work');
    expect(wf.connections['Daily: Idle?'].main[1][0].node).toBe('Loop Over Items');
  });
});
