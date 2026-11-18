'use strict';
// 生成した Workflow の Code ノードを、n8n に近い形（items に json と binary、$('ノード名') に複数の item）で動かす。
// n8n-mock.js の runNode は json だけを扱うので、バイナリや $('X').all() を使うノードはこちらで試す。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadWorkflow(file = 'SHAMAN_Workflow.json') {
  return JSON.parse(fs.readFileSync(path.join(__dirname, '../../workflows', file), 'utf8'));
}

function codeOf(workflow, name) {
  const node = workflow.nodes.find((n) => n.name === name);
  if (!node) throw new Error(`Node "${name}" not found`);
  return node.parameters.jsCode;
}

/**
 * @param {object} workflow  loadWorkflow() の値
 * @param {string} name      Code ノードの名前
 * @param {object[]} items   [{ json, binary? }]
 * @param {object} refs      { ノード名: [{ json, binary? }] }。refs にないノードは「未実行」（n8n では例外）。
 *                           同じ実行の中で何度も動いたノードは { runs: [[item…]（1 回目）, [item…]（2 回目）…] }。
 * @param {object} helpers   this.helpers（getBinaryDataBuffer など）。executionId で $execution.id を替える
 * @returns {Promise<object[]>} 出力の item（{ json, binary? }）
 */
// 同じ実行の中で何度も動いたノード（{ runs: [...] }）の、指定の回の出力。runIndex を省くと最初の回（index 0）にする。
// n8n のドキュメントの書き方に合わせた、厳しい側の扱い。2.35.7 のソースでは省くと最後の回になるので、どちらでも
// 正しく動くよう、コードは -1（最後の回）を明示する（PR #89 レビュー B1）。
function runOf(ref, runIndex) {
  if (!ref || !Array.isArray(ref.runs)) return ref;
  if (runIndex === undefined) return ref.runs[0];
  if (runIndex === -1) return ref.runs[ref.runs.length - 1];
  if (!ref.runs[runIndex]) throw new Error(`Run ${runIndex} not found`);
  return ref.runs[runIndex];
}

async function runCode(workflow, name, items, refs = {}, helpers = {}) {
  const ctx = {
    // n8n 2.35.7 と同じく、入力は $input で受け取る（暗黙の items は渡さない）。
    $input: { first: () => items[0], all: () => items },
    $: (node) => {
      const missing = () => { throw new Error(`Node '${node}' hasn't been executed`); };
      if (!(node in refs)) return { first: missing, all: missing, isExecuted: false };
      const pickRun = (runIndex) => runOf(refs[node], runIndex);
      return {
        first: (branchIndex, runIndex) => pickRun(runIndex)[0],
        all: (branchIndex, runIndex) => pickRun(runIndex),
        isExecuted: true,
      };
    },
    $execution: { id: helpers.executionId || 'exec-1' },
    console: { log() {}, warn() {} },
    Buffer, JSON, Object, String, Number, Error, Array, Math, Date, RegExp, Promise,
    helpers,
  };
  const fn = vm.runInContext(`(async function () {\n${codeOf(workflow, name)}\n})`, vm.createContext(ctx));
  return fn.call(ctx);
}

module.exports = { loadWorkflow, codeOf, runCode, runOf };
