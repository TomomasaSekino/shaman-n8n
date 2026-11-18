'use strict';
const { runNode } = require('../helpers/n8n-mock');

// 2026-11-09 ハルシネーション対策（DOCS/P1_KNOWLEDGE.md）。
// - P1 だけが Google 検索をする。情報源（URL）はモデルに書かせず、応答の groundingMetadata から取る。
// - 検索の情報源が無い・確かめられなかった情報があるときは、p1ResearchStatus で分かるようにする。
// - P4 の品質ゲートは、自サイトの他の記事への言及と、本文の URL を見つける。最後にリンクと URL は外す。

// 本番の P1 の応答（評価の実行 6、KEYWORD「アジング ジグヘッド 重さ」）の書き出しを、新しいナレッジの見出しで整理した形。
const KEYWORD = 'アジング ジグヘッド 重さ';
function knowledgeText(gaps) {
  return [
    '## メインキーワードと検索意図',
    '「アジング用のジグヘッドは何グラムを買えばいいのか分からない」「状況に合わせてどう使い分ければいいのか知りたい」という悩み。',
    '## 関連キーワード',
    'アジング ジグヘッド 重さ 使い分け',
    'アジング ジグヘッド 0.6g',
    '## 記事に使える具体的な事実',
    '風が弱く水深が浅い場所では軽いジグヘッドが基本になる。',
    '## 数値・製品仕様・固有名詞',
    '基準となる重さは1g前後。',
    '## 確認できなかった情報',
    gaps,
  ].join('\n');
}
function gemini(text, groundingMetadata) {
  const cand = { content: { parts: [{ text }] }, finishReason: 'STOP' };
  if (groundingMetadata) cand.groundingMetadata = groundingMetadata;
  return { candidates: [cand], usageMetadata: { promptTokenCount: 364, candidatesTokenCount: 1994 } };
}
// Gemini API の Google 検索グラウンディングの形（webSearchQueries・groundingChunks・groundingSupports）
const GROUNDING = {
  webSearchQueries: ['アジング ジグヘッド 重さ', 'アジング ジグヘッド 使い分け'],
  groundingChunks: [
    { web: { uri: 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA', title: 'example-fishing.jp' } },
    { web: { uri: 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB', title: 'tackle-maker.co.jp' } },
  ],
  groundingSupports: [
    { segment: { startIndex: 0, endIndex: 20, text: '基準となる重さは1g前後。' }, groundingChunkIndices: [0, 1] },
  ],
};
async function research(text, grounding, attempt = 1) {
  const out = await runNode('Parse Research', [gemini(text, grounding)], {
    nodeRefs: { 'Build P1 Request': { KEYWORD, requestBody: 'x', p1Attempt: attempt } },
  });
  return out[0];
}

describe('P1：情報源は検索の結果から取り、URL は記事を書く工程に渡さない', () => {
  test('検索の情報源・検索語・根拠の対応を p1Sources に残す', async () => {
    const out = await research(knowledgeText('なし'), GROUNDING);
    expect(out.p1Sources.queries).toEqual(GROUNDING.webSearchQueries);
    expect(out.p1Sources.sources).toEqual([
      { no: 1, title: 'example-fishing.jp', url: 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA' },
      { no: 2, title: 'tackle-maker.co.jp', url: 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB' },
    ]);
    expect(out.p1Sources.supports).toEqual([{ text: '基準となる重さは1g前後。', sources: [1, 2] }]);
    expect(out.p1Sources.checkedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(out.p1ResearchStatus).toBeNull();
    expect(out.p1ResearchGaps).toBe('');
  });

  test('ナレッジにはサイト名と確認日だけを書き、URL は書かない', async () => {
    const out = await research(knowledgeText('なし'), GROUNDING);
    expect(out.p1Knowledge).toContain('## 情報源（Google 検索で参照したサイト。確認日 ');
    expect(out.p1Knowledge).toContain('- [1] example-fishing.jp');
    expect(out.p1Knowledge).not.toContain('vertexaisearch');
    expect(out.p1Knowledge).not.toContain('https://');
  });
});

// 本番の実行 43（2026-11-09、KEYWORD「アジング コツ 夜」）：P1 の情報源が 0 件のまま記事を投稿した。Google 検索を
// するかどうかはモデルが決める。1 回目で 0 件なら 1 回だけ呼び直す（P1: Retry?）。
describe('P1：情報源が 0 件なら 1 回だけ呼び直す', () => {
  async function loop(groundings, maxCalls = 5) {
    const runs = [];
    let data = { KEYWORD, MODEL_PRO: 'gemini-x' };
    for (let call = 1; call <= maxCalls; call++) {
      const [built] = await runNode('Build P1 Request', [data]);
      runs.push(built);
      const g = groundings[Math.min(call, groundings.length) - 1];
      const [out] = await runNode('Parse Research', [gemini(knowledgeText('なし'), g)],
        { nodeRefs: { 'Build P1 Request': { runs: runs.slice() } } });
      if (!out.p1Retry) return { calls: call, out };
      data = out;
    }
    throw new Error('P1 を ' + maxCalls + ' 回呼んでも止まりません');
  }

  test('1 回目に情報源があれば、1 回だけ呼ぶ', async () => {
    const { calls, out } = await loop([GROUNDING]);
    expect(calls).toBe(1);
    expect(out.p1Sources.sources).toHaveLength(2);
  });

  test('1 回目が 0 件・2 回目にあれば、2 回目のナレッジで進む', async () => {
    const { calls, out } = await loop([null, GROUNDING]);
    expect(calls).toBe(2);
    expect(out.p1Attempt).toBe(2);
    expect(out.p1Sources.sources).toHaveLength(2);
    expect(out.p1ResearchStatus).toBeNull();
  });

  test('2 回とも 0 件なら、3 回目を呼ばずに、記事を作らずに止める', async () => {
    await expect(loop([null, null, GROUNDING])).rejects.toThrow(/SHAMAN_RUN_NG: P1 を 2 回呼んでも、Google 検索の情報源がありません/);
  });

  test('1 回目で呼び直すときは、ナレッジを次へ渡さない', async () => {
    const out = await research(knowledgeText('なし'), null, 1);
    expect(out.p1Retry).toBe(true);
    expect(out.p1Knowledge).toBeUndefined();
  });
});

describe('P1：調査が足りないことを分かるようにする（異常系）', () => {
  test('2 回目も検索の情報源が 1 件も無ければ、止める（本番の実行 43：groundingMetadata が無い応答）', async () => {
    await expect(research(knowledgeText('なし'), null, 2)).rejects.toThrow('SHAMAN_RUN_NG: P1 を 2 回呼んでも');
    await expect(research(knowledgeText('なし'), { webSearchQueries: [], groundingChunks: [] }, 2)).rejects.toThrow('SHAMAN_RUN_NG');
  });

  test('「確認できなかった情報」があれば、その中身と状態を残す', async () => {
    const gaps = '- 0.4g以下の製品の具体的な製品名\n- 水深ごとの推奨ウェイトの公式な目安';
    const out = await research(knowledgeText(gaps), GROUNDING);
    expect(out.p1ResearchGaps).toBe(gaps);
    expect(out.p1ResearchStatus).toContain('確認できなかった情報があります');
    expect(out.p1ResearchStatus).not.toContain('情報源がありません');
  });

  test('「確認できなかった情報」の中に「### 」の小見出しがあっても、次の「## 」まで取り出す', async () => {
    const gaps = '### 製品\n- 0.4g以下の製品名\n### 釣り方\n- 発光体でアタリを見分けられるか';
    const out = await research(knowledgeText(gaps), GROUNDING);
    expect(out.p1ResearchGaps).toBe(gaps);
  });

  test('「なし」「特になし。」は、確かめられなかった情報とみなさない', async () => {
    for (const none of ['なし', '特になし。', '- なし']) {
      const out = await research(knowledgeText(none), GROUNDING);
      expect(out.p1ResearchGaps).toBe('');
      expect(out.p1ResearchStatus).toBeNull();
    }
  });
});

// ---- P4 ----
const P3 = '<p>導入</p><h2>章A</h2><h3>節1</h3><p>短い</p><h3>節2</h3><p>短い</p>';
function section(title, body) {
  return `<h3>${title}</h3><h4>小見出し</h4><p>${'あ'.repeat(800)}</p><p>${body}</p><ul><li>要点</li></ul>`;
}
async function strengthen(html, attempt, extra = {}) {
  const cfg = { KEYWORD: 'k', htmlContent: P3, p1Knowledge: 'kn', p4Attempt: attempt, ...extra };
  const out = await runNode('Parse Strengthened', [{ stop_reason: 'end_turn', content: [{ type: 'text', text: html }] }],
    { nodeRefs: { 'Build P4 Request': cfg } });
  return out[0];
}

describe('P4：記事の外を指す書き方を見つける', () => {
  test('実在しない自サイト記事への言及（リンクが無くても）は、作り直しで消してもらう', async () => {
    const html = '<p>導入</p><h2>章A</h2>'
      + section('節1', 'ラインの結び方は別の記事で詳しく解説しています。')
      + section('節2', 'あわせて読みたい：常夜灯の攻め方');
    const out = await strengthen(html, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4QualityFeedback).toContain('自サイトの他の記事への言及が 2 箇所');
    expect(out.p4QualityFeedback).toContain('別の記事');
  });

  test('「この記事」は今の記事なので言及とみなさない', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', 'この記事では夜の釣り方をまとめます。') + section('節2', 'この記事のまとめです。');
    const out = await strengthen(html, 1);
    expect(out.p4Retry).toBe(false);
    expect(out.p4QualityIssues).toBeNull();
  });

  test('本文の URL の文字列を指摘する', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', '詳しくは https://kuro-aji-lab.com/night-ajing を見てください。') + section('節2', 'x');
    const out = await strengthen(html, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4QualityFeedback).toContain('URL の文字列が 1 個');
  });

  test('2 回目でも残ったリンクと URL は外して、記事に残さない', async () => {
    const html = '<p>導入</p><h2>章A</h2>'
      + section('節1', '<a href="https://kuro-aji-lab.com/jig">ジグヘッドの選び方</a>を押さえておきましょう。')
      + section('節2', '参考：www.example.com/page');
    const out = await strengthen(html, 2);
    expect(out.p4Retry).toBe(false);
    expect(out.articleHtml).not.toMatch(/<a\b/);
    expect(out.articleHtml).toContain('ジグヘッドの選び方を押さえておきましょう。');
    expect(out.articleHtml).not.toContain('www.example.com');
    expect(out.p4LinksRemoved).toBe(1);
    expect(out.p4UrlsRemoved).toBe(1);
  });

  test('2 回呼んでも自サイトの記事への言及が残ったら、記事として採用せずに止める（PR #85 レビュー B1）', async () => {
    const html = '<p>導入</p><h2>章A</h2>'
      + section('節1', 'ジグヘッドの選び方は関連記事を見てください。') + section('節2', 'x');
    await expect(strengthen(html, 2)).rejects.toThrow(/SHAMAN_RUN_NG: P4 を 2 回呼んでも、自サイトの他の記事への言及が 1 箇所残っています（関連記事）/);
  });

  test('1 回目の方が良くて選ばれても、言及が残っていれば止める', async () => {
    // 1 回目：言及が 1 つだけ。2 回目：言及は消えたが、節が短く指摘が 3 つ → 指摘の少ない 1 回目が選ばれる
    const first = '<p>導入</p><h2>章A</h2>' + section('節1', '別の記事でも書きました。') + section('節2', 'x');
    const second = '<p>導入</p><h2>章A</h2><h3>節1</h3><p>短い</p><h3>節2</h3><p>短い</p>';
    await expect(strengthen(second, 2, { p4PrevHtml: first, p4PrevIssues: ['・言及'] })).rejects.toThrow(/別の記事/);
  });

  test('2 回目で言及が消えていれば、そのまま進む', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', 'ラインの結び方も押さえておきましょう。') + section('節2', 'x');
    const out = await strengthen(html, 2);
    expect(out.p4QualityIssues).toBeNull();
    expect(out.articleHtml).toContain('ラインの結び方も押さえておきましょう。');
  });

  test('タグの属性の中は書き換えない（class などを壊さない）', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', '<span class="marker-yellow">要点</span>') + section('節2', 'x');
    const out = await strengthen(html, 2);
    expect(out.articleHtml).toContain('<span class="marker-yellow">要点</span>');
    expect(out.p4UrlsRemoved).toBe(0);
  });
});

// P4 も P2 と同じく、Build P4 Request → Claude → Parse Strengthened → P4: Retry? が 1 つの実行の中で 2 回動く。
// モックは runIndex を省くと最初の回を返す。Parse Strengthened が今の回を読まないと、作り直しが止まらない（PR #89 レビュー B1）。
describe('P4 の作り直しの回数（実行の中で何度も動くノード）', () => {
  const short = '<p>導入</p><h2>章A</h2><h3>節1</h3><p>短い</p><h3>節2</h3><p>短い</p>';
  async function loop(responses, maxCalls = 5) {
    const runs = [];
    let data = { KEYWORD: 'k', htmlContent: P3, p1Knowledge: 'kn', SITENAME: 's', AUTHOR: 'a', BASEURL: 'b', FIRSTPERSON: '僕' };
    for (let call = 1; call <= maxCalls; call++) {
      const [built] = await runNode('Build P4 Request', [data]);
      runs.push(built);
      const html = responses[Math.min(call, responses.length) - 1];
      const [out] = await runNode('Parse Strengthened', [{ stop_reason: 'end_turn', content: [{ type: 'text', text: html }] }],
        { nodeRefs: { 'Build P4 Request': { runs: runs.slice() } } });
      if (!out.p4Retry) return { calls: call, out };
      data = out;
    }
    throw new Error('P4 を ' + maxCalls + ' 回呼んでも止まりません');
  }

  test('1 回目に指摘があれば 2 回目を呼び、2 回目の後は指摘が残っても 3 回目を呼ばない', async () => {
    const { calls, out } = await loop([short, short, short]);
    expect(calls).toBe(2);
    expect(out.p4Attempt).toBe(2);
    expect(out.p4QualityIssues).not.toBeNull();
  });

  test('1 回目に指摘が無ければ、1 回だけ呼ぶ', async () => {
    const good = '<p>導入</p><h2>章A</h2>' + section('節1', 'x') + section('節2', 'y');
    const { calls } = await loop([good]);
    expect(calls).toBe(1);
  });
});
