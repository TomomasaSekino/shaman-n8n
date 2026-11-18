"""SHAMAN プロンプト評価 Workflow（DOCS/PROMPT_EVAL_DESIGN.md）。

LLM のモデルやプロンプトを変えたときの影響と出力の品質を、ステップごとに評価する。
Build・API・Parse のノードは、本番と同じ組み立て関数（generate_workflows.p1Nodes など）から作る。
評価のためにプロンプトや解析の処理を複製しない。

1 回の実行で、試験用スプレッドシートの「評価の実行」の待機中の 1 行・1 ステップだけを動かす。
出力は「評価データ」に項目ごとに全文を残し（長い本文は分割して、切り詰めない）、結果 ID から復元できる。
API の呼び出しは、リクエストと応答の全文を「評価ログ」に残す（認証の情報は残さない）。
"""
import hashlib
import json
import re
from pathlib import Path

import generate_workflows as gw

# WPD は別途承認された試験専用WordPressホストだけ。空リストなら送信禁止。
WP_TEST_SITES_PATH = Path(__file__).parent / 'config' / 'wp_test_sites.json'

def approved_wp_test_sites(path=WP_TEST_SITES_PATH):
    values = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        raise ValueError('wp_test_sites.json must be a list of hostnames')
    domain = re.compile(r'^(?:test|staging)\.(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$')
    if any(not domain.fullmatch(x) for x in values) or len(set(values)) != len(values):
        raise ValueError('wp_test_sites.json contains unsafe or duplicate hostnames')
    return values

FILENAME = 'SHAMAN_Prompt_Eval.json'
NAME = 'SHAMAN: Prompt Eval'
WORKFLOW_ID = 'shaman_test'  # 前の Test Workflow の ID を引き継ぐ
RUN_WEBHOOK_PATH = 'shaman-prompt-eval'  # Actions から実行する Webhook の path（DOCS/RUN_FROM_ACTIONS.md）
DOCUMENT = gw.QUEUE_TEST_SPREADSHEET

# 試験用スプレッドシートのシート（1 行目が見出し）
SHEET_RUNS = '評価の実行'
SHEET_RESULTS = '評価結果'
SHEET_DATA = '評価データ'
SHEET_LOG = '評価ログ'
HEADER_ROW = 1

RUN_COLUMNS = ('実行キー', 'ステップ', '入力の結果ID', '基準の結果ID', 'KEYWORD', 'OPTION1', 'SITE_ID',
               'モデル', '回数', '状態', '実行ID', '結果ID', 'メモ')
RESULT_COLUMNS = ('結果ID', '実行キー', '実行ID', '日時', 'ステップ', '回', 'モデル', 'プロンプトのハッシュ',
                  '処理のハッシュ', '入力の結果ID', '基準の結果ID', '状態', 'エラー', 'API呼び出し回数',
                  '出力の文字数', '測定値', '基準の測定値')
DATA_COLUMNS = ('結果ID', '項目', '型', '分割番号', '分割数', '文字数', '本文')
LOG_COLUMNS = ('日時', '実行ID', '実行キー', '結果ID', '回', '種類', 'ステップ', 'モデル', 'プロンプトのハッシュ',
               '呼び出し', '分割番号', '分割数', 'HTTPの状態', '所要時間ms', '停止理由', 'トークン数', 'エラー',
               'リクエスト', '応答')

# 1 つのセルには 50,000 文字まで。それより長い本文は、この長さで分けて複数の行に書く。
CHUNK = 45000
MAX_ROUNDS = 5
# 本番の Map Variables が読むキューの列（P1 の入力を、キューの行と同じ形で渡す）
QUEUE_KEYWORD_COL = 'KEYWORD\n（必須）\nフォーカスキーワード'
QUEUE_OPTION1_COL = 'OPTION1\n（必須）\n記事の結論・スタンス'

# 測定に使う語（prompts.py の指示と、過去の失敗から）
# P1 のナレッジの見出し（prompts.P1_PROMPT と同じ）。評価で、5 つがそろっているかを見る。
P1_SECTIONS = ('メインキーワードと検索意図', '関連キーワード', '記事に使える具体的な事実', '数値・製品仕様・固有名詞', '確認できなかった情報')
STOCK_PHRASES = ('具体的には', '目安としては', '判断基準としては', 'つまずきやすいのは', '初心者がやりがちなのが',
                 '理由は', '結論は')
BANNED_HEADING_WORDS = gw.BANNED_HEADING_WORDS

STEP_ORDER = ('P1', 'P2', 'P3', 'P4', 'P5', 'P6A', 'P6', 'WP', 'WPD')


def _steps():
    """ステップごとの、本番の組み立て関数・ノード名・モデルの項目・必要な入力・プロンプト。"""
    return {
        'P1': {'nodes': gw.p1Nodes, 'build': 'Build P1 Request', 'http': 'Gemini: Research',
               'parse': 'Parse Research', 'model': 'MODEL_PRO', 'output': 'researchText',
               'requires': ('KEYWORD',), 'prompts': (gw.P1_PROMPT,)},
        'P2': {'nodes': gw.p2Nodes, 'build': 'Build P2 Request', 'http': 'Gemini: Generate Outline',
               'parse': 'Parse Outline', 'model': 'MODEL_OUTLINE', 'output': 'outlineText',
               'requires': ('KEYWORD', 'p1Knowledge'), 'prompts': (gw.P2_T1, gw.P2_T2)},
        'P3': {'nodes': gw.p3Nodes, 'build': 'Build P3 Request', 'http': 'Gemini: Generate Content',
               'parse': 'Parse Content', 'model': 'MODEL_PRO', 'output': 'htmlContent',
               'requires': ('KEYWORD', 'outlineText', 'p1Knowledge'), 'prompts': (gw.P3_RAW,)},
        'P4': {'nodes': gw.p4Nodes, 'build': 'Build P4 Request', 'http': 'Claude: Strengthen Content',
               'parse': 'Parse Strengthened', 'model': 'MODEL_CLAUDE', 'output': 'articleHtml',
               'requires': ('KEYWORD', 'htmlContent', 'p1Knowledge'), 'prompts': (gw.P4_RAW, gw.P4_REPAIR_RAW)},
        'P5': {'nodes': gw.p5Nodes, 'build': 'Build P5 Request', 'http': 'Gemini: Generate Meta',
               'parse': 'Parse Meta + Extract Title', 'model': 'MODEL_FLASH_LITE', 'output': 'metaDescription',
               'requires': ('KEYWORD', 'articleHtml'), 'prompts': (gw.P5_RAW,)},
        'P6A': {'nodes': gw.p6AnalysisNodes, 'build': 'Build Section Analysis Request',
                'http': 'Gemini: Analyze Sections', 'parse': 'Parse Section Plan', 'model': 'MODEL_SECTION_ANALYSIS',
                'output': 'sections', 'requires': ('articleHtml',), 'prompts': (gw.P6_ANALYZE_PROMPT,)},
        # 画像の生成（OpenAI images/edits）。本番と同じく、参考画像を Google Drive から読んで送る。費用を抑えるため、
        # P6A の結果の最初の節だけ、画像を 1 枚作る（DOCS/PROMPT_EVAL_DESIGN.md §4.3、DOCS/P6_IMAGE_REQUEST.md）。
        'P6': {'nodes': _p6_image_nodes, 'entry': 'Check P6 Mode', 'build': 'Build Image Requests',
               'http': 'P6: Generate Image', 'parse': 'Collect Generated Images', 'model': 'MODEL_IMAGE_OPENAI',
               'output': 'p6Results', 'requires': ('sections', 'imgFolder'), 'prompts': (gw.P6_IMAGE_PROMPT_OPENAI, gw.P6_IMAGE_PROMPT_OPENAI_NO_REFERENCE,),
               'raw': True},
        # 2サイト以上で実際のBasic Authを順番に評価する。WordPressへはGETのみ。
        'WP': {'nodes': _wp_auth_nodes, 'build': 'WP: Build Auth Probe',
               'http': 'WP: Probe GET', 'parse': 'WP: Parse Auth Probe',
               'model': 'WP_AUTH_MODEL_UNUSED', 'output': 'wpAuthResult',
               'requires': ('siteId',), 'prompts': ('WP Basic Auth multisite GET-only probe',),
               'raw': True},
        # テスト専用ドメインだけへ下書き1件を作る。公開記事・本番ドメインに書き込まない。
        'WPD': {'nodes': _wp_draft_nodes, 'build': 'WP: Build Test Draft',
                'http': 'WP: Test Draft POST', 'parse': 'WP: Parse Test Draft',
                'model': 'WP_AUTH_MODEL_UNUSED', 'output': 'wpDraftResult',
                'requires': ('siteId',), 'prompts': ('WP draft creation on isolated test host',),
                'raw': True},
    }


# P6 の、参考画像を読むノード（本番と同じ）の並び。失敗は、その回のエラーの結果にする（Eval: Parse Failed）。
P6_REFERENCE_CHAIN = ('Check P6 Mode', 'P6: Find IMG Root', 'P6: Verify IMG Root',
                      'P6: Find Image Folder', 'P6: Check Image Folder',
                      'P6: Has Image Folder?', 'P6: List Reference Images', 'P6: Check Reference Images',
                      'P6: Has Reference Images?', 'P6: Download Reference Image',
                      'Read Reference Images', 'Eval: P6 Section')
P6_IMAGE_NODES = ('Build Image Requests', 'P6: Generate Image', 'Collect Generated Images')

P6_CHECK_MODE_JS = r'''
// 本番の Check P6 Mode（IF）の代わり。参考画像のノード（本番と同じ）が、この名前で行の設定を読む。
return [{ json: Object.assign({}, items[0].json) }];
'''

