"""SHAMAN: 接続確認 Workflow（外部サービスへの接続だけを確かめる、最小構成の Workflow）。

記事の生成から WordPress への投稿までの E2E 試験とは別に実施する（DOCS/CONNECTION_CHECK.md）。
外部への接続は、本番と同じ Credential・同じ組み立て関数（generate_workflows.py）で作る。

確かめること（どれも書き込みは試験用の場所だけ。本番のキューや WordPress には書かない）：
- Google Sheets：試験用シートの予約行を読み、実行ID 列に書き、読み直して一致するか
- Gemini・Anthropic・OpenAI：本番で使うモデルを、Credential で取得できるか（モデル情報の取得は課金されない）
- WordPress：sites.json の各サイトで、自分のユーザー情報を読めるか（読むだけ）
- Google Drive：サイトごとに、本番と同じ参考画像のフォルダー（IMG_FOLDER または SITE_ID）から画像を 1 枚ダウンロードできるか
- Discord：結果を通知用チャンネルに送れるか
1 つでも失敗すれば、最後のノードで実行を失敗させる（n8n の実行履歴で、成功／失敗が分かる）。
"""
import generate_workflows as gw

FILENAME = 'SHAMAN_Connection_Check.json'
NAME = 'SHAMAN: Connection Check'
WORKFLOW_ID = 'shaman_connection_check'
RUN_WEBHOOK_PATH = 'shaman-connection-check'  # Actions から実行する Webhook の path（DOCS/RUN_FROM_ACTIONS.md）
# 試験用シートの予約行（自動 DRY_RUN と同じ行）。この行の 実行ID 列だけを書き換える。
RESERVED_KEYWORD = '__SHAMAN_AUTO_E2E__'
RUN_COL = '実行ID\n【自動】'
# 参考画像のフォルダー（Google Drive の SHAMAN/IMG）。本番の P6 と同じ値を使う。
IMG_FOLDER = gw.IMG_FOLDER
DRIVE_FOLDER_MIME = gw.DRIVE_FOLDER_MIME

GEMINI_MODELS = (gw.MODEL_PRO, gw.MODEL_FLASH_LITE, gw.MODEL_IMAGE)

# 各ノードは失敗しても次へ進め、最後にまとめて判定する（どこが失敗したかを 1 回の実行で全部出す）。
CONTINUE = 'continueRegularOutput'
ROW_FOUND = 'Conn: Sheets Row Found?'


def _continue(node):
    node['onError'] = CONTINUE
    return node


def _always(node):
    # シートが空でも 1 件を出して、次のノード（判定）まで進める。
    node['alwaysOutputData'] = True
    return node


