import copy
import json, os, re, uuid

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'workflows')
SITES_PATH = os.path.join(BASE, 'config', 'sites.json')
# Google Drive の SHAMAN/IMG フォルダーの ID（Secret ではない。URL の /folders/ の後ろ）。
DRIVE_PATH = os.path.join(BASE, 'config', 'drive.json')

# === Sites (replaces the queue's 「サイト共通設定」 sheet) ===
# sites.json は既存サイトの表示設定だけを持つ任意ファイル。SITE_ID から URL を生成する。
# 認証情報は WordPress 共通 Basic Auth Credential のみに保持する。
# SITE_ID はドメイン形式（example.com）も許す。ドットは区切りとしてだけ使える（先頭・末尾・連続は不可）。
SITE_ID_RE = re.compile(r'^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$')
SITE_KEYS = ('SITENAME', 'BASEURL', 'AUTHOR', 'FIRSTPERSON', 'IMG_FOLDER', 'WP_URL', 'META_KEY')
# IMG_FOLDER は Google Drive の検索の式に入る。引用符・バックスラッシュ・改行は入れさせない。
IMG_FOLDER_RE = re.compile(r'^[^\'"\\\r\n]+$')
SITE_REQUIRED = ()

def load_sites(path=SITES_PATH):
    """sites.json を読み、形を検査して {siteId: 設定} を返す。Secret や知らない項目があれば止める。"""
    with open(path, encoding='utf-8') as f:
        sites = json.load(f)
    if not isinstance(sites, dict):
        raise ValueError('sites.json must be an object of {siteId: settings}')
    for site_id, site in sites.items():
        if not SITE_ID_RE.match(site_id):
            raise ValueError(f'invalid SITE_ID in sites.json: {site_id!r}')
        if not isinstance(site, dict):
            raise ValueError(f'sites.json[{site_id}] must be an object')
        unknown = sorted(set(site) - set(SITE_KEYS))
        if unknown:
            raise ValueError(f'unknown keys in sites.json[{site_id}] (secrets belong in n8n Credentials): {unknown}')
        for key in SITE_REQUIRED:
            if not site.get(key):
                raise ValueError(f'sites.json[{site_id}] needs {key}')
        for key, value in site.items():
            if not isinstance(value, str):
                raise ValueError(f'sites.json[{site_id}].{key} must be a string')
        if 'IMG_FOLDER' in site and not IMG_FOLDER_RE.match(site['IMG_FOLDER']):
            raise ValueError(f'sites.json[{site_id}].IMG_FOLDER must not contain quotes, backslashes or newlines')
        if 'WP_URL' in site and site['WP_URL'] != 'https://' + site_id:
            raise ValueError(f'sites.json[{site_id}].WP_URL must match SITE_ID (https://{site_id})')
    return sites

# === AI model registry (single source of truth) ===
# Centralized so a model rename is one edit here. The values are embedded in the generated
# JSON (Map Variables). n8n 2.x blocks $env in nodes, so the workflow never reads them at run time
# (N8N_LUNA_MIGRATION_PLAN.md §2.3). To change a model, edit here and regenerate.
MODEL_PRO = 'gemini-3.1-pro-preview'
MODEL_FLASH_LITE = 'gemini-3.1-flash-lite'  # P5（メタディスクリプション）
# P2（構成案）。2026-11-09 オーナーの指定。Google AI Studio のモデル一覧で Stable、料金は 100 万トークンあたり入力 $0.30・出力 $2.50
MODEL_OUTLINE = 'gemini-3.5-flash-lite'
# P6A（画像を入れる見出しの選定）。2026-11-09 オーナーの指定。モデル一覧で New、料金は入力 $0.75・出力 $3.75（2027-01-01 から $1.50・$7.50）
MODEL_SECTION_ANALYSIS = 'gemini-3.8-flash'
MODEL_IMAGE = 'gemini-3.1-flash-image-preview'
MODEL_IMAGE_OPENAI = 'gpt-image-2'
MODEL_CLAUDE = 'claude-opus-5-5'  # 2026-11-07 オーナーの指定（Sonnet は問題が多かった）
# P6 image settings (not secrets; embedded at generate time like the models).
IMAGE_PROVIDER = 'openai'  # 'openai' or 'gemini' (rollback: regenerate with 'gemini')
IMAGE_QUALITY = 'medium'   # OpenAI image quality: low / medium / high

# === Prompts ===
# Single source of truth: prompts.py. Each constant is the final text sent to
# the LLM -- no stripping, splitting or substitution happens here.
from prompts import (
    P1_PROMPT,
    P2_T1,
    P2_RETRY_PREFIX,
    P2_T2,
    P3_RAW,
    P4_RAW,
    P4_REPAIR_RAW,
    P5_RAW,
    P6_ANALYZE_PROMPT,
    P6_IMAGE_PROMPT,
    P6_IMAGE_PROMPT_OPENAI,
    P6_IMAGE_PROMPT_OPENAI_NO_REFERENCE,
    P6_IMAGE_PROMPT_NO_REFERENCE,
)

META = {'instanceId': 'c4e229d83a32942a2a030f2eab91ddd6d27c0043e6686616ed608588c9eeb759'}
SETTINGS = {'executionOrder': 'v1'}
_c = [0]

def uid():
    _c[0] += 1
    h = format(_c[0], '08x')
    return h + '-0000-4000-a000-' + h.rjust(12, '0')

# === Helpers ===
def sticky(name, content, pos, color=7, w=520, h=300):
    return {'parameters': {'content': content, 'height': h, 'width': w, 'color': color}, 'id': uid(), 'name': name, 'type': 'n8n-nodes-base.stickyNote', 'typeVersion': 1, 'position': pos}

def trigger(pos):
    return {'parameters': {}, 'id': uid(), 'name': 'Execute Workflow Trigger', 'type': 'n8n-nodes-base.executeWorkflowTrigger', 'typeVersion': 1, 'position': pos}

def setN(name, assignments, pos, inclOther=False):
    assigns = [{'id': 'a' + str(i), **a} for i, a in enumerate(assignments)]
    p = {'assignments': {'assignments': assigns}, 'options': {}}
    if inclOther:
        p['includeOtherFields'] = True
    return {'parameters': p, 'id': uid(), 'name': name, 'type': 'n8n-nodes-base.set', 'typeVersion': 3.4, 'position': pos}

# 入力の item を、n8n の暗黙の `items` に頼らず、$input.all() で明示的に受け取る（レビュー指摘。PR #66）。
# Code ノードは既定の runOnceForAllItems で動かす。コードが自分で items を宣言していれば足さない。
_ITEMS_USED = re.compile(r'(?<![\w$.])items\b')
_ITEMS_DECLARED = re.compile(r'\b(?:let|const|var)\s+items\b')
ITEMS_PRELUDE = 'const items = $input.all();\n'

# 出力が 0 件のノードがあると、n8n はそこで黙って止まり、実行は「Succeeded」になる。途中で止まったのに Succeeded に
# しない（2026-11-06 オーナーの指示）。Code ノードは、コードを包み、出力が 0 件なら例外にする（n8n の実行は Error）。
# Code ノードが 0 件を返して先へ進まないのは、この Workflow では、どこでも「途中で止まる」ことなので、一律に包む。
NO_SILENT_STOP_MARK = '// SHAMAN_NO_SILENT_STOP'

def _no_silent_stop(name, jsCode):
    message = json.dumps('SHAMAN_RUN_NG: 「' + name + '」の出力が 0 件です（ここで止まるので、実行を Error にする）',
                         ensure_ascii=False)
    return '\n'.join([
        NO_SILENT_STOP_MARK + '：出力が 0 件なら例外にする（n8n が黙って止まり、Succeeded になるのを防ぐ）',
        'const __shamanOut = await (async () => {',
        jsCode,
        '})();',
        'if (__shamanOut == null || (Array.isArray(__shamanOut) && __shamanOut.length === 0)) {',
        '  throw new Error(' + message + ');',
        '}',
        'return __shamanOut;',
    ])

def code_body(jsCode):
    """_no_silent_stop で包む前のコード（試験で使う）。"""
    lines = jsCode.split('\n')
    if not lines[0].startswith(NO_SILENT_STOP_MARK):
        raise ValueError('not wrapped by _no_silent_stop')
    return '\n'.join(lines[2:-5])