P6_SECTION_JS = r'''
// P6A の結果の節のうち、最初の 1 つだけを、本番の Parse Section Plan が出す節の形にする（画像は 1 枚だけ作る）。
var data = Object.assign({}, $('Check P6 Mode').first().json);
var secs = Array.isArray(data.sections) ? data.sections : [];
if (!secs.length || !secs[0] || !secs[0].imageDescription) throw new Error('入力の結果に、画像を作る節（sections）がありません');
delete data.sections;
delete data.referenceImages;
return [{ json: Object.assign(data, secs[0]) }];
'''


def _p6_image_nodes():
    nodes = [gw.code('Check P6 Mode', P6_CHECK_MODE_JS, [0, 0])]
    nodes += gw.p6ReferenceImageNodes(0, 0)
    nodes.append(gw.code('Eval: P6 Section', P6_SECTION_JS, [0, 0]))
    nodes += [n for n in gw.p6ImageNodes(0, 0, provider='openai') if n['name'] in P6_IMAGE_NODES]
    for n in nodes:
        if n['name'] in P6_REFERENCE_CHAIN and n['type'] != 'n8n-nodes-base.if':
            n['onError'] = 'continueErrorOutput'
    return nodes



def _wp_auth_nodes():
    """実機テスト：同じWordPress HTTPノードを2件以上のSITE_IDで順番に実行する。書き込みはしない。"""
    build = gw.code('WP: Build Auth Probe', r'''
var data = Object.assign({}, items[0].json);
var siteId = String(data.siteId || '').trim().toLowerCase();
var valid = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
if (!valid.test(siteId)) throw new Error('WP_TEST_INVALID_SITE_ID: ' + siteId);
data.siteId = siteId;
data.wpUrl = 'https://' + siteId;
return [{ json: data }];
''', [0, 0])
    http = gw.wordpressHttp('WP: Probe GET', [0, 0], 'GET', '/wp-json/wp/v2/users/me?context=edit')
    parse = gw.code('WP: Parse Auth Probe', r'''
var response = items[0].json || {};
var target = $('WP: Build Auth Probe').first(0, -1).json;
var status = Number(response.statusCode || 0);
var body = response.body || {};
if (response.error || status !== 200 || !Number.isInteger(Number(body.id)) || Number(body.id) < 1) {
  throw new Error('WP_AUTH_TEST_FAILED: ' + target.siteId + ' HTTP ' + status);
}
return [{ json: {
  siteId: target.siteId,
  wpAuthResult: { siteId: target.siteId, ok: true, userId: Number(body.id), statusCode: status }
} }];
''', [0, 0])
    return [build, http, parse]



def _wp_draft_nodes():
    """試験サイトへ下書き1件だけを投稿。指定ドメインと明示確認を必須にする。"""
    build = gw.code('WP: Build Test Draft', r'''
var data = Object.assign({}, items[0].json);
var run = $('Eval: Pick Run').first().json.run;
var domain = String(data.siteId || '').trim().toLowerCase();
var allowed = /^(?:test|staging)\.(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/;
if (run.option1 !== 'TEST_DRAFT_ALLOWED' || !allowed.test(domain))
  throw new Error('WP_TEST_DRAFT_DENIED: テスト専用ドメインと明示確認が必要です');
if (data.wpUrl && data.wpUrl !== 'https://' + domain)
  throw new Error('WP_TEST_DRAFT_DENIED: SITE_IDと送信先が一致しません');
var production = __PROD_DOMAINS__;
if (production.indexOf(domain) !== -1)
  throw new Error('WP_TEST_DRAFT_DENIED: 本番登録サイトは使用できません');
var approved = __APPROVED_TEST_DOMAINS__;
if (approved.indexOf(domain) === -1)
  throw new Error('WP_TEST_DRAFT_DENIED: 承認済み試験ドメインではありません');
data.siteId = domain;
data.wpUrl = 'https://' + domain;
data.wpPostBody = { title: '[SHAMAN TEST] ' + $execution.id, content: '<p>SHAMAN テスト用の下書きです。</p>',
                    status: 'draft', comment_status: 'closed', ping_status: 'closed' };
return [{ json: data }];
'''.replace('__PROD_DOMAINS__', gw.js_str(sorted(gw.load_sites())))
   .replace('__APPROVED_TEST_DOMAINS__', gw.js_str(approved_wp_test_sites())), [0, 0])
    post = gw.wordpressHttp('WP: Test Draft POST', [0, 0], 'POST', '/wp-json/wp/v2/posts',
                            body='={{ $json.wpPostBody }}')
    parse = gw.code('WP: Parse Test Draft', r'''
var response = items[0].json || {};
var body = response.body || {};
var target = $('WP: Build Test Draft').first(0, -1).json;
if (response.error || Number(response.statusCode) !== 201 || !Number(body.id) || body.status !== 'draft')
  throw new Error('WP_TEST_DRAFT_FAILED: ' + target.siteId + ' HTTP ' + Number(response.statusCode || 0));
return [{ json: {
  siteId: target.siteId,
  wpDraftResult: { ok: true, siteId: target.siteId, postId: Number(body.id), status: 'draft' }
} }];
''', [0, 0])
    return [build, post, parse]


def _hash(*parts):
    return hashlib.sha256('\0'.join(parts).encode('utf-8')).hexdigest()[:12]


def _step_table(step_nodes):
    """Code ノードに埋め込むステップの表（ノード名・モデルの項目・必要な入力・ハッシュ）。"""
    table = {}
    for step, spec in _steps().items():
        nodes = {n['name']: n for n in step_nodes[step]}
        code_text = nodes[spec['build']]['parameters']['jsCode'] + '\0' + nodes[spec['parse']]['parameters']['jsCode']
        table[step] = {'build': spec['build'], 'http': spec['http'], 'parse': spec['parse'],
                       'model': spec['model'], 'output': spec['output'], 'requires': list(spec['requires']),
                       'promptHash': _hash(*spec['prompts']), 'codeHash': _hash(code_text),
                       'raw': bool(spec.get('raw'))}
    return table


# ── Code ノードの共通部分 ──────────────────────────────────────────────
REDACT_JS = r'''
function redact(s) {
  return String(s == null ? '' : s)
    .replace(/(key=)[^&\s"',}]+/gi, '$1***')
    .replace(/((?:x-(?:goog-)?api-key|authori[z]ation)["']?\s*[:=]\s*["']?)(?:Bearer\s+)?[^\s"',}]+/gi, '$1***')
    .replace(/Bearer\s+[^\s"',}]+/gi, 'Bearer ***')
    .replace(/AIza[0-9A-Za-z_\-]{20,}/g, '***')
    .replace(/\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}/g, '***');
}
// HTTP のエラーは、文字列のことも、オブジェクトのこともある。オブジェクトをそのまま文字列にしない（リクエストの設定が入りうる）。
function errorText(e) {
  if (e == null) return '';
  if (typeof e === 'string') return e;
  var parts = [e.message, e.description, e.code, e.httpCode].filter(function(x) { return x != null && x !== ''; });
  return parts.length ? parts.map(String).join(' / ') : '通信エラー';
}
'''

CHUNK_JS = r'''
var CHUNK = __CHUNK__;
// 長い本文を CHUNK 文字ずつに分ける。サロゲートペアの途中では切らない。空なら 1 つの空文字。
function chunks(text) {
  var out = [], i = 0;
  while (i < text.length) {
    var end = Math.min(i + CHUNK, text.length);
    if (end < text.length) { var c = text.charCodeAt(end - 1); if (c >= 0xD800 && c <= 0xDBFF) end--; }
    out.push(text.slice(i, end));
    i = end;
  }
  if (!out.length) out.push('');
  return out;
}
function nowIso() { return new Date().toISOString(); }
'''

CONTEXT_JS = r'''
var STEPS = __STEPS__;
var run = $('Eval: Pick Run').first().json.run;
var spec = STEPS[run.step];
'''


def _js(template, **values):
    text = template
    for key, value in values.items():
        text = text.replace('__' + key + '__', value)
    left = re.findall(r'__[A-Z0-9_]+__', text)
    if left:
        raise ValueError('unreplaced placeholders: ' + ', '.join(sorted(set(left))))
    return text


