'use strict';
const { runNode } = require('../helpers/n8n-mock');

const BASE_CONFIG = {
  KEYWORD: 'テストキーワード',
  articleHtml: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `<h2>見出し${i}</h2><p>本文</p>`).join(''),
  wpUrl: 'https://example.com',
};

function makeAnalyzeResponse(sections) {
  return {
    candidates: [{
      content: { parts: [{ text: JSON.stringify({ sections }) }] },
      finishReason: 'STOP'
    }]
  };
}

const VALID_SECTIONS = [
  { insertionContext: '<h2>見出し1</h2>', imageDescription: '説明1', altText: 'alt1' },
  { insertionContext: '<h2>見出し2</h2>', imageDescription: '説明2', altText: 'alt2' },
  { insertionContext: '<h2>見出し3</h2>', imageDescription: '説明3', altText: 'alt3' },
  { insertionContext: '<h2>見出し4</h2>', imageDescription: '説明4', altText: 'alt4' },
  { insertionContext: '<h2>見出し5</h2>', imageDescription: '説明5', altText: 'alt5' },
];

describe('Parse Section Plan — 正常系', () => {
  test('5件のセクションが個別 item で返却される', async () => {
    const response = makeAnalyzeResponse(VALID_SECTIONS);
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(5);
    expect(out[0].insertionContext).toBe('<h2>見出し1</h2>');
    expect(out[0].imageDescription).toBe('説明1');
    expect(out[0].altText).toBe('alt1');
    expect(out[0].sectionIndex).toBe(1);
  });

  test('各 item に親設定（KEYWORD, wpUrl 等）が引き継がれる', async () => {
    const response = makeAnalyzeResponse(VALID_SECTIONS);
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out[0].KEYWORD).toBe('テストキーワード');
    expect(out[0].wpUrl).toBe('https://example.com');
  });

  test('JSONがコードブロック（```json ... ```）で囲まれていても解析できる', async () => {
    const wrapped = '```json\n' + JSON.stringify({ sections: VALID_SECTIONS }) + '\n```';
    const response = {
      candidates: [{
        content: { parts: [{ text: wrapped }] },
        finishReason: 'STOP'
      }]
    };
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(5);
  });
});

