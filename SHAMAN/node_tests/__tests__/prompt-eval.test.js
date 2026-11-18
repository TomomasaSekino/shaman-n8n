'use strict';
// プロンプト評価 Workflow（DOCS/PROMPT_EVAL_DESIGN.md）の Code ノード。
// 特に、出力を切り詰めずに保存し、結果 ID から完全に復元できること、ログに認証の情報を残さないことを確かめる。
const { loadWorkflow, runCode } = require('../helpers/code-node');

const WF = loadWorkflow('SHAMAN_Prompt_Eval.json');
const j = (json) => ({ json });
// 試験の偽のキー。キーの形の文字列をソースに置かないよう、実行時に組み立てる（gitleaks の誤検出を避ける）。
const FAKE_GOOGLE_KEY = ['AI', 'za', 'Sy'].join('') + 'A'.repeat(33);
const FAKE_ANTHROPIC_KEY = ['sk', 'ant'].join('-') + '-' + 'S'.repeat(24);
const runRow = (over = {}) => ({
  row_number: 2, '実行キー': 'k1', 'ステップ': 'P3', '入力の結果ID': 'R10-1', '基準の結果ID': '', KEYWORD: '',
  OPTION1: '', SITE_ID: '', 'モデル': '', '回数': '', '状態': '待機中', ...over,
});
const pick = (run) => ({ 'Eval: Pick Run': [j({ ok: true, reason: '', rowNumber: 2, run })] });
const RUN = { key: 'k1', step: 'P3', inputId: 'R10-1', baselineId: '', keyword: '', option1: '', siteId: '', model: '', rounds: 1 };

describe('Eval: Pick Run（「評価の実行」から 1 行を選ぶ）', () => {
  test('待機中の 1 行を、評価の指示にする', async () => {
    const [out] = await runCode(WF, 'Eval: Pick Run', [j(runRow({ '回数': '3', 'モデル': 'gemini-9-pro', 'ステップ': 'p3' })), j(runRow({ row_number: 3, '状態': '完了' }))]);
    expect(out.json).toEqual({ ok: true, reason: '', rowNumber: 2,
      run: { ...RUN, model: 'gemini-9-pro', rounds: 3 } });
  });

  test.each([
    [[], '0 件'],
    [[runRow(), runRow({ row_number: 3 })], '2 件'],
    [[runRow({ 'ステップ': 'P9' })], 'ステップ「P9」'],
    [[runRow({ '回数': '6' })], '回数は 1〜5'],
    [[runRow({ '回数': '1.5' })], '回数は 1〜5'],
    [[runRow({ 'モデル': 'gpt 5' })], 'モデル「gpt 5」'],
    [[runRow({ '入力の結果ID': '' })], 'P3 は入力の結果ID'],
    [[runRow({ '基準の結果ID': 'x' })], '基準の結果ID「x」'],
    [[runRow({ 'ステップ': 'P1', '入力の結果ID': '', KEYWORD: '' })], 'P1 は KEYWORD'],
    [[runRow({ 'ステップ': 'P1', '入力の結果ID': '', KEYWORD: 'k', SITE_ID: 'nosite' })], '有効なドメイン'],
  ])('止める：%#', async (rows, msg) => {
    const [out] = await runCode(WF, 'Eval: Pick Run', rows.map(j));
    expect(out.json.ok).toBe(false);
    expect(out.json.reason).toContain(msg);
  });
});

// Eval: Record の出力（評価データの行）を、シートから読んだ形にして Eval: Prepare Input に渡す。
async function saveAndRestore(step, parseOut, nextStep = 'P5') {
  const run = { ...RUN, step };
  const [rec] = await runCode(WF, 'Eval: Record', parseOut.map(j), {
    ...pick(run), 'Eval: Repeat': [j({ evalRound: 1, evalBaseline: '' })],
    'Eval: Apply Model': [j({ MODEL_PRO: 'm-pro', MODEL_CLAUDE: 'm-claude', MODEL_FLASH_LITE: 'm-lite', htmlContent: '<h3>a</h3>' })],
    'Eval: Response': [j({ candidates: [{ content: { parts: [{ text: 'raw' }] }, finishReason: 'STOP' }] })],
    'Build P4 Request': [j({ p4Attempt: 1 })],
  }, { executionId: '55' });
  const rows = rec.json.dataRows.map((r) => ({ ...r, '分割番号': String(r['分割番号']), '分割数': String(r['分割数']), '文字数': String(r['文字数']) }));
  const next = { ...RUN, step: nextStep, inputId: 'R55-1' };
  const restored = await runCode(WF, 'Eval: Prepare Input', [], {
    ...pick(next), 'Eval: Read Input': rows.map(j), 'Eval: Read Baseline': [j({})],
  });
  return { rec: rec.json, rows, restored };
}