def _pick_run_js(table):
    return _js(r'''
var STEPS = __STEPS__;
var SITES = __SITES__;
var MAX_ROUNDS = __MAX_ROUNDS__;
var rows = items.map(function(it) { return it.json || {}; }).filter(function(r) { return r.row_number != null; });
var waiting = rows.filter(function(r) { return String(r['状態'] || '').trim() === '待機中'; });
// Actions から実行したときは、この実行が足した行（実行キーで特定）だけを使う。ほかの待機中の行は使わない。
var setup = $('Eval: Sheet Setup').isExecuted ? $('Eval: Sheet Setup').first().json : {};
var addedKey = setup.newRun ? String($('Eval: New Run Row').first().json['実行キー']) : '';
// 止めた理由。足した行なら、その行を「エラー」にする（Eval: Mark Failed。待機中のまま残さない）。
var rowNo = null;
function stop(reason) { return [{ json: { ok: false, reason: reason, evalReason: reason, rowNumber: rowNo, run: null } }]; }
var r;
if (addedKey) {
  var added = waiting.filter(function(x) { return String(x['実行キー'] || '').trim() === addedKey; });
  if (added.length !== 1) return stop('Actions から足した行（実行キー ' + addedKey + '）が「評価の実行」の待機中に ' + added.length + ' 件です');
  r = added[0];
  rowNo = Number(r.row_number);
} else {
  if (waiting.length !== 1) return stop('「評価の実行」の待機中の行が ' + waiting.length + ' 件です（1 件だけにしてください）');
  r = waiting[0];
}
var s = function(k) { return String(r[k] == null ? '' : r[k]).trim(); };
var run = {
  key: s('実行キー'), step: s('ステップ').toUpperCase(), inputId: s('入力の結果ID'), baselineId: s('基準の結果ID'),
  keyword: s('KEYWORD'), option1: s('OPTION1'), siteId: s('SITE_ID'), model: s('モデル'),
  rounds: s('回数') === '' ? 1 : Number(s('回数'))
};
var ID = /^R\d+-\d+$/;
if (!Object.prototype.hasOwnProperty.call(STEPS, run.step)) return stop('ステップ「' + run.step + '」は ' + Object.keys(STEPS).join('・') + ' のどれかにしてください');
if (!(Number.isInteger(run.rounds) && run.rounds >= 1 && run.rounds <= MAX_ROUNDS)) return stop('回数は 1〜' + MAX_ROUNDS + ' にしてください');
if (run.model && !/^[a-z0-9][a-z0-9.\-]*$/.test(run.model)) return stop('モデル「' + run.model + '」に使えない文字があります（英小文字・数字・.・- だけ）');
if (run.baselineId && !ID.test(run.baselineId)) return stop('基準の結果ID「' + run.baselineId + '」の形が違います（例: R123-1）');
if (run.step === 'P1' || run.step === 'WP' || run.step === 'WPD') {
  if (run.step === 'P1' && !run.keyword) return stop('P1 は KEYWORD が必要です');
  var domains = run.step === 'WP' ? run.siteId.split(',').map(function(s) { return s.trim().toLowerCase(); }) : [run.siteId.trim().toLowerCase()];
  var DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
  if (domains.some(function(s) { return !DOMAIN.test(s); })) return stop('SITE_ID は有効なドメインを指定してください');
  if (run.step === 'WP' && (domains.length < 2 || domains.length !== run.rounds || new Set(domains).size !== domains.length))
    return stop('WP認証テストは重複しない2〜5サイトをSITE_IDにカンマ区切りで指定し、回数をサイト数に合わせてください');
  if (run.step === 'WPD' && (domains.length !== 1 || run.rounds !== 1 ||
      run.option1 !== 'TEST_DRAFT_ALLOWED' || !/^(?:test|staging)\./.test(domains[0]) ||
      __APPROVED_TEST_DOMAINS__.indexOf(domains[0]) === -1 ||
      Object.prototype.hasOwnProperty.call(SITES, domains[0])))
    return stop('WPDはtest/stagingから始まる未登録の試験専用サイト1件と明示確認が必要です');
  if (run.inputId) return stop(run.step + ' は入力の結果IDを使いません');
  if (run.step === 'WP') run.authSites = domains;
  run.siteId = domains[0];
} else if (!ID.test(run.inputId)) {
  return stop(run.step + ' は入力の結果ID（例: R123-1）が必要です');
}
return [{ json: { ok: true, reason: '', rowNumber: Number(r.row_number), run: run } }];
''', STEPS=gw.js_str(table), SITES=gw.js_str(gw.load_sites()), MAX_ROUNDS=str(MAX_ROUNDS),
        APPROVED_TEST_DOMAINS=gw.js_str(approved_wp_test_sites()))


def _prepare_input_js(table):
    return _js(r'''
var STEPS = __STEPS__;
var SITES = __SITES__;
var pick = $('Eval: Pick Run').first().json;
var run = pick.run;
var spec = STEPS[run.step];
function stop(reason) { return [{ json: { evalOk: false, evalReason: reason } }]; }
function restore(id) {
  var rows = $('Eval: Read Input').all().map(function(it) { return it.json || {}; })
    .filter(function(r) { return String(r['結果ID'] || '') === id; });
  if (!rows.length) return { error: '結果ID「' + id + '」の評価データがありません' };
  var fields = {};
  rows.forEach(function(r) {
    var name = String(r['項目'] || '');
    (fields[name] = fields[name] || []).push(r);
  });
  var data = {};
  var names = Object.keys(fields);
  for (var i = 0; i < names.length; i++) {
    var parts = fields[names[i]].slice().sort(function(a, b) { return Number(a['分割番号']) - Number(b['分割番号']); });
    var n = Number(parts[0]['分割数']);
    var ok = parts.length === n && parts.every(function(p, k) { return Number(p['分割番号']) === k + 1 && Number(p['分割数']) === n; });
    if (!ok) return { error: '結果ID「' + id + '」の項目「' + names[i] + '」の分割がそろっていません' };
    var text = parts.map(function(p) { return p['本文'] == null ? '' : String(p['本文']); }).join('');
    if (text.length !== Number(parts[0]['文字数'])) return { error: '結果ID「' + id + '」の項目「' + names[i] + '」の文字数が保存したときと違います' };
    var type = String(parts[0]['型'] || '');
    if (type === 'string') data[names[i]] = text;
    else if (type === 'json') { try { data[names[i]] = JSON.parse(text); } catch (e) { return { error: '結果ID「' + id + '」の項目「' + names[i] + '」を JSON として読めません' }; } }
    else return { error: '結果ID「' + id + '」の項目「' + names[i] + '」の型「' + type + '」が分かりません' };
  }
  return { data: data };
}
var base;
if (run.step === 'P1' || run.step === 'WP' || run.step === 'WPD') {
  // WPは各回で異なるSITE_IDを通し、同じHTTPノードのCredential式を再評価する。
  base = { siteId: run.siteId, siteConfig: SITES[run.siteId] || {}, rowIndex: null };
  if (run.step === 'P1') {
    base[__KEYWORD_COL__] = run.keyword;
    base[__OPTION1_COL__] = run.option1;
  }
} else {
  var got = restore(run.inputId);
  if (got.error) return stop(got.error);
  var meta = got.data._meta || {};
  if (meta.status !== '成功') return stop('結果ID「' + run.inputId + '」は成功した結果ではありません（' + (meta.status || '状態なし') + '）');
  base = Object.assign({}, got.data);
  delete base._meta;
  var missing = spec.requires.filter(function(k) { return base[k] == null || base[k] === ''; });
  if (missing.length) return stop('結果ID「' + run.inputId + '」には ' + run.step + ' の入力（' + missing.join('・') + '）がありません');
}
var baseline = '';
if (run.baselineId) {
  var b = $('Eval: Read Baseline').all().map(function(it) { return it.json || {}; })
    .filter(function(r) { return String(r['結果ID'] || '') === run.baselineId; });
  if (b.length !== 1) return stop('基準の結果ID「' + run.baselineId + '」が評価結果に ' + b.length + ' 件あります');
  baseline = String(b[0]['測定値'] || '');
}
var out = [];
for (var i = 1; i <= run.rounds; i++) {
  var one = Object.assign({}, base, { evalOk: true, evalRound: i, evalBaseline: baseline });
  if (run.step === 'WP') one.siteId = run.authSites[i - 1];
  out.push({ json: one });
}
return out;
''', STEPS=gw.js_str(table), SITES=gw.js_str(gw.load_sites()),
        KEYWORD_COL=gw.js_str(QUEUE_KEYWORD_COL), OPTION1_COL=gw.js_str(QUEUE_OPTION1_COL))


def _apply_model_js(table):
    models = {'MODEL_PRO': gw.MODEL_PRO, 'MODEL_FLASH_LITE': gw.MODEL_FLASH_LITE, 'MODEL_OUTLINE': gw.MODEL_OUTLINE,
              'MODEL_SECTION_ANALYSIS': gw.MODEL_SECTION_ANALYSIS, 'MODEL_CLAUDE': gw.MODEL_CLAUDE,
              'MODEL_IMAGE_OPENAI': gw.MODEL_IMAGE_OPENAI}
    return _js(CONTEXT_JS + r'''
var MODELS = __MODELS__;
// 保存した入力には、そのときのモデル名や P4 の途中の状態が入っている。今の本番の値に戻してから、指定のモデルを入れる。
var data = Object.assign({}, items[0].json);
['evalOk', 'evalRound', 'evalBaseline', 'evalCallAt', 'requestBody', 'geminiModel', 'claudeModel',
 'p1Attempt', 'p1Retry', 'p2Attempt', 'p2Retry', 'p2PrevOutline', 'p2QualityFeedback', 'p2FirstIssues', 'p2Issues',
 'p4Attempt', 'p4Retry', 'p4PrevHtml', 'p4PrevIssues', 'p4QualityFeedback',
 'p6Attempt', 'p6Results', 'p6RetryNeeded', 'referenceImages'].forEach(function(k) { delete data[k]; });
Object.keys(MODELS).forEach(function(k) { data[k] = MODELS[k]; });
if (run.model) data[spec.model] = run.model;
return [{ json: data }];
''', STEPS=gw.js_str(table), MODELS=gw.js_str(models))


STAMP_JS = r'''
// API を呼ぶ直前の時刻。所要時間をログに残すため。
return items.map(function(it) { return { json: Object.assign({}, it.json, { evalCallAt: Date.now() }) }; });
'''


