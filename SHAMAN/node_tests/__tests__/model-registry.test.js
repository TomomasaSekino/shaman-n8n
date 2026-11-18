'use strict';
const { runNode } = require('../helpers/n8n-mock');

// Map Variables embeds model names from the registry in generate_workflows.py (no $env at run time).
// Build nodes read data.MODEL_* with a literal fallback.

const ROW = { siteConfig: {}, siteId: 'S1', rowIndex: 3 };

describe('Model Registry — Map Variables 集約', () => {
  test('env未設定時はリテラルにフォールバックする', async () => {
    const out = await runNode('Map Variables', [ROW]);
    expect(out[0].MODEL_PRO).toBe('gemini-3.1-pro-preview');
    expect(out[0].MODEL_FLASH_LITE).toBe('gemini-3.1-flash-lite');
    expect(out[0].MODEL_IMAGE).toBe('gemini-3.1-flash-image-preview');
    expect(out[0].MODEL_CLAUDE).toBe('claude-opus-5-5');
  });

  test('$env を読まない（n8n 2.x は拒否する）。モデルは生成時に埋め込む', async () => {
    const out = await runNode('Map Variables', [ROW], { env: { GEMINI_MODEL_PRO: 'ignored' } });
    expect(out[0].MODEL_PRO).toBe('gemini-3.1-pro-preview');
    expect(out[0].IMAGE_PROVIDER).toBe('openai');
    expect(out[0].IMAGE_QUALITY).toBe('medium');
    expect(out[0].imgFolder).toBe('S1');
  });
});

describe('Model Registry — Build ノードが data.MODEL_* を参照', () => {
  test('Build P1 Request: data.MODEL_PRO を使う', async () => {
    const out = await runNode('Build P1 Request', [{ KEYWORD: 'k', MODEL_PRO: 'custom-pro' }]);
    expect(out[0].geminiModel).toBe('custom-pro');
  });

  test('Build P1 Request: MODEL_PRO 未指定ならリテラルfallback', async () => {
    const out = await runNode('Build P1 Request', [{ KEYWORD: 'k' }]);
    expect(out[0].geminiModel).toBe('gemini-3.1-pro-preview');
  });

  test('Build P4 Request: data.MODEL_CLAUDE が body と claudeModel に反映', async () => {
    const out = await runNode('Build P4 Request', [{ htmlContent: '<p>x</p>', MODEL_CLAUDE: 'claude-custom' }]);
    expect(out[0].claudeModel).toBe('claude-custom');
    expect(JSON.parse(out[0].requestBody).model).toBe('claude-custom');
  });
});