describe('Eval: Record → Eval: Prepare Input（出力を保存し、結果 ID から復元する）', () => {
  test('長い本文も切り詰めず分割し、完全に元に戻る（サロゲートペアの境目、数値・配列・null も）', async () => {
    const big = 'あ'.repeat(44999) + '😀' + 'い'.repeat(50000) + '\n  末尾の空白  ';
    const out = { KEYWORD: 'k', articleHtml: big, p4QualityIssues: null, p4Selected: 'attempt1', p4Attempt: 1,
      sections: undefined, meta: { a: [1, 2, { b: '=SUM(A1)' }] }, num: 0, empty: '',
      requestBody: 'SHOULD NOT BE SAVED', evalCallAt: 123 };
    const { rec, rows, restored } = await saveAndRestore('P4', [out]);
    const parts = rows.filter((r) => r['項目'] === 'articleHtml');
    expect(parts.length).toBe(3);
    parts.forEach((p) => expect(p['本文'].length).toBeLessThanOrEqual(45000));
    expect(parts[0]['本文'].endsWith('あ')).toBe(true); // 😀 の途中で切らない
    expect(rows.some((r) => r['項目'] === 'requestBody' || r['項目'] === 'evalCallAt')).toBe(false);
    expect(restored).toHaveLength(1);
    const data = restored[0].json;
    expect(data.articleHtml).toBe(big);
    expect(data.meta).toEqual(out.meta);
    expect(data.num).toBe(0);
    expect(data.empty).toBe('');
    expect(data.p4QualityIssues).toBeNull();
    expect(data._meta).toBeUndefined();
    expect(rec.resultRow['結果ID']).toBe('R55-1');
    expect(rec.resultRow['状態']).toBe('成功');
  });

  test('分割が欠けている・文字数が違う・型が分からない・失敗した結果は、入力にしない', async () => {
    const { rows } = await saveAndRestore('P4', [{ KEYWORD: 'k', articleHtml: 'x'.repeat(50000) }]);
    const next = { ...RUN, step: 'P5', inputId: 'R55-1' };
    const restore = (rs) => runCode(WF, 'Eval: Prepare Input', [], { ...pick(next), 'Eval: Read Input': rs.map(j), 'Eval: Read Baseline': [j({})] });
    const cases = [
      [rows.filter((r) => !(r['項目'] === 'articleHtml' && r['分割番号'] === '2')), '分割がそろっていません'],
      [rows.map((r) => (r['項目'] === 'KEYWORD' ? { ...r, '本文': 'kk' } : r)), '文字数が保存したときと違います'],
      [rows.map((r) => (r['項目'] === 'KEYWORD' ? { ...r, '型': 'x' } : r)), '型「x」'],
      [[], '評価データがありません'],
    ];
    for (const [rs, msg] of cases) {
      const [out] = await restore(rs);
      expect(out.json.evalOk).toBe(false);
      expect(out.json.evalReason).toContain(msg);
    }
    const failed = await runCode(WF, 'Eval: Record', [j({ evalError: 'boom' })], {
      ...pick({ ...RUN, step: 'P4' }), 'Eval: Repeat': [j({ evalRound: 2 })], 'Eval: Apply Model': [j({})],
      'Eval: Response': [j({})], 'Build P4 Request': [j({ p4Attempt: 2 })],
    }, { executionId: '56' });
    expect(failed[0].json.resultRow['状態']).toBe('エラー');
    expect(failed[0].json.resultRow['API呼び出し回数']).toBe(2);
    const [out] = await restore(failed[0].json.dataRows.map((r) => ({ ...r })).map((r) => ({ ...r, '結果ID': 'R55-1' })));
    expect(out.json.evalReason).toContain('成功した結果ではありません');
  });

  test('次のステップの入力がそろっていなければ止める。そろっていれば回数ぶん出す', async () => {
    const { rows } = await saveAndRestore('P4', [{ KEYWORD: 'k', articleHtml: '<h2>a</h2>' }]);
    const p3 = { ...RUN, step: 'P3', inputId: 'R55-1' };
    const [bad] = await runCode(WF, 'Eval: Prepare Input', [], { ...pick(p3), 'Eval: Read Input': rows.map(j), 'Eval: Read Baseline': [j({})] });
    expect(bad.json.evalReason).toContain('outlineText・p1Knowledge');
    const p5 = { ...RUN, step: 'P5', inputId: 'R55-1', rounds: 3, baselineId: 'R9-1' };
    const outs = await runCode(WF, 'Eval: Prepare Input', [], {
      ...pick(p5), 'Eval: Read Input': rows.map(j), 'Eval: Read Baseline': [j({ '結果ID': 'R9-1', '測定値': '{"chars":130}' })],
    });
    expect(outs.map((o) => o.json.evalRound)).toEqual([1, 2, 3]);
    expect(outs[0].json.evalBaseline).toBe('{"chars":130}');
    expect(outs[0].json.articleHtml).toBe('<h2>a</h2>');
  });

  test('P1 は、キューの行と同じ形で Map Variables に渡す', async () => {
    const p1 = { ...RUN, step: 'P1', inputId: '', keyword: 'アジング', option1: '研究者', siteId: 's1' };
    const [out] = await runCode(WF, 'Eval: Prepare Input', [], { ...pick(p1), 'Eval: Read Input': [j({})], 'Eval: Read Baseline': [j({})] });
    expect(out.json['KEYWORD\n（必須）\nフォーカスキーワード']).toBe('アジング');
    expect(out.json['OPTION1\n（必須）\n記事の結論・スタンス']).toBe('研究者');
    expect(out.json.siteId).toBe('s1');
    expect(out.json.rowIndex).toBeNull();
  });

  test('P6A：節を sections にまとめて保存する', async () => {
    const secs = [1, 2, 3, 4, 5].map((i) => ({ articleHtml: 'h', insertionContext: 'c' + i, imageDescription: 'd', altText: 'a', scene: i === 1 ? { x: 1 } : null, sectionIndex: i }));
    const { rec, restored } = await saveAndRestore('P6A', secs, 'P6A');
    const m = JSON.parse(rec.resultRow['測定値']);
    expect(m.sections).toBe(5);
    expect(m.withScene).toBe(1);
    expect(restored[0].json.sections.map((s) => s.insertionContext)).toEqual(['c1', 'c2', 'c3', 'c4', 'c5']);
    expect(restored[0].json.insertionContext).toBeUndefined();
  });
});