def _call_log_js(table):
    return _js(CONTEXT_JS + REDACT_JS + CHUNK_JS + r'''
// 出力が 2 つあるノード（成功・エラー、Loop の done・loop）は、出力の番号を明示する。省くと n8n は、上流へたどって
// 最初に見つかったつながりの出力を使い、空のエラー出力を選ぶことがある（2026-11-07、実行 6 の Eval: Record）。
// 1 つの実行で、回の数と P2・P4 の出し直しのぶん、同じノードが何度も動く。今の回の出力を読むため、最後の実行（-1）を
// 明示する（省略したときの扱いが n8n のドキュメントとソースで食い違う。PR #89 レビュー B1・B3）。
var stamp = $('Eval: Stamp').first(0, -1).json;
var res = $(spec.http).first(0, -1).json;
var round = $('Eval: Repeat').first(1, -1).json.evalRound;
var status = res.statusCode == null ? '' : res.statusCode;
var body = res.body === undefined ? null : res.body;
var ok = !res.error && Number(status) >= 200 && Number(status) < 300;
var error = '';
if (res.error) error = redact(errorText(res.error)) || '通信エラー';
else if (!ok) error = redact('HTTP ' + status + ': ' + (body && body.error ? errorText(body.error) : (typeof body === 'string' ? body : JSON.stringify(body))));
// エラー文は出力ではないので、セルに収まる長さにする（全文は応答の列に残る）。
if (error.length > 2000) error = error.substring(0, 2000) + '…';
var stopReason = '', tokens = '';
if (body && typeof body === 'object') {
  if (body.stop_reason) stopReason = body.stop_reason;
  else if (body.candidates && body.candidates[0] && body.candidates[0].finishReason) stopReason = body.candidates[0].finishReason;
  if (body.usage) tokens = JSON.stringify(body.usage);
  else if (body.usageMetadata) tokens = JSON.stringify(body.usageMetadata);
}
// 認証は Credential がヘッダーに付けるので、リクエストの本文には入らない。応答のヘッダーは残さない。
// 成功の応答は出力そのものなので、そのまま残す（伏せない）。エラーの応答とエラー文だけを伏せる。
// 画像（参考画像の data: URL、生成した画像の base64）は、長さだけを残す（ログのシートが画像で埋まらないよう）。
function shrink(k, v) {
  if ((k === 'image_url' || k === 'b64_json') && typeof v === 'string' && v.length > 200) return v.substring(0, 40) + '…（' + v.length + ' 文字）';
  return v;
}
var rb = stamp.requestBody;
var request = rb == null ? '' : (typeof rb === 'string' ? rb : JSON.stringify(rb, shrink));
// WordPress の /users/me の応答はユーザー情報を含む。シートにはIDとHTTP結果以外は残さない。
if (run.step === 'WP' || run.step === 'WPD') body = {
  id: body && body.id != null ? Number(body.id) : null,
  status: run.step === 'WPD' ? String(body && body.status || '') : undefined
};
var bodyText = body == null ? '' : (typeof body === 'string' ? body : JSON.stringify(body, shrink));
var response = ok ? bodyText : redact(res.error ? '' : (body == null ? JSON.stringify({ statusCode: status }) : bodyText));
var req = chunks(request), resp = chunks(response);
var n = Math.max(req.length, resp.length);
// P2・P4 は出し直しで 2 回呼ぶことがある。何回目の呼び出しかを残す（トークン数は呼び出しごとの行にある）。
var call = run.step === 'P4' ? (stamp.p4Attempt || 1) : run.step === 'P2' ? (stamp.p2Attempt || 1)
  : run.step === 'P1' ? (stamp.p1Attempt || 1) : 1;
var rows = [];
for (var i = 0; i < n; i++) {
  rows.push({ json: {
    '日時': nowIso(), '実行ID': String($execution.id), '実行キー': run.key,
    '結果ID': 'R' + $execution.id + '-' + round, '回': round, '種類': '呼び出し', 'ステップ': run.step,
    'モデル': String(stamp.geminiModel || stamp.claudeModel || (rb && typeof rb === 'object' && rb.model) || ''), 'プロンプトのハッシュ': spec.promptHash,
    '呼び出し': call, '分割番号': i + 1, '分割数': n, 'HTTPの状態': status,
    '所要時間ms': i === 0 ? Date.now() - Number(stamp.evalCallAt) : '', '停止理由': stopReason, 'トークン数': tokens,
    'エラー': i === 0 ? error : '', 'リクエスト': req[i] || '', '応答': resp[i] || ''
  } });
}
return rows;
''', STEPS=gw.js_str(table), CHUNK=str(CHUNK))


def _response_js(table):
    return _js(CONTEXT_JS + REDACT_JS + r'''
// 本番の Parse ノードが受け取るのと同じ形（応答の本文）に戻す。通信エラーは、伏せたメッセージだけを渡す。
var res = $(spec.http).first(0, -1).json;
// P6 の Collect Generated Images は、HTTP の応答（statusCode・body・error）をそのまま読む（本番と同じ形）。
if (spec.raw) return [{ json: res.error ? { error: { message: redact(errorText(res.error)) } } : res }];
if (res.error) return [{ json: { error: { message: redact(errorText(res.error)) } } }];
var body = res.body;
if (body && typeof body === 'object' && !Array.isArray(body)) return [{ json: body }];
return [{ json: { error: { message: redact('JSON ではない応答（HTTP ' + res.statusCode + '）: ' + String(body).substring(0, 500)) } } }];
''', STEPS=gw.js_str(table))


PARSE_FAILED_JS = REDACT_JS + r'''
// その回のステップのノード（本番の Build・Parse と、評価のノード）が失敗した。その回をエラーの結果にして、次の回へ進む。
var e = items[0].json || {};
var msg = e.error ? errorText(e.error) : (e.message || 'ノードが失敗しました');
return [{ json: { evalError: redact(msg) } }];
'''

MEASURE_JS = r'''
var STOCK = __STOCK__;
var BANNED = __BANNED__;
__ARTICLE_REF__
var P1_SECTIONS = __P1_SECTIONS__;
function plainLen(s) { return String(s || '').replace(/<[^>]+>/g, '').replace(/&nbsp;/g, '').replace(/\s/g, '').length; }
function count(re, s) { return (String(s || '').match(re) || []).length; }
function tagTexts(s, tag) {
  var out = [], re = new RegExp('<' + tag + '\\b[^>]*>([\\s\\S]*?)</' + tag + '>', 'gi'), m;
  while ((m = re.exec(String(s || '')))) out.push(m[1].replace(/<[^>]+>/g, '').trim());
  return out;
}
function hrefs(s) {
  var out = [], re = /<a\b[^>]*href\s*=\s*["']([^"']+)["']/gi, m;
  while ((m = re.exec(String(s || '')))) out.push(m[1]);
  return out;
}
function notIn(urls, text) { return urls.filter(function(u) { return String(text || '').indexOf(u) === -1; }); }
function h3Sections(html) {
  return String(html || '').split('<h3').slice(1).map(function(part) {
    var sec = part.split('<h2')[0];
    var title = sec.substring(sec.indexOf('>') + 1, sec.indexOf('</h3>')).replace(/<[^>]+>/g, '').trim();
    return { title: title, chars: plainLen(sec.substring(sec.indexOf('</h3>') + 5)),
             h4: count(/<h4\b/gi, sec), table: count(/<table\b/gi, sec), ol: count(/<ol\b/gi, sec), ul: count(/<ul\b/gi, sec) };
  });
}
function stockCounts(html) {
  var text = String(html || '').replace(/<[^>]+>/g, ''), out = {};
  STOCK.forEach(function(p) { var n = text.split(p).length - 1; if (n) out[p] = n; });
  return out;
}
function rawText(body) {
  if (!body || typeof body !== 'object') return '';
  if (Array.isArray(body.content)) return body.content.filter(function(c) { return c.type === 'text'; }).map(function(c) { return c.text || ''; }).join('');
  var c = body.candidates && body.candidates[0] && body.candidates[0].content;
  return c && c.parts ? c.parts.map(function(p) { return p.text || ''; }).join('') : '';
}
// トークン数（最後の呼び出しの応答。Gemini は usageMetadata、Claude・OpenAI は usage）。P4 で 2 回呼んだときの
// 1 回目は、評価ログの「トークン数」にある。
function usageOf(body) {
  var g = body && body.usageMetadata, u = body && body.usage, n = function(v) { return typeof v === 'number' ? v : null; };
  if (g) return { in: n(g.promptTokenCount), out: n(g.candidatesTokenCount), thinking: n(g.thoughtsTokenCount),
                  cacheRead: n(g.cachedContentTokenCount), total: n(g.totalTokenCount) };
  if (u) return { in: n(u.input_tokens), out: n(u.output_tokens), cacheRead: n(u.cache_read_input_tokens),
                  cacheWrite: n(u.cache_creation_input_tokens), total: n(u.total_tokens) };
  return null;
}
function measure(step, out, outs, input, body) {
  var raw = rawText(body);
  var m = { rawChars: raw.length, usage: usageOf(body) };
  if (step === 'P1') {
    m.chars = String(out.researchText || '').length;
    m.headings = count(/^#{1,6}\s/gm, out.researchText);
    var src = out.p1Sources || {};
    m.sources = (src.sources || []).length;
    m.queries = (src.queries || []).length;
    m.supports = (src.supports || []).length;
    m.missingSections = P1_SECTIONS.filter(function(h) { return !new RegExp('^#{1,4}\\s*' + h + '\\s*$', 'm').test(String(out.researchText || '')); });
    m.gapsChars = String(out.p1ResearchGaps || '').length;
    m.researchStatus = out.p1ResearchStatus || null;
    m.urlsInText = urlsIn(out.researchText).length;
    m.attempts = out.p1Attempt || 1;
  } else if (step === 'P2') {
    var lines = String(out.outlineText || '').split('\n');
    var heads = lines.filter(function(l) { return /^#{2,3}\s/.test(l); }).map(function(l) { return l.replace(/^#+\s*/, '').trim(); });
    var kw = String(out.KEYWORD || '').trim();
    m.chars = String(out.outlineText || '').length;
    m.h2 = lines.filter(function(l) { return /^##\s/.test(l); }).length;
    m.h3 = lines.filter(function(l) { return /^###\s/.test(l); }).length;
    m.bannedHeadings = heads.filter(function(h) { return BANNED.some(function(w) { return h.indexOf(w) !== -1; }); });
    m.keywordHeadings = kw ? heads.filter(function(h) { return h.indexOf(kw) !== -1; }).length : 0;
    // 25 文字を超える見出し（記号と空白を除いて数える）
    m.overLength = heads.filter(function(h) { return h.replace(/[\s\u3000!-\/:-@\[-`{-~、。・「」『』（）【】！？ー〜]/g, '').length > 25; });
    m.attempts = out.p2Attempt || 1;
    m.firstIssues = out.p2FirstIssues || null;
  } else if (step === 'P3') {
    m.chars = plainLen(out.htmlContent);
    m.h2 = count(/<h2\b/gi, out.htmlContent);
    m.h3 = count(/<h3\b/gi, out.htmlContent);
    m.linksRemoved = out.p3LinksRemoved || 0;
    m.urlsNotInInput = notIn(hrefs(raw), String(input.outlineText || '') + String(input.p1Knowledge || ''));
    m.siteRefs = siteRefsIn(out.htmlContent);
    m.urlTexts = urlsIn(out.htmlContent).length;
  } else if (step === 'P4') {
    var html = String(out.articleHtml || ''), base = String(input.htmlContent || '');
    m.chars = plainLen(html);
    m.baseChars = plainLen(base);
    m.qualityIssues = out.p4QualityIssues || null;
    m.selected = out.p4Selected || '';
    m.attempts = out.p4Attempt || 1;
    m.sections = h3Sections(html);
    m.stockPhrases = stockCounts(html);
    // リンクは Parse Strengthened が外すので、モデルが出したリンクは生の応答（最後の呼び出し）で数える
    var emitted = raw || html;
    m.linksAdded = count(/<a\b/gi, emitted) - count(/<a\b/gi, base);
    m.urlsNotInInput = notIn(hrefs(emitted), base + String(input.p1Knowledge || ''));
    m.siteRefs = siteRefsIn(html);
    m.linksRemoved = out.p4LinksRemoved || 0;
    m.urlsRemoved = out.p4UrlsRemoved || 0;
    m.headingsMatch = tagTexts(base, 'h2').join('|') === tagTexts(html, 'h2').join('|')
      && tagTexts(base, 'h3').join('|') === tagTexts(html, 'h3').join('|');
  } else if (step === 'P5') {
    var meta = String(out.metaDescription || '');
    m.chars = meta.length;
    m.inRange = meta.length >= 120 && meta.length <= 140;
    m.rawTrailingCountNote = /[\s　]*(?:[（(【\[]\s*(?:文字数\s*[:：]\s*\d{2,4}\s*(?:文字|字)?|\d{2,4}\s*(?:文字|字))\s*[）)】\]]|\d{2,4}\s*(?:文字|字))[\s　]*$/.test(raw.trim());
    m.title = out.articleTitle || '';
  } else if (step === 'P6') {
    var rs = out.p6Results || [];
    m.images = rs.filter(function(r) { return r.imageB64; }).length;
    m.imageBytes = rs.map(function(r) { return r.imageB64 ? Math.floor(r.imageB64.length * 3 / 4) : 0; });
    m.errors = rs.filter(function(r) { return r.imageError; }).map(function(r) { return r.imageError; });
  } else if (step === 'P6A') {
    var secs = outs.filter(function(o) { return o.sectionIndex != null; });
    m.p6Status = out.p6Status || null;
    m.sections = secs.length;
    m.withScene = secs.filter(function(o) { return o.scene && typeof o.scene === 'object'; }).length;
    m.dropped = out.p6SectionsDropped || 0;
    // insertionContext が、記事の見出しタグと一字一句同じ（タグごと）だった節の数
    m.exactContext = secs.filter(function(o) { return String(input.articleHtml || '').indexOf(String(o.insertionContext || '')) !== -1 && /^<h[2-4]\b/i.test(String(o.insertionContext || '')); }).length;
  }
  return m;
}
'''


