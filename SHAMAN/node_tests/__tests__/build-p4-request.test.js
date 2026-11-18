'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Build P4 Request fills the P4 prompt template from the pipeline data and
// appends the P3 article after the body marker. P4's job is to strengthen the
// article for the searcher, grounded in the P1 research — so the research must
// reach the prompt, and no template placeholder may leak to Claude.

const DATA = {
  KEYWORD: 'アジング 秋アジ 攻略',
  SITENAME: '黒耳アジング研究所',
  AUTHOR: '黒耳',
  BASEURL: 'kuro-aji-lab.com',
  FIRSTPERSON: '僕',
  MODEL_CLAUDE: 'claude-sonnet-5',
  p1Knowledge: 'KNOWLEDGE-MARKER: 秋はベイトフィッシュパターンが混在する',
  htmlContent: '<h2>章</h2><h3>節</h3><p>P3-BODY-MARKER</p>',
};

async function buildPrompt(data) {
  const out = await runNode('Build P4 Request', [data]);
  const body = JSON.parse(out[0].requestBody);
  return body.messages[0].content;
}

describe('Build P4 Request — prompt assembly', () => {
  test('P1 のリサーチ（p1Knowledge）がプロンプトに入る', async () => {
    const prompt = await buildPrompt(DATA);
    expect(prompt).toContain('KNOWLEDGE-MARKER');
  });

  test('テンプレートのプレースホルダが残らない', async () => {
    const prompt = await buildPrompt(DATA);
    expect(prompt).not.toMatch(/\$\{[A-Z0-9_]+\}/);
  });

  test('キーワード・サイト名・執筆者名が差し込まれる', async () => {
    const prompt = await buildPrompt(DATA);
    expect(prompt).toContain('アジング 秋アジ 攻略');
    expect(prompt).toContain('黒耳アジング研究所');
  });

  test('P3 の記事本文はリサーチより後ろ（本文マーカーの後）に置かれる', async () => {
    const prompt = await buildPrompt(DATA);
    expect(prompt.indexOf('P3-BODY-MARKER')).toBeGreaterThan(prompt.indexOf('KNOWLEDGE-MARKER'));
    expect(prompt.indexOf('P3-BODY-MARKER')).toBeGreaterThan(prompt.lastIndexOf('### 以下記事本文 ###'));
  });

  test('p1Knowledge が無くてもプレースホルダを残さず組み立てる', async () => {
    const { p1Knowledge, ...noKnowledge } = DATA;
    const prompt = await buildPrompt(noKnowledge);
    expect(prompt).not.toMatch(/\$\{[A-Z0-9_]+\}/);
    expect(prompt).toContain('P3-BODY-MARKER');
  });
});