describe('Eval: Record の測定値', () => {
  const record = async (step, out, input = {}, body = {}) => {
    const [rec] = await runCode(WF, 'Eval: Record', [j(out)], {
      ...pick({ ...RUN, step, baselineId: 'R1-1' }), 'Eval: Repeat': [j({ evalRound: 1, evalBaseline: '{"chars":1}' })],
      'Eval: Apply Model': [j({ MODEL_PRO: 'gemini-x', MODEL_FLASH_LITE: 'lite-x', MODEL_OUTLINE: 'outline-x', MODEL_CLAUDE: 'claude-x', ...input })],
      'Eval: Response': [j(body)], 'Build P4 Request': [j({ p4Attempt: out.p4Attempt || 1 })],
    }, { executionId: '7' });
    return { row: rec.json.resultRow, m: JSON.parse(rec.json.resultRow['測定値']) };
  };

  test('P2：禁止語を含む見出しと、キーワード全体を含む見出しの数', async () => {
    const { m, row } = await record('P2', { KEYWORD: 'アジング', outlineText: '## アジングの基本\n### 研究データが示す釣果\n### 道具\n## まとめ' });
    expect(m.h2).toBe(2);
    expect(m.h3).toBe(2);
    expect(m.bannedHeadings).toEqual(['研究データが示す釣果']);
    expect(m.keywordHeadings).toBe(1);
    expect(row['モデル']).toBe('outline-x');
    expect(row['基準の測定値']).toBe('{"chars":1}');
  });

  test('トークン数：Gemini（usageMetadata）と Claude（usage）の最後の呼び出し', async () => {
    const gem = { candidates: [{ content: { parts: [{ text: '<h2>a</h2>' }] } }],
      usageMetadata: { promptTokenCount: 364, candidatesTokenCount: 1994, thoughtsTokenCount: 1445, totalTokenCount: 3803 } };
    const a = await record('P3', { htmlContent: '<h2>a</h2>' }, { outlineText: '', p1Knowledge: '' }, gem);
    expect(a.m.usage).toEqual({ in: 364, out: 1994, thinking: 1445, cacheRead: null, total: 3803 });
    const cl = { content: [{ type: 'text', text: '<h2>A</h2>' }], stop_reason: 'end_turn',
      usage: { input_tokens: 12000, output_tokens: 30000, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 } };
    const b = await record('P4', { articleHtml: '<h2>A</h2>' }, { htmlContent: '<h2>A</h2>', p1Knowledge: '' }, cl);
    expect(b.m.usage).toEqual({ in: 12000, out: 30000, cacheRead: 0, cacheWrite: 0, total: null });
  });

  test('P3：応答の生の本文にあった、入力にない URL', async () => {
    const body = { candidates: [{ content: { parts: [{ text: '<h2>a</h2><a href="https://fake.example/x">x</a><a href="https://ok.example">y</a>' }] } }] };
    const { m } = await record('P3', { htmlContent: '<h2>a</h2>xy', p3LinksRemoved: 2 }, { outlineText: 'https://ok.example', p1Knowledge: '' }, body);
    expect(m.linksRemoved).toBe(2);
    expect(m.urlsNotInInput).toEqual(['https://fake.example/x']);
  });

  test('P4：h3 ごとの数、決まり文句、追加されたリンク、見出しの一致、品質ゲート', async () => {
    const base = '<h2>A</h2><h3>B</h3>p';
    const html = '<h2>A</h2><h3>B</h3><h4>c</h4><table></table><ul></ul>具体的には、x。具体的には、y。n。詳しくは別の記事で解説しています。';
    const body = { content: [{ type: 'text', text: '```html\n' + html.replace('n。', '<a href="https://new.example">n</a>。') + '\n```' }], stop_reason: 'end_turn' };
    const { m, row } = await record('P4', { articleHtml: html, p4QualityIssues: '・x', p4Selected: 'attempt2', p4Attempt: 2, p4LinksRemoved: 1, p4UrlsRemoved: 0 }, { htmlContent: base, p1Knowledge: '' }, body);
    expect(m.sections).toEqual([{ title: 'B', chars: expect.any(Number), h4: 1, table: 1, ol: 0, ul: 1 }]);
    expect(m.stockPhrases).toEqual({ '具体的には': 2 });
    expect(m.linksAdded).toBe(1);
    expect(m.urlsNotInInput).toEqual(['https://new.example']);
    expect(m.headingsMatch).toBe(true);
    expect(m.siteRefs).toEqual(['別の記事']);
    expect(m.linksRemoved).toBe(1);
    expect(m.qualityIssues).toBe('・x');
    expect(m.attempts).toBe(2);
    expect(row['API呼び出し回数']).toBe(2);
    expect(row['モデル']).toBe('claude-x');
  });

  test('P5：文字数と、生の出力の末尾の文字数の注記', async () => {
    const body = { candidates: [{ content: { parts: [{ text: 'メタの説明文（133文字）' }] } }] };
    const { m } = await record('P5', { metaDescription: 'x'.repeat(130), articleTitle: 't' }, {}, body);
    expect(m.chars).toBe(130);
    expect(m.inRange).toBe(true);
    expect(m.rawTrailingCountNote).toBe(true);
    expect(m.usage).toBeNull();
  });

  test('P6A：p6Status があれば、状態はエラー', async () => {
    const { row } = await record('P6A', { p6Status: 'section_parse_error' });
    expect(row['状態']).toBe('エラー');
    expect(row['エラー']).toBe('section_parse_error');
  });
});

describe('Eval: Call Log Rows（ログ：リクエストと応答の全文。認証の情報は残さない）', () => {
  const refs = (res, stamp = {}) => ({
    ...pick({ ...RUN, step: 'P4' }), 'Eval: Repeat': [j({ evalRound: 2 })],
    'Eval: Stamp': [j({ requestBody: '{"model":"c"}', claudeModel: 'claude-x', evalCallAt: Date.now() - 50, p4Attempt: 2, ...stamp })],
    'Claude: Strengthen Content': [j(res)],
  });

  test('成功：状態コード、停止理由、トークン数、所要時間、呼び出しの回', async () => {
    const res = { statusCode: 200, headers: { 'x-api-key': FAKE_ANTHROPIC_KEY }, body: { stop_reason: 'end_turn', usage: { input_tokens: 3 }, content: [] } };
    const rows = await runCode(WF, 'Eval: Call Log Rows', [], refs(res), { executionId: '9' });
    expect(rows).toHaveLength(1);
    const r = rows[0].json;
    expect(r['結果ID']).toBe('R9-2');
    expect(r['HTTPの状態']).toBe(200);
    expect(r['停止理由']).toBe('end_turn');
    expect(r['トークン数']).toBe('{"input_tokens":3}');
    expect(r['呼び出し']).toBe(2);
    expect(r['所要時間ms']).toBeGreaterThanOrEqual(0);
    expect(r['リクエスト']).toBe('{"model":"c"}');
    expect(JSON.stringify(rows)).not.toContain(FAKE_ANTHROPIC_KEY); // 応答のヘッダーは残さない
  });

  test('長いリクエストは分割して全文を残す', async () => {
    const long = 'p'.repeat(100000);
    const rows = await runCode(WF, 'Eval: Call Log Rows', [], refs({ statusCode: 200, body: { content: [] } }, { requestBody: long }));
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.json['リクエスト']).join('')).toBe(long);
    expect(rows.map((r) => r.json['分割番号'])).toEqual([1, 2, 3]);
  });

  test('エラーと応答の中の API キーは伏せる', async () => {
    const res = { error: { message: 'GET https://x/v1?key=' + FAKE_GOOGLE_KEY + ' failed', description: 'x-goog-api-key: ' + FAKE_GOOGLE_KEY } };
    const rows = await runCode(WF, 'Eval: Call Log Rows', [], refs(res));
    const text = JSON.stringify(rows);
    expect(text).not.toContain(FAKE_GOOGLE_KEY.substring(0, 10));
    expect(rows[0].json['エラー']).toContain('key=***');
    const bad = await runCode(WF, 'Eval: Call Log Rows', [], refs({ statusCode: 401, body: { error: { message: 'invalid x-api-key: ' + FAKE_ANTHROPIC_KEY } } }));
    expect(bad[0].json['エラー']).toContain('HTTP 401');
    expect(JSON.stringify(bad)).not.toContain(FAKE_ANTHROPIC_KEY);
  });
});