def _record_js(table):
    return _js(CONTEXT_JS + CHUNK_JS + MEASURE_JS.replace('__STOCK__', gw.js_str(list(STOCK_PHRASES)))
               .replace('__BANNED__', gw.js_str(list(BANNED_HEADING_WORDS)))
               .replace('__ARTICLE_REF__', '\n'.join(gw.ARTICLE_REF_JS))
               .replace('__P1_SECTIONS__', gw.js_str(list(P1_SECTIONS))) + r'''
// 出力が 2 つあるノードは、出力の番号を明示する（0：成功、Loop の 1：loop）。Eval: Parse Failed から来たときは、
// 失敗したノードの成功の出力が空なので、無ければ {} にする（2026-11-07、実行 6）。
function out0(name) {
  if (!$(name).isExecuted) return null;
  var it = $(name).first(0, -1);
  return it ? it.json : null;
}
var round = $('Eval: Repeat').first(1, -1).json.evalRound;
var baseline = $('Eval: Repeat').first(1, -1).json.evalBaseline || '';
var resultId = 'R' + $execution.id + '-' + round;
var input = out0('Eval: Apply Model') || {};
var model = String(input[spec.model] || '');
var outs = items.map(function(it) { return it.json || {}; });
var failed = outs.length === 1 && outs[0].evalError != null;
var out = outs[0] || {};
var error = failed ? String(out.evalError) : '';
if (!failed && run.step === 'P6A' && out.p6Status) error = String(out.p6Status);
if (!failed && run.step === 'WP' && !(out.wpAuthResult && out.wpAuthResult.ok === true))
  error = 'WP認証確認結果がありません';
if (!failed && run.step === 'WPD' && !(out.wpDraftResult && out.wpDraftResult.ok === true))
  error = 'WP下書き試験の投稿IDがありません';
if (!failed && run.step === 'P6') {
  var bad = (out.p6Results || []).filter(function(r) { return !r.imageB64; });
  if (!(out.p6Results || []).length) error = '画像の結果がありません';
  else if (bad.length) error = bad.map(function(r) { return String(r.imageError || '画像がありません'); }).join(' / ');
}
var status = error ? 'エラー' : '成功';
var calls = run.step === 'P4' ? (out0('Build P4 Request') ? (out0('Build P4 Request').p4Attempt || 1) : 0)
  : run.step === 'P2' ? (out0('Build P2 Request') ? (out0('Build P2 Request').p2Attempt || 1) : 0)
  : run.step === 'P1' ? (out0('Build P1 Request') ? (out0('Build P1 Request').p1Attempt || 1) : 0) : 1;
var body = out0('Eval: Response') || {};
var metrics = {};
// 測定の失敗で、出力を失わない（出力は保存し、測定値に失敗の理由を書く）。
if (!failed) { try { metrics = measure(run.step, out, outs, input, body); } catch (e) { metrics = { measureError: String(e && e.message || e) }; } }

// 出力：次のステップが使う項目を、すべて全文で残す（内部の項目とリクエストの本文は除く）。
var data = {};
if (!failed) {
  Object.keys(out).forEach(function(k) {
    if (k.indexOf('eval') === 0 || k === 'requestBody') return;
    data[k] = out[k];
  });
  if (run.step === 'P6') {
    // 画像そのもの（base64）は評価データに残さず、長さだけを残す。
    data.p6Results = (out.p6Results || []).map(function(r) {
      var o = Object.assign({}, r);
      if (o.imageB64) { o.imageB64Chars = o.imageB64.length; delete o.imageB64; }
      return o;
    });
  }
  if (run.step === 'P6A') {
    ['insertionContext', 'imageDescription', 'altText', 'scene', 'sectionIndex'].forEach(function(k) { delete data[k]; });
    data.sections = outs.filter(function(o) { return o.sectionIndex != null; }).map(function(o) {
      return { sectionIndex: o.sectionIndex, insertionContext: o.insertionContext, imageDescription: o.imageDescription,
               altText: o.altText, scene: o.scene == null ? null : o.scene };
    });
  }
}
data._meta = { status: status, step: run.step, model: model, promptHash: spec.promptHash, codeHash: spec.codeHash,
               inputResultId: run.inputId, executionId: String($execution.id), round: round, error: error };
var dataRows = [];
Object.keys(data).forEach(function(k) {
  if (data[k] === undefined) return;
  var isText = typeof data[k] === 'string';
  var text = isText ? data[k] : JSON.stringify(data[k]);
  var parts = chunks(text);
  parts.forEach(function(p, i) {
    dataRows.push({ '結果ID': resultId, '項目': k, '型': isText ? 'string' : 'json',
                    '分割番号': i + 1, '分割数': parts.length, '文字数': text.length, '本文': p });
  });
});
var metricsText = JSON.stringify(metrics);
if (metricsText.length > CHUNK) {
  // セルに入らない。全文を評価データの _metrics に残し、評価結果には置き場所だけを書く。
  chunks(metricsText).forEach(function(p, i, all) {
    dataRows.push({ '結果ID': resultId, '項目': '_metrics', '型': 'json', '分割番号': i + 1, '分割数': all.length,
                    '文字数': metricsText.length, '本文': p });
  });
  metricsText = JSON.stringify({ storedIn: '評価データ _metrics', chars: metricsText.length });
}
var outputText = data[spec.output] == null ? '' : (typeof data[spec.output] === 'string' ? data[spec.output] : JSON.stringify(data[spec.output]));
var resultRow = {
  '結果ID': resultId, '実行キー': run.key, '実行ID': String($execution.id), '日時': nowIso(), 'ステップ': run.step,
  '回': round, 'モデル': model, 'プロンプトのハッシュ': spec.promptHash, '処理のハッシュ': spec.codeHash,
  '入力の結果ID': run.inputId, '基準の結果ID': run.baselineId, '状態': status, 'エラー': error,
  'API呼び出し回数': calls, '出力の文字数': outputText.length, '測定値': metricsText, '基準の測定値': baseline
};
return [{ json: { resultRow: resultRow, dataRows: dataRows } }];
''', STEPS=gw.js_str(table), CHUNK=str(CHUNK))


DATA_ROWS_JS = "return items[0].json.dataRows.map(function(r) { return { json: r }; });"
RESULT_ROW_JS = "return [{ json: $('Eval: Record').first().json.resultRow }];"
NEXT_JS = "return [{ json: $('Eval: Result Row').first().json }];"


