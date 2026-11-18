'use strict';
/**
 * n8n Code node execution mock.
 *
 * Loads jsCode from the generated workflow JSON and runs it inside a Node.js VM
 * with a mocked n8n execution environment. No n8n, no APIs, no billing.
 *
 * Usage:
 *   const { runNode } = require('../helpers/n8n-mock');
 *   const output = await runNode('Map Variables', [inputJson]);
 */

const vm = require('vm');
const path = require('path');
const fs = require('fs');

const WORKFLOW_JSON = path.join(__dirname, '../../workflows/SHAMAN_Workflow.json');

let _workflow = null;
function getWorkflow() {
  if (!_workflow) {
    _workflow = JSON.parse(fs.readFileSync(WORKFLOW_JSON, 'utf8'));
  }
  return _workflow;
}

/** Invalidate cached workflow (call after re-generating JSON in tests). */
function resetWorkflow() { _workflow = null; }

/** Return raw jsCode string for a named node. */
function getNodeCode(nodeName) {
  const node = getWorkflow().nodes.find(n => n.name === nodeName);
  if (!node) throw new Error(`Node not found in workflow: "${nodeName}"`);
  if (!node.parameters || !node.parameters.jsCode)
    throw new Error(`Node "${nodeName}" has no jsCode`);
  return node.parameters.jsCode;
}

/**
 * Execute a Code node with mock data.
 *
 * @param {string}   nodeName    - Node name as it appears in the workflow
 * @param {Object[]} inputJsons  - Array of plain JSON objects (the .json of each item)
 * @param {Object}   options
 * @param {Object}   options.env       - $env variables  (default: {})
 * @param {Object}   options.nodeRefs  - Mocked $('NodeName').first().json values（何度も動いたノードは { runs: [...] }）
 * @param {Object}   options.fsMock       - Mock for require('fs')  (default: real fs)
 * @param {Function} options.httpMock     - Mock for this.helpers.httpRequest
 * @param {Object}   options.requireMocks - Additional module mocks: { moduleName: mockObject }
 * @returns {Object[]} Array of output .json objects
 */
async function runNode(nodeName, inputJsons, { env = {}, nodeRefs = {}, fsMock = null, httpMock = null, requireMocks = {}, globals = {} } = {}) {
  const jsCode = getNodeCode(nodeName);
  const items = inputJsons.map(j => ({ json: j }));

  // Build require mock — swap fs and any additional mocks if provided
  const mockRequire = (mod) => {
    if (mod === 'fs') return fsMock || fs;
    if (requireMocks[mod]) return requireMocks[mod];
    return require(mod);
  };

  // Build context exposed to the Code node
  const ctx = {
    // n8n globals（入力は $input で受け取る。暗黙の items は渡さない）
    $env: { ...env },
    $input: {
      first: () => items[0] || { json: {} },
      all: () => items,
      item: items[0] || { json: {} },
    },
    // nodeRefs の値が { runs: [1 回目の json, 2 回目の json, …] } なら、同じ実行の中で何度も動いたノード。
    // runIndex を省くと最初の回、-1 で最後の回（code-node.js の runOf と同じ扱い）。
    $: (name) => {
      const ref = nodeRefs[name];
      const pick = (runIndex) => {
        if (!ref || !Array.isArray(ref.runs)) return ref || {};
        if (runIndex === undefined) return ref.runs[0];
        if (runIndex === -1) return ref.runs[ref.runs.length - 1];
        if (!ref.runs[runIndex]) throw new Error(`Run ${runIndex} not found`);
        return ref.runs[runIndex];
      };
      return {
        isExecuted: Boolean(ref && ref.isExecuted),
        first: (branchIndex, runIndex) => ({ json: pick(runIndex) }),
        all: (branchIndex, runIndex) => ref && Array.isArray(ref.items) ? ref.items.map(json => ({ json })) : [{ json: pick(runIndex) }],
        item: { json: pick(-1) },
      };
    },
    $execution: globals.$execution || { id: 'exec-test-1' },
    // Node.js globals needed inside VMs
    Buffer,
    console,
    setTimeout: globals.setTimeout || setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Promise,
    JSON,
    Math,
    Date,
    Error,
    Array,
    Object,
    String,
    Number,
    Boolean,
    RegExp,
    require: mockRequire,
    // this.helpers mock
    helpers: {
      httpRequest: httpMock || (() => { throw new Error('httpRequest not mocked'); }),
    },
  };

  // Wrap code so `this` inside the function === ctx (helpers accessible via this.helpers)
  const wrapped = `(async function VmCodeWrapper() {\n${jsCode}\n})`;
  const script = new vm.Script(wrapped);
  const vmCtx = vm.createContext(ctx);
  const fn = script.runInContext(vmCtx);

  // Call with ctx as `this` so this.helpers works
  const result = await fn.call(ctx);

  if (!Array.isArray(result)) throw new Error(`Node "${nodeName}" did not return an array`);
  return result.map(item => item.json);
}

module.exports = { runNode, getNodeCode, resetWorkflow };