describe('Eval: Response / Eval: Parse Failed / Eval: Apply Model', () => {
  const refs = (res) => ({ ...pick({ ...RUN, step: 'P3' }), 'Gemini: Generate Content': [j(res)] });

  test('本番の Parse が受け取る形（応答の本文）に戻す。通信エラーは伏せたメッセージだけ', async () => {
    const body = { candidates: [] };
    expect((await runCode(WF, 'Eval: Response', [], refs({ statusCode: 200, body })))[0].json).toEqual(body);
    const [err] = await runCode(WF, 'Eval: Response', [], refs({ error: { message: 'x?key=SECRETKEY1', options: { headers: { 'x-goog-api-key': 'K' } } } }));
    expect(err.json).toEqual({ error: { message: 'x?key=***' } });
    const [txt] = await runCode(WF, 'Eval: Response', [], refs({ statusCode: 502, body: '<html>bad</html>' }));
    expect(txt.json.error.message).toContain('JSON ではない応答（HTTP 502）');
  });

  test('Parse の失敗は、伏せたメッセージで結果にする', async () => {
    const [out] = await runCode(WF, 'Eval: Parse Failed', [j({ error: { message: 'Gemini error: ?key=ABCDEF' } })]);
    expect(out.json).toEqual({ evalError: 'Gemini error: ?key=***' });
  });

  test('保存したときのモデル名と P4 の途中の状態を消し、今の本番のモデルか指定のモデルにする', async () => {
    const item = { MODEL_PRO: 'old-pro', MODEL_CLAUDE: 'old', geminiModel: 'old', requestBody: 'x', p4Attempt: 2,
      p4PrevHtml: 'h', p4QualityFeedback: 'f', evalRound: 1, evalOk: true, evalBaseline: '', htmlContent: 'c' };
    const [p3] = await runCode(WF, 'Eval: Apply Model', [j(item)], pick({ ...RUN, step: 'P3', model: 'gemini-new' }));
    expect(p3.json.MODEL_PRO).toBe('gemini-new');
    expect(p3.json.MODEL_CLAUDE).toBe('claude-opus-5-5');
    for (const k of ['geminiModel', 'requestBody', 'p4Attempt', 'p4PrevHtml', 'p4QualityFeedback', 'evalRound', 'evalOk', 'evalBaseline']) {
      expect(p3.json).not.toHaveProperty(k);
    }
    expect(p3.json.htmlContent).toBe('c');
    const [p4] = await runCode(WF, 'Eval: Apply Model', [j(item)], pick({ ...RUN, step: 'P4' }));
    expect(p4.json.MODEL_PRO).toBe('gemini-3.1-pro-preview');
    expect(p4.json.MODEL_CLAUDE).toBe('claude-opus-5-5');
  });
});

describe('開始・終了・停止のログ、終わりの集計', () => {
  test('Eval: Finish は、成功とエラーの数と結果 ID を「評価の実行」に書く', async () => {
    const [out] = await runCode(WF, 'Eval: Finish', [
      j({ '結果ID': 'R1-1', '回': 1, 'モデル': 'm', '状態': '成功', 'API呼び出し回数': 1, '出力の文字数': 10, '測定値': '{"sources":3,"usage":{"in":5,"out":7}}' }),
      j({ '結果ID': 'R1-2', '回': 2, 'モデル': 'm', '状態': 'エラー', 'エラー': 'x', 'API呼び出し回数': 1, '出力の文字数': 0, '測定値': '{}' })],
      { 'Eval: Pick Run': [j({ ok: true, rowNumber: 4, run: RUN })] });
    expect(out.json).toEqual({ rowNumber: 4, status: 'エラーあり', resultIds: 'R1-1 R1-2', memo: '成功 1 件・エラー 1 件', results: [
      { resultId: 'R1-1', round: 1, model: 'm', status: '成功', error: '', calls: 1, outputChars: 10, measures: { sources: 3, usage: { in: 5, out: 7 } } },
      { resultId: 'R1-2', round: 2, model: 'm', status: 'エラー', error: 'x', calls: 1, outputChars: 0, measures: {} }] });
  });

  test('停止のログと例外に、止めた理由を書く', async () => {
    const refs = { 'Eval: Pick Run': [j({ ok: false, reason: '待機中の行が 0 件です', run: null })] };
    const [row] = await runCode(WF, 'Eval: Stop Rows', [], refs);
    expect(row.json['種類']).toBe('停止');
    expect(row.json['エラー']).toContain('0 件');
    const [stop] = await runCode(WF, 'Eval: Stop', [], refs);
    expect(stop.json.shamanRun).toMatchObject({ ok: false, workflow: 'test', reason: 'SHAMAN_PROMPT_EVAL_STOPPED: 待機中の行が 0 件です' });
    const after = { 'Eval: Pick Run': [j({ ok: true, run: RUN })], 'Eval: Prepare Input': [j({ evalOk: false, evalReason: '入力がない' })] };
    expect((await runCode(WF, 'Eval: Stop Rows', [], after))[0].json['エラー']).toBe('入力がない');
    expect((await runCode(WF, 'Eval: Stop', [], after))[0].json.shamanRun.reason).toContain('入力がない');
  });

  test('開始と終了のログ', async () => {
    const [start] = await runCode(WF, 'Eval: Log Start Rows', [], { 'Eval: Pick Run': [j({ ok: true, run: RUN })] });
    expect(start.json['種類']).toBe('開始');
    expect(JSON.parse(start.json['リクエスト'])).toEqual(RUN);
    const [end] = await runCode(WF, 'Eval: Log End Rows', [], {
      'Eval: Pick Run': [j({ ok: true, run: RUN })], 'Eval: Finish': [j({ status: '完了', resultIds: 'R1-1' })],
    });
    expect(end.json['種類']).toBe('終了');
    expect(JSON.parse(end.json['応答']).resultIds).toBe('R1-1');
  });
});