def _finish_js():
    return r'''
var results = items.map(function(it) { return it.json || {}; });
var ids = results.map(function(r) { return r['結果ID']; });
var bad = results.filter(function(r) { return r['状態'] !== '成功'; });
// 結果ごとの測定値を、Actions のログにも出す（Eval: Run Result）。シートを開かずに評価を比べるため。
var detail = results.map(function(r) {
  var m = r['測定値'];
  try { m = m ? JSON.parse(m) : null; } catch (e) { /* 文字のまま */ }
  return { resultId: r['結果ID'], round: r['回'], model: r['モデル'], status: r['状態'], error: r['エラー'] || '',
           calls: r['API呼び出し回数'], outputChars: r['出力の文字数'], measures: m };
});
return [{ json: {
  rowNumber: $('Eval: Pick Run').first().json.rowNumber,
  status: bad.length ? 'エラーあり' : '完了',
  results: detail,
  resultIds: ids.join(' '),
  memo: '成功 ' + (results.length - bad.length) + ' 件・エラー ' + bad.length + ' 件'
} }];
'''


def _event_log_js(kind):
    """開始・終了・停止の 1 行。リクエストの列に「評価の実行」の内容を、応答の列に結果を書く。"""
    if kind == '開始':
        detail = "''"
    elif kind == '終了':
        # 結果ごとの測定値（results）は評価結果のシートにあるので、ログの 1 セルには入れない
        detail = "JSON.stringify(Object.assign({}, $('Eval: Finish').first().json, { results: undefined }))"
    else:
        detail = 'reason'
    return CHUNK_JS.replace('__CHUNK__', str(CHUNK)) + r'''
var pick = $('Eval: Pick Run').first().json;
var reason = pick.ok ? '' : pick.reason;
if (pick.ok && $('Eval: Prepare Input').isExecuted) {
  var prep = $('Eval: Prepare Input').first().json;
  if (prep.evalOk === false) reason = prep.evalReason;
}
var run = pick.run || {};
return [{ json: {
  '日時': nowIso(), '実行ID': String($execution.id), '実行キー': run.key || '', '結果ID': '', '回': '',
  '種類': __KIND__, 'ステップ': run.step || '', 'モデル': run.model || '', 'プロンプトのハッシュ': '', '呼び出し': '',
  '分割番号': 1, '分割数': 1, 'HTTPの状態': '', '所要時間ms': '', '停止理由': '', 'トークン数': '',
  'エラー': reason, 'リクエスト': JSON.stringify(run), '応答': __DETAIL__
} }];
'''.replace('__KIND__', gw.js_str(kind)).replace('__DETAIL__', detail)


# 実行結果（Actions の run-shaman-n8n.yml が読む。DOCS/RUN_FROM_ACTIONS.md）。「エラーあり」は ng にする
# （HTTP 200 だけで成功にしない）。行が無い・入力が無いときは Eval: Stop が止める（n8n が 500 を返す）。
RUN_RESULT_JS = r'''
var f = $('Eval: Finish').first().json;
return [{ json: { shamanRun: {
  ok: f.status === '完了', workflow: 'test', executionId: String($execution.id),
  reason: f.status === '完了' ? '' : '評価の実行が「' + f.status + '」で終わりました',
  rowNumber: f.rowNumber, status: f.status, resultIds: f.resultIds, memo: f.memo, results: f.results || []
} } }];
'''


STOP_JS = r'''
var pick = $('Eval: Pick Run').first().json;
var reason = pick.ok ? '' : pick.reason;
if (pick.ok && $('Eval: Prepare Input').isExecuted) reason = $('Eval: Prepare Input').first().json.evalReason;
// 例外にせず、止めた理由を返す。例外にすると Webhook（lastNode）の応答が空になり、Actions に理由が出ない。
return [{ json: { result: 'SHAMAN_PROMPT_EVAL_STOPPED: ' + reason, shamanRun: {
  ok: false, workflow: 'test', executionId: String($execution.id), reason: 'SHAMAN_PROMPT_EVAL_STOPPED: ' + reason
} } }];
'''


# ── ノード ──────────────────────────────────────────────────────────
def _sheet(name, operation, pos, sheet, columns=None, lookup=None):
    node = gw.sheetsNode(name, operation, pos, DOCUMENT, columns, sheet=sheet, header_row=HEADER_ROW, lookup=lookup)
    if operation == 'append':
        node.pop('alwaysOutputData', None)
    return node


def _ensure_sheets_node(pos):
    """評価のシートが無ければ作る。n8n の create は、同じ名前のシートがあれば作らない（その item は出力しない）。"""
    node = gw.sheetsNode('Eval: Ensure Sheets', 'append', pos, DOCUMENT, (), sheet=SHEET_RUNS, header_row=HEADER_ROW)
    p = node['parameters']
    for key in ('sheetName', 'columns', 'options'):
        p.pop(key, None)
    p.update({'operation': 'create', 'title': '={{ $json.title }}', 'options': {}})
    # 4 つともあれば 0 件になる。次へ進めるため。
    node['alwaysOutputData'] = True
    return node


def _header_write_node(name, pos, sheet):
    """見出しを書く。autoMapInputData は、シートが空なら item の項目名を 1 行目に書く（n8n 2.35.7）。
    その実行で作ったシート（空）にだけ使う。前からあるシートには書かない（何も変えない）。"""
    node = gw.sheetsNode(name, 'append', pos, DOCUMENT, (), sheet=sheet, header_row=HEADER_ROW)
    node['parameters']['columns'] = {'mappingMode': 'autoMapInputData', 'value': {}, 'matchingColumns': [],
                                     'schema': []}
    node.pop('alwaysOutputData', None)
    return node


SHEETS_READY_JS = r'''
// この実行で作ったシートの名前。Eval: Ensure Sheets は、作ったシートだけを出す（4 つともあれば {} が 1 件）。
var created = items.map(function(it) { return it.json && it.json.title; }).filter(function(t) { return !!t; });
return [{ json: { created: created } }];
'''


SHEET_SETUP_JS = r'''
// 評価のシート（DOCS/PROMPT_EVAL_DESIGN.md §4.2）。無ければ次の Eval: Ensure Sheets が作る。
var TITLES = __TITLES__;
// Actions から実行したときは、Webhook の本文に評価の行（evalRun）が入っている。画面から実行したときは無い。
var body = (items[0] && items[0].json && items[0].json.body) || {};
var newRun = body.evalRun && typeof body.evalRun === 'object' ? body.evalRun : null;
return TITLES.map(function(t) { return { json: { title: t, newRun: newRun } }; });
'''

NEW_RUN_ROW_JS = r'''
// Actions の入力から「評価の実行」の行を作る。値の確かめは Eval: Pick Run がする（手で書いた行と同じ）。
var r = $('Eval: Sheet Setup').first().json.newRun;
var s = function(k) { return String(r[k] == null ? '' : r[k]).trim(); };
return [{ json: {
  // Actions の実行 ID と n8n の実行 ID で、行を一意にする（Eval: Pick Run がこの行だけを選ぶ）。
  // 本番の実行から入力を取り込んだときは、Eval: Import Rows が評価データに書いた結果 ID を入力にする。
  '実行キー': 'actions-' + (s('runId') || 'manual') + '-' + $execution.id, 'ステップ': s('step'),
  '入力の結果ID': r.importData ? 'R' + $execution.id + '-0' : s('inputId'),
  '基準の結果ID': s('baselineId'), 'KEYWORD': s('keyword'), 'OPTION1': s('option1'), 'SITE_ID': s('siteId'),
  'モデル': s('model'), '回数': s('rounds'), '状態': '待機中', '実行ID': '', '結果ID': '', 'メモ': ''
} }];
'''


IMPORT_ROWS_JS = _js(CHUNK_JS + r'''
// 本番の実行から取り込んだ入力（Actions の eval_input_execution。DOCS/PROMPT_EVAL_DESIGN.md §4.3）を、
// 評価データに結果 ID「R<この実行の ID>-0」で書く（回は 1 から始まるので、評価の結果と重ならない）。
// 評価の結果と同じ形にするので、Eval: Prepare Input が同じように復元し、同じ ID を何度でも入力に使える。
var imp = $('Eval: Sheet Setup').first().json.newRun.importData;
if (!imp || typeof imp.data !== 'object' || imp.data === null || Array.isArray(imp.data)) {
  throw new Error('SHAMAN_RUN_NG: 取り込んだ入力の形が違います');
}
var resultId = 'R' + $execution.id + '-0';
// 認証の値は Credential にあり、データには入らない。念のため、それらしい名前の項目は残さない。
var SECRET = /token|secret|password|passwd|api[_-]?key|webhook|authori[sz]ation|cookie/i;
var data = {};
Object.keys(imp.data).forEach(function(k) {
  if (SECRET.test(k) || k.indexOf('eval') === 0 || k === 'requestBody') return;
  data[k] = imp.data[k];
});
data._meta = { status: '成功', step: '取り込み', source: 'n8n 実行 ' + String(imp.executionId) + ' の ' + String(imp.node),
               executionId: String($execution.id) };
var rows = [];
Object.keys(data).forEach(function(k) {
  if (data[k] === undefined) return;
  var isText = typeof data[k] === 'string';
  var text = isText ? data[k] : JSON.stringify(data[k]);
  var parts = chunks(text);
  parts.forEach(function(p, i) {
    rows.push({ json: { '結果ID': resultId, '項目': k, '型': isText ? 'string' : 'json',
                        '分割番号': i + 1, '分割数': parts.length, '文字数': text.length, '本文': p } });
  });
});
return rows;
''', CHUNK=str(CHUNK))


def _header_js(columns):
    return _js('return [{ json: __ROW__ }];', ROW=gw.js_str({c: '' for c in columns}))