describe('Parse Section Plan — エラー系', () => {
  test('sections < 5 のとき p6Status: insufficient_sections_N で1件返却', async () => {
    const response = makeAnalyzeResponse(VALID_SECTIONS.slice(0, 3));
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(1);
    expect(out[0].p6Status).toContain('insufficient_sections');
    expect(out[0].p6Status).toContain('3');
  });

  test('JSONパース失敗のとき p6Status: section_parse_error で1件返却', async () => {
    const response = {
      candidates: [{
        content: { parts: [{ text: 'これはJSONではありません' }] },
        finishReason: 'STOP'
      }]
    };
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(1);
    expect(out[0].p6Status).toBe('section_parse_error');
  });

  test('Gemini APIエラー時 p6Status: section_analysis_error', async () => {
    const errorResponse = { error: { message: 'API quota exceeded' } };
    const out = await runNode('Parse Section Plan', [errorResponse], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(1);
    expect(out[0].p6Status).toBe('section_analysis_error');
  });

  test('candidates が空のとき p6Status: section_analysis_error', async () => {
    const response = { candidates: [] };
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(1);
    expect(out[0].p6Status).toBe('section_analysis_error');
  });

  test('sections > 7（8件）のとき最初の7件のみ返却される', async () => {
    const manySections = Array.from({ length: 8 }, (_, i) => ({
      insertionContext: `<h2>見出し${i + 1}</h2>`,
      imageDescription: `説明${i + 1}`,
      altText: `alt${i + 1}`,
    }));
    const response = makeAnalyzeResponse(manySections);
    const out = await runNode('Parse Section Plan', [response], {
      nodeRefs: { 'Build Section Analysis Request': BASE_CONFIG },
    });
    expect(out.length).toBe(7);
    expect(out[6].sectionIndex).toBe(7);
  });
});

describe('Parse Section Plan — scene 構造化フィールド', () => {
  const CFG = { KEYWORD: 'テスト', articleHtml: [1, 2, 3, 4, 5].map((i) => `<h2>見出し${i}</h2>`).join('') };

  function makeResponse(sections) {
    return {
      candidates: [{
        content: { parts: [{ text: JSON.stringify({ sections }) }] },
        finishReason: 'STOP',
      }],
    };
  }

  function makeSectionsJson(count, extra = {}) {
    return Array.from({ length: count }, (_, i) => ({
      insertionContext: `<h2>見出し${i + 1}</h2>`,
      imageDescription: `説明${i + 1}`,
      altText: `alt${i + 1}`,
      ...extra,
    }));
  }

  test('scene オブジェクトがあれば出力アイテムに透過される', async () => {
    const scene = { location: '海辺', action: '散歩', mood: '爽やか' };
    const out = await runNode('Parse Section Plan',
      [makeResponse(makeSectionsJson(5, { scene }))],
      { nodeRefs: { 'Build Section Analysis Request': CFG } });
    expect(out.length).toBe(5);
    expect(out[0].scene).toEqual(scene);
  });

  test('scene が無くてもエラーにならず scene: null で通る（後方互換）', async () => {
    const out = await runNode('Parse Section Plan',
      [makeResponse(makeSectionsJson(5))],
      { nodeRefs: { 'Build Section Analysis Request': CFG } });
    expect(out.length).toBe(5);
    expect(out[0].scene).toBeNull();
    expect(out[0].p6Status).toBeUndefined();
  });

  test('scene が配列など不正型なら null に落とす', async () => {
    const out = await runNode('Parse Section Plan',
      [makeResponse(makeSectionsJson(5, { scene: ['bad'] }))],
      { nodeRefs: { 'Build Section Analysis Request': CFG } });
    expect(out[0].scene).toBeNull();
  });
});

// 2026-11-09：insertionContext は、記事に実在する見出しでなければ使わない（DOCS/P1_KNOWLEDGE.md §5）。
// 見出しは、本番の P4 の出力（評価の結果 R10-1、KEYWORD「アジング コツ 夜」）の実際の h3。
describe('Parse Section Plan — 見出しの実在', () => {
  const REAL = ['なぜ夜のアジングでコツを掴むと釣れるのか', '常夜灯まわりで狙うべき明暗の境目',
    '夜釣りで有利になるエステルラインの選び方', '夜間のトラブルを防ぐ便利な必須アイテム',
    'アジング コツ 夜のまとめ', '潮の動きと時合いの見極め方'];
  const CFG = { KEYWORD: 'アジング コツ 夜',
    articleHtml: '<h2>アジングで夜に釣果を伸ばすコツ</h2>' + REAL.map((t) => `<h3>${t}</h3><h4>小見出し</h4><p>本文</p>`).join('') };
  const sec = (ctx, i) => ({ insertionContext: ctx, imageDescription: `説明${i}`, altText: `alt${i}` });
  const resp = (sections) => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ sections }) }] } }] });
  const run = (sections) => runNode('Parse Section Plan', [resp(sections)], { nodeRefs: { 'Build Section Analysis Request': CFG } });

  test('実在する見出し（タグごと・空白の違い）だけを使う', async () => {
    const out = await run([
      sec('<h3>なぜ夜のアジングでコツを掴むと釣れるのか</h3>', 1),
      sec('<h3>常夜灯まわりで狙うべき明暗の境目</h3>', 2),
      sec('夜釣りで有利になるエステルラインの選び方', 3),
      sec('<h3>夜間のトラブルを防ぐ　便利な必須アイテム</h3>', 4),  // 全角の空白が入っているだけ → 実在しない（文字が違う）
      sec('<h3>アジング コツ 夜のまとめ</h3>', 5),
      sec('<h3>潮の動きと時合いの見極め方</h3>', 6),
    ]);
    expect(out.length).toBe(5);
    expect(out.map((o) => o.insertionContext)).not.toContain('<h3>夜間のトラブルを防ぐ　便利な必須アイテム</h3>');
    expect(out[0].p6SectionsDropped).toBe(1);
  });

  test('記事に無い見出しを作ったら使わず、5 件に足りなければ insufficient_sections にする', async () => {
    const out = await run([
      sec('<h3>なぜ夜のアジングでコツを掴むと釣れるのか</h3>', 1),
      sec('<h3>常夜灯まわりで狙うべき明暗の境目</h3>', 2),
      sec('<h3>実釣データで分かった夜の最適ジグヘッド</h3>', 3),   // 記事に無い
      sec('<h3>夜のアジングにおすすめのワーム10選</h3>', 4),       // 記事に無い
      sec('<h3>アジング コツ 夜のまとめ</h3>', 5),
      sec('<p>本文</p>', 6),                                   // 見出しではない
    ]);
    expect(out.length).toBe(1);
    expect(out[0].p6Status).toBe('insufficient_sections_3');
    expect(out[0].p6SectionsDropped).toBe(3);
  });

  test('同じ見出しを 2 回選んだら、2 回目は使わない', async () => {
    const out = await run(REAL.slice(0, 5).map((t, i) => sec(`<h3>${t}</h3>`, i)).concat([sec(`<h3>${REAL[0]}</h3>`, 9)]));
    expect(out.length).toBe(5);
    expect(out[0].p6SectionsDropped).toBe(1);
  });
});