describe('レビューの指摘への対応', () => {
  const logRefs = (res) => ({
    ...pick({ ...RUN, step: 'P3' }), 'Eval: Repeat': [j({ evalRound: 1 })],
    'Eval: Stamp': [j({ requestBody: '{}', geminiModel: 'g', evalCallAt: Date.now() })],
    'Gemini: Generate Content': [j(res)],
  });

  test('成功の応答は、そのまま残す（英語の slug や key= を伏せない）', async () => {
    const body = { candidates: [{ content: { parts: [{ text: 'desk-organizer-ideas-for-home-2026 ?key=abc' }] } }] };
    const [row] = await runCode(WF, 'Eval: Call Log Rows', [], logRefs({ statusCode: 200, body }));
    expect(row.json['応答']).toBe(JSON.stringify(body));
    expect(row.json['エラー']).toBe('');
  });

  test('文字列のエラー（タイムアウトなど）も、内容を残す', async () => {
    const [row] = await runCode(WF, 'Eval: Call Log Rows', [], logRefs({ error: 'ETIMEDOUT after 300000ms' }));
    expect(row.json['エラー']).toBe('ETIMEDOUT after 300000ms');
    const [res] = await runCode(WF, 'Eval: Response', [], { ...pick({ ...RUN, step: 'P3' }), 'Gemini: Generate Content': [j({ error: 'ETIMEDOUT' })] });
    expect(res.json).toEqual({ error: { message: 'ETIMEDOUT' } });
  });

  test('エラー文はセルに収まる長さにし、最初の行だけに書く。応答の全文は分割して残す', async () => {
    const html = '<html>' + 'x'.repeat(60000) + '</html>';
    const rows = await runCode(WF, 'Eval: Call Log Rows', [], logRefs({ statusCode: 502, body: html }));
    expect(rows.length).toBe(2);
    expect(rows[0].json['エラー'].length).toBeLessThanOrEqual(2001);
    expect(rows[1].json['エラー']).toBe('');
    expect(rows.map((r) => r.json['応答']).join('')).toBe(html);
  });

  test('本文がない応答は、状態コードだけを残す（応答のヘッダーを残さない）', async () => {
    const [ok] = await runCode(WF, 'Eval: Call Log Rows', [], logRefs({ statusCode: 204, headers: { 'set-cookie': 'S' } }));
    expect(ok.json['応答']).toBe('');
    const [bad] = await runCode(WF, 'Eval: Call Log Rows', [], logRefs({ statusCode: 500, headers: { 'set-cookie': 'S' } }));
    expect(bad.json['応答']).toBe('{"statusCode":500}');
    expect(JSON.stringify([ok, bad])).not.toContain('set-cookie');
  });

  test('回の途中で失敗したとき（API を呼ぶ前）も、Record は止まらずにエラーの結果を作る', async () => {
    const [rec] = await runCode(WF, 'Eval: Record', [j({ evalError: 'Build failed' })], {
      ...pick({ ...RUN, step: 'P4' }), 'Eval: Repeat': [j({ evalRound: 1 })],
    }, { executionId: '8' });
    expect(rec.json.resultRow['状態']).toBe('エラー');
    expect(rec.json.resultRow['API呼び出し回数']).toBe(0);
  });

  test('測定値がセルに入らないときは、評価データの _metrics に全文を残す', async () => {
    const outline = Array.from({ length: 4000 }, (_, i) => '### 研究データ' + i).join('\n');
    const [rec] = await runCode(WF, 'Eval: Record', [j({ KEYWORD: 'k', outlineText: outline })], {
      ...pick({ ...RUN, step: 'P2' }), 'Eval: Repeat': [j({ evalRound: 1 })], 'Eval: Apply Model': [j({})], 'Eval: Response': [j({})],
    }, { executionId: '8' });
    expect(JSON.parse(rec.json.resultRow['測定値']).storedIn).toBe('評価データ _metrics');
    const parts = rec.json.dataRows.filter((r) => r['項目'] === '_metrics');
    expect(JSON.parse(parts.map((r) => r['本文']).join('')).bannedHeadings).toHaveLength(4000);
  });
});

describe('Eval: Run Result（Actions が読む実行結果。DOCS/RUN_FROM_ACTIONS.md）', () => {
  const result = (status) => runCode(WF, 'Eval: Run Result', [j({})], {
    'Eval: Finish': [j({ rowNumber: 4, status, resultIds: 'R1-1 R1-2', memo: 'm', results: [{ resultId: 'R1-1', measures: { chars: 1 } }] })],
  }, { executionId: 'exec-9' });

  test('「完了」なら ok', async () => {
    const [out] = await result('完了');
    expect(out.json.shamanRun).toEqual({ ok: true, workflow: 'test', executionId: 'exec-9', reason: '',
      rowNumber: 4, status: '完了', resultIds: 'R1-1 R1-2', memo: 'm', results: [{ resultId: 'R1-1', measures: { chars: 1 } }] });
  });

  test('「エラーあり」なら ng（HTTP 200 でも成功にしない）', async () => {
    const [out] = await result('エラーあり');
    expect(out.json.shamanRun.ok).toBe(false);
    expect(out.json.shamanRun.reason).toBe('評価の実行が「エラーあり」で終わりました');
  });
});

