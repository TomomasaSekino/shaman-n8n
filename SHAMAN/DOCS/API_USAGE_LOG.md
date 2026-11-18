# API使用量の記録

2026-11-09、オーナーの依頼：「全体実行はこれで 3 回めなので、今回から各 API の in・out のトークン数を数える」。

## 1. どこに残るか

- 本番のスプレッドシート「SHAMAN本番用」（Repository Variable `SHAMAN_QUEUE_SPREADSHEET_ID`）のシート **「API使用量」**。1 行目が見出し。
- シートと見出しは、本番の Workflow が最初に作る（`Usage: Ensure Sheet` → `Usage: New Sheet?` → `Usage: Header Write`）。前からあるシートには見出しを書かない。
- API の呼び出し 1 回ごとに 1 行。P4 の作り直し（2 回目の Claude）や、画像の節ごと・作り直しの呼び出しも、それぞれ 1 行。

## 2. 列

| 列 | 中身 |
|---|---|
| 日時 | 記録した時刻（UTC、ISO 8601） |
| 実行ID | n8n の実行 ID |
| キューの行 | 「記事キュー」の行番号 |
| SITE_ID・KEYWORD | キューの行の値 |
| 工程 | P1・P2・P3・P4・P5・P6A（画像の計画）・P6（画像の生成） |
| モデル | 応答のモデル名（無ければリクエストのモデル名） |
| HTTPの状態 | 200 など。P6 は失敗の状態コード（400・429 など）も残す。通信エラーは 0 |
| 入力トークン | Gemini：`promptTokenCount`。Claude・OpenAI：`input_tokens` |
| 出力トークン | Gemini：`candidatesTokenCount`（思考は含まない）。Claude：`output_tokens`（思考を含む）。OpenAI：`output_tokens` |
| 思考トークン | Gemini：`thoughtsTokenCount`。Claude・OpenAI は、応答に内訳があるときだけ |
| キャッシュ読み込みトークン・キャッシュ作成トークン | Gemini：`cachedContentTokenCount`。Claude：`cache_read_input_tokens`・`cache_creation_input_tokens` |
| 合計トークン | Gemini：`totalTokenCount`。OpenAI：`total_tokens`。Claude は応答に無いので空 |
| 所要時間ms | API を呼ぶ直前（`Usage: Stamp`）から、記録する（`Usage: Row`）までの時間 |
| 停止理由 | Gemini：`finishReason`。Claude：`stop_reason` |
| usage | 応答の使用量の項目をそのまま（JSON）。読めなかったときは理由 |

## 3. 作り

`Build …` → `Usage: Stamp <工程>` → API → `Usage: Row <工程>` → `Usage: Write <工程>`（「API使用量」へ追記）→ `Usage: Pass <工程>`（API の出力をそのまま渡す）→ もとの次のノード（Parse など）。

- 枝を分けずに一続きにする（先のないノードを作らない）。`Usage: Pass` は API の出力をそのまま返すので、Parse などが受け取るものは変わらない。
- 記録の失敗で記事を止めない。
  - `Usage: Ensure Sheet`（シートの作成）・`Usage: Header Write`（見出し）は、失敗しても（3 回まで待ってやり直したあと）キューの読み込みへ進む。作成に失敗したときは見出しを書かない。
  - `Usage: Row` は読めない項目を空にし、`Usage: Write` は失敗しても次へ進む（Sheets の一時的な失敗は 3 回まで待ってやり直す）。
  - シートを作れなかったときは、その実行の記録は残らない（記事は処理する）。

## 4. 記録できないもの

- P1〜P5・P6A の API が失敗した呼び出し（HTTP のエラー・時間切れ）。これらのノードは失敗すると実行を止めるので、記録まで進まない。時間切れでも、API 側では課金されていることがある（2026-11-07、実行 8 の P4）。
- プロンプト評価の呼び出しは、試験用スプレッドシートの「評価ログ」の `トークン数` に残る（このシートには書かない）。
