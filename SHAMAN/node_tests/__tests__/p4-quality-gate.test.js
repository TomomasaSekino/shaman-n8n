'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Parse Strengthened compares the P4 article with the P3 draft it was built from.
// P4 must keep every h2/h3 heading, give every <h3> section at least 700 chars
// and one ol/ul/table, and add no links. Attempt 1 failing → p4Retry (loop back
// to Build P4 Request with feedback). Attempt 2 failing → continue, but record
// the shortfall in p4QualityIssues.

const P3 = '<p>導入</p><h2>章A</h2><h3>節1</h3><p>短い</p><h3>節2</h3><p>短い</p>';

function section(title, chars, extra = '<ul><li>要点</li></ul>') {
  return `<h3>${title}</h3><h4>小見出し</h4><p>${'あ'.repeat(chars)}</p>${extra}`;
}
function claude(html) {
  return { stop_reason: 'end_turn', content: [{ type: 'text', text: html }] };
}
function config(attempt) {
  return { KEYWORD: 'k', htmlContent: P3, outlineText: 'o', researchText: 'r', p1Knowledge: 'kn', p4Attempt: attempt };
}
async function parse(html, attempt) {
  const out = await runNode('Parse Strengthened', [claude(html)], { nodeRefs: { 'Build P4 Request': config(attempt) } });
  return out[0];
}
const GOOD = '<p>導入</p><h2>章A</h2>' + section('節1', 800) + section('節2', 800);

describe('P4 quality gate — pass', () => {
  test('基準を満たせば再生成せず、中間フィールドを削除する', async () => {
    const out = await parse(GOOD, 1);
    expect(out.p4Retry).toBe(false);
    expect(out.p4QualityIssues).toBeFalsy();
    expect(out.htmlContent).toBeUndefined();
    expect(out.articleHtml).toContain('<h3>節2</h3>');
  });
});

describe('P4 quality gate — attempt 1 fails → retry', () => {
  const SHORT = '<p>導入</p><h2>章A</h2>' + section('節1', 800) + section('節2', 300);

  test('700文字未満の節があれば p4Retry=true で、再生成に必要な入力を残す', async () => {
    const out = await parse(SHORT, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.htmlContent).toBe(P3);
    expect(out.p1Knowledge).toBe('kn');
    expect(out.p4QualityFeedback).toContain('節2');
    expect(out.p4QualityFeedback).toContain('700');
  });

  test('ol/ul/table の無い節を指摘する', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', 800) + section('節2', 800, '');
    const out = await parse(html, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4QualityFeedback).toContain('節2');
  });

  test('見出しが変わっていれば指摘する', async () => {
    const html = '<p>導入</p><h2>章A</h2>' + section('節1', 800) + section('節2を改題', 800);
    const out = await parse(html, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4QualityFeedback).toContain('見出し');
  });

  test('リンクが追加されていれば指摘する', async () => {
    const html = GOOD.replace('<p>導入</p>', '<p>導入 <a href="https://example.com">x</a></p>');
    const out = await parse(html, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4QualityFeedback).toContain('リンク');
  });
});

describe('P4 quality gate — attempt 2 fails → continue and record', () => {
  test('2回目も不合格なら先へ進み、p4QualityIssues に残す', async () => {
    const SHORT = '<p>導入</p><h2>章A</h2>' + section('節1', 800) + section('節2', 300);
    const out = await parse(SHORT, 2);
    expect(out.p4Retry).toBe(false);
    expect(out.p4QualityIssues).toContain('節2');
    expect(out.htmlContent).toBeUndefined();
    expect(out.articleHtml).toContain('<h3>節2</h3>');
  });
});

describe('P4 quality gate — no baseline', () => {
  test('P3 に <h3> が無い（比較対象なし）場合は検査しない', async () => {
    const out = await runNode('Parse Strengthened', [claude('<h2>見出し</h2><p>本文</p>')], {
      nodeRefs: { 'Build P4 Request': { htmlContent: '<p>old</p>', p4Attempt: 1 } },
    });
    expect(out[0].p4Retry).toBe(false);
    expect(out[0].p4QualityIssues).toBeFalsy();
  });
});