describe('途中で黙って止まらない（n8n の実行を Succeeded にしない。2026-11-06）', () => {
  test.each(['Eval: Claim Run', 'Eval: Mark Done', 'Eval: Mark Failed'])(
    '%s が行を見つけられず 0 件なら、次の Check が例外にする', async (name) => {
      // update は alwaysOutputData：0 件なら空の item が 1 つ来る。
      await expect(runCode(WF, name + ' Check', [j({})])).rejects.toThrow('SHAMAN_RUN_NG: 「' + name + '」で書き込めた行が 0 件です');
      const out = await runCode(WF, name + ' Check', [j({ row_number: 2, '状態': '処理中' })]);
      expect(out.map((o) => o.json.row_number)).toEqual([2]);
    });

  test('Code ノードの出力が 0 件なら例外（例：Eval: Data Rows に保存する行が無い）', async () => {
    await expect(runCode(WF, 'Eval: Data Rows', [j({ dataRows: [] })]))
      .rejects.toThrow('SHAMAN_RUN_NG: 「Eval: Data Rows」の出力が 0 件です');
  });

  test('どの工程にも当たらなければ、Eval: Unknown Step が例外にする', async () => {
    await expect(runCode(WF, 'Eval: Unknown Step', [j({})], pick({ ...RUN, step: 'P9' })))
      .rejects.toThrow('SHAMAN_RUN_NG: 工程「P9」の分岐がありません');
  });
});

describe('評価のシートと行を、Workflow が自分で用意する（2026-11-06）', () => {
  const TITLES = ['評価の実行', '評価結果', '評価データ', '評価ログ'];

  test('Eval: Sheet Setup：4 つのシート名を出し、Webhook の evalRun を渡す', async () => {
    const evalRun = { step: 'P1', keyword: 'k' };
    const out = await runCode(WF, 'Eval: Sheet Setup', [j({ body: { nonce: 'n', evalRun } })]);
    expect(out.map((o) => o.json.title)).toEqual(TITLES);
    expect(out[0].json.newRun).toEqual(evalRun);
  });

  test('Eval: Sheet Setup：画面から実行したとき（本文なし）は newRun が null', async () => {
    const out = await runCode(WF, 'Eval: Sheet Setup', [j({})]);
    expect(out.map((o) => o.json.title)).toEqual(TITLES);
    expect(out.every((o) => o.json.newRun === null)).toBe(true);
  });

  test('Eval: Header Runs：見出しの項目名だけの 1 行（値は空）', async () => {
    const [out] = await runCode(WF, 'Eval: Header Runs', [j({})]);
    expect(Object.keys(out.json)).toEqual(['実行キー', 'ステップ', '入力の結果ID', '基準の結果ID', 'KEYWORD', 'OPTION1',
      'SITE_ID', 'モデル', '回数', '状態', '実行ID', '結果ID', 'メモ']);
    expect(Object.values(out.json).every((v) => v === '')).toBe(true);
  });

  test('Eval: New Run Row：Actions の入力から、待機中の行を作る', async () => {
    const newRun = { step: 'P1', keyword: ' アジング ', option1: '研究者としてのスタンスで', siteId: 'kuro-aji-lab.com',
      inputId: '', baselineId: '', model: '', rounds: '1', runId: '123' };
    const [out] = await runCode(WF, 'Eval: New Run Row', [j({})],
      { 'Eval: Sheet Setup': [j({ title: '評価の実行', newRun })] });
    expect(out.json).toEqual({ '実行キー': 'actions-123-exec-1', 'ステップ': 'P1', '入力の結果ID': '', '基準の結果ID': '',
      KEYWORD: 'アジング', OPTION1: '研究者としてのスタンスで', SITE_ID: 'kuro-aji-lab.com', 'モデル': '', '回数': '1',
      '状態': '待機中', '実行ID': '', '結果ID': '', 'メモ': '' });
  });

  test('Eval: New Run Row → Eval: Pick Run：足した行がそのまま評価の指示になる', async () => {
    const newRun = { step: 'P1', keyword: 'アジング', option1: 'o', siteId: 'kuro-aji-lab.com', rounds: '' };
    const [row] = await runCode(WF, 'Eval: New Run Row', [j({})], { 'Eval: Sheet Setup': [j({ newRun })] },
      { executionId: '77' });
    const refs = { 'Eval: Sheet Setup': [j({ newRun })], 'Eval: New Run Row': [row] };
    // 前からある待機中の行（手で書いた行・前の実行の残り）があっても、足した行だけを使う（B2）
    const [out] = await runCode(WF, 'Eval: Pick Run', [j(runRow({ row_number: 2 })), j({ ...row.json, row_number: 3 })], refs);
    expect(out.json.ok).toBe(true);
    expect(out.json.rowNumber).toBe(3);
    expect(out.json.run).toEqual({ key: 'actions-manual-77', step: 'P1', inputId: '', baselineId: '', keyword: 'アジング',
      option1: 'o', siteId: 'kuro-aji-lab.com', model: '', rounds: 1 });
  });

  test('Eval: Pick Run：足した行が不正なら、その行の番号を付けて止める（Eval: Mark Failed が「エラー」にする）', async () => {
    const newRun = { step: 'P1', keyword: '', siteId: 'kuro-aji-lab.com' };
    const [row] = await runCode(WF, 'Eval: New Run Row', [j({})], { 'Eval: Sheet Setup': [j({ newRun })] });
    const refs = { 'Eval: Sheet Setup': [j({ newRun })], 'Eval: New Run Row': [row] };
    const [out] = await runCode(WF, 'Eval: Pick Run', [j({ ...row.json, row_number: 5 })], refs);
    expect(out.json).toMatchObject({ ok: false, rowNumber: 5, run: null });
    expect(out.json.reason).toContain('P1 は KEYWORD');
    expect(out.json.evalReason).toBe(out.json.reason);
  });

  test('Eval: Pick Run：足した行が見つからなければ止める（ほかの待機中の行は使わない）', async () => {
    const newRun = { step: 'P1', keyword: 'k', siteId: 'kuro-aji-lab.com' };
    const refs = { 'Eval: Sheet Setup': [j({ newRun })], 'Eval: New Run Row': [j({ '実行キー': 'actions-1-2' })] };
    const [out] = await runCode(WF, 'Eval: Pick Run', [j(runRow())], refs);
    expect(out.json).toMatchObject({ ok: false, rowNumber: null });
    expect(out.json.reason).toContain('actions-1-2');
  });

  test('本番の実行から取り込んだ入力：評価データに R<実行>-0 で書き、行の入力の結果ID にし、P4 の入力に復元できる（2026-11-07）', async () => {
    const long = 'あ'.repeat(46000);
    const importData = { executionId: '8', node: 'Parse Content', data: {
      KEYWORD: 'アジング', htmlContent: long, p1Knowledge: { facts: ['x'] }, SITENAME: 's',
      discordWebhookUrl: 'https://discord.example/x', apiKey: 'k', evalOk: true, requestBody: '{}' } };
    const newRun = { step: 'P4', inputId: '', rounds: '1', runId: '9', importData };
    const refs = { 'Eval: Sheet Setup': [j({ newRun })] };
    const rows = await runCode(WF, 'Eval: Import Rows', [j({})], refs, { executionId: '70' });
    expect(new Set(rows.map((r) => r.json['結果ID']))).toEqual(new Set(['R70-0']));
    const items = rows.map((r) => r.json['項目']);
    // 認証らしい項目と評価の内部の項目は書かない
    expect(items).not.toEqual(expect.arrayContaining(['discordWebhookUrl']));
    expect(items).not.toContain('apiKey');
    expect(items).not.toContain('evalOk');
    expect(items).not.toContain('requestBody');
    // 長い本文は分割する
    expect(rows.filter((r) => r.json['項目'] === 'htmlContent').length).toBe(2);
    const meta = JSON.parse(rows.find((r) => r.json['項目'] === '_meta').json['本文']);
    expect(meta).toMatchObject({ status: '成功', source: 'n8n 実行 8 の Parse Content' });

    const [row] = await runCode(WF, 'Eval: New Run Row', [j({})], refs, { executionId: '70' });
    expect(row.json['入力の結果ID']).toBe('R70-0');
    const [picked] = await runCode(WF, 'Eval: Pick Run', [j({ ...row.json, row_number: 4 })],
      { ...refs, 'Eval: New Run Row': [row] });
    expect(picked.json.ok).toBe(true);
    expect(picked.json.run).toMatchObject({ step: 'P4', inputId: 'R70-0' });

    const read = rows.map((r) => ({ ...r.json, '分割番号': String(r.json['分割番号']), '分割数': String(r.json['分割数']), '文字数': String(r.json['文字数']) }));
    const [prep] = await runCode(WF, 'Eval: Prepare Input', [], {
      'Eval: Pick Run': [picked], 'Eval: Read Input': read.map(j), 'Eval: Read Baseline': [j({})] });
    expect(prep.json.evalOk).toBe(true);
    expect(prep.json.htmlContent).toBe(long);
    expect(prep.json.p1Knowledge).toEqual({ facts: ['x'] });
    expect(prep.json.KEYWORD).toBe('アジング');
  });

  test('Eval: Sheets Ready：作ったシートの名前だけ（4 つともあれば空）', async () => {
    const [made] = await runCode(WF, 'Eval: Sheets Ready', [j({ sheetId: 1, title: '評価結果' }), j({ sheetId: 2, title: '評価ログ' })]);
    expect(made.json.created).toEqual(['評価結果', '評価ログ']);
    const [none] = await runCode(WF, 'Eval: Sheets Ready', [j({})]);
    expect(none.json.created).toEqual([]);
  });
});