def code(name, jsCode, pos):
    if _ITEMS_USED.search(jsCode) and not _ITEMS_DECLARED.search(jsCode):
        jsCode = ITEMS_PRELUDE + jsCode
    return {'parameters': {'jsCode': _no_silent_stop(name, jsCode)}, 'id': uid(), 'name': name,
            'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos}

# === n8n Credentials for external APIs ===
# 本番と接続確認 Workflow が同じ Credential・同じノード定義を使うための組み立て関数。
# Credential の ID は、配備のときに Repository Variable で置き換える（N8N_LUNA_MIGRATION_PLAN.md §3）。
# 認証は n8n の Credential が HTTP リクエストに付ける。API キーをノードや $env に書かない。
def n8n_credential(name, variable):
    return {'id': '__ENV:' + variable + '__', 'name': name}

API_CREDENTIALS = {
    # googlePalmApi は API キーを ?key= で付ける。anthropicApi は x-api-key、openAiApi は Authorization を付ける。
    'gemini': ('googlePalmApi', n8n_credential('Google / Gemini', 'SHAMAN_GEMINI_CREDENTIAL_ID')),
    'anthropic': ('anthropicApi', n8n_credential('Anthropic / API', 'SHAMAN_ANTHROPIC_CREDENTIAL_ID')),
    'openai': ('openAiApi', n8n_credential('OpenAI / API', 'SHAMAN_OPENAI_CREDENTIAL_ID')),
}
DISCORD_CREDENTIAL = n8n_credential('SHAMAN / Discord / Notify', 'SHAMAN_DISCORD_CREDENTIAL_ID')
# Google Drive は、シートと同じサービスアカウントの Credential（`Google / Sheets`、googleApi）で読む（2026-11-10）。
# 前は OAuth2 の `SHAMAN / Google Drive` だったが、同意画面が「テスト」の OAuth2 はトークンが 7 日で切れ、
# 本番の実行 45 が P6: Find Image Folder で止まった。サービスアカウントには期限が無く、人がログインし直さなくてよい。
# サービスアカウントには Google Drive の SHAMAN/IMG を閲覧者として共有する（DOCS/CONNECTION_CHECK.md）。
DRIVE_CREDENTIAL = n8n_credential('Google / Sheets', 'SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID')
DRIVE_AUTH = 'serviceAccount'
DRIVE_CREDENTIAL_TYPES = {'serviceAccount': 'googleApi', 'oAuth2': 'googleDriveOAuth2Api'}
ANTHROPIC_VERSION = '2023-06-01'
# P4 の HTTP の上限。900 秒では本番の実行 8 が時間切れになった（2026-11-07）。
P4_TIMEOUT_MS = 1800000

# Actions から Workflow を実行する入口（DOCS/RUN_FROM_ACTIONS.md）。Header Auth の合言葉は n8n の Credential と
# GitHub Secret（SHAMAN_RUN_WEBHOOK_TOKEN）にだけ置く。Actions は luna の runner から 127.0.0.1 に送る。
RUN_WEBHOOK_CREDENTIAL = n8n_credential('SHAMAN / Run Webhook', 'SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID')
RUN_WEBHOOK_HEADER = 'X-SHAMAN-Run-Token'

# 実行の最後のノード。結果（shamanRun）が ok でなければ例外にし、n8n の実行を Error で終わらせる
# （途中で止まった・NG なのに「Succeeded」にしない。2026-11-06 オーナーの指示）。ok なら、そのまま返す。
# 例外のときの Webhook の応答は中身の無い 500 なので、Actions は n8n の API で、この例外の文を読んでログに出す。
RUN_CHECK_JS = '\n'.join([
    "var run = items[0] && items[0].json && items[0].json.shamanRun;",
    "if (!run) throw new Error('SHAMAN_RUN_NG: 実行結果（shamanRun）がありません');",
    "if (run.ok !== true) {",
    "  var detail = run.rows ? ' / 行: ' + JSON.stringify(run.rows) : '';",
    "  throw new Error(('SHAMAN_RUN_NG: ' + (run.reason || '理由不明') + detail).substring(0, 2000));",
    "}",
    "return items;",
])

def runCheckNode(pos, name='Run: Check'):
    return code(name, RUN_CHECK_JS, pos)

# Google Sheets の update は、row_number の行が無いと 0 件を返す（n8n 2.35.7 の update.operation.ts）。
# 0 件だと次へ進まず、実行は Succeeded になる。そのため update は alwaysOutputData にし（sheetsNode）、次にこの
# ノードを置いて、書き込めた行が 1 行であることを確かめる。onError が continueRegularOutput なら、エラーの item も見る。
SHEETS_UPDATE_CHECK_MARK = '// SHAMAN_SHEETS_UPDATE_CHECK'

def sheetsUpdateCheckNode(name, update_node, pos, suffix_js="''"):
    """update_node の次に置く。suffix_js は、エラー文の後ろに足す JavaScript の式。"""
    label = json.dumps('「' + update_node + '」', ensure_ascii=False)
    return code(name, '\n'.join([
        SHEETS_UPDATE_CHECK_MARK + '：' + update_node + ' が 1 行を書き込んだことを確かめる（0 件のまま止まらない）',
        'var label = ' + label + ';',
        'var suffix = ' + suffix_js + ';',
        'var failed = items.filter(function (it) { return it.json && it.json.error; });',
        'if (failed.length > 0) {',
        '  var e = failed[0].json.error;',
        "  throw new Error(('SHAMAN_RUN_NG: ' + label + 'に書き込めません: ' + String((e && e.message) || e)).substring(0, 500) + suffix);",
        '}',
        'var written = items.filter(function (it) { return it.json && Object.keys(it.json).length > 0; });',
        'if (written.length !== 1) {',
        "  throw new Error('SHAMAN_RUN_NG: ' + label + 'で書き込めた行が ' + written.length + ' 件です（1 件のはず。row_number の行が見つからない、など）' + suffix);",
        '}',
        'return written;',
    ]), pos)

def runWebhookNode(path, pos, name='Run Webhook'):
    """Actions（SHAMAN | n8n Workflow実行）が呼ぶ Webhook。合言葉のヘッダーが無ければ n8n が 403 を返す。

    responseMode は lastNode：実行が終わるまで応答しない。Actions は応答で成否を知る（失敗なら n8n が 500 を返す）。
    """
    return {
        'parameters': {'httpMethod': 'POST', 'path': path, 'authentication': 'headerAuth',
                       'responseMode': 'lastNode', 'options': {}},
        'id': uid(), 'name': name, 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2,
        'position': pos, 'webhookId': path,
        'credentials': {'httpHeaderAuth': dict(RUN_WEBHOOK_CREDENTIAL)},
    }

def apiHttp(name, pos, service, method, url, headers=(), body=None, timeout=60000):
    """外部 API への HTTP Request ノード。認証は service の Credential が付ける。"""
    cred_type, cred = API_CREDENTIALS[service]
    p = {'method': method, 'url': url,
         'authentication': 'predefinedCredentialType', 'nodeCredentialType': cred_type}
    if service == 'anthropic':
        headers = (('anthropic-version', ANTHROPIC_VERSION),) + tuple(headers)
    if headers:
        p['sendHeaders'] = True
        p['headerParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in headers]}
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    p['options'] = {'timeout': timeout}
    return {'parameters': p, 'id': uid(), 'name': name, 'type': 'n8n-nodes-base.httpRequest',
            'typeVersion': 4.2, 'position': pos, 'credentials': {cred_type: dict(cred)}}

def geminiHttp(name, pos, timeout=180000):
    """Gemini の generateContent。モデルは $json.geminiModel、本文は $json.requestBody。"""
    return apiHttp(name, pos, 'gemini', 'POST',
                   "={{ 'https://generativelanguage.googleapis.com/v1beta/models/' + $json.geminiModel + ':generateContent' }}",
                   body='={{ $json.requestBody }}', timeout=timeout)

def claudeHttp(name, pos, timeout=300000):
    """Claude の Messages API。本文は $json.requestBody。anthropic-version は apiHttp が付ける。"""
    return apiHttp(name, pos, 'anthropic', 'POST', 'https://api.anthropic.com/v1/messages',
                   body='={{ $json.requestBody }}', timeout=timeout)

# n8n に登録する唯一の WordPress HTTP Basic Auth Credential。
# Username/Password の式は SITE_ID をキーにし、秘密値は Credential の中だけに保存する。
WP_MULTISITE_CREDENTIAL = {'id': '__ENV:SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID__',
                           'name': 'SHAMAN / WordPress / MultiSite'}

def wordpressHttp(name, pos, method, path, body=None, timeout=60000):
    """1 記事ずつの処理で共通ノードを再利用。サイトと認証は実行 item の siteId に従う。"""
    p = {'method': method,
         'url': "={{ $json.wpUrl.replace(/\\/$/, '') + " + js_str(path) + " }}",
         'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth'}
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    p['options'] = {'timeout': timeout}
    return {'parameters': p, 'id': uid(), 'name': name, 'type': 'n8n-nodes-base.httpRequest',
            'typeVersion': 4.2, 'position': pos,
            'credentials': {'httpBasicAuth': dict(WP_MULTISITE_CREDENTIAL)}}

def discordNode(name, pos, content, continue_on_fail=False):
    """Discord の Webhook に送るノード。Webhook の URL は Credential に置く（URL そのものが Secret）。

    continue_on_fail：通知が失敗しても（Discord の 429・5xx など）、後のノードへ進める。
    """
    # webhookId：n8n は保存のとき、Discord のノードに webhookId が無ければ足す（返信待ちの操作のため）。
    # 足されると配備の読み戻しが一致しないので、ノード名から決まる値をこちらで入れておく（2026-11-06）。
    node = {'parameters': {'authentication': 'webhook', 'operation': 'sendLegacy', 'content': content, 'options': {}},
            'id': uid(), 'name': name, 'type': 'n8n-nodes-base.discord', 'typeVersion': 2, 'position': pos,
            'webhookId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'shaman/discord/' + name)),
            'credentials': {'discordWebhookApi': dict(DISCORD_CREDENTIAL)}}
    if continue_on_fail:
        node['onError'] = 'continueRegularOutput'
    return node

def _drive_credentials(auth):
    if auth not in DRIVE_CREDENTIAL_TYPES:
        raise ValueError(f'unknown Google Drive auth: {auth}')
    return {DRIVE_CREDENTIAL_TYPES[auth]: dict(DRIVE_CREDENTIAL)}

def driveSearchNode(name, pos, query, limit=1, auth=DRIVE_AUTH):
    """Google Drive をクエリで探すノード。0 件でも次へ進め、後の判定で「見つからない」とする。"""
    return {'parameters': {'authentication': auth, 'resource': 'fileFolder', 'operation': 'search', 'searchMethod': 'query',
                           'queryString': query, 'limit': limit, 'filter': {},
                           'options': {'fields': ['id', 'name', 'mimeType']}},
            'id': uid(), 'name': name, 'type': 'n8n-nodes-base.googleDrive', 'typeVersion': 3, 'position': pos,
            'credentials': _drive_credentials(auth), 'alwaysOutputData': True}

def driveDownloadNode(name, pos, file_id, auth=DRIVE_AUTH):
    return {'parameters': {'authentication': auth, 'resource': 'file', 'operation': 'download',
                           'fileId': {'__rl': True, 'value': file_id, 'mode': 'id'},
                           'options': {'binaryPropertyName': 'data'}},
            'id': uid(), 'name': name, 'type': 'n8n-nodes-base.googleDrive', 'typeVersion': 3, 'position': pos,
            'credentials': _drive_credentials(auth)}

# === Queue (Google Sheets) ===
# The queue is a Google spreadsheet read and written with the standard Google Sheets node
# (DOCS/QUEUE_GOOGLE_SHEETS_DESIGN.md). Spreadsheet and credential IDs are replaced at deploy
# time from Repository Variables. Both auth methods are free; the owner picks one in C-6.
QUEUE_SPREADSHEET = '__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__'
QUEUE_TEST_SPREADSHEET = '__ENV:SHAMAN_QUEUE_TEST_SPREADSHEET_ID__'
QUEUE_SHEET = '記事キュー'
SITE_REGISTRY_SHEET = 'サイト登録'
QUEUE_HEADER_ROW = 3
QUEUE_SHEETS_AUTH = 'serviceAccount'
SHEETS_CREDENTIAL_TYPES = {'serviceAccount': 'googleApi', 'oAuth2': 'googleSheetsOAuth2Api'}
SHEETS_CREDENTIAL = {'id': '__ENV:SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID__', 'name': 'Google / Sheets'}
# Columns the pipeline writes back (header text is "<name>\n【自動】").
QUEUE_WRITABLE = ('STATUS', 'WP_POST_ID', 'QUALITY', '開始日時', '完了日時', 'エラー内容')
QUEUE_MAX_VALUE_LEN = 2000

# 本番の実行結果。この実行（実行ID）が確保した行がすべて「完了」なら ok。1 行でも「エラー」「画像エラー」などなら ng。
# 1 行も無ければ ng（何も処理していない）。Actions（run_shaman_workflow.py）は shamanRun.ok が true のときだけ成功にする。
RUN_RESULT_JS = '\n'.join([
    "var RUN = String($execution.id);",
    "function col(r, name) { return String(r[name + '\\n【自動】'] == null ? '' : r[name + '\\n【自動】']); }",
    "var rows = items.map(function(it) { return it.json || {}; })",
    "  .filter(function(r) { return col(r, '実行ID') === RUN; })",
    "  .map(function(r) { return { row: r.row_number, status: col(r, 'STATUS'), wpPostId: col(r, 'WP_POST_ID'),",
    "                              quality: col(r, 'QUALITY'), error: col(r, 'エラー内容').substring(0, 300) }; });",
    "var ng = rows.filter(function(r) { return r.status !== '完了'; });",
    "var reason = !rows.length ? 'この実行で処理したキューの行がありません'",
    "  : ng.length ? ng.length + ' / ' + rows.length + ' 行が完了していません' : '';",
    "return [{ json: { shamanRun: { ok: rows.length > 0 && ng.length === 0, workflow: 'production',",
    "  executionId: RUN, reason: reason, rows: rows } } }];",
])

def sheetsNode(name, operation, pos, document, columns=None, auth=QUEUE_SHEETS_AUTH,
               sheet=QUEUE_SHEET, header_row=QUEUE_HEADER_ROW, lookup=None):
    """Google Sheets のノード。operation は read・update・append。

    sheet・header_row：キュー以外のシート（プロンプト評価のシート）を読み書きするとき。
    lookup：read で、(列名, 値の式) に一致する行だけを返す（v4.5 は一致した行をすべて返す）。
    """
    if auth not in SHEETS_CREDENTIAL_TYPES:
        raise ValueError(f'unknown Google Sheets auth: {auth}')
    p = {
        'authentication': auth, 'resource': 'sheet', 'operation': operation,
        'documentId': {'__rl': True, 'value': document, 'mode': 'id'},
        'sheetName': {'__rl': True, 'value': sheet, 'mode': 'name'},
    }
    if lookup is not None:
        if operation != 'read':
            raise ValueError('lookup is only for read')
        p['filtersUI'] = {'values': [{'lookupColumn': lookup[0], 'lookupValue': lookup[1]}]}
    node = {'parameters': p, 'id': uid(), 'name': name, 'type': 'n8n-nodes-base.googleSheets', 'typeVersion': 4.5,
            'position': pos, 'credentials': {SHEETS_CREDENTIAL_TYPES[auth]: dict(SHEETS_CREDENTIAL)}}
    if operation == 'read':
        p['options'] = {'dataLocationOnSheet': {'values': {
            'rangeDefinition': 'specifyRange', 'headerRow': header_row, 'firstDataRow': header_row + 1}}}
        # 行が 0 件でも次の Code ノードを動かし、「行が見つからない」で止めるため。
        node['alwaysOutputData'] = True
    else:
        p['columns'] = {
            'mappingMode': 'defineBelow', 'value': columns,
            'matchingColumns': ['row_number'] if operation == 'update' else [],
            'schema': [{'id': k, 'displayName': k, 'required': False, 'defaultMatch': False, 'display': True,
                        'type': 'string', 'canBeUsedToMatch': True} for k in columns],
        }
        # 値をそのまま書く。「=」で始まるエラー文を数式にせず、実行 ID や日時を数値・日付に変えない。
        p['options'] = {'cellFormat': 'RAW'}
        # 見出しの行。指定しないと n8n は 1 行目を見出しとして扱い、列が見つからない（キューの見出しは 3 行目）。
        # n8n 2.35.7 の Google Sheets v4.5：update は headerRow と firstDataRow、append は headerRow だけ（2026-11-06）。
        location = {'headerRow': header_row}
        if operation == 'update':
            location['firstDataRow'] = header_row + 1
            # 行が見つからず 0 件でも、次の sheetsUpdateCheckNode を動かして止める（黙って止まらない）。
            node['alwaysOutputData'] = True
        p['options']['locationDefine'] = {'values': location}
    return node

# === Article drafts (image-error resume) ===
# Drafts are written and read with the standard Convert to File / Read/Write Files from Disk /
# Extract from File nodes: n8n 2.x Code nodes cannot require('fs'). The directory is replaced at
# deploy time and must be inside N8N_RESTRICT_FILE_ACCESS_TO (DOCS/QUEUE_GOOGLE_SHEETS_DESIGN.md §6.4).
DRAFT_DIR = '__ENV:SHAMAN_DRAFT_DIR__'
DRAFT_PREFIX = DRAFT_DIR + '/article_draft_'
DRAFT_NODES = ('P6: Save Article', 'P6: Upload Abort', 'Read Draft')

def draft_builder_js(p6_status_js):
    """JS for a Code node that turns the article into a draft (draftJson) and its path (draftFile)."""
    return [
        'var data = items[0].json;',
        "if (data.rowIndex == null) throw new Error('途中保存の行番号がありません');",
        'var articleData = {',
        '  siteId: data.siteId, KEYWORD: data.KEYWORD, OPTION1: data.OPTION1,',
        '  articleHtml: data.articleHtml, articleTitle: data.articleTitle,',
        '  metaDescription: data.metaDescription, slug: data.slug,',
        '  wpUrl: data.wpUrl, AUTHOR: data.AUTHOR, SITENAME: data.SITENAME,',
        '  BASEURL: data.BASEURL, FIRSTPERSON: data.FIRSTPERSON,',
        "  IMAGE_TRIGGER: data.IMAGE_TRIGGER, imgFolder: data.imgFolder,",
        '  rowIndex: data.rowIndex, p6Status: ' + p6_status_js + ',',
        '  savedAt: new Date().toISOString()',
        '};',
        '// 書き込みは P6: Write Draft などの標準ノードが行う。ここでは中身とパスだけを作る。',
        'var draftFile = ' + js_str(DRAFT_PREFIX) + " + data.rowIndex + '.json';",
    ]

def draftWriteNodes(builder, prefix, pos):
    """Convert to File → Read/Write Files from Disk for a draft builder. Both send failures to their error output."""
    x, y = pos
    return [
        {'parameters': {'operation': 'toText', 'sourceProperty': 'draftJson', 'binaryPropertyName': 'data',
                        'options': {'encoding': 'utf8'}},
         'id': uid(), 'name': prefix + 'Draft To File', 'type': 'n8n-nodes-base.convertToFile', 'typeVersion': 1.1,
         'position': [x, y], 'onError': 'continueErrorOutput'},
        {'parameters': {'operation': 'write', 'fileName': "={{ $('" + builder + "').first().json.draftFile }}",
                        'dataPropertyName': 'data', 'options': {}},
         'id': uid(), 'name': prefix + 'Write Draft', 'type': 'n8n-nodes-base.readWriteFile', 'typeVersion': 1,
         'position': [x + 220, y], 'onError': 'continueErrorOutput'},
    ]

# The reason a node sent to its error output (shape checked on the device, design §7).
ERROR_REASON_JS = ("var reason = (items[0] && items[0].json && (items[0].json.error && "
                   "(items[0].json.error.message || items[0].json.error))) || '不明';")

# Shared by the Code nodes that check a re-read row: find it by row_number and make sure
# it is still the row Map Variables read (SITE_ID + KEYWORD, plan §2.6).
QUEUE_ROW_JS = [
    "var SITE_COL = 'SITE_ID\\n（必須）\\nサイト識別子';",
    "var KEY_COL = 'KEYWORD\\n（必須）\\nフォーカスキーワード';",
    "var STATUS_COL = 'STATUS\\n【自動】';",
    "var RUN_COL = '実行ID\\n【自動】';",
    'function queueRow(rowNumber) {',
    '  var found = items.map(function(it) { return it.json || {}; })',
    '    .filter(function(r) { return r.row_number != null && Number(r.row_number) === Number(rowNumber); });',
    "  if (found.length !== 1) throw new Error('キューの行 ' + rowNumber + ' が見つかりません');",
    '  return found[0];',
    '}',
    'function checkSameRow(r, mv) {',
    "  if (String(r[SITE_COL] || '').trim() !== String(mv.siteId || '').trim()",
    "      || String(r[KEY_COL] || '').trim() !== String(mv.KEYWORD || '').trim()) {",
    "    throw new Error('キューの行 ' + mv.rowIndex + ' の SITE_ID・KEYWORD が読み込み時と一致しません（照合の失敗）');",
    '  }',
    '}',
    'function isOurs(r) {',
    "  return String(r[STATUS_COL] || '') === '処理中' && String(r[RUN_COL] || '') === String($execution.id);",
    '}',
]

# 止まった実行の行の再開（DOCS/RUN_FROM_ACTIONS.md「止まった行の再開」）。Actions の recover_execution に n8n の実行 ID を
# 書くと、run_shaman_workflow.py が「その実行は本番の実行で、止まっていて（crashed・error・canceled）、WordPress の
# ノード（名前に WP を含むもの）が 1 つも動いていない。本番の実行がほかに動いていない」を n8n の API で確かめてから、
# Webhook の本文に recover.executionId を入れる。Workflow は、STATUS が「処理中」で、実行ID がその実行で、
# WP_POST_ID が空の行だけを拾う（そのときは、ほかの待機中・画像エラーの行は拾わない）。人がシートを手で戻さなくてよいようにする（2026-11-09）。
RECOVER_JS = [
    "var RECOVER = (function() {",
    "  try { var b = $('Webhook').first().json.body || {}; var id = String((b.recover || {}).executionId || ''); return /^\\d+$/.test(id) ? id : null; }",
    "  catch (e) { return null; }",
    "})();",
    "function recoverable(r) {",
    "  return RECOVER !== null && String(r['STATUS\\n【自動】'] || '') === '処理中'",
    "    && String(r['実行ID\\n【自動】'] || '') === RECOVER && String(r['WP_POST_ID\\n【自動】'] || '').trim() === '';",
    "}",
    "// 再開のときは、再開の対象の行だけ。ほかの「待機中」「画像エラー」の行は、この実行では処理しない（PR #90 レビュー B1）。",
    "function claimable(r) {",
    "  if (RECOVER !== null) return recoverable(r);",
    "  var st = String(r['STATUS\\n【自動】'] || '');",
    "  return st === '待機中' || st === '画像エラー';",
    "}",
]

def cn(to):
    return {'node': to, 'type': 'main', 'index': 0}

def wf(name, nodes, connections, wf_id, tags=None, active=False):
    return {'name': name, 'nodes': nodes, 'pinData': {}, 'connections': connections, 'active': active, 'settings': SETTINGS, 'meta': META, 'id': wf_id, 'tags': tags or []}

# === 同じ実行の中で何度も動くノードの出力を読む ===
# P2・P4 の出し直しでは、Build → API → Parse が 1 つの実行の中で 2 回動く（評価では回数ぶん繰り返す）。Parse は、
# 今の回の Build の出力（試行回数を含む）を読む必要がある。n8n の公式ドキュメントは「runIndex を省くと最初の実行
# （index 0）」と書いているが、n8n 2.35.7 のソースでは省くと最後の実行（workflow-data-proxy.ts の
# defaultReturnRunIndex = -1）。ドキュメントとソースが食い違うので、省略に頼らず、出力 0 の最後の実行（-1）を明示する
# （PR #89 レビュー B1）。
def latestRun(nodeName, output=0):
    return "$('" + nodeName + "').first(" + str(output) + ", -1).json"


def parseTextCode(buildNodeName, outputProp, extraValidation=None, returns=True):
    lines = [
        'var response = $input.first().json;',
        "var config = " + latestRun(buildNodeName) + ";",
        'if (!response.candidates || !response.candidates[0] || !response.candidates[0].content || !response.candidates[0].content.parts) {',
        "  throw new Error('Gemini API error: ' + JSON.stringify(response).substring(0, 500));",
        '}',
        'var parts = response.candidates[0].content.parts;',
        "var text = '';",
        'for (var i = 0; i < parts.length; i++) { if (parts[i].text) text += parts[i].text; }',
        "if (!text || text.trim().length === 0) throw new Error('Gemini returned empty response');"
    ]
    if extraValidation:
        lines.append(extraValidation)
    if returns:
        lines.append('return [{ json: { ...config, ' + outputProp + ': text.trim() } }];')
    return '\n'.join(lines)


def retryIf(name, flag, position):
    """品質の確認で 1 回目が条件を満たさなかったとき（json[flag] が true）、Build に戻す IF ノード。"""
    return {
        'parameters': {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                'conditions': [{'id': flag.lower(), 'leftValue': '={{ $json.' + flag + ' }}', 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
                'combinator': 'and'
            },
            'options': {}
        },
        'id': uid(), 'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': position
    }

# === Standard HTML parse code ===
def parseHtmlCode(buildNodeName, outputProp, deleteFields=None, unwrapLinks=False):
    lines = [
        'var response = $input.first().json;',
        "var config = " + latestRun(buildNodeName) + ";",
        'if (!response.candidates || !response.candidates[0] || !response.candidates[0].content || !response.candidates[0].content.parts) {',
        "  throw new Error('Gemini API error: ' + JSON.stringify(response).substring(0, 500));",
        '}',
        'var parts = response.candidates[0].content.parts;',
        "var raw = '';",
        'for (var i = 0; i < parts.length; i++) { if (parts[i].text) raw += parts[i].text; }',
        "if (!raw || raw.trim().length === 0) throw new Error('Gemini returned empty content');",
        'var html = raw.trim();',
        "var m = html.match(/^```(?:html)?\\s*([\\s\\S]*?)```\\s*$/i);",
        'if (m) html = m[1].trim();',
        "if (html.indexOf('<h2') === -1 && html.indexOf('<h3') === -1) throw new Error('Not HTML: ' + html.substring(0,300));",
    ]
    if unwrapLinks:
        lines.extend([
            '// P3 has no search tool, so any link it writes is invented (real run 1295: three 404 internal links).',
            '// Keep the anchor text; verified links are added in a later phase.',
            'var p3LinksRemoved = (html.match(/<a\\b[^>]*>/gi) || []).length;',
            "html = html.replace(/<a\\b[^>]*>([\\s\\S]*?)<\\/a>/gi, '$1');",
        ])
    if deleteFields:
        for f in deleteFields:
            lines.append('delete config["' + f + '"];')
    if unwrapLinks:
        lines.append('return [{ json: { ...config, ' + outputProp + ': html, p3LinksRemoved: p3LinksRemoved } }];')
    else:
        lines.append('return [{ json: { ...config, ' + outputProp + ': html } }];')
    return '\n'.join(lines)

# 記事の外を指す書き方（DOCS/P1_KNOWLEDGE.md §4）。実在を確かめられないので、記事に残さない。
# - 自サイトの他の記事への言及（「別の記事で解説しています」「関連記事」など）。リンクが無くても、存在しない記事を指す。
#   「この記事」は今の記事なので対象にしない。
# - 本文の URL の文字列（a タグの外。http(s):// か www. で始まるもの）。
ARTICLE_REF_JS = [
    'var SITE_REF = /(?:別|他|ほか|以前|過去|前回|次回|関連|下記|こちら)の(?:記事|ページ|エントリー?)|別記事|関連記事|(?:あわせて|合わせて)読みたい|おすすめ記事|当(?:サイト|ブログ)の(?:記事|ページ)|(?:サイト|ブログ)内の(?:記事|ページ)/g;',
    'var URL_TEXT = /(?:https?:\\/\\/|www\\.)[^\\s<>"\'（）「」、。]+/gi;',
    "function textOf(s) { return String(s || '').replace(/<[^>]+>/g, ' '); }",
    'function siteRefsIn(s) { return textOf(s).match(SITE_REF) || []; }',
    'function urlsIn(s) { return textOf(s).match(URL_TEXT) || []; }',
    '// タグの中（属性の値）は変えず、本文の文字だけから URL を消す',
    "function stripUrls(s) { return String(s || '').split(/(<[^>]+>)/).map(function(p) { return p.charAt(0) === '<' ? p : p.replace(URL_TEXT, ''); }).join(''); }",
]


# === Claude response parser: handles Anthropic Messages API format ===
def parseClaudeHtmlCode(buildNodeName, outputProp, deleteFields=None, qualityGate=False):
    lines = [
        'var response = $input.first().json;',
        "var config = " + latestRun(buildNodeName) + ";",
        "if (response.stop_reason === 'max_tokens') {",
        "  throw new Error('Claude output truncated (stop_reason=max_tokens): thinking + article exceeded max_tokens');",
        '}',
        "if (response.stop_reason === 'refusal') {",
        "  throw new Error('Claude refused the request (stop_reason=refusal)');",
        '}',
        'if (!response.content || !Array.isArray(response.content) || response.content.length === 0) {',
        "  throw new Error('Claude API error: ' + JSON.stringify(response).substring(0, 500));",
        '}',
        "var raw = '';",
        'for (var i = 0; i < response.content.length; i++) {',
        "  if (response.content[i].type === 'text' && response.content[i].text) raw += response.content[i].text;",
        '}',
        "if (!raw || raw.trim().length === 0) throw new Error('Claude returned empty content');",
        'var html = raw.trim();',
        "var m = html.match(/^```(?:html)?\\s*([\\s\\S]*?)```\\s*$/i);",
        'if (m) html = m[1].trim();',
        '// Claude often returns a full HTML document; keep only the <body> inner content',
        "var bodyMatch = html.match(/<body[^>]*>([\\s\\S]*?)<\\/body>/i);",
        'if (bodyMatch) html = bodyMatch[1];',
        '// Strip document scaffolding + CSS so the site/theme stylesheet governs (keep class/id)',
        "html = html.replace(/<style[\\s\\S]*?<\\/style>/gi, '');",
        "html = html.replace(/<script[\\s\\S]*?<\\/script>/gi, '');",
        "html = html.replace(/<head[\\s\\S]*?<\\/head>/gi, '');",
        "html = html.replace(/<title[\\s\\S]*?<\\/title>/gi, '');",
        "html = html.replace(/<!DOCTYPE[^>]*>/gi, '');",
        "html = html.replace(/<\\/?(?:html|head|body)[^>]*>/gi, '');",
        "html = html.replace(/<meta[^>]*>/gi, '');",
        "html = html.replace(/<link[^>]*>/gi, '');",
        "html = html.replace(/\\sstyle\\s*=\\s*(\"[^\"]*\"|'[^']*')/gi, '');",
        'html = html.trim();',
        "if (html.indexOf('<h2') === -1 && html.indexOf('<h3') === -1) throw new Error('Not HTML: ' + html.substring(0,300));",
    ]
    if qualityGate:
        lines.extend([
        '// -- P4 quality gate: compare with the P3 draft this article was built from --',
        "var baseline = config.htmlContent || '';",
        'var attempt = config.p4Attempt || 1;',
        'var NL = String.fromCharCode(10);',
        'function plainLen(s) {',
        "  var t = s.replace(/<[^>]+>/g, '');",
        "  ['&nbsp;', ' ', NL, String.fromCharCode(13), String.fromCharCode(9), String.fromCharCode(12288)].forEach(function(w) { t = t.split(w).join(''); });",
        '  return t.length;',
        '}',
        'function tagTexts(s, tag) {',
        "  var out = [], open = '<' + tag, close = '</' + tag + '>', i = 0;",
        '  while ((i = s.indexOf(open, i)) !== -1) {',
        "    var gt = s.indexOf('>', i), end = s.indexOf(close, gt);",
        '    if (gt === -1 || end === -1) break;',
        "    out.push(s.substring(gt + 1, end).replace(/<[^>]+>/g, '').trim());",
        '    i = end + close.length;',
        '  }',
        '  return out;',
        '}',
        'var issues = [];',
        "if (baseline.indexOf('<h3') !== -1) {",
        "  if (tagTexts(baseline, 'h2').join('|') !== tagTexts(html, 'h2').join('|') || tagTexts(baseline, 'h3').join('|') !== tagTexts(html, 'h3').join('|')) {",
        "    issues.push('・h2/h3 の見出しテキスト、順番、数が下書きと変わっています');",
        '  }',
        "  html.split('<h3').slice(1).forEach(function(part) {",
        "    var sec = part.split('<h2')[0];",
        "    var title = sec.substring(sec.indexOf('>') + 1, sec.indexOf('</h3>')).replace(/<[^>]+>/g, '').trim();",
        "    var len = plainLen(sec.substring(sec.indexOf('</h3>') + 5));",
        "    if (len < 700) issues.push('・「' + title + '」の本文が ' + len + ' 文字で、700文字に届いていません');",
        "    if (!/<(ol|ul|table)[ >]/i.test(sec)) issues.push('・「' + title + '」に ol / ul / table がありません');",
        '  });',
        "  var added = html.split('<a ').length - baseline.split('<a ').length;",
        "  if (added > 0) issues.push('・下書きに無いリンク（a タグ）が ' + added + ' 本追加されています');",
        '}',
        ] + ARTICLE_REF_JS + [
        'var siteRefs = siteRefsIn(html);',
        "if (siteRefs.length) issues.push('・自サイトの他の記事への言及が ' + siteRefs.length + ' 箇所あります（' + siteRefs.slice(0, 5).join('、') + '）。存在しない記事を指すので、削除してください');",
        'var urlTexts = urlsIn(html);',
        "if (urlTexts.length) issues.push('・本文に URL の文字列が ' + urlTexts.length + ' 個あります（' + urlTexts.slice(0, 3).join('、') + '）。実在を確かめていない URL なので、削除してください');",
        'if (issues.length > 0 && attempt < 2) {',
        '  return [{ json: Object.assign({}, config, { p4Retry: true, p4QualityFeedback: issues.join(NL), p4PrevHtml: html, p4PrevIssues: issues }) }];',
        '}',
        '// Keep the better attempt: fewer gate issues wins, ties go to the longer article',
        "var selected = 'attempt' + attempt;",
        'if (config.p4PrevHtml) {',
        '  var prevIssues = config.p4PrevIssues || [];',
        '  if (prevIssues.length < issues.length || (prevIssues.length === issues.length && plainLen(config.p4PrevHtml) > plainLen(html))) {',
        "    html = config.p4PrevHtml; issues = prevIssues; selected = 'attempt' + (attempt - 1);",
        '  }',
        '}',
        'delete config.p4PrevHtml;',
        'delete config.p4PrevIssues;',
        '// 2 回呼んでも自サイトの他の記事への言及が残ったら、記事として採用しない（存在しない記事を指すため。PR #85 レビュー B1）。',
        '// 文の一部なので機械では消さず、ここで止めて WordPress に投稿しない。',
        'var remainingRefs = siteRefsIn(html);',
        'if (remainingRefs.length) {',
        "  throw new Error('SHAMAN_RUN_NG: P4 を ' + attempt + ' 回呼んでも、自サイトの他の記事への言及が ' + remainingRefs.length + ' 箇所残っています（' + remainingRefs.slice(0, 5).join('、') + '）。存在しない記事を指すので、この記事は投稿しません');",
        '}',
        '// 実在を確かめたリンクはまだ無い（WBS Phase 3）。残ったリンクは外して文字だけ残し、URL の文字列は消す。',
        'var p4LinksRemoved = (html.match(/<a\\b/gi) || []).length;',
        "html = html.replace(/<a\\b[^>]*>([\\s\\S]*?)<\\/a>/gi, '$1').replace(/<\\/?a\\b[^>]*>/gi, '');",
        'var p4UrlsRemoved = urlsIn(html).length;',
        "html = stripUrls(html);",
        ])
    if deleteFields:
        for f in deleteFields:
            lines.append('delete config["' + f + '"];')
    if qualityGate:
        lines.append('return [{ json: { ...config, ' + outputProp + ": html, p4Retry: false, p4Selected: selected, p4QualityIssues: issues.length ? issues.join(NL) : null, p4LinksRemoved: p4LinksRemoved, p4UrlsRemoved: p4UrlsRemoved } }];")
    else:
        lines.append('return [{ json: { ...config, ' + outputProp + ': html } }];')
    return '\n'.join(lines)

# Helper to safely embed a Python string as a JS string literal in jsCode
def js_str(s):
    return json.dumps(s, ensure_ascii=False)

# =============== SHARED: BUILD UPDATED HTML ===============
def _build_updated_html_js():
    """JS for the Build Updated HTML node in buildWorkflow()."""
    return '\n'.join([
        'var allItems = items;',
        "if (!allItems || allItems.length === 0) throw new Error('Build Updated HTML: no items received');",
        '// Use items[0] directly — avoids $() ref returning stale data on loop iterations',
        'var baseConfig = items[0].json;',
        'var html = baseConfig.articleHtml || "";',
        'var insertedCount = 0;',
        '// Normalize: unify full-width/half-width spaces, zero-width chars, NFC',
        'function normalizeStr(s) {',
        "  return s.normalize('NFC')",
        "    .replace(/[\\u200B-\\u200D\\uFEFF\\u3000]/g, ' ')",
        "    .replace(/\\s+/g, ' ').trim();",
        '}',
        'for (var i = 0; i < allItems.length; i++) {',
        '  var img = allItems[i].json;',
        '  if (!img.mediaUrl) continue;',
        "  var altText = (img.altText || 'article image').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');",
        "  var cls = img.mediaId ? ' wp-image-' + img.mediaId : '';",
        "  var safeMediaUrl = (img.mediaUrl || '').replace(/[\"'<>]/g, '');",
        "  if (!/^https?:\\/\\//i.test(safeMediaUrl)) { safeMediaUrl = ''; }",
        '  var imgTag = "\\n<figure class=\\"wp-block-image size-full" + cls + "\\"><img src=\\"" + safeMediaUrl + "\\" alt=\\"" + altText + "\\" loading=\\"lazy\\" /></figure>\\n";',
        "  var context = (img.insertionContext || '').trim();",
        "  context = context.replace(/<[^>]+>/g, '').trim();  // strip HTML tags Gemini may include",
        '  var matched = false;',
        '  if (context) {',
        "    var safeContext = context.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');",
        '    var normCtx = normalizeStr(context);',
        "    var normSafe = normCtx.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');",
        "    var flexSafe = normSafe.replace(/ /g, '[\\\\s\\\\u3000\\\\u00A0]+');",
        '    // 1. Heading-aware: if the context is a heading, insert the image AFTER the heading block (not inside it)',
        "    var headPat = '(<(h[1-6])\\\\b[^>]*>(?:(?!</h[1-6]>)[\\\\s\\\\S])*?(?:' + safeContext + '|' + flexSafe + ')(?:(?!</h[1-6]>)[\\\\s\\\\S])*?</\\\\2>)';",
        "    var headRegex = new RegExp(headPat, 'i');",
        '    if (headRegex.test(html)) {',
        '      html = html.replace(headRegex, function(m) { return m + imgTag; });',
        '      insertedCount++; matched = true;',
        '    }',
        '    // 2. Exact text match (context not inside a heading) — insert right after the text',
        '    if (!matched) {',
        "      var regex = new RegExp('(' + safeContext + ')', 'i');",
        '      if (regex.test(html)) {',
        '        html = html.replace(regex, function(m) { return m + imgTag; });',
        '        insertedCount++; matched = true;',
        '      }',
        '    }',
        '    // 3. Flex-whitespace text match (full-width/NBSP/any-whitespace)',
        '    if (!matched) {',
        "      var flexRegex = new RegExp('(' + flexSafe + ')', 'i');",
        '      if (flexRegex.test(html)) {',
        '        html = html.replace(flexRegex, function(m) { return m + imgTag; });',
        '        insertedCount++; matched = true;',
        '      }',
        '    }',
        '  }',
        '  // 3. Fallback: append to end — never silently discard an uploaded image',
        '  if (!matched) { html = html + imgTag; insertedCount++; }',
        '}',
        'return [{ json: { ...baseConfig, updatedHtml: html, imagesInserted: insertedCount } }];'
    ])


def _wp_upload_batch_error_js():
    """Shared JS tail for WP: Upload All Images — batch error summary without corrupting successful items."""
    return [
        'var failedUploads = results.filter(function(r) { return r.uploadError; });',
        'if (failedUploads.length > 0) {',
        # The regex strips the structured prefix set by the catch block:
        # 'upload_failed [index=N, status=X]: ' + e.message
        # If the uploadError format in the catch block above changes, update this regex too.
        '  // uploadError format: "upload_failed [index=N, status=X]: <e.message>" — update regex below if format changes',
        "  var firstErrMsg = failedUploads[0].uploadError.replace(/^upload_failed \\[.*?\\]:\\s*/, '').substring(0, 150);",
        "  var batchUploadError = 'upload_failed_' + failedUploads.length + '_of_' + results.length + ': ' + firstErrMsg;",
        '  results = results.map(function(r) { return Object.assign({}, r, { batchUploadError: batchUploadError }); });',
        '}',
    ]




# === P6 / P7: Google Drive の参考画像、画像の生成、WordPress への投稿（Credential を使うノード） ===
# n8n 2.x の Code ノードは require を使えず、$env も読めない。外部への接続は、HTTP Request・
# Google Drive ノードで行い、認証は Credential が付ける（N8N_LUNA_MIGRATION_PLAN.md §3、DOCS/CONNECTION_CHECK.md）。

# 参考画像は Google Drive の SHAMAN/IMG/<サイトのフォルダー>/ に置く。フォルダーの名前は sites.json の
# IMG_FOLDER（省略時は SITE_ID）。SHAMAN/IMG の ID は config/drive.json に置く。
def load_img_folder_id(path=DRIVE_PATH):
    with open(path, encoding='utf-8') as f:
        folder_id = json.load(f).get('IMG_FOLDER_ID', '')
    # Drive の検索の式に入るので、ID の文字だけを通す。
    if not isinstance(folder_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,128}', folder_id):
        raise ValueError('config/drive.json IMG_FOLDER_ID must be a Google Drive folder ID')
    return folder_id

IMG_FOLDER = load_img_folder_id()
DRIVE_FOLDER_MIME = 'application/vnd.google-apps.folder'
REFERENCE_MIMES = ('image/jpeg', 'image/png', 'image/webp')
MAX_REFERENCE_IMAGES = 16  # OpenAI images/edits に送れる画像の上限
# 節ごとのリクエスト（最大 7 件）に、すべての参考画像が入る。実行のデータが大きくなりすぎないよう、合計に上限を設ける
# （今の参考画像は、1 サイトで約 3.6MB）。
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
# 画像の生成で、429・5xx・通信エラーになった節だけを作り直す回数（最初の 1 回を含めて 3 回まで）。
MAX_IMAGE_ATTEMPTS = 3
IMAGE_RETRY_WAIT_SECONDS = 10
OPENAI_IMAGES_EDITS_URL = 'https://api.openai.com/v1/images/edits'


def _if_not_empty(name, field, pos, cond_id):
    """$json.<field> が空でなければ true（出力 0）、空なら false（出力 1）。"""
    return {
        'parameters': {'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
            'conditions': [{'id': cond_id, 'leftValue': '={{ $json.' + field + ' }}', 'rightValue': '',
                            'operator': {'type': 'string', 'operation': 'notEmpty', 'singleValue': True}}],
            'combinator': 'and'}, 'options': {}},
        'id': uid(), 'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': pos,
    }


def _full_response(node):
    """HTTP の状態コードを判定に使う。4xx・5xx も例外にせず、応答として次のノードへ渡す。"""
    node['parameters']['options']['response'] = {'response': {'neverError': True, 'fullResponse': True}}
    # 通信エラー（応答がない）も止めずに、{ error } の item として渡す。
    node['onError'] = 'continueRegularOutput'
    return node


def _binary_body(node, field):
    node['parameters'].update({'sendBody': True, 'contentType': 'binaryData', 'inputDataFieldName': field})
    return node


def p6ReferenceImageNodes(x, y):
    """サイトの参考画像を Google Drive から読み、Read Reference Images が referenceImages にまとめる。"""
    mimes = ' or '.join("mimeType = '" + m + "'" for m in REFERENCE_MIMES)
    return [
        # 共有ルートIMGが見えなくなった場合、サイト別フォルダーの「0件」と区別して中断する。
        driveSearchNode('P6: Find IMG Root', [x - 240, y],
                        "name = 'IMG' and mimeType = '" + DRIVE_FOLDER_MIME + "' and trashed = false",
                        limit=100),
        code('P6: Verify IMG Root', '\n'.join([
            'var expected = ' + js_str(IMG_FOLDER) + ';',
            'var ids = items.filter(function(it) { return it.json && it.json.id; }).map(function(it) { return it.json.id; });',
            "var problem = items.find(function(it) { return it.json && it.json.error; });",
            "if (problem || ids.indexOf(expected) < 0) throw new Error('SHAMAN_DRIVE_IMG_ACCESS_LOST: SHAMAN/IMG の共有またはアクセス権を確認してください');",
            "return [{ json: $('Check P6 Mode').first().json }];",
        ]), [x - 110, y]),
        dict(driveSearchNode('P6: Find Image Folder', [x, y],
                             "='" + IMG_FOLDER + "' in parents and name = '{{ $json.imgFolder }}' and mimeType = '"
                             + DRIVE_FOLDER_MIME + "' and trashed = false"), alwaysOutputData=True),
        code('P6: Check Image Folder', '\n'.join([
            "var cfg = $('Check P6 Mode').first().json;",
            'var f = (items[0] && items[0].json) || {};',
            "if (f.error) throw new Error('参考画像のフォルダーを探せません: ' + String(f.error.message || f.error).substring(0, 300));",
            '// フォルダー未作成は正常なゼロ枚。通信エラーは従来どおり止める。',
            "return [{ json: { folderId: f.id || '', folderName: f.name || cfg.imgFolder, imageReferenceNote: f.id ? '' : '参照画像なし（フォルダ無し）' } }];",
        ]), [x + 110, y]),
        _if_not_empty('P6: Has Image Folder?', 'folderId', [x + 160, y + 180], 'p6-folder-present'),
        dict(driveSearchNode('P6: List Reference Images', [x + 220, y],
                             "='{{ $json.folderId }}' in parents and (" + mimes + ") and trashed = false",
                             limit=MAX_REFERENCE_IMAGES), alwaysOutputData=True),
        code('P6: Check Reference Images', '\n'.join([
            "var folder = $('P6: Check Image Folder').first().json;",
            'var files = items.filter(function(it) { return it.json && it.json.id; });',
            '// 参照画像ゼロ枚なら生成APIへ進む。APIの失敗と権限エラーは隠さない。',
            "var error = items.find(function(it) { return it.json && it.json.error; });",
            "if (error) throw new Error('参考画像一覧の取得に失敗: ' + String(error.json.error.message || error.json.error).substring(0, 300));",
            "if (files.length === 0) return [{ json: { hasReferenceImages: '', referenceImages: [], imageReferenceNote: '参照画像なし（0枚）' } }];",
            "return files.map(function(it) { return { json: Object.assign({}, it.json, { hasReferenceImages: 'yes' }) }; });",
        ]), [x + 330, y]),
        _if_not_empty('P6: Has Reference Images?', 'hasReferenceImages', [x + 380, y + 180], 'p6-images-present'),
        driveDownloadNode('P6: Download Reference Image', [x + 440, y], '={{ $json.id }}'),
        code('Read Reference Images', '\n'.join([
            "var data = $('Check P6 Mode').first().json;",
            'var MIMES = ' + js_str(list(REFERENCE_MIMES)) + ';',
            'var referenceImages = [];',
            'for (var i = 0; i < items.length; i++) {',
            '  var bin = items[i].binary && items[i].binary.data;',
            '  if (!bin || MIMES.indexOf(bin.mimeType) < 0) continue;',
            "  var buf = await this.helpers.getBinaryDataBuffer(i, 'data');",
            "  referenceImages.push({ b64: buf.toString('base64'), mime: bin.mimeType, file: bin.fileName || ('ref-' + (i + 1)) });",
            '}',
            '// 参照画像が無い場合も本文から新規画像を生成する。',
            '// 参考画像をダウンロードした場合にだけ、対応しない形式・空の画像を異常として扱う。',
            "var needImages = $('P6: Check Reference Images').isExecuted === true && $('P6: Check Reference Images').first(0).json.hasReferenceImages === 'yes';",
            "if (needImages && referenceImages.length === 0) throw new Error('参照画像のダウンロード結果が空です: ' + data.imgFolder);",
            'var totalBytes = referenceImages.reduce(function(n, r) { return n + Math.floor(r.b64.length * 3 / 4); }, 0);',
            'if (totalBytes > ' + str(MAX_REFERENCE_BYTES) + ') {',
            "  throw new Error('参考画像の合計が ' + Math.round(totalBytes / 1048576) + 'MB です（上限 ' + "
            + str(MAX_REFERENCE_BYTES // 1048576) + " + 'MB）。フォルダー「' + data.imgFolder + '」の画像を減らすか小さくしてください');",
            '}',
            '// Drive の返す順に左右されないよう、ファイル名の順にする。',
            'referenceImages.sort(function(a, b) { return a.file < b.file ? -1 : (a.file > b.file ? 1 : 0); });',
            "var root = $('P6: Check Image Folder').first().json;",
            "var note = root.imageReferenceNote || (referenceImages.length ? '' : '参照画像なし（0枚）');",
            'return [{ json: { ...data, referenceImages: referenceImages, imageReferenceNote: note } }];',
        ]), [x + 550, y]),
    ]


def _openai_request_js():
    return [
        'var fixedTemplate = ' + js_str(P6_IMAGE_PROMPT_OPENAI) + ';',
        'var noReferenceTemplate = ' + js_str(P6_IMAGE_PROMPT_OPENAI_NO_REFERENCE) + ';',
        "var sceneLabels = [['location', '場所'], ['action', '行動'], ['pose', 'ポーズ'], ['expression', '表情'], ['composition', '構図'], ['cameraAngle', 'カメラアングル'], ['background', '背景'], ['props', '小物'], ['mood', '演出・雰囲気']];",
        'function buildScenePrompt(section) {',
        '  var lines = [];',
        '  var sc = section.scene;',
        "  if (sc && typeof sc === 'object' && !Array.isArray(sc)) {",
        '    for (var j = 0; j < sceneLabels.length; j++) {',
        '      var v = sc[sceneLabels[j][0]];',
        "      if (v && typeof v === 'string') lines.push(sceneLabels[j][1] + ': ' + v);",
        '    }',
        '  }',
        "  if (lines.length === 0) lines.push(section.imageDescription || '');",
        "  return lines.join('\\n');",
        '}',
        '// images/edits の JSON の本文で送る。参考画像は data: URL で images に入れる（最大 16 枚）。',
        '// multipart の本文を自分で組み立てて送ると、n8n 2.x はそれを 1 バイトずつフォームの項目に作り直し、',
        '// n8n の本体が CPU を握ったまま止まる（2026-11-08、本番の実行 11。DOCS/P6_IMAGE_REQUEST.md）。',
        'function buildRequest(section, i) {',
        "  var prompt = (refImages.length ? fixedTemplate : noReferenceTemplate).replace('# 以下にシーン指示を入力してください #', buildScenePrompt(section));",
        "  var images = refImages.map(function(r) { return { image_url: 'data:' + r.mime + ';base64,' + r.b64 }; });",
        "  var imageApiPath = images.length ? 'https://api.openai.com/v1/images/edits' : 'https://api.openai.com/v1/images/generations';",
        '  var requestBody = {',
        "    model: section.MODEL_IMAGE_OPENAI || " + js_str(MODEL_IMAGE_OPENAI) + ",",
        '    prompt: prompt,',
        "    size: '1536x864',",
        "    quality: section.IMAGE_QUALITY || " + js_str(IMAGE_QUALITY) + ",",
        "    output_format: 'jpeg',",
        '    output_compression: 80,',
        '    n: 1',
        '  };',
        '  if (images.length) requestBody.images = images;',
        '  return { json: Object.assign({}, section, { imageApiPath: imageApiPath, requestBody: requestBody }) };',
        '}',
    ]


def _gemini_request_js():
    return [
        'var imageTemplate = ' + js_str(P6_IMAGE_PROMPT) + ';',
        'var noReferenceTemplate = ' + js_str(P6_IMAGE_PROMPT_NO_REFERENCE) + ';',
        'function buildRequest(section, i) {',
        '  var parts = refImages.map(function(r) { return { inlineData: { mimeType: r.mime, data: r.b64 } }; });',
        "  parts.push({ text: (refImages.length ? imageTemplate : noReferenceTemplate).replace('# 以下に画像説明を入力してください #', section.imageDescription || '') });",
        '  return { json: Object.assign({}, section, {',
        "    geminiModel: section.MODEL_IMAGE || " + js_str(MODEL_IMAGE) + ",",
        "    requestBody: { contents: [{ role: 'user', parts: parts }],",
        "      generationConfig: { responseModalities: ['IMAGE', 'TEXT'], temperature: 0.7, maxOutputTokens: 65536 } }",
        '  }) };',
        '}',
    ]


def p6ImageNodes(x, y, provider=None):
    """節ごとに画像を作る。429・5xx・通信エラーの節だけを、待ってから作り直す（最大 MAX_IMAGE_ATTEMPTS 回）。

    Build Image Requests → P6: Generate Image（節ごとに 1 回の HTTP）→ Collect Generated Images
    → P6: Retry Images?（作り直す節があれば P6: Retry Wait → Build Image Requests）→ P6: Images Ready。
    """
    provider = provider or IMAGE_PROVIDER
    if provider not in ('openai', 'gemini'):
        raise ValueError('IMAGE_PROVIDER must be openai or gemini: ' + repr(provider))
    if provider == 'openai':
        http = apiHttp('P6: Generate Image', [x + 220, y], 'openai', 'POST',
                       '={{ $json.imageApiPath }}', body='={{ $json.requestBody }}', timeout=180000)
        builder = _openai_request_js()
    else:
        http = geminiHttp('P6: Generate Image', [x + 220, y], 180000)
        builder = _gemini_request_js()
    return [
        code('Build Image Requests', '\n'.join([
            "var refImages = $('Read Reference Images').first().json.referenceImages || [];",
            "var refNote = $('Read Reference Images').first().json.imageReferenceNote || '';",
        ] + builder + [
            '// 最初は Parse Section Plan の節。作り直しのときは、P6: Retry Wait の 1 件（results）から、作り直す節だけを選ぶ。',
            'var retry = items.length === 1 && Array.isArray(items[0].json.p6Results);',
            'var attempt = retry ? items[0].json.p6Attempt + 1 : 1;',
            'var sections = retry',
            '  ? items[0].json.p6Results.filter(function(r) { return r.imageRetryable; }).map(function(r) {',
            '      var s = Object.assign({}, r); delete s.imageError; delete s.imageRetryable; return s; })',
            '  : items.map(function(it) { return it.json; });',
            'return sections.map(function(section, i) {',
            '  var req = buildRequest(section, i);',
            '  req.json = Object.assign({}, req.json, { p6Attempt: attempt, imageReferenceNote: refNote });',
            '  return req;',
            '});',
        ]), [x, y]),
        _full_response(http),
        code('Collect Generated Images', '\n'.join([
            'var PROVIDER = ' + js_str(provider) + ';',
            'var MAX_ATTEMPTS = ' + str(MAX_IMAGE_ATTEMPTS) + ';',
            "var reqs = $('Build Image Requests').all();",
            'var attempt = reqs.length ? reqs[0].json.p6Attempt : 1;',
            '// 前の回の結果（作り直しのときだけある）。作り直した節を、今回の結果で置き換える。',
            'var prev = [];',
            "if (attempt > 1) prev = $('P6: Retry Wait').first().json.p6Results || [];",
            "function redact(t) { return String(t).replace(/(key=|Bearer\\s+)[^&\\s\"',}]+/gi, '$1***').substring(0, 300); }",
            'function imageOf(body) {',
            "  if (PROVIDER === 'gemini') {",
            '    var parts = (body && body.candidates && body.candidates[0] && body.candidates[0].content && body.candidates[0].content.parts) || [];',
            '    var p = parts.find(function(x) { return x.inlineData && x.inlineData.data; });',
            "    return p ? { b64: p.inlineData.data, mime: p.inlineData.mimeType || 'image/png' } : null;",
            '  }',
            "  var b = body && body.data && body.data[0] && body.data[0].b64_json;",
            "  return b ? { b64: b, mime: 'image/jpeg' } : null;",
            '}',
            'var fresh = reqs.map(function(req, i) {',
            '  var section = Object.assign({}, req.json);',
            '  delete section.requestBody;',
        '  delete section.imageApiPath;',
            '  var idx = section.sectionIndex;',
            '  var res = (items[i] && items[i].json) || {};',
            '  // 通信エラー（応答なし）は res.error。応答があれば statusCode と body がある。',
            '  var status = res.error ? 0 : Number(res.statusCode || 0);',
            '  var body = res.body || {};',
            '  var img = status >= 200 && status < 300 ? imageOf(body) : null;',
            '  if (img) {',
            "    var ext = img.mime === 'image/png' ? 'png' : 'jpg';",
            "    return Object.assign(section, { imageB64: img.b64, imageMime: img.mime, imageFilename: 'article-image-' + idx + '.' + ext, imageIndex: idx });",
            '  }',
            '  if (status >= 200 && status < 300) return Object.assign(section, { imageError: \'no_image_section_\' + idx, imageIndex: idx });',
            "  var detail = res.error ? String(res.error.message || res.error) : ('[' + status + ((body.error && body.error.code) ? '/' + body.error.code : '') + '] ' + JSON.stringify(body.error || body));",
            '  // 429・5xx・通信エラーは作り直す。そのほかの 4xx（moderation_blocked など）は作り直さない。',
            '  var retryable = status === 0 || status === 429 || status >= 500;',
            "  var tag = retryable ? 'api_error_section_' : 'policy_or_bad_request_section_';",
            "  return Object.assign(section, { imageError: tag + idx + ': ' + redact(detail), imageIndex: idx, imageRetryable: retryable && attempt < MAX_ATTEMPTS });",
            '});',
            'var byIndex = {};',
            'prev.concat(fresh).forEach(function(r) { byIndex[r.sectionIndex] = r; });',
            'var results = Object.keys(byIndex).map(Number).sort(function(a, b) { return a - b; }).map(function(k) { return byIndex[k]; });',
            'var retry = results.some(function(r) { return r.imageRetryable; });',
            "return [{ json: { p6Results: results, p6Attempt: attempt, p6RetryNeeded: retry ? 'yes' : '' } }];",
        ]), [x + 440, y]),
        _if_not_empty('P6: Retry Images?', 'p6RetryNeeded', [x + 660, y], 'p6-retry'),
        {'parameters': {'amount': IMAGE_RETRY_WAIT_SECONDS, 'unit': 'seconds'},
         'id': uid(), 'name': 'P6: Retry Wait', 'type': 'n8n-nodes-base.wait', 'typeVersion': 1.1,
         'position': [x + 660, y - 160], 'webhookId': 'shaman-p6-retry-wait'},
        code('P6: Images Ready', '\n'.join([
            "var results = (items[0].json.p6Results || []).map(function(r) { var o = Object.assign({}, r); delete o.imageRetryable; return o; });",
            'var successCount = results.filter(function(r) { return r.imageB64; }).length;',
            'if (successCount < 5) {',
            "  results = results.map(function(r) { return Object.assign({}, r, { p6Status: 'insufficient_images_' + successCount }); });",
            '}',
            'return results.map(function(r) { return { json: r }; });',
        ]), [x + 880, y]),
    ]


def _site_switch(name, pos, sites):
    """siteId でサイトごとの出力に分ける。sites.json にないサイトは最後の出力（WP: Unknown Site）へ。"""
    rules = [{'conditions': {
        'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
        'conditions': [{'id': 'site-' + str(i), 'leftValue': '={{ $json.siteId }}', 'rightValue': site_id,
                        'operator': {'type': 'string', 'operation': 'equals'}}],
        'combinator': 'and'}, 'renameOutput': True, 'outputKey': site_id}
        for i, site_id in enumerate(sorted(sites), 1)]
    return {'parameters': {'rules': {'values': rules},
                           'options': {'fallbackOutput': 'extra', 'renameFallbackOutput': 'unknown site'}},
            'id': uid(), 'name': name, 'type': 'n8n-nodes-base.switch', 'typeVersion': 3.2, 'position': pos}


WP_TARGET_GUARD_JS = '\n'.join([
    'var DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;',
    'items.forEach(function(it) {',
    '  var target = it.json || {};',
    "  var id = String(target.siteId || '');",
    "  if (!DOMAIN.test(id) || target.wpUrl !== 'https://' + id) {",
    "    throw new Error('WP_SITE_ID_INVALID: SITE_ID と送信先URLの不一致: ' + id);",
    '  }',
    '});',
    'return items;',
])


def wpSiteNodes(sites, x, y):
    """どのサイトも同じアップロード・投稿ノードを使う。サイトを増やしてもノード数は一定。"""
    upload = wordpressHttp('WP: Upload Media', [x + 220, y],
                           'POST', '/wp-json/wp/v2/media', timeout=60000)
    upload['parameters'].update({'sendHeaders': True, 'headerParameters': {'parameters': [
        {'name': 'Content-Disposition', 'value': '={{ "attachment; filename=\\\"" + $json.mediaFilename + "\\\"" }}'}]}})
    upload = _full_response(_binary_body(upload, 'media'))
    post = wordpressHttp('WP: Create Post', [x + 880, y + 220],
                         'POST', '/wp-json/wp/v2/posts', body='={{ $json.wpPostBody }}')
    post['parameters']['options']['response'] = {'response': {'neverError': True}}
    return [code('WP: Validate Upload Target', WP_TARGET_GUARD_JS, [x, y]), upload,
            code('WP: Validate Post Target', WP_TARGET_GUARD_JS, [x + 660, y + 220]), post], {
        'WP: Validate Upload Target': {'main': [[cn('WP: Upload Media')]]},
        'WP: Upload Media': {'main': [[cn('WP: Collect Uploads')]]},
        'WP: Validate Post Target': {'main': [[cn('WP: Create Post')]]},
        'WP: Create Post': {'main': [[cn('Parse WP Response')]]},
    }


def wpUploadNodes(x, y):
    """画像を WordPress のメディアに上げる前後のノード（サイトによらない部分）。"""
    return [
        code('WP: Prepare Media', '\n'.join([
            '// 画像のある節だけを、バイナリにしてアップロードする。',
            'var out = [];',
            'items.forEach(function(it) {',
            '  var img = it.json;',
            '  if (!img.imageB64) return;',
            '  var base = Object.assign({}, img);',
            '  delete base.imageB64;',
            "  var filename = String(img.imageFilename || ('article-image-' + img.imageIndex + '.png')).replace(/[^a-zA-Z0-9._-]/g, '_');",
            "  out.push({ json: Object.assign(base, { mediaFilename: filename }), binary: { media: { data: img.imageB64, mimeType: img.imageMime || 'image/png', fileName: filename } } });",
            '});',
            "if (out.length === 0) throw new Error('アップロードする画像がありません');",
            'return out;',
        ]), [x, y]),
        code('WP: Collect Uploads', '\n'.join([
            "var prepared = $('WP: Prepare Media').all();",
            'var uploaded = {};',
            'for (var i = 0; i < prepared.length; i++) {',
            '  var idx = prepared[i].json.imageIndex;',
            '  var res = (items[i] && items[i].json) || {};',
            '  var status = res.error ? 0 : Number(res.statusCode || 0);',
            '  var body = res.body || {};',
            '  if (status >= 200 && status < 300 && body.id) {',
            "    uploaded[idx] = { mediaId: body.id, mediaUrl: body.source_url || (body.guid && body.guid.rendered) || '' };",
            '  } else {',
            "    var detail = res.error ? String(res.error.message || res.error) : (body.message || JSON.stringify(body));",
            "    uploaded[idx] = { mediaId: null, mediaUrl: '', uploadError: 'upload_failed [index=' + idx + ', status=' + status + ']: ' + String(detail).substring(0, 300) };",
            '  }',
            '}',
            "var results = $('P6: Images Ready').all().map(function(it) {",
            '  var base = Object.assign({}, it.json);',
            '  delete base.imageB64;',
            "  return Object.assign(base, uploaded[base.imageIndex] || { mediaId: null, mediaUrl: '' });",
            '});',
        ] + _wp_upload_batch_error_js() + [
            'return results.map(function(r) { return { json: r }; });',
        ]), [x + 440, y]),
    ]


# P1 の情報源（DOCS/P1_KNOWLEDGE.md）。モデルには URL を書かせない（書けば作り話になる）。応答の
# groundingMetadata（Google 検索で実際に参照したページ）から取り、内部のデータ（p1Sources）として持つ。
# 記事を書く P2〜P4 には URL を渡さず、サイト名だけをナレッジに書く（リンクを作らせないため）。
# 検索の情報源が 1 件も無いとき・「確認できなかった情報」があるときは、p1ResearchStatus に残す
# （キューのエラー内容と Discord に出る。記事の処理は止めない）。
P1_SOURCES_JS = [
    'var now = new Date().toISOString();',
    'var gm = response.candidates[0].groundingMetadata || {};',
    'var chunks = Array.isArray(gm.groundingChunks) ? gm.groundingChunks : [];',
    'var p1Sources = { checkedAt: now, queries: Array.isArray(gm.webSearchQueries) ? gm.webSearchQueries : [], sources: [], supports: [] };',
    'chunks.forEach(function(c, i) {',
    '  var w = (c && c.web) || {};',
    "  if (w.uri || w.title) p1Sources.sources.push({ no: i + 1, title: String(w.title || ''), url: String(w.uri || '') });",
    '});',
    '(Array.isArray(gm.groundingSupports) ? gm.groundingSupports : []).forEach(function(sp) {',
    "  var t = sp && sp.segment && sp.segment.text ? String(sp.segment.text) : '';",
    "  var ids = (sp && Array.isArray(sp.groundingChunkIndices) ? sp.groundingChunkIndices : []).map(function(n) { return n + 1; });",
    '  if (t && ids.length) p1Sources.supports.push({ text: t.substring(0, 200), sources: ids });',
    '});',
    "// 「## 確認できなかった情報」の見出しから次の「## 」までを取り出す。「なし」だけなら空とみなす。",
    "var gaps = '';",
    "var gapAt = researchText.search(/^#{1,4}\\s*確認できなかった情報\\s*$/m);",
    'if (gapAt !== -1) {',
    "  var rest = researchText.substring(gapAt).replace(/^[^\\n]*\\n?/, '');",
    "  var nextAt = rest.search(/^#{1,2}\\s/m);  // 次の「## 」まで（中の「### 」の小見出しは含める）",
    '  gaps = (nextAt === -1 ? rest : rest.substring(0, nextAt)).trim();',
    "  if (/^[\\s*・\\-]*(なし|特になし|ありません)[。．.]?\\s*$/.test(gaps)) gaps = '';",
    '}',
    'var researchStatus = [];',
    "if (p1Sources.sources.length === 0) researchStatus.push('P1: Google 検索の情報源がありません（追加の調査が必要）');",
    "if (gaps) researchStatus.push('P1: 検索で確認できなかった情報があります（追加の調査が必要）');",
    "researchStatus = researchStatus.length ? researchStatus.join(' / ') : null;",
    "var sourceNote = '\\n## 情報源（Google 検索で参照したサイト。確認日 ' + now.substring(0, 10) + '）\\n' + (p1Sources.sources.length",
    "  ? p1Sources.sources.map(function(x) { return '- [' + x.no + '] ' + (x.title || '(名前なし)'); }).join('\\n')",
    "  : '- なし（検索の情報源を取得できませんでした。このナレッジの事実は確認されていません）') + '\\n';",
]


# =============== STEPS (本番とプロンプト評価で共用) ===============
def p1Nodes():
    """Phase 1: Research。本番とプロンプト評価 Workflow が同じノードを使う（DOCS/PROMPT_EVAL_DESIGN.md）。"""
    return [
        code('Build P1 Request', '\n'.join([
            'var data = items[0].json;',
            'var template = ' + js_str(P1_PROMPT) + ';',
            "var prompt = template.split('${KEYWORD}').join(data.KEYWORD || '');",
            'var requestBody = JSON.stringify({',
            "  contents: [{ role: 'user', parts: [{ text: prompt }] }],",
            '  tools: [{ googleSearch: {} }],',
            '  generationConfig: { temperature: 0.7, maxOutputTokens: 65536 }',
            '});',
            '// 情報源が 0 件で出し直すときは 2 回目（P1: Retry?）。',
            'var next = Object.assign({}, data); delete next.p1Retry;',
            "return [{ json: { ...next, p1Attempt: (data.p1Attempt || 0) + 1, geminiModel: (data.MODEL_PRO || '" + MODEL_PRO + "'), requestBody: requestBody } }];"
        ]), [1100, 400]),
        geminiHttp('Gemini: Research', [1320, 400], 180000),
        code('Parse Research', '\n'.join([
            'var response = $input.first().json;',
            "var config = " + latestRun('Build P1 Request') + ";",
            'if (!response.candidates || !response.candidates[0] || !response.candidates[0].content || !response.candidates[0].content.parts) throw new Error("Gemini error: " + JSON.stringify(response).substring(0,500));',
            'var parts = response.candidates[0].content.parts;',
            "var researchText = '';",
            'for (var i = 0; i < parts.length; i++) { if (parts[i].text) researchText += parts[i].text; }',
            "if (!researchText || researchText.trim().length === 0) throw new Error('Empty research');",
        ] + P1_SOURCES_JS + [
            "var knowledge = '# P1 Knowledge Base\\n**生成日時:** ' + now + '\\n**キーワード:** ' + config.KEYWORD + '\\n\\n---\\n\\n## Geminiリサーチ結果 (Google Search)\\n\\n' + researchText + '\\n' + sourceNote;",
            '// Google 検索をするかどうかはモデルが決めるので、検索せずに答えることがある（本番の実行 43、2026-11-09：情報源 0 件）。',
            '// 検索しなかったナレッジには、確かめられていない内容（架空の研究データ、人名、地名など）が混ざる（実行 43）。',
            '// 1 回目で情報源が 0 件なら、1 回だけ呼び直す。2 回目も 0 件なら、記事を作らずに止める（オーナーの判断、2026-11-10）。',
            '// 止まった行は、Actions の recover_execution でやり直せる（DOCS/RUN_FROM_ACTIONS.md）。',
            'var attempt = config.p1Attempt || 1;',
            'if (!p1Sources.sources.length && attempt < 2) return [{ json: { ...config, p1Retry: true } }];',
            "if (!p1Sources.sources.length) throw new Error('SHAMAN_RUN_NG: P1 を ' + attempt + ' 回呼んでも、Google 検索の情報源がありません（検索せずに答えた）。確かめられていないナレッジで記事を書かないため、この記事は作りません');",
            'return [{ json: { ...config, p1Retry: false, p1Knowledge: knowledge, researchText: researchText, p1Sources: p1Sources, p1ResearchStatus: researchStatus, p1ResearchGaps: gaps } }];'
        ]), [1540, 400]),
        retryIf('P1: Retry?', 'p1Retry', [1650, 580]),
    ]

# P2 の構成案の見出しの条件（prompts.P2_T2）のうち、機械的に数えられるもの。1 回目が満たさなければ
# 条件を伝えて出し直してもらい（P2: Retry?）、2 回目も満たさなければ実行を NG で止める。
BANNED_HEADING_WORDS = ('研究', '実験', '実証', '検証', '調査', '分析', 'データ', '科学', '統計')
OUTLINE_GATE_JS = '\n'.join([
    'var attempt = config.p2Attempt || 1;',
    'var outline = text.trim();',
    "var lines = outline.split('\\n');",
    "var h2 = lines.filter(function(l) { return /^##\\s/.test(l); }).length;",
    "var h3 = lines.filter(function(l) { return /^###\\s/.test(l); }).length;",
    "var heads = lines.filter(function(l) { return /^#{2,3}\\s/.test(l); }).map(function(l) { return l.replace(/^#+\\s*/, '').trim(); });",
    '// 25 文字は記号と空白を除いて数える（プロンプトの「記号を除いて25文字以内」）',
    "var over = heads.filter(function(h) { return h.replace(/[\\s\\u3000!-\\/:-@\\[-`{-~、。・「」『』（）【】！？ー〜]/g, '').length > 25; });",
    'var banned = ' + js_str(list(BANNED_HEADING_WORDS)) + ';',
    'var bannedHeads = heads.filter(function(h) { return banned.some(function(w) { return h.indexOf(w) !== -1; }); });',
    'var issues = [];',
    "if (h2 !== 2) issues.push('・H2 見出しが ' + h2 + ' 個あります。H2 見出しは 2 個にしてください。');",
    "if (h2 + h3 < 12 || h2 + h3 > 13) issues.push('・見出しが H2 と H3 を合わせて ' + (h2 + h3) + ' 個あります（H2 が ' + h2 + ' 個、H3 が ' + h3 + ' 個）。合わせて 12 個か 13 個にしてください。');",
    "if (over.length) issues.push('・記号を除いて 25 文字を超える見出しがあります：' + over.join(' / ') + '。25 文字以内にしてください。');",
    "if (bannedHeads.length) issues.push('・見出しに使えない語（' + banned.join('・') + '）を含む見出しがあります：' + bannedHeads.join(' / ') + '。');",
    "var result = Object.assign({}, config, { outlineText: outline, p2Attempt: attempt, p2Issues: issues.length ? issues : null });",
    'delete result.p2PrevOutline; delete result.p2QualityFeedback;',
    'if (issues.length && attempt < 2) {',
    "  return [{ json: Object.assign(result, { p2Retry: true, p2PrevOutline: outline, p2QualityFeedback: issues.join('\\n') }) }];",
    '}',
    "if (issues.length) throw new Error('SHAMAN_RUN_NG: P2 を ' + attempt + ' 回呼んでも、記事構成案の見出しが条件を満たしません。' + issues.join(' ') + ' この記事は投稿しません');",
    'result.p2Retry = false;',
    'return [{ json: result }];',
])


def p2Nodes():
    """Phase 2: Outline。本番とプロンプト評価 Workflow が同じノードを使う（DOCS/PROMPT_EVAL_DESIGN.md）。"""
    return [
        code('Build P2 Request', '\n'.join([
            'var data = items[0].json;',
            'var t1Prefix = ' + js_str(P2_T1) + ';',
            "var turn1 = t1Prefix + '\\n\\n' + (data.p1Knowledge || '');",
            'var t2Template = ' + js_str(P2_T2) + ';',
            "var turn2 = t2Template.split('${KEYWORD}').join(data.KEYWORD || '').split('${OPTION1}').join(data.OPTION1 || '');",
            'var requestBody = JSON.stringify({',
            '  contents: [',
            "    { role: 'user', parts: [{ text: turn1 }] },",
            "    { role: 'model', parts: [{ text: 'インプットしました' }] },",
            "    { role: 'user', parts: [{ text: turn2 }] }",
            '  ],',
            '  generationConfig: { temperature: 0.7, maxOutputTokens: 65536 }',
            '});',
            "return [{ json: { ...data, geminiModel: (data.MODEL_OUTLINE || '" + MODEL_OUTLINE + "'), requestBody: requestBody } }];"
        ]).replace(
            # 2 回目：1 回目の構成案と、満たしていない条件を続けて渡し、構成案全体を出し直してもらう
            "    { role: 'user', parts: [{ text: turn2 }] }\n  ],",
            "    { role: 'user', parts: [{ text: turn2 }] }\n  ].concat(retry),"
        ).replace(
            'var requestBody = JSON.stringify({',
            '\n'.join([
                'var attempt = (data.p2Attempt || 0) + 1;',
                'var retry = (data.p2PrevOutline && data.p2QualityFeedback) ? [',
                "  { role: 'model', parts: [{ text: data.p2PrevOutline }] },",
                "  { role: 'user', parts: [{ text: " + js_str(P2_RETRY_PREFIX) + " + '\\n' + data.p2QualityFeedback }] }",
                '] : [];',
                'var requestBody = JSON.stringify({',
            ])
        ).replace(
            "return [{ json: { ...data, geminiModel:",
            "var next = Object.assign({}, data); delete next.p2Retry; delete next.p2QualityFeedback; delete next.p2PrevOutline;\n"
            "if (data.p2QualityFeedback) next.p2FirstIssues = data.p2QualityFeedback;\n"
            "return [{ json: { ...next, p2Attempt: attempt, geminiModel:"
        ), [1760, 400]),
        geminiHttp('Gemini: Generate Outline', [1980, 400], 120000),
        code('Parse Outline', parseTextCode('Build P2 Request', 'outlineText', OUTLINE_GATE_JS, returns=False), [2200, 400]),
        retryIf('P2: Retry?', 'p2Retry', [2310, 580]),
    ]


def p3Nodes():
    """Phase 3: Content。本番とプロンプト評価 Workflow が同じノードを使う（DOCS/PROMPT_EVAL_DESIGN.md）。"""
    return [
        code('Build P3 Request', '\n'.join([
            'var data = items[0].json;',
            "var turn1 = '以下の記事構成案をインプットしてください。インプットが完了したら「インプットしました」とだけ返答してください。\\n\\n' + (data.outlineText || '');",
            'var t2Template = ' + js_str(P3_RAW) + ';',
            "var turn2 = t2Template.split('${KEYWORD}').join(data.KEYWORD || '').split('${SITENAME}').join(data.SITENAME || '').split('${AUTHOR}').join(data.AUTHOR || '').split('${BASEURL}').join(data.BASEURL || '').split('${FIRSTPERSON}').join(data.FIRSTPERSON || '僕').split('${P1_KNOWLEDGE}').join(data.p1Knowledge || '');",
            'var requestBody = JSON.stringify({',
            '  contents: [',
            "    { role: 'user', parts: [{ text: turn1 }] },",
            "    { role: 'model', parts: [{ text: 'インプットしました' }] },",
            "    { role: 'user', parts: [{ text: turn2 }] }",
            '  ],',
            '  generationConfig: { temperature: 0.7, maxOutputTokens: 65536 }',
            '});',
            "return [{ json: { ...data, geminiModel: (data.MODEL_PRO || '" + MODEL_PRO + "'), requestBody: requestBody } }];"
        ]), [2420, 400]),
        geminiHttp('Gemini: Generate Content', [2640, 400], 300000),
        code('Parse Content', parseHtmlCode('Build P3 Request', 'htmlContent', unwrapLinks=True), [2860, 400]),
    ]


def p4Nodes():
    """Phase 4: Strengthen (Claude Sonnet 4.6)。本番とプロンプト評価 Workflow が同じノードを使う（DOCS/PROMPT_EVAL_DESIGN.md）。"""
    # Gemini 3.1 Pro emits MALFORMED_FUNCTION_CALL on long SEO prompts
    # (hallucinates tool calls). Claude does not exhibit this behavior.
    return [
        code('Build P4 Request', '\n'.join([
            'var data = items[0].json;',
            'var attempt = (data.p4Attempt || 0) + 1;',
            '// Retry = repair: fix only the flagged sections of attempt 1 instead of regenerating from P3',
            'var repair = !!(data.p4PrevHtml && data.p4QualityFeedback);',
            'var template = repair ? ' + js_str(P4_REPAIR_RAW) + ' : ' + js_str(P4_RAW) + ';',
            "template = template.split('${KEYWORD}').join(data.KEYWORD || '').split('${SITENAME}').join(data.SITENAME || '').split('${AUTHOR}').join(data.AUTHOR || '').split('${BASEURL}').join(data.BASEURL || '').split('${FIRSTPERSON}').join(data.FIRSTPERSON || '僕').split('${P1_KNOWLEDGE}').join(data.p1Knowledge || '').split('${P4_FEEDBACK}').join(data.p4QualityFeedback || '');",
            "var body = repair ? data.p4PrevHtml : (data.htmlContent || '');",
            "var marker = '### 以下記事本文 ###';",
            'var idx = template.lastIndexOf(marker);',
            "var prompt = idx !== -1 ? template.substring(0, idx + marker.length) + '\\n\\n' + body : template + '\\n\\n' + body;",
            # Claude Opus 5.5: sampling params (temperature/top_p/top_k) return 400, thinking cannot
            # be disabled, and effort defaults to medium -> set 'high' explicitly. max_tokens caps
            # thinking + article combined. On luna, claude-sonnet-4-6 ran past the 900s HTTP timeout
            # (production execution 8, 2026-11-07), so the timeout is 1800s.
            'var requestBody = JSON.stringify({',
            "  model: (data.MODEL_CLAUDE || '" + MODEL_CLAUDE + "'),",
            '  max_tokens: 96000,',
            "  thinking: { type: 'adaptive' },",
            "  output_config: { effort: 'high' },",
            "  messages: [{ role: 'user', content: prompt }]",
            '});',
            'var next = Object.assign({}, data); delete next.p4Retry; delete next.p4QualityFeedback;',
            "return [{ json: { ...next, p4Attempt: attempt, claudeModel: (data.MODEL_CLAUDE || '" + MODEL_CLAUDE + "'), requestBody: requestBody } }];"
        ]), [3080, 400]),
        claudeHttp('Claude: Strengthen Content', [3300, 400], P4_TIMEOUT_MS),
        code('Parse Strengthened', parseClaudeHtmlCode('Build P4 Request', 'articleHtml',
            deleteFields=['htmlContent', 'outlineText', 'researchText'], qualityGate=True), [3520, 400]),
        # Attempt 1 failing the quality gate loops back once; attempt 2 always continues.
        retryIf('P4: Retry?', 'p4Retry', [3630, 580]),
    ]


def p5Nodes():
    """Phase 5: Meta Description。本番とプロンプト評価 Workflow が同じノードを使う（DOCS/PROMPT_EVAL_DESIGN.md）。"""
    return [
        code('Build P5 Request', '\n'.join([
            'var data = items[0].json;',
            "var turn1 = '以下の記事をインプットしてください。インプットが完了したら「インプットしました」とだけ返答してください。\\n\\n' + (data.articleHtml || '');",
            'var t2Template = ' + js_str(P5_RAW) + ';',
            "var turn2 = t2Template.split('${KEYWORD}').join(data.KEYWORD || '');",
            'var requestBody = JSON.stringify({',
            '  contents: [',
            "    { role: 'user', parts: [{ text: turn1 }] },",
            "    { role: 'model', parts: [{ text: 'インプットしました' }] },",
            "    { role: 'user', parts: [{ text: turn2 }] }",
            '  ],',
            '  generationConfig: { temperature: 0.5, maxOutputTokens: 65536 }',
            '});',
            "return [{ json: { ...data, geminiModel: (data.MODEL_FLASH_LITE || '" + MODEL_FLASH_LITE + "'), requestBody: requestBody } }];"
        ]), [3740, 400]),
        geminiHttp('Gemini: Generate Meta', [3960, 400], 120000),
        code('Parse Meta + Extract Title', '\n'.join([
            'var response = $input.first().json;',
            "var config = $('Build P5 Request').first().json;",
            'if (!response.candidates || !response.candidates[0] || !response.candidates[0].content || !response.candidates[0].content.parts) throw new Error("Gemini error: " + JSON.stringify(response).substring(0,500));',
            'var parts = response.candidates[0].content.parts;',
            "var metaText = '';",
            'for (var i = 0; i < parts.length; i++) { if (parts[i].text) metaText += parts[i].text; }',
            'metaText = metaText.trim();',
            '// P5 is asked for a strict 120-140 chars and the model sometimes appends the count, e.g. "（133文字）"',
            'metaText = metaText.replace(/[\\s\\u3000]*(?:[（(【\\[]\\s*(?:文字数\\s*[:：]\\s*\\d{2,4}\\s*(?:文字|字)?|\\d{2,4}\\s*(?:文字|字))\\s*[）)】\\]]|\\d{2,4}\\s*(?:文字|字))[\\s\\u3000]*$/, \"\").trim();',
            "if (!metaText) throw new Error('Empty meta description');",
            'var articleHtml = config.articleHtml || "";',
            '// Title: prefer <h1> (Claude emits the real title there), fall back to first <h2>, then KEYWORD',
            'var h1Match = articleHtml.match(/<h1[^>]*>([\\s\\S]*?)<\\/h1>/i);',
            'var h2Match = articleHtml.match(/<h2[^>]*>([\\s\\S]*?)<\\/h2>/i);',
            "var rawTitle = h1Match ? h1Match[1] : (h2Match ? h2Match[1] : '');",
            "var articleTitle = rawTitle ? rawTitle.replace(/<[^>]+>/g,'').trim() : (config.KEYWORD || 'Blog Article');",
            '// Drop the <h1> from the body so it does not duplicate the WordPress post title',
            "var cleanedHtml = articleHtml.replace(/<h1[^>]*>[\\s\\S]*?<\\/h1>\\s*/i, '');",
            "var slug = (config.KEYWORD || 'article').toLowerCase().replace(/[\\u3000-\\u9FFF]/g,'').replace(/[^\\w\\s-]/g,'').replace(/\\s+/g,'-').replace(/-+/g,'-').replace(/^-|-$/g,'').substring(0,60);",
            "if (!slug) slug = 'article-' + Date.now();",
            'return [{ json: { ...config, articleHtml: cleanedHtml, metaDescription: metaText, articleTitle: articleTitle, slug: slug } }];'
        ]), [4180, 400]),
    ]


def p6AnalysisNodes():
    """P6 の節の分析（記事から画像を入れる節を選ぶ）。本番とプロンプト評価 Workflow が同じノードを使う。"""
    return [
        code('Build Section Analysis Request', '\n'.join([
            'var data = items[0].json;',
            'var template = ' + js_str(P6_ANALYZE_PROMPT) + ';',
            "var prompt = template.replace('# 以下に記事本文を入力してください #', data.articleHtml || '');",
            'var requestBody = JSON.stringify({',
            "  contents: [{ role: 'user', parts: [{ text: prompt }] }],",
            '  generationConfig: { temperature: 0.7, maxOutputTokens: 65536 }',
            '});',
            "return [{ json: { ...data, geminiModel: (data.MODEL_SECTION_ANALYSIS || '" + MODEL_SECTION_ANALYSIS + "'), requestBody: requestBody } }];"
        ]), [4840, 180]),
        geminiHttp('Gemini: Analyze Sections', [5060, 180], 120000),
        code('Parse Section Plan', '\n'.join([
            'var response = $input.first().json;',
            "var config = $('Build Section Analysis Request').first().json;",
            'var cfg = Object.assign({}, config);',
            'delete cfg.requestBody;',
            'delete cfg.referenceImages;',
            "if (response.error) return [{ json: Object.assign({}, cfg, { p6Status: 'section_analysis_error' }) }];",
            'if (!response.candidates || !response.candidates[0] || !response.candidates[0].content)',
            "  return [{ json: Object.assign({}, cfg, { p6Status: 'section_analysis_error' }) }];",
            'var parts = response.candidates[0].content.parts || [];',
            "var text = '';",
            'for (var i = 0; i < parts.length; i++) { if (parts[i].text) text += parts[i].text; }',
            'text = text.trim();',
            "var m = text.match(/^```(?:json)?\\s*([\\s\\S]*?)```\\s*$/i);",
            'if (m) text = m[1].trim();',
            'var parsed;',
            "try { parsed = JSON.parse(text); } catch(e) { return [{ json: Object.assign({}, cfg, { p6Status: 'section_parse_error' }) }]; }",
            'var sections = (parsed && parsed.sections) ? parsed.sections : [];',
            'var complete = sections.filter(function(s) { return s && s.insertionContext && s.imageDescription && s.altText; });',
            '// insertionContext は、記事に実在する見出し（h2〜h4）でなければ使わない（作られた見出しには画像を入れられない）。',
            '// 空白の違いだけは同じ見出しとみなす。同じ見出しを 2 回選んだら、最初の 1 つだけを使う。',
            "function norm(t) { return String(t || '').replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').replace(/[\\s\\u3000\\u00A0]+/g, ' ').trim(); }",
            "var headings = [], hre = /<(h[2-4])\\b[^>]*>([\\s\\S]*?)<\\/\\1>/gi, hm;",
            "while ((hm = hre.exec(config.articleHtml || '')) !== null) headings.push(norm(hm[2]));",
            'var used = {};',
            'var valid = complete.filter(function(s) {',
            '  var t = norm(s.insertionContext);',
            '  if (!t || headings.indexOf(t) === -1 || used[t]) return false;',
            '  used[t] = true; return true;',
            '});',
            'cfg.p6SectionsDropped = complete.length - valid.length;',
            "if (valid.length < 5) return [{ json: Object.assign({}, cfg, { p6Status: 'insufficient_sections_' + valid.length }) }];",
            '// min: 5 (guard above), max: 7 (slice below) — keep these thresholds in sync',
            'return valid.slice(0, 7).map(function(s, idx) {',
            '  // scene is optional structured variable-info; malformed values fall back to imageDescription downstream',
            "  var scene = (s.scene && typeof s.scene === 'object' && !Array.isArray(s.scene)) ? s.scene : null;",
            '  return { json: Object.assign({}, cfg, { insertionContext: s.insertionContext, imageDescription: s.imageDescription, altText: s.altText, scene: scene, sectionIndex: idx + 1 }) };',
            '});'
        ]), [5280, 180]),
    ]


# =============== WORKFLOW ===============
# === API の使用量（2026-11-09、オーナーの依頼。DOCS/API_USAGE_LOG.md） ===
# 本番のスプレッドシートのシート「API使用量」に、API の呼び出し 1 回ごとに 1 行を書く。シートと見出しは Workflow が作る。
USAGE_SHEET = 'API使用量'
USAGE_HEADER_ROW = 1
USAGE_COLUMNS = ('日時', '実行ID', 'キューの行', 'SITE_ID', 'KEYWORD', '工程', 'モデル', 'HTTPの状態',
                 '入力トークン', '出力トークン', '思考トークン', 'キャッシュ読み込みトークン', 'キャッシュ作成トークン',
                 '合計トークン', '所要時間ms', '停止理由', 'usage')
# (工程, Build ノード, API のノード)。Build と API の間に Usage: Stamp を入れ、API のあとに
# Usage: Row → Usage: Write → Usage: Pass（API の出力をそのまま次へ渡す）を入れる。
USAGE_CALLS = (('P1', 'Build P1 Request', 'Gemini: Research'),
               ('P2', 'Build P2 Request', 'Gemini: Generate Outline'),
               ('P3', 'Build P3 Request', 'Gemini: Generate Content'),
               ('P4', 'Build P4 Request', 'Claude: Strengthen Content'),
               ('P5', 'Build P5 Request', 'Gemini: Generate Meta'),
               ('P6A', 'Build Section Analysis Request', 'Gemini: Analyze Sections'),
               ('P6', 'Build Image Requests', 'P6: Generate Image'))

USAGE_STAMP_JS = """// API を呼ぶ直前の時刻（API使用量の所要時間に使う）。item はそのまま渡す。
return items.map(function(it) { return { json: Object.assign({}, it.json, { apiRequestedAt: Date.now() }) }; });"""

USAGE_ROW_JS = r"""// API の応答から、使ったトークン数を 1 回 1 行にする（Gemini：usageMetadata、Claude・OpenAI：usage）。
// 記事の処理は止めない。読めない項目は空にし、読めなかった理由を usage の列に書く。
var STEP = __STEP__, STAMP = __STAMP__;
var mv = {}, stamps = [];
// 呼び直し（P1・P2・P4）と行ごとのループで同じノードが何度も動くので、最後の実行（-1）を明示して読む。
try { mv = $('Map Variables').first(0, -1).json || {}; } catch (e) {}
try { stamps = $(STAMP).all(0, -1); } catch (e) {}
function n(v) { return typeof v === 'number' && isFinite(v) ? v : ''; }
return items.map(function(it, i) {
  try { return usageRow(it, i); } catch (e) {
    return { json: { '日時': new Date().toISOString(), '実行ID': String($execution.id), '工程': STEP,
                     'usage': '記録できません: ' + String(e && e.message || e).substring(0, 300) } };
  }
});
function usageRow(it, i) {
  var res = it.json || {};
  var full = Object.prototype.hasOwnProperty.call(res, 'statusCode') && Object.prototype.hasOwnProperty.call(res, 'body');
  var body = full ? (res.body || {}) : res;
  var st = (stamps[i] && stamps[i].json) || {};
  var rb = st.requestBody;
  if (typeof rb === 'string') { try { rb = JSON.parse(rb); } catch (e) { rb = {}; } }
  if (!rb || typeof rb !== 'object') rb = {};
  var row = { '日時': new Date().toISOString(), '実行ID': String($execution.id), 'キューの行': mv.rowIndex,
    'SITE_ID': mv.siteId || '', 'KEYWORD': mv.KEYWORD || '', '工程': STEP,
    // リクエストの model を、前の工程から残った geminiModel より先に見る（P6 の OpenAI に P6A の Gemini の名前が
    // 入っていた。本番の実行 43）。
    'モデル': String(body.modelVersion || body.model || rb.model || st.geminiModel || st.claudeModel || ''),
    'HTTPの状態': res.error ? 0 : (full ? res.statusCode : 200),
    '入力トークン': '', '出力トークン': '', '思考トークン': '', 'キャッシュ読み込みトークン': '', 'キャッシュ作成トークン': '',
    '合計トークン': '', '所要時間ms': st.apiRequestedAt ? Date.now() - st.apiRequestedAt : '', '停止理由': '', 'usage': '' };
  var um = body.usageMetadata, u = body.usage;
  if (um && typeof um === 'object') {
    row['入力トークン'] = n(um.promptTokenCount);
    row['出力トークン'] = n(um.candidatesTokenCount);
    row['思考トークン'] = n(um.thoughtsTokenCount);
    row['キャッシュ読み込みトークン'] = n(um.cachedContentTokenCount);
    row['合計トークン'] = n(um.totalTokenCount);
    row['usage'] = JSON.stringify(um);
  } else if (u && typeof u === 'object') {
    row['入力トークン'] = n(u.input_tokens);
    row['出力トークン'] = n(u.output_tokens);
    var od = u.output_tokens_details || {};
    row['思考トークン'] = n(od.thinking_tokens != null ? od.thinking_tokens : od.reasoning_tokens);
    row['キャッシュ読み込みトークン'] = n(u.cache_read_input_tokens);
    row['キャッシュ作成トークン'] = n(u.cache_creation_input_tokens);
    row['合計トークン'] = n(u.total_tokens);
    row['usage'] = JSON.stringify(u);
  }
  var c = body.candidates && body.candidates[0];
  row['停止理由'] = String(body.stop_reason || (c && c.finishReason) || (res.error ? 'error' : ''));
  if (res.error) row['usage'] = String(res.error.message || res.error).substring(0, 300);
  return { json: row };
}"""

USAGE_PASS_JS = """// API の出力を、そのまま次のノード（Parse など）へ渡す（記録の前と同じ item）。
return $(__API__).all();"""

USAGE_SHEET_READY_JS = """// Usage: Ensure Sheet は、シートを作ったときだけ、そのシートを出す（前からあれば {}）。
var made = items.some(function(it) { return it.json && it.json.title === __SHEET__; });
return [{ json: { usageSheetCreated: made } }];"""


def _usage_sheet(name, operation, pos, columns=None):
    node = sheetsNode(name, operation, pos, QUEUE_SPREADSHEET, columns, sheet=USAGE_SHEET, header_row=USAGE_HEADER_ROW)
    node.pop('alwaysOutputData', None)
    return node


def usageNodes():
    """API使用量のノードとつながり。返り値：(nodes, connections, Build→Stamp→API の置き換え)。"""
    nodes, conn = [], {}
    ensure = sheetsNode('Usage: Ensure Sheet', 'append', [60, 120], QUEUE_SPREADSHEET, (), sheet=USAGE_SHEET,
                        header_row=USAGE_HEADER_ROW)
    for key in ('sheetName', 'columns', 'options'):
        ensure['parameters'].pop(key, None)
    ensure['parameters'].update({'operation': 'create', 'title': USAGE_SHEET, 'options': {}})
    ensure['alwaysOutputData'] = True  # 前からあれば 0 件。それでも次へ進む
    # シートを作れなくても、記事の処理は止めない（PR #82 レビュー）。失敗の item は Usage: Sheet Ready が「作っていない」とし、
    # 見出しを書かずにキューへ進む。そのあとの Usage: Write も、失敗しても次へ進む。
    ensure.update({'onError': 'continueRegularOutput', 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000})
    header = code('Usage: Header', 'return [{ json: ' + js_str({c: '' for c in USAGE_COLUMNS}) + ' }];', [500, 0])
    header['executeOnce'] = True
    header_write = _usage_sheet('Usage: Header Write', 'append', [700, 0], {})
    # 作ったばかりの空のシートに、item の項目名を 1 行目として書く（前からあるシートには書かない）。
    header_write['parameters']['columns'] = {'mappingMode': 'autoMapInputData', 'value': {}, 'matchingColumns': [],
                                             'schema': []}
    # 見出しを書けなくても、記事の処理は止めない（PR #82 レビュー）。
    header_write.update({'onError': 'continueRegularOutput', 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000})
    nodes += [ensure,
              code('Usage: Sheet Ready', USAGE_SHEET_READY_JS.replace('__SHEET__', js_str(USAGE_SHEET)), [160, 120]),
              {'parameters': {'conditions': {
                  'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                  'conditions': [{'id': 'usage-new', 'leftValue': '={{ $json.usageSheetCreated }}', 'rightValue': '',
                                  'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
                  'combinator': 'and'}, 'options': {}},
               'id': uid(), 'name': 'Usage: New Sheet?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2,
               'position': [300, 120]},
              header, header_write]
    conn.update({'Usage: Ensure Sheet': {'main': [[cn('Usage: Sheet Ready')]]},
                 'Usage: Sheet Ready': {'main': [[cn('Usage: New Sheet?')]]},
                 'Usage: New Sheet?': {'main': [[cn('Usage: Header')], [cn('Queue: Read')]]},
                 'Usage: Header': {'main': [[cn('Usage: Header Write')]]},
                 'Usage: Header Write': {'main': [[cn('Queue: Read')]]}})
    columns = {c: '={{ $json[' + js_str(c) + '] }}' for c in USAGE_COLUMNS}
    for step, build, api in USAGE_CALLS:
        stamp, row, write = 'Usage: Stamp ' + step, 'Usage: Row ' + step, 'Usage: Write ' + step
        nodes.append(code(stamp, USAGE_STAMP_JS, [0, 0]))
        nodes.append(code(row, USAGE_ROW_JS.replace('__STEP__', js_str(step)).replace('__STAMP__', js_str(stamp)),
                          [0, 0]))
        w = _usage_sheet(write, 'append', [0, 0], columns)
        # 記録の失敗で記事を止めない。Sheets の一時的な失敗は待ってやり直す。
        w.update({'onError': 'continueRegularOutput', 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000})
        nodes.append(w)
        nodes.append(code('Usage: Pass ' + step, USAGE_PASS_JS.replace('__API__', js_str(api)), [0, 0]))
        conn[row] = {'main': [[cn(write)]]}
        conn[write] = {'main': [[cn('Usage: Pass ' + step)]]}
    return nodes, conn


def applyUsage(nodes, conn):
    """API使用量のノードを、本番の Workflow に入れる。

    Build → Usage: Stamp → API → Usage: Row → Usage: Write → Usage: Pass → （もとの次のノード）。
    枝を分けずに一続きにする（先のないノードを作らない。test_no_node_can_stop_the_run_silently）。
    Usage: Pass は API の出力をそのまま渡すので、Parse などが受け取る item は変わらない。
    """
    by_name = {n['name']: n for n in nodes}
    usage, usage_conn = usageNodes()
    conn['Webhook'] = {'main': [[cn('Usage: Ensure Sheet')]]}
    for step, build, api in USAGE_CALLS:
        stamp, row, write = 'Usage: Stamp ' + step, 'Usage: Row ' + step, 'Usage: Write ' + step
        targets = conn[build]['main'][0]
        if [t['node'] for t in targets] != [api]:
            raise ValueError(build + ' must connect only to ' + api)
        conn[build]['main'][0] = [cn(stamp)]
        conn[stamp] = {'main': [[cn(api)]]}
        after = conn[api]['main']
        conn[api] = {'main': [[cn(row)]]}
        conn['Usage: Pass ' + step] = {'main': after}
        x, y = by_name[api]['position']
        u = {n['name']: n for n in usage}
        u[stamp]['position'] = [x - 110, y - 160]
        u[row]['position'] = [x, y - 260]
        u[write]['position'] = [x + 110, y - 260]
        u['Usage: Pass ' + step]['position'] = [x + 220, y - 160]
    nodes.extend(usage)
    conn.update(usage_conn)
    return nodes, conn


def buildWorkflow():
    """全フェーズを1ワークフローに統合。外部ファイル・サブワークフロー不要。"""
    wp_nodes, wp_conn = wpSiteNodes(load_sites(), 6160, 760)

    # ── Phase 0: Entry & Loop ──────────────────────────────────────────
    p0_nodes = [
        sticky('WF README',
               '## SHAMAN\nSynthetic Hack of Akashic Memory & Authoring Node\nv1.3 (2026-09-12)\n\n全フェーズを1ワークフローに統合。\n外部ファイル・サブワークフロー不要。\nP6画像生成: OpenAI images/edits（IMAGE_PROVIDER=gemini で切り戻し可）\n\nP1:Research → P2:Outline → P3:Content → P4:Strengthen → P5:Meta → P6:Images(opt) → P7:WP → Cleanup',
               [-20, -300], 7, 640, 280),
        runWebhookNode('blog-pipeline-upload', [0, 320], name='Webhook'),
        sheetsNode('Queue: Read', 'read', [180, 320], QUEUE_SPREADSHEET),
        code('Queue: Parse', '\n'.join([
            '// rowIndex はスプレッドシートの実際の行番号（row_number）。書き戻しの照合に使う。',
            'var SITES = ' + js_str(load_sites()) + ';',
            '// サイト登録シートのSITE_IDのみを受付する。Credentialの内部エラーに依存しない。',
            "var registry = new Set(items.map(it => String((it.json || {}).SITE_ID || '').trim().toLowerCase()).filter(Boolean));",
            "if (!registry.size) throw new Error('WP_SITE_REGISTRY_EMPTY: サイト登録シートが空です');",
            "var queue = $('Queue: Read').all();",
            'var parsed = queue',
            '  .map(function(it) { return it.json || {}; })',
            '  .filter(function(r) {',
            '    return r.row_number != null && Object.keys(r).some(function(k) {',
            "      return k !== 'row_number' && r[k] !== null && r[k] !== undefined && r[k] !== '';",
            '    });',
            '  })',
            '  .map(function(r) {',
            '    var obj = Object.assign({}, r);',
            '    var row = Number(obj.row_number);',
            '    delete obj.row_number;',
            "    var siteId = String(obj['SITE_ID\\n（必須）\\nサイト識別子'] || '').trim().toLowerCase();",
            '// 未登録サイトはドメインから URL を決める。URLをサイト設定から転送しない（認証情報の送信先固定）。',
            '    var isDomain = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(siteId);',
            '    var overrides = Object.prototype.hasOwnProperty.call(SITES, siteId) ? SITES[siteId] : {};',
            "    var siteConfig = isDomain && registry.has(siteId) ? Object.assign({}, overrides, { WP_URL: 'https://' + siteId }) : null;",
            '    return { json: { ...obj, siteId: siteId, siteConfig: siteConfig, rowIndex: row } };',
            '  });',
            "if (parsed.length === 0 && $('Daily Schedule').isExecuted === true)",
            "  return [{ json: { shamanScheduledNoWork: true } }];",
            'return parsed;',
        ]), [310, 320]),
        code('Filter Rows', '\n'.join(RECOVER_JS + [
            '// Pass rows with valid SITE_ID, STATUS=待機中 or 画像エラー (or the recovered row), and non-empty KEYWORD',
            'var passed = [];',
            'var skipped = [];',
            'for (var i = 0; i < items.length; i++) {',
            '  var row = items[i].json;',
            "  var status = String(row['STATUS\\n【自動】'] || '');",
            "  var keyword = String(row['KEYWORD\\n（必須）\\nフォーカスキーワード'] || '').trim();",
            '  var reason = null;',
            '  if (!row.siteConfig) {',
            "    reason = row.siteId ? 'SITE_ID \"' + row.siteId + '\" はサイト登録シートに未登録、またはドメイン名が不正です' : 'SITE_IDが空';",
            "  } else if (!claimable(row)) {",
            "    reason = RECOVER !== null ? '再開の対象（STATUS=処理中・実行ID ' + RECOVER + '・WP_POST_ID 空）ではない'",
            "      : 'STATUS=\"' + status + '\"（待機中/画像エラー 以外）';",
            "  } else if (keyword === '') {",
            "    reason = 'KEYWORDが空';",
            '  }',
            '  if (reason) {',
            "    skipped.push('行' + row.rowIndex + ': ' + reason);",
            '  } else {',
            '    passed.push(items[i]);',
            '  }',
            '}',
            "var scheduled = $('Daily Schedule').isExecuted === true;",
            "if (scheduled && passed.length === 0) return [{ json: { shamanScheduledNoWork: true } }];",
            "if (scheduled) passed = passed.slice(0, 1);",
            'if (passed.length === 0) {',
            "  var msg = '❌ Filter Rows: 処理対象が0件\\n'",
            "    + '読み取り行数: ' + items.length",
            "    + (skipped.length > 0 ? '\\n\\nスキップ理由:\\n' + skipped.join('\\n') : '');",
            '  // Discord には、Error Trigger → Discord: Notify Error がこのエラー文を送る。',
            '  throw new Error(msg);',
            '}',
            'if (skipped.length > 0) {',
            "  skipped.forEach(function(s) { console.warn('[SKIP] ' + s); });",
            '}',
            'return passed;',
        ]), [440, 320]),
        {'parameters': {'options': {}}, 'id': uid(), 'name': 'Loop Over Items', 'type': 'n8n-nodes-base.splitInBatches', 'typeVersion': 3, 'position': [660, 320]},
        code('Map Variables', '\n'.join([
            'var item = items[0].json;',
            'var sc = item.siteConfig || {};',
            'return [{ json: {',
            "  KEYWORD: item['KEYWORD\\n（必須）\\nフォーカスキーワード'] || '',",
            "  OPTION1: item['OPTION1\\n（必須）\\n記事の結論・スタンス'] || '',",
            "  AUTHOR: item['AUTHOR\\n（省略可）\\n執筆者名'] || sc.AUTHOR || '',",
            "  SITENAME: item['SITENAME\\n（省略可）\\nサイト名'] || sc.SITENAME || '',",
            "  BASEURL: item['BASEURL\\n（省略可）\\nサイトURL'] || sc.BASEURL || '',",
            "  FIRSTPERSON: item['FIRSTPERSON\\n（省略可）\\n一人称'] || sc.FIRSTPERSON || '',",
            "  IMAGE_TRIGGER: String(item['IMAGE_TRIGGER\\n（省略可）\\nTRUE/FALSE'] || 'FALSE').toUpperCase(),",
            "  STATUS: String(item['STATUS\\n【自動】'] || ''),",
            "  siteId: item.siteId || '',",
            "  wpUrl: 'https://' + item.siteId,",
            "  imgFolder: String(sc.IMG_FOLDER || item.siteId || ''),",
            "  metaKey: sc.META_KEY || 'st_description',",
            "  MODEL_PRO: " + js_str(MODEL_PRO) + ",",
            "  MODEL_FLASH_LITE: " + js_str(MODEL_FLASH_LITE) + ",",
            "  MODEL_OUTLINE: " + js_str(MODEL_OUTLINE) + ",",
            "  MODEL_SECTION_ANALYSIS: " + js_str(MODEL_SECTION_ANALYSIS) + ",",
            "  MODEL_IMAGE: " + js_str(MODEL_IMAGE) + ",",
            "  MODEL_IMAGE_OPENAI: " + js_str(MODEL_IMAGE_OPENAI) + ",",
            "  IMAGE_PROVIDER: " + js_str(IMAGE_PROVIDER) + ",",
            "  IMAGE_QUALITY: " + js_str(IMAGE_QUALITY) + ",",
            "  MODEL_CLAUDE: " + js_str(MODEL_CLAUDE) + ",",
            '  pipelineStartedAt: new Date().toISOString(),',
            '  rowIndex: item.rowIndex ?? null',
            '} }];'
        ]), [880, 400]),
        # ── 行の確保：読み直して照合し、STATUS=処理中 と実行ID を書き、読み直して自分のものか確かめる ──
        sheetsNode('Queue: Recheck', 'read', [880, 560], QUEUE_SPREADSHEET),
        code('Queue: Check Claim', '\n'.join(QUEUE_ROW_JS + RECOVER_JS + [
            "var mv = $('Map Variables').first().json;",
            'var r = queueRow(mv.rowIndex);',
            'checkSameRow(r, mv);',
            "var status = String(r[STATUS_COL] || '');",
            'if (!claimable(r)) {',
            "  throw new Error('キューの行 ' + mv.rowIndex + ' は STATUS「' + status + '」のため処理しません（別の実行が処理中か、人が変えた）');",
            '}',
            'return [{ json: { row: mv.rowIndex, runId: String($execution.id), startedAt: mv.pipelineStartedAt } }];',
        ]), [990, 560]),
        sheetsNode('Queue: Claim', 'update', [1100, 560], QUEUE_SPREADSHEET, {
            'row_number': '={{ $json.row }}',
            'STATUS\n【自動】': '処理中',
            '実行ID\n【自動】': '={{ $json.runId }}',
            '開始日時\n【自動】': '={{ $json.startedAt }}',
        }),
        sheetsUpdateCheckNode('Queue: Claim Check', 'Queue: Claim', [1155, 720]),
        sheetsNode('Queue: Reread', 'read', [1210, 560], QUEUE_SPREADSHEET),
        code('Queue: Confirm Claim', '\n'.join(QUEUE_ROW_JS + [
            "var mv = $('Map Variables').first().json;",
            'var r = queueRow(mv.rowIndex);',
            'checkSameRow(r, mv);',
            "if (!isOurs(r)) throw new Error('キューの行 ' + mv.rowIndex + ' を確保できませんでした（別の実行が確保した）');",
            'return [{ json: mv }];',
        ]), [1320, 560]),
        {
            'parameters': {
                'conditions': {
                    'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                    'conditions': [{'id': 'art-status', 'leftValue': '={{ $json.STATUS }}', 'rightValue': '画像エラー', 'operator': {'type': 'string', 'operation': 'equals'}}],
                    'combinator': 'and'
                },
                'options': {}
            },
            'id': uid(), 'name': 'Check Article Status', 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': [1100, 400]
        },
        # 途中保存を読む。ファイルがない・読めない・別の行のものは、Draft Unusable で STATUS を「エラー」にする。
        {'parameters': {'fileSelector': '=' + DRAFT_PREFIX + '{{ $json.rowIndex }}.json', 'options': {'dataPropertyName': 'data'}},
         'id': uid(), 'name': 'Read Draft', 'type': 'n8n-nodes-base.readWriteFile', 'typeVersion': 1, 'position': [1100, 560],
         # ファイルがないと 0 件になるので、空の item を Draft From File へ渡してエラー出力にする。
         'alwaysOutputData': True, 'onError': 'continueErrorOutput'},
        {'parameters': {'operation': 'text', 'binaryPropertyName': 'data', 'options': {}},
         'id': uid(), 'name': 'Draft From File', 'type': 'n8n-nodes-base.extractFromFile', 'typeVersion': 1,
         'position': [1320, 560], 'onError': 'continueErrorOutput'},
        dict(code('Load Saved Article', '\n'.join([
            "var data = $('Map Variables').first().json;",
            "var text = items[0] && items[0].json && items[0].json.data;",
            "if (typeof text !== 'string' || !text) throw new Error('途中保存の中身が空です');",
            'var articleData;',
            "try { articleData = JSON.parse(text); } catch (e) { throw new Error('途中保存を JSON として読めません'); }",
            '// 下書きは行番号で探すので、別の行の古い下書きを使わないよう SITE_ID・KEYWORD を照合する（計画書 C-7）。',
            "if (String(articleData.siteId || '') !== String(data.siteId || '')",
            "    || String(articleData.KEYWORD || '').trim() !== String(data.KEYWORD || '').trim()) {",
            "  throw new Error('途中保存の SITE_ID・KEYWORD が行と一致しません');",
            '}',
            'return [{ json: Object.assign({}, data, articleData, {',
            "  imageRetryCount: 0, p6Status: null, imageRetryNeeded: false, IMAGE_TRIGGER: 'TRUE'",
            '}) }];'
        ]), [1540, 560]), onError='continueErrorOutput'),
        code('Draft Unusable', '\n'.join([
            '// 途中保存が使えない。画像だけの再開はできないので、STATUS を「エラー」にして書き戻す（「処理中」のまま残さない）。',
            "var data = $('Map Variables').first().json;",
            ERROR_REASON_JS,
            'return [{ json: Object.assign({}, data, {',
            "  p6Status: 'draft_load_error', wpPostId: null, wpPostLink: null,",
            "  wpError: '途中保存を使えません: ' + String(reason).substring(0, 300) + '（STATUS を「待機中」にすると最初から作り直す）'",
            '}) }];',
        ]), [1760, 720]),
    ]

    # ── Phase 1: Research ─────────────────────────────────────────────
    p1_nodes = p1Nodes()

    # ── Phase 2: Outline ──────────────────────────────────────────────
    p2_nodes = p2Nodes()

    # ── Phase 3: Content ──────────────────────────────────────────────
    p3_nodes = p3Nodes()

    # ── Phase 4: Strengthen (Claude Sonnet 4.6) ─────────────────────
    p4_nodes = p4Nodes()

    # ── Phase 5: Meta Description ─────────────────────────────────────
    p5_nodes = p5Nodes()

    # ── Phase 6: Images (conditional) ─────────────────────────────────
    p6_nodes = [
        {
            'parameters': {
                'conditions': {
                    'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                    'conditions': [{'id': 'cond-p6', 'leftValue': '={{ $json.IMAGE_TRIGGER }}', 'rightValue': 'TRUE', 'operator': {'type': 'string', 'operation': 'equals'}}],
                    'combinator': 'and'
                },
                'options': {}
            },
            'id': uid(), 'name': 'Check P6 Mode', 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': [4400, 400]
        },
        # TRUE branch — image generation
        *p6ReferenceImageNodes(4620, 180),
        *p6AnalysisNodes(),
        _if_not_empty('P6: Plan Error?', 'p6Status', [5500, 180], 'p6-plan-error'),
        *p6ImageNodes(5500, 380),
        *wpUploadNodes(5940, 180),
        *wp_nodes,
        code('Build Updated HTML', _build_updated_html_js(), [6160, 180]),
        # FALSE branch: Check P6 Mode → Build WP Post (direct, no skip node needed)
        # ── P6 error gates ────────────────────────────────────────────────
        # n8n's filter operation is 'notEmpty'. An unknown name such as 'isNotEmpty'
        # does not raise — it evaluates as false, which silently disables the gate.
        {
            'parameters': {
                'conditions': {
                    'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                    'conditions': [{'id': 'p6err', 'leftValue': '={{ $json.p6Status }}', 'rightValue': '',
                                    'operator': {'type': 'string', 'operation': 'notEmpty', 'singleValue': True}}],
                    'combinator': 'and'
                },
                'options': {}
            },
            'id': uid(), 'name': 'P6: Error Gate?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': [5720, 180]
        },
        {
            'parameters': {
                'conditions': {
                    'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
                    'conditions': [{'id': 'uperr', 'leftValue': '={{ $json.batchUploadError }}', 'rightValue': '',
                                    'operator': {'type': 'string', 'operation': 'notEmpty', 'singleValue': True}}],
                    'combinator': 'and'
                },
                'options': {}
            },
            'id': uid(), 'name': 'WP: Upload Error?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': [5940, 380]
        },
        # P6: Upload Abort — WP upload failure hard stop.
        # Saves the draft for manual recovery (set STATUS to 画像エラー), then P6: Abort Draft Saved /
        # Failed throws to mark the n8n execution FAILED. Error Trigger fires → Discord error notification.
        code('P6: Upload Abort', '\n'.join(draft_builder_js("'upload_error'") + [
            "var abortMessage = '[P6] WP image upload failed for \"' + (data.KEYWORD || '?') + '\" | ' + (data.batchUploadError || data.uploadError || 'unknown error');",
            'return [{ json: Object.assign({}, data, { draftFile: draftFile, draftJson: JSON.stringify(articleData, null, 2), abortMessage: abortMessage }) }];',
        ]), [5940, 560]),
        *draftWriteNodes('P6: Upload Abort', 'P6: Abort ', [6160, 560]),
        code('P6: Abort Draft Saved', '\n'.join([
            "throw new Error($('P6: Upload Abort').first().json.abortMessage + '（途中保存: ' + $('P6: Upload Abort').first().json.draftFile + '）');",
        ]), [6600, 480]),
        code('P6: Abort Draft Failed', '\n'.join([
            ERROR_REASON_JS,
            "throw new Error($('P6: Upload Abort').first().json.abortMessage + '（途中保存にも失敗: ' + String(reason).substring(0, 300) + '）');",
        ]), [6600, 640]),
        code('P6: Save Article', '\n'.join(draft_builder_js('data.p6Status') + [
            'return [{ json: Object.assign({}, data, { draftFile: draftFile, draftJson: JSON.stringify(articleData, null, 2) }) }];',
        ]), [5940, 0]),
        *draftWriteNodes('P6: Save Article', 'P6: ', [6160, 0]),
        code('P6: Draft Saved', '\n'.join([
            '// 保存できたときだけ「画像エラー」にする（次の実行が途中保存から画像だけを作り直す）。',
            "var data = Object.assign({}, $('P6: Save Article').first().json);",
            'delete data.draftJson;',
            'return [{ json: Object.assign(data, {',
            "  p6Status: 'image_error', articleDraftFile: data.draftFile,",
            '  wpPostId: null, wpPostLink: null',
            '}) }];'
        ]), [6600, -80]),
        code('P6: Draft Save Failed', '\n'.join([
            '// 保存できなかった。「画像エラー」にすると再開で読めないので、「エラー」にする。',
            "var data = Object.assign({}, $('P6: Save Article').first().json);",
            'delete data.draftJson;',
            ERROR_REASON_JS,
            'return [{ json: Object.assign(data, {',
            "  p6Status: 'draft_save_error', wpPostId: null, wpPostLink: null,",
            "  wpError: '画像の生成に失敗し（' + (data.p6Status || '不明') + '）、途中保存にも失敗しました: ' + String(reason).substring(0, 300)",
            '}) }];'
        ]), [6600, 80]),
    ]

    # ── Phase 7: WordPress ────────────────────────────────────────────
    p7_nodes = [
        code('Build WP Post', '\n'.join([
            'var data = items[0].json;',
            "var finalHtml = (data.updatedHtml && data.updatedHtml.length > 0) ? data.updatedHtml : data.articleHtml;",
            "if (!finalHtml || finalHtml.length === 0) throw new Error('No HTML content to publish.');",
            "var articleTitle = data.articleTitle || data.KEYWORD || 'Blog Article';",
            "var slug = data.slug || 'article-' + Date.now();",
            "var metaDescription = data.metaDescription || '';",
            "var metaKey = data.metaKey || 'st_description';",
            'var metaObj = {}; metaObj[metaKey] = metaDescription;',
            'var postBody = JSON.stringify({',
            "  title: articleTitle, content: finalHtml, status: 'draft', slug: slug,",
            '  meta: metaObj',
            '});',
            '// 共通 Basic Auth Credential が SITE_ID に応じて認証値を選ぶ。',
            "return [{ json: { ...data, finalHtml: finalHtml, wpPostBody: postBody } }];"
        ]), [6600, 400]),
        code('Parse WP Response', '\n'.join([
            'var response = $input.first().json;',
            "var _ref = $('Build WP Post').first().json;",
            "var config = Object.assign({}, _ref); delete config.wpPostBody;",
            'var postId = response.id || null;',
            "var postLink = response.link || (response.guid && response.guid.rendered) || null;",
            "var wpError = postId ? null : ('wp_post_failed: ' + JSON.stringify(response).substring(0, 300));",
            "if (wpError) console.warn('WP post failed: ' + wpError);",
            'return [{ json: { ...config, wpPostId: postId, wpPostLink: postLink, wpError: wpError } }];'
        ]), [7040, 400]),
    ]

    # ── Phase 8: Cleanup & Discord ────────────────────────────────────
    p8_nodes = [
        code('Cleanup', '\n'.join([
            "var mapVars = $('Map Variables').first().json;",
            'var rowIndex = mapVars.rowIndex;',
            'var data = items[0].json;',
            "var excelStatus = data.wpPostId ? '完了' : (data.p6Status === 'image_error' ? '画像エラー' : 'エラー');",
            "var completedAt = new Date().toISOString();",
            "// ── QUALITY算出 ──",
            "var finalHtml = data.updatedHtml || data.articleHtml || '';",
            "var textLength = finalHtml.replace(/<[^>]+>/g, '').length;",
            "var imageCount = (finalHtml.match(/<img /g) || []).length;",
            'var textScore = Math.min(textLength / 12000, 1);',
            'var imageScore = Math.min(imageCount / 7, 1);',
            'var qualityPct = Math.round((textScore * 0.7 + imageScore * 0.3) * 100);',
            "var qualityStr = qualityPct + '%（' + textLength + '字/' + imageCount + '枚）' + (data.imageReferenceNote ? ' / ' + data.imageReferenceNote : '');",
            "// ── エラー内容 ──",
            "var errors = [data.p1ResearchStatus, data.p4QualityIssues, data.p6Status, data.uploadError, data.batchUploadError, data.wpError].filter(function(e) { return e; });",
            "var errorStr = errors.join('; ');",
            "// ── キューに書き戻す値（Queue: Check Final → Queue: Update） ──",
            "if (rowIndex == null) throw new Error('書き戻す行番号がありません');",
            '// 6 列すべてを送る（DRY_RUN で QUALITY を書かないときも、空として書く）。',
            'var values = ' + js_str({c: '' for c in QUEUE_WRITABLE}) + ';',
            '// 制御文字を除き、上限で切る。切って残った片方だけのサロゲートも除く。',
            "function writeCell(col, value) {",
            "  var v = String(value == null ? '' : value).replace(/[\\u0000-\\u0008\\u000b\\u000c\\u000e-\\u001f]/g, '');",
            "  values[col] = v.substring(0, " + str(QUEUE_MAX_VALUE_LEN) + ").replace(/[\\ud800-\\udbff](?![\\udc00-\\udfff])|(?<![\\ud800-\\udbff])[\\udc00-\\udfff]/g, '');",
            "}",
            "writeCell('STATUS', excelStatus);",
            "writeCell('WP_POST_ID', data.wpPostId ? String(data.wpPostId) : '');",
            "writeCell('QUALITY', qualityStr);",
            "writeCell('開始日時', data.pipelineStartedAt || '');",
            "writeCell('完了日時', completedAt);",
            "writeCell('エラー内容', errorStr);",
            'return [{ json: { ...data, cleanup: "success", updatedRowIndex: rowIndex, timestamp: completedAt, queueValues: values } }];'
        ]), [7260, 400]),
        # ── 書き戻し：読み直して、まだ自分が確保している行にだけ書く ──
        sheetsNode('Queue: Final Recheck', 'read', [7260, 240], QUEUE_SPREADSHEET),
        code('Queue: Check Final', '\n'.join(QUEUE_ROW_JS + [
            "var mv = $('Map Variables').first().json;",
            "var done = $('Cleanup').first().json;",
            'try {',
            '  var r = queueRow(mv.rowIndex);',
            '  checkSameRow(r, mv);',
            "  if (!isOurs(r)) throw new Error('キューの行 ' + mv.rowIndex + ' の確保が外れています');",
            '} catch (e) {',
            '  // 投稿は済んでいることがある。STATUS は「処理中」のまま残るので、次の実行は同じ行を拾わない。',
            "  throw new Error(e.message + ' / WP 投稿 ID: ' + (done.wpPostId || 'なし') + '（書き戻していない。WordPress を確かめてから手で直す）');",
            '}',
            'return [{ json: { row: mv.rowIndex, values: done.queueValues } }];',
        ]), [7370, 240]),
        dict(sheetsNode('Queue: Update', 'update', [7480, 240], QUEUE_SPREADSHEET,
                        dict([('row_number', '={{ $json.row }}')]
                             + [(c + '\n【自動】', '={{ $json.values["' + c + '"] }}') for c in QUEUE_WRITABLE])),
             # 失敗はエラーの item にして、Queue: Update Check で止める（エラー出力は使わない。alwaysOutputData は
             # 出力 0 が空のときに空の item を足すので、エラー出力と両方に流れてしまうため）。
             onError='continueRegularOutput'),
        # 書き戻しの失敗・行が見つからないとき：投稿は済んでいるので、WP 投稿 ID を出して止める。
        sheetsUpdateCheckNode('Queue: Update Check', 'Queue: Update', [7590, 80],
                              suffix_js="' / WP 投稿 ID: ' + ($('Cleanup').first().json.wpPostId || 'なし')"
                                        " + '（STATUS は「処理中」のまま。WordPress を確かめてから手で直す）'"),
        code('Queue: Done', '\n'.join([
            "var data = Object.assign({}, $('Cleanup').first().json);",
            'delete data.queueValues;',
            'return [{ json: data }];',
        ]), [7590, 240]),
        discordNode('Discord: Notify Success', [7480, 400],
                    '={{ ((function(s,u,w,q,r){ var hasErr = !!s || !!u || !!w || !!q || !!r; '
                    'return (hasErr ? "⚠️ 記事生成完了（エラーあり）" : "✅ 記事生成完了")'
                    ' + "\\nタイトル: " + ($json.articleTitle || $json.KEYWORD || "不明")'
                    ' + "\\nURL: " + ($json.wpPostLink || "未設定")'
                    ' + "\\nキーワード: " + ($json.KEYWORD || "不明")'
                     ' + ($json.imageReferenceNote ? "\\n⚠️ " + $json.imageReferenceNote : "")'
                    ' + (s ? "\\n⚠️ 画像ステータス: " + s : "") + (u ? "\\n⚠️ アップロードエラー: " + u : "")'
                    ' + (w ? "\\n⚠️ WordPress: " + w : "") + (q ? "\\n⚠️ P4品質: " + q : "")'
                    ' + (r ? "\\n⚠️ " + r : ""); })'
                    '($json.p6Status, $json.uploadError, $json.wpError, $json.p4QualityIssues, $json.p1ResearchStatus)).substring(0, 1900) }}',
                    continue_on_fail=True),
        # Error handler nodes
        {
            'parameters': {},
            'id': uid(), 'name': 'Error Trigger', 'type': 'n8n-nodes-base.errorTrigger', 'typeVersion': 1, 'position': [220, 560]
        },
        discordNode('Discord: Notify Error', [440, 560],
                    '={{ "❌ 記事生成エラー\\nワークフロー: " + ($json.workflow && $json.workflow.name || "不明")'
                    ' + "\\nノード: " + ($json.execution && $json.execution.lastNodeExecuted || "不明")'
                    ' + "\\nエラー: " + String($json.execution && $json.execution.error && $json.execution.error.message || "不明")'
                    '.replace(/(key=|Bearer\\s+)[^&\\s"\',}]+/gi, "$1***").substring(0, 1500)'
                    ' + "\\n実行ID: " + ($json.execution && $json.execution.id || "不明") }}'),
        # ── 実行結果（Actions の run-shaman-n8n.yml が読む。DOCS/RUN_FROM_ACTIONS.md） ──
        # ループが終わったら、キューを読み直し、この実行が確保した行の STATUS で成否を決める（HTTP 200 だけで成功にしない）。
        dict(sheetsNode('Queue: Result Read', 'read', [880, 120], QUEUE_SPREADSHEET), executeOnce=True),
        code('Run Result', RUN_RESULT_JS, [1100, 120]),
        runCheckNode([1320, 120]),
    ]

    nodes = p0_nodes + p1_nodes + p2_nodes + p3_nodes + p4_nodes + p5_nodes + p6_nodes + p7_nodes + p8_nodes

    conn = {
        'Webhook':                    {'main': [[cn('Queue: Read')]]},
        'Queue: Read':                {'main': [[cn('Sites: Read Registry')]]},
        'Sites: Read Registry':       {'main': [[cn('Queue: Parse')]]},
        'Queue: Parse':               {'main': [[cn('Filter Rows')]]},
        'Filter Rows':                {'main': [[cn('Loop Over Items')]]},
        'Loop Over Items':            {'main': [[cn('Queue: Result Read')], [cn('Map Variables')]]},
        'Queue: Result Read':         {'main': [[cn('Run Result')]]},
        'Run Result':                 {'main': [[cn('Run: Check')]]},
        'Map Variables':              {'main': [[cn('Queue: Recheck')]]},
        'Queue: Recheck':             {'main': [[cn('Queue: Check Claim')]]},
        'Queue: Check Claim':         {'main': [[cn('Queue: Claim')]]},
        'Queue: Claim':               {'main': [[cn('Queue: Claim Check')]]},
        'Queue: Claim Check':         {'main': [[cn('Queue: Reread')]]},
        'Queue: Reread':              {'main': [[cn('Queue: Confirm Claim')]]},
        'Queue: Confirm Claim':       {'main': [[cn('Check Article Status')]]},
        'Check Article Status':       {'main': [[cn('Read Draft')], [cn('Build P1 Request')]]},
        'Read Draft':                 {'main': [[cn('Draft From File')], [cn('Draft Unusable')]]},
        'Draft From File':            {'main': [[cn('Load Saved Article')], [cn('Draft Unusable')]]},
        'Load Saved Article':         {'main': [[cn('Check P6 Mode')], [cn('Draft Unusable')]]},
        'Draft Unusable':             {'main': [[cn('Cleanup')]]},
        'Build P1 Request':           {'main': [[cn('Gemini: Research')]]},
        'Gemini: Research':           {'main': [[cn('Parse Research')]]},
        'Parse Research':             {'main': [[cn('P1: Retry?')]]},
        'P1: Retry?':                 {'main': [[cn('Build P1 Request')], [cn('Build P2 Request')]]},
        'Build P2 Request':           {'main': [[cn('Gemini: Generate Outline')]]},
        'Gemini: Generate Outline':   {'main': [[cn('Parse Outline')]]},
        'Parse Outline':              {'main': [[cn('P2: Retry?')]]},
        'P2: Retry?':                 {'main': [[cn('Build P2 Request')], [cn('Build P3 Request')]]},
        'Build P3 Request':           {'main': [[cn('Gemini: Generate Content')]]},
        'Gemini: Generate Content':   {'main': [[cn('Parse Content')]]},
        'Parse Content':              {'main': [[cn('Build P4 Request')]]},
        'Build P4 Request':           {'main': [[cn('Claude: Strengthen Content')]]},
        'Claude: Strengthen Content': {'main': [[cn('Parse Strengthened')]]},
        'Parse Strengthened':         {'main': [[cn('P4: Retry?')]]},
        'P4: Retry?':                 {'main': [[cn('Build P4 Request')], [cn('Build P5 Request')]]},
        'Build P5 Request':           {'main': [[cn('Gemini: Generate Meta')]]},
        'Gemini: Generate Meta':      {'main': [[cn('Parse Meta + Extract Title')]]},
        'Parse Meta + Extract Title': {'main': [[cn('Check P6 Mode')]]},
        'Check P6 Mode':              {'main': [[cn('P6: Find IMG Root')], [cn('Build WP Post')]]},
        'P6: Find IMG Root':        {'main': [[cn('P6: Verify IMG Root')]]},
        'P6: Verify IMG Root':      {'main': [[cn('P6: Find Image Folder')]]},
        'P6: Find Image Folder':      {'main': [[cn('P6: Check Image Folder')]]},
        'P6: Check Image Folder':     {'main': [[cn('P6: Has Image Folder?')]]},
        'P6: Has Image Folder?':      {'main': [[cn('P6: List Reference Images')], [cn('Read Reference Images')]]},
        'P6: List Reference Images':  {'main': [[cn('P6: Check Reference Images')]]},
        'P6: Check Reference Images': {'main': [[cn('P6: Has Reference Images?')]]},
        'P6: Has Reference Images?':  {'main': [[cn('P6: Download Reference Image')], [cn('Read Reference Images')]]},
        'P6: Download Reference Image': {'main': [[cn('Read Reference Images')]]},
        'Read Reference Images':      {'main': [[cn('Build Section Analysis Request')]]},
        'Build Section Analysis Request': {'main': [[cn('Gemini: Analyze Sections')]]},
        'Gemini: Analyze Sections':   {'main': [[cn('Parse Section Plan')]]},
        'Parse Section Plan':         {'main': [[cn('P6: Plan Error?')]]},
        'P6: Plan Error?':            {'main': [[cn('P6: Error Gate?')], [cn('Build Image Requests')]]},
        'Build Image Requests':       {'main': [[cn('P6: Generate Image')]]},
        'P6: Generate Image':         {'main': [[cn('Collect Generated Images')]]},
        'Collect Generated Images':   {'main': [[cn('P6: Retry Images?')]]},
        'P6: Retry Images?':          {'main': [[cn('P6: Retry Wait')], [cn('P6: Images Ready')]]},
        'P6: Retry Wait':             {'main': [[cn('Build Image Requests')]]},
        'P6: Images Ready':           {'main': [[cn('P6: Error Gate?')]]},
        'P6: Error Gate?':            {'main': [[cn('P6: Save Article')], [cn('WP: Prepare Media')]]},
        'WP: Prepare Media':          {'main': [[cn('WP: Validate Upload Target')]]},
        'WP: Collect Uploads':        {'main': [[cn('WP: Upload Error?')]]},
        'WP: Upload Error?':          {'main': [[cn('P6: Upload Abort')], [cn('Build Updated HTML')]]},
        'P6: Upload Abort':           {'main': [[cn('P6: Abort Draft To File')]]},
        'P6: Abort Draft To File':    {'main': [[cn('P6: Abort Write Draft')], [cn('P6: Abort Draft Failed')]]},
        'P6: Abort Write Draft':      {'main': [[cn('P6: Abort Draft Saved')], [cn('P6: Abort Draft Failed')]]},
        'P6: Save Article':           {'main': [[cn('P6: Draft To File')]]},
        'P6: Draft To File':          {'main': [[cn('P6: Write Draft')], [cn('P6: Draft Save Failed')]]},
        'P6: Write Draft':            {'main': [[cn('P6: Draft Saved')], [cn('P6: Draft Save Failed')]]},
        'P6: Draft Saved':            {'main': [[cn('Cleanup')]]},
        'P6: Draft Save Failed':      {'main': [[cn('Cleanup')]]},
        'Build Updated HTML':         {'main': [[cn('Build WP Post')]]},
        'Build WP Post':              {'main': [[cn('WP: Validate Post Target')]]},
        'Parse WP Response':          {'main': [[cn('Cleanup')]]},
        'Cleanup':                    {'main': [[cn('Queue: Final Recheck')]]},
        'Queue: Final Recheck':       {'main': [[cn('Queue: Check Final')]]},
        'Queue: Check Final':         {'main': [[cn('Queue: Update')]]},
        'Queue: Update':              {'main': [[cn('Queue: Update Check')]]},
        'Queue: Update Check':        {'main': [[cn('Queue: Done')]]},
        'Queue: Done':                {'main': [[cn('Discord: Notify Success')]]},
        'Discord: Notify Success':    {'main': [[cn('Loop Over Items')]]},
        'Error Trigger':              {'main': [[cn('Discord: Notify Error')]]}
    }
    conn.update(wp_conn)

    # Workflow version is recorded in the WF README sticky, not in the name or
    # the id — renaming the file must never change the id, since n8n treats the
    # id as the identity of the live workflow (history, activation, webhooks).
    # The SHAMAN rename is a *new* workflow: importing this id creates a separate
    # box, and the pre-rename 'seo_blog_auto_writing' workflow must be deleted by
    # hand, otherwise the two fight over the same webhook path.
    nodes, conn = applyUsage(nodes, conn)
    # 日本時間6時に1記事だけ処理。Webhook の通常実行・復旧の絞り込みは変更しない。
    nodes.append({
        'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 6 * * *'}]}},
        'id': uid(), 'name': 'Daily Schedule', 'type': 'n8n-nodes-base.scheduleTrigger',
        'typeVersion': 1.2, 'position': [0, 720],
    })
    nodes.append(retryIf('Daily: Idle?', 'shamanScheduledNoWork', [580, 540]))
    nodes.append({
        'parameters': {}, 'id': uid(), 'name': 'Daily: No Work',
        'type': 'n8n-nodes-base.noOp', 'typeVersion': 1, 'position': [800, 720],
    })
    # 固定IDの読み取りノードを末尾追加する。既存ノードID（実行履歴・関連付け）を変えない。
    registry = copy.deepcopy(next(n for n in nodes if n['name'] == 'Queue: Read'))
    registry['name'] = 'Sites: Read Registry'
    registry['id'] = '00000299-0000-4000-a000-000000000299'
    registry['position'] = [310, 320]
    registry['executeOnce'] = True  # キューの行数によらず1回だけ読む
    registry['parameters']['sheetName']['value'] = SITE_REGISTRY_SHEET
    registry['parameters']['options']['dataLocationOnSheet']['values'].update(
        headerRow=1, firstDataRow=2)
    nodes.append(registry)
    conn['Daily Schedule'] = {'main': [[cn('Queue: Read')]]}
    conn['Filter Rows'] = {'main': [[cn('Daily: Idle?')]]}
    conn['Daily: Idle?'] = {'main': [[cn('Daily: No Work')], [cn('Loop Over Items')]]}
    w = wf('SHAMAN', nodes, conn, 'shaman',
           [{'id': 'ObIsFe2BJLGsg0uR', 'name': 'blog-pipeline'},
            {'id': 'SHAMANwfTag00001', 'name': 'shaman'}], True)
    # 実行データを残す（成功・失敗とも）。n8n 全体の既定に任せない（2026-11-07、実行 8 のデータが見当たらなかった）。
    w['settings'] = dict(w['settings'])
    w['settings']['saveDataSuccessExecution'] = 'all'
    w['settings']['saveDataErrorExecution'] = 'all'
    w['settings']['timezone'] = 'Asia/Tokyo'
    # Error Trigger node is present → n8n automatically uses this workflow
    # as its own error workflow (n8n default behavior; no explicit setting needed).
    return w


# =============== WRITE ALL ===============
if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    name = 'SHAMAN_Workflow.json'
    filepath = os.path.join(OUT, name)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(buildWorkflow(), f, indent=2, ensure_ascii=False)
    print(f'Generated: {name}')

    # プロンプト評価 Workflow（前の Test Workflow の ID を引き継ぐ）。本番と同じ組み立て関数を使う。
    from prompt_eval import buildPromptEvalWorkflow, FILENAME as EVAL_FILENAME
    with open(os.path.join(OUT, EVAL_FILENAME), 'w', encoding='utf-8') as f:
        json.dump(buildPromptEvalWorkflow(), f, indent=2, ensure_ascii=False)
    print(f'Generated: {EVAL_FILENAME}')

    # 接続確認 Workflow は別のモジュールで組み立てる（この Workflow の組み立て関数を使う）。
    from connection_check import buildConnectionCheckWorkflow, FILENAME as CONNECTION_FILENAME
    with open(os.path.join(OUT, CONNECTION_FILENAME), 'w', encoding='utf-8') as f:
        json.dump(buildConnectionCheckWorkflow(), f, indent=2, ensure_ascii=False)
    print(f'Generated: {CONNECTION_FILENAME}')
