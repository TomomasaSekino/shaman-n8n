'use strict';
const { runNode } = require('../helpers/n8n-mock');

// 2026-11-09 P2 の見出しの確認（DOCS/P1_KNOWLEDGE.md §7.4）。
// R27-1（KEYWORD「アジング コツ 夜」、gemini-3.5-flash-lite）は H2 2 + H3 9 = 11 個で、条件の 12〜13 個を満たさないまま P3 に渡った。
// Parse Outline が見出しを数え、1 回目が満たさなければ 1 回だけ出し直す（P2: Retry?）。2 回目も満たさなければ止める。
const KEYWORD = 'アジング コツ 夜';
const H3 = [
  'ナイトゲームで釣れる時間帯と潮回り',
  '常夜灯の明暗の境目を狙う理由',
  'ジグヘッドの重さを風と水深で選ぶ',
  'ワームカラーを水の濁りで使い分ける',
  'レンジを刻んで探るカウントダウン',
  '常夜灯なしの暗い場所での探り方',
  'アタリが出ないときの見直しポイント',
  '夜の釣り場で守りたい安全対策',
  '夜のアジングで役立つ道具の準備',
];
function outline(h3List, extra = []) {
  return [
    '## アジングの夜のコツを押さえる基本',
    ...h3List.slice(0, 5).map((h) => '### ' + h),
    '',
    '## アジングの夜のコツを実践で活かす',
    ...h3List.slice(5).map((h) => '### ' + h),
    ...extra,
  ].join('\n');
}
const SUMMARY = '### アジングの夜のコツのまとめ';
const OK = outline(H3, [SUMMARY]);           // H2 2 + H3 10 = 12
const R27_LIKE = outline(H3.slice(0, 8), [SUMMARY]); // H2 2 + H3 9 = 11

function gemini(text) {
  return { candidates: [{ content: { parts: [{ text }] }, finishReason: 'STOP' }] };
}
async function parse(text, attempt) {
  const out = await runNode('Parse Outline', [gemini(text)], {
    nodeRefs: { 'Build P2 Request': { KEYWORD, p1Knowledge: 'kn', p2Attempt: attempt, requestBody: 'x' } },
  });
  return out[0];
}

describe('Parse Outline：見出しの条件を満たす（正常系）', () => {
  test('H2 2 個・合計 12 個なら、そのまま P3 へ進む', async () => {
    const out = await parse(OK, 1);
    expect(out.p2Retry).toBe(false);
    expect(out.p2Issues).toBeNull();
    expect(out.outlineText).toBe(OK);
  });

  test('合計 13 個も条件の内', async () => {
    const out = await parse(outline(H3, ['### 夜の釣りを始める前の確認', SUMMARY]), 1);
    expect(out.p2Retry).toBe(false);
  });

  test('2 回目で満たせば進み、1 回目の構成案と条件は次へ渡さない', async () => {
    const out = await runNode('Parse Outline', [gemini(OK)], {
      nodeRefs: { 'Build P2 Request': { KEYWORD, p2Attempt: 2, p2FirstIssues: '・見出しが 11 個', requestBody: 'x' } },
    });
    expect(out[0].p2Retry).toBe(false);
    expect(out[0].p2Attempt).toBe(2);
    expect(out[0].p2FirstIssues).toBe('・見出しが 11 個');
    expect(out[0].p2PrevOutline).toBeUndefined();
  });
});

describe('Parse Outline：見出しの条件を満たさない（異常系）', () => {
  test('1 回目が 11 個（R27-1）なら、条件を伝えて出し直す', async () => {
    const out = await parse(R27_LIKE, 1);
    expect(out.p2Retry).toBe(true);
    expect(out.p2PrevOutline).toBe(R27_LIKE);
    expect(out.p2QualityFeedback).toContain('H2 と H3 を合わせて 11 個あります（H2 が 2 個、H3 が 9 個）。合わせて 12 個か 13 個にしてください。');
  });

  test('2 回目も 11 個なら、実行を NG で止める', async () => {
    await expect(parse(R27_LIKE, 2)).rejects.toThrow(/SHAMAN_RUN_NG: P2 を 2 回呼んでも、記事構成案の見出しが条件を満たしません。.*11 個/);
  });

  test('H2 が 3 個・25 文字を超える見出し・使えない語を、それぞれ指摘する', async () => {
    const text = outline(H3.slice(0, 7), [
      '### 夜のアジングで釣果を伸ばすための仕掛けと道具と立ち位置の総まとめ',
      '## アジングの夜のコツの補足',
      '### 夜のアタリをデータで読み解く',
      SUMMARY,
    ]);
    const out = await parse(text, 1);
    const fb = out.p2QualityFeedback;
    expect(fb).toContain('H2 見出しが 3 個あります');
    expect(fb).toContain('25 文字を超える見出しがあります：夜のアジングで釣果を伸ばすための仕掛けと道具と立ち位置の総まとめ');
    expect(fb).toContain('使えない語');
    expect(fb).toContain('夜のアタリをデータで読み解く');
  });

  test('記号と空白は 25 文字に数えない', async () => {
    const h = 'アジングの夜、常夜灯の明暗（境目）を狙う！基本のコツ'; // 27 文字、記号を除くと 22 文字
    const out = await parse(outline(H3.slice(0, 8), ['### ' + h, SUMMARY]), 1);
    expect(out.p2Retry).toBe(false);
  });
});