describe('P6（画像を 1 枚作る。2026-11-09）', () => {
  const P6 = { ...RUN, step: 'P6', inputId: 'R20-1' };
  const SECTIONS = [{ sectionIndex: 1, imageDescription: 'd1', altText: 'a1', insertionContext: 'c1', scene: { location: '港' } },
    { sectionIndex: 2, imageDescription: 'd2', altText: 'a2', insertionContext: 'c2', scene: null }];

  test('Eval: P6 Section：最初の節だけを、本番の節の形にする', async () => {
    const data = { KEYWORD: 'k', imgFolder: 'kuro-aji-lab.com', MODEL_IMAGE_OPENAI: 'gpt-image-2', sections: SECTIONS };
    const out = await runCode(WF, 'Eval: P6 Section', [j({ ...data, referenceImages: [{ b64: 'x' }] })],
      { 'Check P6 Mode': [j(data)] });
    expect(out).toHaveLength(1);
    expect(out[0].json).toEqual({ KEYWORD: 'k', imgFolder: 'kuro-aji-lab.com', MODEL_IMAGE_OPENAI: 'gpt-image-2', ...SECTIONS[0] });
    await expect(runCode(WF, 'Eval: P6 Section', [j({})], { 'Check P6 Mode': [j({ KEYWORD: 'k' })] }))
      .rejects.toThrow('sections');
  });

  test('Eval: Apply Model：画像のモデルを本番の値に戻し、指定があれば入れる', async () => {
    const [out] = await runCode(WF, 'Eval: Apply Model', [j({ MODEL_IMAGE_OPENAI: 'old', p6Results: [1], p6Attempt: 2, sections: SECTIONS })],
      pick({ ...P6, model: 'gpt-image-9' }));
    expect(out.json.MODEL_IMAGE_OPENAI).toBe('gpt-image-9');
    expect(out.json).not.toHaveProperty('p6Results');
    expect(out.json).not.toHaveProperty('p6Attempt');
    expect(out.json.sections).toEqual(SECTIONS);
  });

  test('Eval: Response：Collect Generated Images には HTTP の応答をそのまま渡す', async () => {
    const res = { statusCode: 200, body: { data: [{ b64_json: 'img' }], usage: { input_tokens: 10, output_tokens: 20 } } };
    const [out] = await runCode(WF, 'Eval: Response', [], { ...pick(P6), 'P6: Generate Image': [j(res)] });
    expect(out.json).toEqual(res);
  });

  test('Eval: Call Log Rows：画像（data: URL・b64_json）は長さだけを残し、トークン数は残す', async () => {
    const big = 'A'.repeat(5000);
    const stamp = { requestBody: { model: 'gpt-image-2', prompt: 'p', images: [{ image_url: 'data:image/png;base64,' + big }] }, evalCallAt: Date.now() };
    const res = { statusCode: 200, body: { data: [{ b64_json: big }], usage: { input_tokens: 1234, output_tokens: 5678 } } };
    const [row] = await runCode(WF, 'Eval: Call Log Rows', [], {
      ...pick(P6), 'Eval: Stamp': [j(stamp)], 'P6: Generate Image': [j(res)], 'Eval: Repeat': [j({ evalRound: 1 })] });
    expect(row.json['リクエスト']).not.toContain(big);
    expect(row.json['リクエスト']).toContain('（5022 文字）');
    expect(row.json['応答']).not.toContain(big);
    expect(row.json['応答']).toContain('（5000 文字）');
    expect(JSON.parse(row.json['トークン数'])).toEqual({ input_tokens: 1234, output_tokens: 5678 });
    expect(row.json['モデル']).toBe('gpt-image-2');
  });

  test('Eval: Record：画像そのものは残さず、枚数・大きさ・エラーを残す', async () => {
    const results = [{ ...SECTIONS[0], imageB64: Buffer.alloc(300).toString('base64'), imageMime: 'image/jpeg', imageIndex: 1 }];
    const refs = { ...pick(P6), 'Eval: Repeat': [j({ evalRound: 1, evalBaseline: '' })],
      'Eval: Apply Model': [j({ MODEL_IMAGE_OPENAI: 'gpt-image-2' })], 'Eval: Response': [j({ statusCode: 200, body: {} })] };
    const [rec] = await runCode(WF, 'Eval: Record', [j({ p6Results: results, p6Attempt: 1, p6RetryNeeded: '' })], refs, { executionId: '30' });
    expect(rec.json.resultRow['状態']).toBe('成功');
    expect(rec.json.resultRow['モデル']).toBe('gpt-image-2');
    expect(JSON.parse(rec.json.resultRow['測定値'])).toMatchObject({ images: 1, imageBytes: [300], errors: [] });
    const saved = rec.json.dataRows.find((r) => r['項目'] === 'p6Results');
    expect(saved['本文']).not.toContain('imageB64"');
    expect(JSON.parse(saved['本文'])[0].imageB64Chars).toBe(400);

    const bad = [{ ...SECTIONS[0], imageError: 'policy_or_bad_request_section_1: [400] x', imageIndex: 1 }];
    const [err] = await runCode(WF, 'Eval: Record', [j({ p6Results: bad, p6Attempt: 1 })], refs, { executionId: '31' });
    expect(err.json.resultRow['状態']).toBe('エラー');
    expect(err.json.resultRow['エラー']).toContain('policy_or_bad_request_section_1');
  });
});

