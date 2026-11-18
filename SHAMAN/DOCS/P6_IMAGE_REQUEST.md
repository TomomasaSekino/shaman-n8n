# P6 の画像生成のリクエスト（OpenAI images/edits）

## 1. 何が起きたか（2026-11-08、本番の実行 11）

- 本番の実行 11 が `P6: Generate Image` で止まり、5 時間たっても終わらなかった。n8n の本体は CPU を 90% 使い続け、Web コンソールも応答しなくなった。n8n を再起動して止めた（実行 11 は「クラッシュ」の扱い。キュー「SHAMAN本番用」の「記事キュー」31 行目は「処理中」のまま残った）。
- WordPress への投稿は P6 のあとなので、行われていない。

## 2. 原因

- 2026-11-04 の変更（外部への接続を Credential に移す）で、P6 は `Build Image Requests`（Code ノード）が multipart の本文を自分で組み立て、バイナリとして HTTP Request ノード（`contentType: binaryData`）に渡す形になった。Content-Type は `multipart/form-data; boundary=…`。
- n8n 2.35.7 は、Content-Type に `multipart/form-data` を含み、本文が FormData でないとき、本文をオブジェクトとして展開して FormData を作り直す（`@n8n/backend-network` の `src/http/axios/legacy.ts` 99〜108 行、`createFormDataObject`）。2.39.8 も同じ。
- 本文は Buffer なので、1 バイトが 1 つのフォームの項目になる。参考画像は約 1.4〜2.2MB（kuromimi.online：PNG 6 枚、kuro-aji-lab.com：PNG 8 枚）なので、約 140 万〜220 万の項目ができる。手元で同じ処理を測ると、30 万バイトで作り直しに約 2.6 秒、作り直した本文は約 32MB になった。2.2MB では本文が数百 MB になり、それを細切れのまま送ろうとして、n8n の本体が CPU を握り続けた。その間は、通信の時間の上限（180 秒）も動けない。
- 変更のあと、P6 を実際に通して確かめていなかった（接続確認は images/edits を呼ばない。プロンプト評価に画像の生成のステップがなかった）。

## 3. 直し方

- images/edits を **JSON の本文**で呼ぶ。参考画像は `images: [{ image_url: "data:<mime>;base64,<…>" }]`（1〜16 枚、1 つの data: URL は 20MB まで。OpenAI の API リファレンス「Create image edit」の application/json の本文）。HTTP Request ノードは、ほかの API と同じく `specifyBody: json`、`jsonBody: {{ $json.requestBody }}`。
- n8n の multipart の機能（`multipart-form-data` の `formBinaryData`）は使わない。同じ名前（`image[]`）の欄を複数作ると、n8n は最後の 1 つで上書きする（`HttpRequestV3.node.ts` の `parametersToKeyValue`）。
- WordPress へのメディアのアップロード（`WP n: Upload Media`）もバイナリの本文で送るが、Content-Type は画像の形式（`image/jpeg` など）なので、作り直しの処理は通らない。macOS の n8n ではまだ通していないので、本番を通すときに確かめる。

## 4. 確かめ方

- プロンプト評価の `P6` ステップ（画像を 1 枚だけ作る。DOCS/PROMPT_EVAL_DESIGN.md）で、本番と同じノードで 1 回呼んで確かめてから、本番を通す。