def _row_found_node(position):
    """予約行を 1 件に特定できたときだけ、Sheets Write に進める。

    特定できなければ（0 件・複数件・読み込みの失敗）書き込まず、Conn: Sheets Check へ直接進めて NG にする。
    """
    return {
        'parameters': {'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 1},
            'conditions': [{'id': 'conn-row-found', 'leftValue': '={{ $json.ok }}', 'rightValue': '',
                            'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
            'combinator': 'and'}, 'options': {}},
        'id': gw.uid(), 'name': ROW_FOUND, 'type': 'n8n-nodes-base.if', 'typeVersion': 2, 'position': position,
    }


def _pick_row_js():
    return '\n'.join([
        "var KEY_COL = 'KEYWORD\\n（必須）\\nフォーカスキーワード';",
        'var rows = items.map(function(it) { return it.json || {}; }).filter(function(r) {',
        '  return r.row_number != null && String(r[KEY_COL] || \'\').trim() === ' + gw.js_str(RESERVED_KEYWORD) + ';',
        '});',
        "var err = items[0] && items[0].json && items[0].json.error;",
        "if (err) return [{ json: { ok: false, detail: '読み込めません: ' + String(err.message || err) } }];",
        "if (rows.length !== 1) return [{ json: { ok: false, detail: '予約行（KEYWORD " + RESERVED_KEYWORD
        + "）が ' + rows.length + ' 件です（1 件のはず）' } }];",
        "return [{ json: { ok: true, row: Number(rows[0].row_number), mark: 'CONN-' + String($execution.id) } }];",
    ])


def _check_sheets_js():
    return '\n'.join([
        "var pick = $('Conn: Sheets Pick Row').first().json;",
        'if (!pick.ok) return [{ json: { ok: false, detail: pick.detail } }];',
        "var write = $('Conn: Sheets Write').first().json;",
        "if (write.error) return [{ json: { ok: false, detail: '書き込めません: ' + String(write.error.message || write.error) } }];",
        'var row = items.map(function(it) { return it.json || {}; })',
        '  .filter(function(r) { return Number(r.row_number) === pick.row; })[0];',
        "if (!row) return [{ json: { ok: false, detail: '読み直した中に行 ' + pick.row + ' がありません' } }];",
        'var got = String(row[' + gw.js_str(RUN_COL) + "] || '');",
        "if (got !== pick.mark) return [{ json: { ok: false, detail: '行 ' + pick.row + ' の実行IDが「' + got + '」（書いたのは「' + pick.mark + '」）' } }];",
        "return [{ json: { ok: true, detail: '行 ' + pick.row + ' の実行IDを書いて読み直せた（' + pick.mark + '）' } }];",
    ])


def _summarize_js(wp_sites, drive_sites):
    """各ノードの出力から、サービスごとの OK/NG を作る。Discord に送る文も作る。"""
    lines = [
        'function out(name) { try { return $(name).all(); } catch (e) { return []; } }',
        'function first(name) { var a = out(name); return a.length ? a[0] : null; }',
        # エラー文は Discord に送るので、URL の key= などの値を伏せ、長さを抑える。
        r"function redact(t) { return String(t).replace(/(key=|Bearer\s+|x-api-key[\"':\s]+)[^&\s\"',}]+/gi, '$1***').substring(0, 300); }",
        'function err(it) { var j = (it && it.json) || {}; return j.error ? redact(j.error.message || j.error) : \'\'; }',
        'var checks = [];',
        'function add(service, ok, detail) { checks.push({ service: service, ok: !!ok, detail: String(detail || \'\') }); }',
        '// Google Sheets',
        "var sh = first('Conn: Sheets Check');",
        "add('Google Sheets', sh && sh.json.ok, sh ? sh.json.detail : '未実行');",
        '// Gemini：本番で使うモデルが、Credential で取得できる一覧にあるか',
        "var g = first('Conn: Gemini Models');",
        "if (!g) add('Gemini', false, '未実行');",
        "else if (err(g)) add('Gemini', false, err(g));",
        'else {',
        "  var names = (g.json.models || []).map(function(m) { return m.name; });",
        '  var missing = ' + gw.js_str(list(GEMINI_MODELS)) + ".filter(function(m) { return names.indexOf('models/' + m) < 0; });",
        "  add('Gemini', missing.length === 0, missing.length ? 'モデルが一覧にない: ' + missing.join(', ') : names.length + ' 件のモデルを取得、本番のモデルあり');",
        '}',
        '// Anthropic・OpenAI：本番で使うモデルの情報を取得できるか',
        "[['Anthropic', 'Conn: Anthropic Model', " + gw.js_str(gw.MODEL_CLAUDE) + "],",
        " ['OpenAI', 'Conn: OpenAI Model', " + gw.js_str(gw.MODEL_IMAGE_OPENAI) + "]].forEach(function(c) {",
        '  var r = first(c[1]);',
        "  if (!r) add(c[0], false, '未実行');",
        '  else if (err(r)) add(c[0], false, err(r));',
        # 別名を指定すると、正式な ID（日付つきなど）で返ることがある。取得できたこと（ID がある）で判定する。
        "  else add(c[0], !!r.json.id, r.json.id ? 'モデル ' + c[2] + ' を取得（ID ' + r.json.id + '）' : 'モデル情報がない');",
        '});',
        '// WordPress：サイトごとに、自分のユーザー情報を読めるか（読むだけ）',
        'var WP_SITES = ' + gw.js_str(wp_sites) + ';',
        "function wpResult(i) { try { var a = $('Conn: WordPress').all(0, i); return a.length ? a[0] : null; } catch(e) { return null; } }",
        "if (WP_SITES.length === 0) add('WordPress', false, 'sites.json にサイトがないため未確認');",
        "WP_SITES.forEach(function(s, i) {",
        "  var r = wpResult(i);",
        "  if (!r) add('WordPress ' + s.siteId, false, '未実行');",
        "  else if (err(r)) add('WordPress ' + s.siteId, false, err(r));",
        "  else add('WordPress ' + s.siteId, r.json.id != null, r.json.id != null ? 'ユーザーID ' + r.json.id + ' として認証' : 'ユーザー情報がない');",
        '});',
        '// Google Drive：サイトごとに、本番の P6 と同じフォルダー（SHAMAN/IMG/<IMG_FOLDER または SITE_ID>）から画像を 1 枚ダウンロード',
        'var DRIVE_SITES = ' + gw.js_str(drive_sites) + ';',
        "if (DRIVE_SITES.length === 0) add('Google Drive', false, 'sites.json にサイトがないため未確認');",
        'DRIVE_SITES.forEach(function(s) {',
        "  var service = 'Google Drive ' + s.siteId;",
        '  var folder = first(s.folderNode);',
        '  var image = first(s.imageNode);',
        '  var dl = first(s.downloadNode);',
        "  if (!folder || err(folder) || !folder.json.id) add(service, false, folder && err(folder) ? err(folder) : 'IMG にフォルダー「' + s.folder + '」がない');",
        "  else if (!image || err(image) || !image.json.id) add(service, false, image && err(image) ? err(image) : 'フォルダー「' + s.folder + '」に画像がない');",
        "  else if (!dl || err(dl) || !dl.binary || !dl.binary.data) add(service, false, dl && err(dl) ? err(dl) : '画像の中身を取得できない');",
        "  else add(service, true, '「' + folder.json.name + '/' + image.json.name + '」をダウンロード');",
        '});',
        "var ng = checks.filter(function(c) { return !c.ok; });",
        "var message = (ng.length ? '⚠️' : '✅') + ' SHAMAN 接続確認（実行ID ' + $execution.id + '）\\n'",
        "  + checks.map(function(c) { return (c.ok ? 'OK ' : 'NG ') + c.service + '：' + c.detail; }).join('\\n');",
        'return [{ json: { checks: checks, message: message.substring(0, 1900) } }];',
    ]
    return '\n'.join(lines)


def _result_js():
    return '\n'.join([
        "var s = $('Conn: Summarize').first().json;",
        'var checks = s.checks.slice();',
        'var d = items[0] && items[0].json;',
        "var dErr = d && d.error ? String(d.error.message || d.error) : '';",
        "checks.push({ service: 'Discord', ok: !dErr, detail: dErr || '結果を送信' });",
        'var ng = checks.filter(function(c) { return !c.ok; });',
        # NG でも例外にせず、結果を返す。例外にすると Webhook（lastNode）の応答が空になり、Actions に理由が出ない。
        "var reason = ng.map(function(c) { return c.service + '（' + c.detail + '）'; }).join(' / ');",
        "return [{ json: { result: ng.length ? 'SHAMAN_CONNECTION_CHECK_FAILED: ' + reason : 'SHAMAN_CONNECTION_CHECK_OK',",
        "  checks: checks, shamanRun: { ok: ng.length === 0, workflow: 'connection', executionId: String($execution.id),",
        "  reason: reason, checks: checks } } }];",
    ])


def buildConnectionCheckWorkflow(sites_path=gw.SITES_PATH):
    sites = gw.load_sites(sites_path)
    x = [0]

    def pos():
        x[0] += 220
        return [x[0], 300]

    nodes = [
        gw.sticky('README', '\n'.join([
            '## SHAMAN 接続確認',
            '外部サービスへの接続だけを確かめる。記事は作らない。',
            '本番と同じ Credential を使う。書き込むのは、試験用シートの予約行の 実行ID と、Discord の通知だけ。',
            'Actions の「SHAMAN | n8n Workflow実行」（Run Webhook）か「Execute workflow」で実行し、最後の **Conn: Result** が成功なら全部 OK。',
            '失敗したときは、エラー文に NG のサービスと理由が出る。',
        ]), [0, 0], w=600, h=220),
        {'parameters': {}, 'id': gw.uid(), 'name': 'Manual Trigger', 'type': 'n8n-nodes-base.manualTrigger',
         'typeVersion': 1, 'position': [0, 300]},
        _always(_continue(gw.sheetsNode('Conn: Sheets Read', 'read', pos(), gw.QUEUE_TEST_SPREADSHEET))),
        gw.code('Conn: Sheets Pick Row', _pick_row_js(), pos()),
        _row_found_node(pos()),
        # 書けた行が 0 件でも、判定（Conn: Sheets Check）まで進める（2026-11-06。ここで止まると理由が出ない）。
        _always(_continue(gw.sheetsNode('Conn: Sheets Write', 'update', pos(), gw.QUEUE_TEST_SPREADSHEET, {
            'row_number': '={{ $json.row }}',
            RUN_COL: '={{ $json.mark }}',
        }))),
        _always(_continue(gw.sheetsNode('Conn: Sheets Reread', 'read', pos(), gw.QUEUE_TEST_SPREADSHEET))),
        gw.code('Conn: Sheets Check', _check_sheets_js(), pos()),
        _continue(gw.apiHttp('Conn: Gemini Models', pos(), 'gemini', 'GET',
                             'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000')),
        _continue(gw.apiHttp('Conn: Anthropic Model', pos(), 'anthropic', 'GET',
                             'https://api.anthropic.com/v1/models/' + gw.MODEL_CLAUDE)),
        _continue(gw.apiHttp('Conn: OpenAI Model', pos(), 'openai', 'GET',
                             'https://api.openai.com/v1/models/' + gw.MODEL_IMAGE_OPENAI)),
    ]
    # 接続確認はサイトを 1 件ずつ流し、Credential の式をサイトごとに再評価する。
    wp_sites = [{'siteId': site_id, 'node': 'Conn: WordPress'} for site_id in sorted(sites)]
    wp_site_items = [{'siteId': site_id, 'wpUrl': 'https://' + site_id} for site_id in sorted(sites)]
    nodes.append(gw.code('Conn: WP Sites', '\n'.join([
        'var sites = ' + gw.js_str(wp_site_items) + ';',
        'return sites.map(function(s) { return { json: s }; });'
    ]), pos()))
    nodes.append({'parameters': {'batchSize': 1, 'options': {}},
                  'id': gw.uid(), 'name': 'Conn: WP Loop',
                  'type': 'n8n-nodes-base.splitInBatches', 'typeVersion': 3, 'position': pos()})
    nodes.append(_continue(gw.wordpressHttp('Conn: WordPress', pos(), 'GET',
                                            '/wp-json/wp/v2/users/me?context=edit')))
    nodes += [
    ]
    # Google Drive：本番の P6（P6: Find Image Folder）と同じく、名前が IMG_FOLDER（無ければ SITE_ID）のフォルダーを探す。
    drive_sites = []
    for i, (site_id, site) in enumerate(sorted(sites.items()), 1):
        folder = site.get('IMG_FOLDER') or site_id
        names = {k: 'Conn: Drive %s %d' % (k, i) for k in ('Folder', 'Image', 'Download')}
        nodes += [
            _continue(gw.driveSearchNode(names['Folder'], pos(),
                                         "'" + IMG_FOLDER + "' in parents and name = '" + folder + "' and mimeType = '"
                                         + DRIVE_FOLDER_MIME + "' and trashed = false")),
            _continue(gw.driveSearchNode(names['Image'], pos(),
                                         "='{{ $json.id }}' in parents and mimeType contains 'image/' and trashed = false")),
            _continue(gw.driveDownloadNode(names['Download'], pos(), '={{ $json.id }}')),
        ]
        drive_sites.append({'siteId': site_id, 'folder': folder, 'folderNode': names['Folder'],
                            'imageNode': names['Image'], 'downloadNode': names['Download']})
    nodes += [
        gw.code('Conn: Summarize', _summarize_js(wp_sites, drive_sites), pos()),
        _continue(gw.discordNode('Conn: Discord Notify', pos(), '={{ $json.message }}')),
        gw.code('Conn: Result', _result_js(), pos()),
        # NG なら例外で止め、n8n の実行を Error にする（結果は Conn: Result が先に作る）。
        gw.runCheckNode(pos()),
    ]
    # どのノードも、出力が 0 件でも次へ進める。途中で黙って止まり「Succeeded」になるのを防ぐ（2026-11-06）。
    for node in nodes:
        if node['type'] not in ('n8n-nodes-base.stickyNote', 'n8n-nodes-base.manualTrigger'):
            node['alwaysOutputData'] = True
    # ノードの ID は、この Workflow の中だけで決める（本番・Test の生成順に左右されない）。
    for i, node in enumerate(nodes, 1):
        node['id'] = 'c' + format(i, '07x') + '-0000-4000-a000-' + format(i, '012x')
    chain = [n['name'] for n in nodes if n['type'] != 'n8n-nodes-base.stickyNote']
    conn = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(chain, chain[1:])}
    # WordPress は 1 サイトずつ処理。完了後だけ次の Drive 確認に進む。
    wp_next = chain[chain.index('Conn: WordPress') + 1]
    conn['Conn: WP Sites'] = {'main': [[{'node': 'Conn: WP Loop', 'type': 'main', 'index': 0}]]}
    conn['Conn: WP Loop'] = {'main': [[{'node': wp_next, 'type': 'main', 'index': 0}],
                                          [{'node': 'Conn: WordPress', 'type': 'main', 'index': 0}]]}
    conn['Conn: WordPress'] = {'main': [[{'node': 'Conn: WP Loop', 'type': 'main', 'index': 0}]]}
    # 予約行を特定できないときは、書き込まずに判定へ進む（true → Write、false → Check）。
    conn[ROW_FOUND] = {'main': [[{'node': 'Conn: Sheets Write', 'type': 'main', 'index': 0}],
                                [{'node': 'Conn: Sheets Check', 'type': 'main', 'index': 0}]]}
    # Actions から実行する入口（DOCS/RUN_FROM_ACTIONS.md）。Manual Trigger と同じく、最初の Sheets Read へつなぐ。
    webhook = gw.runWebhookNode(RUN_WEBHOOK_PATH, [0, 500])
    webhook['id'] = 'c' + format(len(nodes) + 1, '07x') + '-0000-4000-a000-' + format(len(nodes) + 1, '012x')
    nodes.append(webhook)
    conn[webhook['name']] = {'main': [[{'node': chain[1], 'type': 'main', 'index': 0}]]}
    w = gw.wf(NAME, nodes, conn, WORKFLOW_ID, [{'id': 'CONNwfTag0000001', 'name': 'connection-check'}])
    w['settings'] = dict(w['settings'])
    w['settings']['saveDataSuccessExecution'] = 'all'
    w['settings']['saveDataErrorExecution'] = 'all'
    return w