// 1 つの実行で、回の数と P2・P4 の出し直しのぶん、同じノードが何度も動く。モック（code-node.js）は runIndex を
// 省くと最初の回を返すので、今の回を明示して読まないと、前の回の応答・呼び出しの番号を記録してしまう（PR #89 レビュー B3）。
describe('評価：同じノードが何度も動いたとき、今の回の応答と呼び出しの番号を残す', () => {
  const call = (n, tokens) => j({ statusCode: 200, body: { candidates: [{ content: { parts: [{ text: 'o' + n }] }, finishReason: 'STOP' }], usageMetadata: { candidatesTokenCount: tokens } } });

  test('Eval: Call Log Rows：P2 の 2 回目の呼び出しは「呼び出し」2 で、2 回目のトークン数', async () => {
    const rows = await runCode(WF, 'Eval: Call Log Rows', [], {
      ...pick({ ...RUN, step: 'P2' }),
      'Eval: Repeat': { runs: [[j({ evalRound: 1 })], [j({ evalRound: 2 })]] },
      'Eval: Stamp': { runs: [[j({ requestBody: '{}', geminiModel: 'g', evalCallAt: Date.now(), p2Attempt: 1 })],
                              [j({ requestBody: '{}', geminiModel: 'g', evalCallAt: Date.now(), p2Attempt: 2 })]] },
      'Gemini: Generate Outline': { runs: [[call(1, 151)], [call(2, 176)]] },
    }, { executionId: '9' });
    expect(rows[0].json['呼び出し']).toBe(2);
    expect(rows[0].json['トークン数']).toBe('{"candidatesTokenCount":176}');
    expect(rows[0].json['結果ID']).toBe('R9-2');
  });

  test('Eval: Response：2 回目の応答を Parse に渡す', async () => {
    const [res] = await runCode(WF, 'Eval: Response', [], {
      ...pick({ ...RUN, step: 'P2' }), 'Gemini: Generate Outline': { runs: [[call(1, 1)], [call(2, 2)]] },
    });
    expect(res.json.candidates[0].content.parts[0].text).toBe('o2');
  });

  test('Eval: Record：P2 を 2 回呼んだら、API呼び出し回数は 2、回は今の回', async () => {
    const outline = '## a\n' + Array.from({ length: 10 }, (_, i) => '### h' + i).join('\n') + '\n## b';
    const [rec] = await runCode(WF, 'Eval: Record', [j({ KEYWORD: 'k', outlineText: outline, p2Attempt: 2, p2FirstIssues: '・x' })], {
      ...pick({ ...RUN, step: 'P2' }),
      'Eval: Repeat': { runs: [[j({ evalRound: 1, evalBaseline: '' })], [j({ evalRound: 2, evalBaseline: '' })]] },
      'Eval: Apply Model': { runs: [[j({ MODEL_OUTLINE: 'old' })], [j({ MODEL_OUTLINE: 'outline-x' })]] },
      'Eval: Response': { runs: [[j({ usageMetadata: { candidatesTokenCount: 1 } })], [j({ usageMetadata: { candidatesTokenCount: 2 } })]] },
      'Build P2 Request': { runs: [[j({ p2Attempt: 1 })], [j({ p2Attempt: 2 })]] },
    }, { executionId: '7' });
    const row = rec.json.resultRow;
    const m = JSON.parse(row['測定値']);
    expect(row['API呼び出し回数']).toBe(2);
    expect(row['結果ID']).toBe('R7-2');
    expect(row['モデル']).toBe('outline-x');
    expect(m.attempts).toBe(2);
    expect(m.firstIssues).toBe('・x');
    expect(m.usage.out).toBe(2);
  });
});