describe('Parse Outline：H2 が 0 個（PR #89 レビュー B2）', () => {
  test('1 回目に H2 が無ければ、止めずに出し直す', async () => {
    const out = await parse(H3.map((h) => '### ' + h).join('\n'), 1);
    expect(out.p2Retry).toBe(true);
    expect(out.p2QualityFeedback).toContain('H2 見出しが 0 個あります');
  });

  test('見出しの無い文章でも、1 回目は出し直す', async () => {
    const out = await parse('構成案を作成しました。', 1);
    expect(out.p2Retry).toBe(true);
    expect(out.p2QualityFeedback).toContain('合わせて 0 個あります');
  });

  test('2 回目も H2 が無ければ、NG で止める', async () => {
    await expect(parse('構成案を作成しました。', 2)).rejects.toThrow(/SHAMAN_RUN_NG: P2 を 2 回呼んでも/);
  });
});

// n8n の Workflow と同じ順（Build P2 Request → API → Parse Outline → P2: Retry?）でノードを動かす。Build P2 Request
// の出力は回ごとに runs へ積む。モックは runIndex を省くと最初の回を返す（n8n のドキュメントの書き方）ので、
// Parse Outline が今の回を明示して読まないと、試行回数が 1 のまま出し直しが止まらない（PR #89 レビュー B1）。
async function loop(responses, maxCalls = 5) {
  const runs = [];
  let data = { KEYWORD, OPTION1: 's', p1Knowledge: 'kn' };
  for (let call = 1; call <= maxCalls; call++) {
    const [built] = await runNode('Build P2 Request', [data]);
    runs.push(built);
    const [out] = await runNode('Parse Outline', [gemini(responses[Math.min(call, responses.length) - 1])], {
      nodeRefs: { 'Build P2 Request': { runs: runs.slice() } },
    });
    if (!out.p2Retry) return { calls: call, out };
    data = out; // P2: Retry? の true → Build P2 Request
  }
  throw new Error('P2 を ' + maxCalls + ' 回呼んでも止まりません');
}

describe('P2 の出し直しの回数（実行の中で何度も動くノード）', () => {
  test('1 回目で満たせば、1 回だけ呼ぶ', async () => {
    const { calls, out } = await loop([OK]);
    expect(calls).toBe(1);
    expect(out.p2Attempt).toBe(1);
  });

  test('1 回目が 11 個・2 回目で満たせば、2 回で進み、1 回目の条件を残す', async () => {
    const { calls, out } = await loop([R27_LIKE, OK]);
    expect(calls).toBe(2);
    expect(out.p2Attempt).toBe(2);
    expect(out.p2FirstIssues).toContain('合わせて 11 個あります');
    expect(out.outlineText).toBe(OK);
  });

  test('2 回とも満たさなければ、3 回目を呼ばずに NG で止める', async () => {
    await expect(loop([R27_LIKE, R27_LIKE, OK])).rejects.toThrow(/SHAMAN_RUN_NG: P2 を 2 回呼んでも/);
  });
});

describe('Build P2 Request：出し直し', () => {
  test('1 回目の構成案と、満たしていない条件を、会話の続きとして送る', async () => {
    const out = await runNode('Build P2 Request', [{
      KEYWORD, OPTION1: 's', p1Knowledge: 'kn', p2Attempt: 1, p2Retry: true, p2PrevOutline: R27_LIKE, p2QualityFeedback: '・見出しが 11 個',
    }]);
    const body = JSON.parse(out[0].requestBody);
    expect(body.contents).toHaveLength(5);
    expect(body.contents[3]).toEqual({ role: 'model', parts: [{ text: R27_LIKE }] });
    expect(body.contents[4].parts[0].text).toMatch(/^この記事構成案は、次の条件を満たしていません。[\s\S]*\n・見出しが 11 個$/);
    expect(out[0].p2Attempt).toBe(2);
    expect(out[0].p2FirstIssues).toBe('・見出しが 11 個');
    expect(out[0].p2Retry).toBeUndefined();
    expect(out[0].p2PrevOutline).toBeUndefined();
  });

  test('1 回目は今までと同じ 3 つの会話だけ', async () => {
    const out = await runNode('Build P2 Request', [{ KEYWORD, OPTION1: 's', p1Knowledge: 'kn' }]);
    expect(JSON.parse(out[0].requestBody).contents).toHaveLength(3);
    expect(out[0].p2Attempt).toBe(1);
  });
});