SETUP_SHEETS = (('Runs', SHEET_RUNS, RUN_COLUMNS), ('Results', SHEET_RESULTS, RESULT_COLUMNS),
                ('Data', SHEET_DATA, DATA_COLUMNS), ('Log', SHEET_LOG, LOG_COLUMNS))


def _setup_nodes():
    """起動したら、評価のシートと見出しを用意し、Actions の入力があれば評価の行を足す。"""
    nodes = [
        gw.code('Eval: Sheet Setup', _js(SHEET_SETUP_JS, TITLES=gw.js_str([s for _, s, _ in SETUP_SHEETS])),
                [220, -60]),
        _ensure_sheets_node([440, -60]),
        gw.code('Eval: Sheets Ready', SHEETS_READY_JS, [660, -60]),
    ]
    x = 880
    for i, (key, sheet, columns) in enumerate(SETUP_SHEETS):
        nodes.append(_if_true('Eval: New ' + key + '?', [x, -60],
                              "={{ $('Eval: Sheets Ready').first().json.created.includes(" + gw.js_str(sheet) + ") }}",
                              'eval-new-' + key.lower()))
        nodes.append(_once(gw.code('Eval: Header ' + key, _header_js(columns), [x + 220, -200])))
        nodes.append(_header_write_node('Eval: Header ' + key + ' Write', [x + 440, -200], sheet))
        x += 660
    nodes += [
        _if_true('Eval: New Run?', [x, -60], "={{ $('Eval: Sheet Setup').first().json.newRun !== null }}",
                 'eval-new-run'),
        _if_true('Eval: Import?', [x + 220, -160],
                 "={{ !!$('Eval: Sheet Setup').first().json.newRun.importData }}", 'eval-import'),
        gw.code('Eval: Import Rows', IMPORT_ROWS_JS, [x + 440, -300]),
        _append('Eval: Save Import', [x + 660, -300], SHEET_DATA, DATA_COLUMNS),
        gw.code('Eval: New Run Row', NEW_RUN_ROW_JS, [x + 880, -160]),
        _append('Eval: Add Run', [x + 1100, -160], SHEET_RUNS, RUN_COLUMNS),
    ]
    return nodes


def _setup_connections():
    cn = gw.cn
    keys = [k for k, _, _ in SETUP_SHEETS]
    conn = {'Eval: Sheet Setup': {'main': [[cn('Eval: Ensure Sheets')]]},
            'Eval: Ensure Sheets': {'main': [[cn('Eval: Sheets Ready')]]},
            'Eval: Sheets Ready': {'main': [[cn('Eval: New ' + keys[0] + '?')]]}}
    for i, key in enumerate(keys):
        after = 'Eval: New ' + keys[i + 1] + '?' if i + 1 < len(keys) else 'Eval: New Run?'
        # 作ったシートなら見出しを書く。前からあるシートには何もしない。
        conn['Eval: New ' + key + '?'] = {'main': [[cn('Eval: Header ' + key)], [cn(after)]]}
        conn['Eval: Header ' + key] = {'main': [[cn('Eval: Header ' + key + ' Write')]]}
        conn['Eval: Header ' + key + ' Write'] = {'main': [[cn(after)]]}
    conn['Eval: New Run?'] = {'main': [[cn('Eval: Import?')], [cn('Eval: Read Runs')]]}
    conn['Eval: Import?'] = {'main': [[cn('Eval: Import Rows')], [cn('Eval: New Run Row')]]}
    conn['Eval: Import Rows'] = {'main': [[cn('Eval: Save Import')]]}
    conn['Eval: Save Import'] = {'main': [[cn('Eval: New Run Row')]]}
    conn['Eval: New Run Row'] = {'main': [[cn('Eval: Add Run')]]}
    conn['Eval: Add Run'] = {'main': [[cn('Eval: Read Runs')]]}
    return conn


def _append(name, pos, sheet, columns):
    return _sheet(name, 'append', pos, sheet, {c: '={{ $json[' + gw.js_str(c) + '] }}' for c in columns})


def _if_true(name, pos, expr, cond_id):
    return {'parameters': {'conditions': {
        'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
        'conditions': [{'id': cond_id, 'leftValue': expr, 'rightValue': '',
                        'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
        'combinator': 'and'}, 'options': {}},
        'id': gw.uid(), 'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': pos}


def _step_switch(name, pos):
    rules = [{'conditions': {
        'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
        'conditions': [{'id': name + '-' + step, 'leftValue': "={{ $('Eval: Pick Run').first().json.run.step }}",
                        'rightValue': step, 'operator': {'type': 'string', 'operation': 'equals'}}],
        'combinator': 'and'}, 'renameOutput': True, 'outputKey': step} for step in STEP_ORDER]
    # どの工程にも当たらない item は、最後の出力（Eval: Unknown Step）で止める（黙って捨てない）。
    return {'parameters': {'rules': {'values': rules},
                           'options': {'fallbackOutput': 'extra', 'renameFallbackOutput': 'unknown step'}},
            'id': gw.uid(), 'name': name, 'type': 'n8n-nodes-base.switch', 'typeVersion': 3.2, 'position': pos}


def _once(node):
    node['executeOnce'] = True
    return node


# 回の途中で失敗しうるノード。失敗は Eval: Parse Failed（その回のエラーの結果）へ流し、次の回へ進む。
ROUND_NODES = ('Map Variables', 'Eval: Apply Model', 'Eval: Stamp', 'Eval: Call Log Rows', 'Eval: Response')


def _retry(node):
    # Google Sheets の一時的な失敗（429・5xx）は、待ってやり直す。続けて失敗したら、実行を失敗にする（§10.3）。
    node.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000})
    return node


def _step_nodes():
    """本番の組み立て関数で、ステップのノードを作る。HTTP は状態コードとエラーもログに残せるようにする。"""
    out = {}
    for row, (step, spec) in enumerate(_steps().items()):
        nodes = spec['nodes']()
        y = 160 + row * 200
        for n in nodes:
            if n['name'] == spec['build']:
                n['onError'] = 'continueErrorOutput'
                n['position'] = [3520, y]
            elif n['name'] == spec['http']:
                gw._full_response(n)
                n['position'] = [4180, y]
            elif n['name'] == spec['parse']:
                n['onError'] = 'continueErrorOutput'
                n['position'] = [5280, y]
            else:  # P1: Retry? / P2: Retry? / P4: Retry?
                n['position'] = [5500, y]
        out[step] = nodes
    return out