describe('Build P4 Request — attempt counter and feedback', () => {
  const DATA = { KEYWORD: 'k', SITENAME: 's', AUTHOR: 'a', BASEURL: 'b', FIRSTPERSON: '僕', p1Knowledge: 'kn', htmlContent: P3 };

  test('初回は p4Attempt=1 で、通常の P4 プロンプトに P3 下書きを渡す', async () => {
    const out = await runNode('Build P4 Request', [DATA]);
    expect(out[0].p4Attempt).toBe(1);
    const prompt = JSON.parse(out[0].requestBody).messages[0].content;
    expect(prompt).not.toContain('【修正が必要な点】');
    expect(prompt.indexOf('<h3>節1</h3>')).toBeGreaterThan(prompt.lastIndexOf('### 以下記事本文 ###'));
  });

  test('再生成は1回目の記事の修正になり、指摘はマーカーの前、1回目の記事はマーカーの後に入る', async () => {
    const PREV = '<p>導入</p><h2>章A</h2><h3>節1</h3><p>PREV-BODY-MARKER</p><h3>節2</h3><p>PREV-BODY-2</p>';
    const out = await runNode('Build P4 Request', [{
      ...DATA, p4Attempt: 1, p4Retry: true, p4PrevHtml: PREV,
      p4PrevIssues: ['・「節2」に ol / ul / table がありません'],
      p4QualityFeedback: '・「節2」に ol / ul / table がありません',
    }]);
    expect(out[0].p4Attempt).toBe(2);
    expect(out[0].p4PrevHtml).toBe(PREV);
    expect(out[0].p4Retry).toBeUndefined();
    const prompt = JSON.parse(out[0].requestBody).messages[0].content;
    const marker = prompt.lastIndexOf('### 以下記事本文 ###');
    expect(prompt).toContain('【修正が必要な点】');
    expect(prompt.indexOf('「節2」に ol / ul / table がありません')).toBeLessThan(marker);
    expect(prompt.indexOf('PREV-BODY-MARKER')).toBeGreaterThan(marker);
    expect(prompt).not.toContain('<p>短い</p>');
    expect(prompt).not.toMatch(/[$][{][A-Z0-9_]+[}]/);
  });
});

describe('P4 quality gate — keep the better attempt', () => {
  // Real run 1293: attempt 1 met every length rule but lacked lists in 5 sections;
  // the regenerated attempt 2 added lists yet shrank 10 sections below 700 chars
  // and still replaced attempt 1. The gate must never publish the worse attempt.

  const ATTEMPT1_NO_LIST = '<p>導入</p><h2>章A</h2>' + section('節1', 900) + section('節2', 900, '');
  const ATTEMPT2_SHORT = '<p>導入</p><h2>章A</h2>' + section('節1', 400) + section('節2', 400);

  async function parseWith(html, attempt, extra) {
    const out = await runNode('Parse Strengthened', [claude(html)], {
      nodeRefs: { 'Build P4 Request': { ...config(attempt), ...extra } },
    });
    return out[0];
  }

  test('1回目が不合格なら、比較用に記事と指摘数を持ち越す', async () => {
    const out = await parse(ATTEMPT1_NO_LIST, 1);
    expect(out.p4Retry).toBe(true);
    expect(out.p4PrevHtml).toBe(ATTEMPT1_NO_LIST);
    expect(out.p4PrevIssues).toHaveLength(1);
  });

  test('2回目の方が指摘が多ければ、1回目の記事を採用する', async () => {
    const out = await parseWith(ATTEMPT2_SHORT, 2, {
      p4PrevHtml: ATTEMPT1_NO_LIST, p4PrevIssues: ['・「節2」に ol / ul / table がありません'],
    });
    expect(out.p4Retry).toBe(false);
    expect(out.p4Selected).toBe('attempt1');
    expect(out.articleHtml).toBe(ATTEMPT1_NO_LIST);
    expect(out.p4QualityIssues.split(String.fromCharCode(10))).toEqual(['・「節2」に ol / ul / table がありません']);
    expect(out.p4PrevHtml).toBeUndefined();
  });

  test('2回目の方が良ければ、2回目を採用する', async () => {
    const out = await parseWith(GOOD, 2, {
      p4PrevHtml: ATTEMPT1_NO_LIST, p4PrevIssues: ['・「節2」に ol / ul / table がありません'],
    });
    expect(out.p4Selected).toBe('attempt2');
    expect(out.articleHtml).toContain('<h3>節2</h3>');
    expect(out.p4QualityIssues).toBeFalsy();
  });

  test('指摘数が同じなら、本文が長い方を採用する', async () => {
    const LONGER = '<p>導入</p><h2>章A</h2>' + section('節1', 1200) + section('節2', 1200, '');
    const out = await parseWith(ATTEMPT1_NO_LIST, 2, {
      p4PrevHtml: LONGER, p4PrevIssues: ['・「節2」に ol / ul / table がありません'],
    });
    expect(out.p4Selected).toBe('attempt1');
    expect(out.articleHtml).toBe(LONGER);
  });
});