def buildPromptEvalWorkflow():
    step_nodes = _step_nodes()
    table = _step_table(step_nodes)
    steps = _steps()
    code = gw.code
    nodes = [
        gw.sticky('Eval README', '## SHAMAN: Prompt Eval\nLLM のモデルやプロンプトを変えたときの出力を、ステップごとに評価する。\n'
                  '試験用スプレッドシートの「評価の実行」の待機中の 1 行を実行する。Actions の「SHAMAN | n8n Workflow実行」\n'
                  '（Run Webhook）か、この画面の Execute workflow で動かす。評価のシートと見出しが無ければ、最初に作る。\n'
                  'Actions から動かすと、入力の値で「評価の実行」に待機中の行を足してから実行する。\n'
                  '設計：SHAMAN/DOCS/PROMPT_EVAL_DESIGN.md', [-20, -260], 7, 640, 220),
        {'parameters': {}, 'id': gw.uid(), 'name': 'Manual Trigger', 'type': 'n8n-nodes-base.manualTrigger',
         'typeVersion': 1, 'position': [0, 300]},
        gw.runWebhookNode(RUN_WEBHOOK_PATH, [0, 500]),
        *_setup_nodes(),
        _sheet('Eval: Read Runs', 'read', [220, 300], SHEET_RUNS),
        code('Eval: Pick Run', _pick_run_js(table), [440, 300]),
        _if_true('Eval: Run OK?', [660, 300], '={{ $json.ok }}', 'eval-run-ok'),
        _once(_sheet('Eval: Claim Run', 'update', [880, 300], SHEET_RUNS, {
            'row_number': '={{ $json.rowNumber }}', '状態': '処理中', '実行ID': '={{ $execution.id }}', 'メモ': ''})),
        gw.sheetsUpdateCheckNode('Eval: Claim Run Check', 'Eval: Claim Run', [990, 460]),
        code('Eval: Log Start Rows', _event_log_js('開始'), [1100, 300]),
        _append('Eval: Log Start', [1320, 300], SHEET_LOG, LOG_COLUMNS),
        _once(_sheet('Eval: Read Input', 'read', [1540, 300], SHEET_DATA, lookup=(
            '結果ID', "={{ $('Eval: Pick Run').first().json.run.inputId || '__none__' }}"))),
        _once(_sheet('Eval: Read Baseline', 'read', [1760, 300], SHEET_RESULTS, lookup=(
            '結果ID', "={{ $('Eval: Pick Run').first().json.run.baselineId || '__none__' }}"))),
        code('Eval: Prepare Input', _prepare_input_js(table), [1980, 300]),
        _if_true('Eval: Input OK?', [2200, 300], '={{ $json.evalOk }}', 'eval-input-ok'),
        {'parameters': {'options': {}}, 'id': gw.uid(), 'name': 'Eval: Repeat', 'type': 'n8n-nodes-base.splitInBatches',
         'typeVersion': 3, 'position': [2420, 300]},
        _if_true('Eval: P1?', [2640, 160], "={{ $('Eval: Pick Run').first().json.run.step === 'P1' }}", 'eval-p1'),
        _map_variables([2860, 60]),
        code('Eval: Apply Model', _apply_model_js(table), [3080, 160]),
        _step_switch('Eval: Route Step', [3300, 160]),
        code('Eval: Stamp', STAMP_JS, [3740, 160]),
        _step_switch('Eval: Route Call', [3960, 160]),
        code('Eval: Call Log Rows', _call_log_js(table), [4400, 160]),
        _append('Eval: Log Call', [4620, 160], SHEET_LOG, LOG_COLUMNS),
        _once(code('Eval: Response', _response_js(table), [4840, 160])),
        _step_switch('Eval: Route Parse', [5060, 160]),
        code('Eval: Unknown Step', "throw new Error('SHAMAN_RUN_NG: 工程「' + $('Eval: Pick Run').first().json.run.step + '」の分岐がありません');",
             [5060, 1400]),
        code('Eval: Parse Failed', PARSE_FAILED_JS, [5500, 1400]),
        code('Eval: Record', _record_js(table), [5720, 300]),
        code('Eval: Data Rows', DATA_ROWS_JS, [5940, 300]),
        _append('Eval: Save Data', [6160, 300], SHEET_DATA, DATA_COLUMNS),
        _once(code('Eval: Result Row', RESULT_ROW_JS, [6380, 300])),
        _append('Eval: Save Result', [6600, 300], SHEET_RESULTS, RESULT_COLUMNS),
        code('Eval: Next', NEXT_JS, [6820, 300]),
        code('Eval: Finish', _finish_js(), [2640, 600]),
        _sheet('Eval: Mark Done', 'update', [2860, 600], SHEET_RUNS, {
            'row_number': '={{ $json.rowNumber }}', '状態': '={{ $json.status }}', '結果ID': '={{ $json.resultIds }}',
            'メモ': '={{ $json.memo }}'}),
        gw.sheetsUpdateCheckNode('Eval: Mark Done Check', 'Eval: Mark Done', [2970, 500]),
        code('Eval: Log End Rows', _event_log_js('終了'), [3080, 600]),
        _append('Eval: Log End', [3300, 600], SHEET_LOG, LOG_COLUMNS),
        code('Eval: Run Result', RUN_RESULT_JS, [3520, 600]),
        gw.runCheckNode([3740, 700]),
        _once(_sheet('Eval: Mark Failed', 'update', [2420, 800], SHEET_RUNS, {
            'row_number': "={{ $('Eval: Pick Run').first().json.rowNumber }}", '状態': 'エラー',
            'メモ': '={{ $json.evalReason }}'})),
        gw.sheetsUpdateCheckNode('Eval: Mark Failed Check', 'Eval: Mark Failed', [2530, 960]),
        code('Eval: Stop Rows', _event_log_js('停止'), [2640, 800]),
        _append('Eval: Log Stop', [2860, 800], SHEET_LOG, LOG_COLUMNS),
        code('Eval: Stop', STOP_JS, [3080, 800]),
        _if_true('Eval: Stop Has Row?', [880, 800], '={{ $json.rowNumber !== null }}', 'eval-stop-has-row'),
    ]
    for step in STEP_ORDER:
        nodes.extend(step_nodes[step])
    for n in nodes:
        if n['name'] in ROUND_NODES:
            n['onError'] = 'continueErrorOutput'
        if n['type'] == 'n8n-nodes-base.googleSheets':
            _retry(n)

    cn = gw.cn
    conn = {
        'Manual Trigger': {'main': [[cn('Eval: Sheet Setup')]]},
        'Run Webhook': {'main': [[cn('Eval: Sheet Setup')]]},
        **_setup_connections(),
        'Eval: Read Runs': {'main': [[cn('Eval: Pick Run')]]},
        'Eval: Pick Run': {'main': [[cn('Eval: Run OK?')]]},
        'Eval: Run OK?': {'main': [[cn('Eval: Claim Run')], [cn('Eval: Stop Has Row?')]]},
        # 足した行で止めたときは、その行を「エラー」にしてから止める（待機中のまま残さない）。
        'Eval: Stop Has Row?': {'main': [[cn('Eval: Mark Failed')], [cn('Eval: Stop Rows')]]},
        'Eval: Claim Run': {'main': [[cn('Eval: Claim Run Check')]]},
        'Eval: Claim Run Check': {'main': [[cn('Eval: Log Start Rows')]]},
        'Eval: Log Start Rows': {'main': [[cn('Eval: Log Start')]]},
        'Eval: Log Start': {'main': [[cn('Eval: Read Input')]]},
        'Eval: Read Input': {'main': [[cn('Eval: Read Baseline')]]},
        'Eval: Read Baseline': {'main': [[cn('Eval: Prepare Input')]]},
        'Eval: Prepare Input': {'main': [[cn('Eval: Input OK?')]]},
        'Eval: Input OK?': {'main': [[cn('Eval: Repeat')], [cn('Eval: Mark Failed')]]},
        'Eval: Repeat': {'main': [[cn('Eval: Finish')], [cn('Eval: P1?')]]},
        'Eval: P1?': {'main': [[cn('Map Variables')], [cn('Eval: Apply Model')]]},
        'Map Variables': {'main': [[cn('Eval: Apply Model')]]},
        'Eval: Apply Model': {'main': [[cn('Eval: Route Step')]]},
        'Eval: Route Step': {'main': [[cn(steps[s].get('entry', steps[s]['build']))] for s in STEP_ORDER]
                             + [[cn('Eval: Unknown Step')]]},
        'Eval: Stamp': {'main': [[cn('Eval: Route Call')]]},
        'Eval: Route Call': {'main': [[cn(steps[s]['http'])] for s in STEP_ORDER] + [[cn('Eval: Unknown Step')]]},
        'Eval: Call Log Rows': {'main': [[cn('Eval: Log Call')]]},
        'Eval: Log Call': {'main': [[cn('Eval: Response')]]},
        'Eval: Response': {'main': [[cn('Eval: Route Parse')]]},
        'Eval: Route Parse': {'main': [[cn(steps[s]['parse'])] for s in STEP_ORDER] + [[cn('Eval: Unknown Step')]]},
        'Eval: Parse Failed': {'main': [[cn('Eval: Record')]]},
        'P1: Retry?': {'main': [[cn('Build P1 Request')], [cn('Eval: Record')]]},
        'P2: Retry?': {'main': [[cn('Build P2 Request')], [cn('Eval: Record')]]},
        'P4: Retry?': {'main': [[cn('Build P4 Request')], [cn('Eval: Record')]]},
        'Eval: Record': {'main': [[cn('Eval: Data Rows')]]},
        'Eval: Data Rows': {'main': [[cn('Eval: Save Data')]]},
        'Eval: Save Data': {'main': [[cn('Eval: Result Row')]]},
        'Eval: Result Row': {'main': [[cn('Eval: Save Result')]]},
        'Eval: Save Result': {'main': [[cn('Eval: Next')]]},
        'Eval: Next': {'main': [[cn('Eval: Repeat')]]},
        'Eval: Finish': {'main': [[cn('Eval: Mark Done')]]},
        'Eval: Mark Done': {'main': [[cn('Eval: Mark Done Check')]]},
        'Eval: Mark Done Check': {'main': [[cn('Eval: Log End Rows')]]},
        'Eval: Log End Rows': {'main': [[cn('Eval: Log End')]]},
        'Eval: Log End': {'main': [[cn('Eval: Run Result')]]},
        'Eval: Run Result': {'main': [[cn('Run: Check')]]},
        'Eval: Stop': {'main': [[cn('Run: Check')]]},
        'Eval: Mark Failed': {'main': [[cn('Eval: Mark Failed Check')]]},
        'Eval: Mark Failed Check': {'main': [[cn('Eval: Stop Rows')]]},
        'Eval: Stop Rows': {'main': [[cn('Eval: Log Stop')]]},
        'Eval: Log Stop': {'main': [[cn('Eval: Stop')]]},
    }
    for name in ROUND_NODES:
        conn[name]['main'].append([cn('Eval: Parse Failed')])
    # P6：参考画像を読む（本番と同じノード）→ 最初の節 → Build Image Requests。失敗はその回のエラーにする。
    # 同じ参照画像の分岐を本番と評価で使う。IF は true/false の2系統、
    # エラー出力を使う Code/Drive は success/error の2系統にする。
    chain = P6_REFERENCE_CHAIN + ('Build Image Requests',)
    for a, b in zip(chain, chain[1:]):
        if a in ('P6: Has Image Folder?', 'P6: Has Reference Images?'):
            skipped_to = 'Read Reference Images'
            conn[a] = {'main': [[cn(b)], [cn(skipped_to)]]}
        else:
            conn[a] = {'main': [[cn(b)], [cn('Eval: Parse Failed')]]}
    for step in STEP_ORDER:
        spec = steps[step]
        conn[spec['build']] = {'main': [[cn('Eval: Stamp')], [cn('Eval: Parse Failed')]]}
        conn[spec['http']] = {'main': [[cn('Eval: Call Log Rows')]]}
        after = {'P1': 'P1: Retry?', 'P2': 'P2: Retry?', 'P4': 'P4: Retry?'}.get(step, 'Eval: Record')
        conn[spec['parse']] = {'main': [[cn(after)], [cn('Eval: Parse Failed')]]}

    # ノードの ID は、ほかの Workflow の生成の順に左右されないよう、この Workflow の中で振り直す。
    for i, node in enumerate(nodes, 1):
        node['id'] = 'e' + format(i, '07x') + '-0000-4000-a000-' + format(i, '012x')
    w = gw.wf(NAME, nodes, conn, WORKFLOW_ID, [{'id': 'TESTwfTag0000001', 'name': 'prompt-eval'}], False)
    w['settings'] = dict(w['settings'])
    w['settings']['saveDataSuccessExecution'] = 'all'
    w['settings']['saveDataErrorExecution'] = 'all'
    return w


def _map_variables(pos):
    """本番の Map Variables（キューの行から、各ステップが使う項目を作る）。P1 の入力に使う。"""
    prod = gw.buildWorkflow()
    node = next(n for n in prod['nodes'] if n['name'] == 'Map Variables')
    node = json.loads(json.dumps(node, ensure_ascii=False))
    node['id'] = gw.uid()
    node['position'] = pos
    return node
