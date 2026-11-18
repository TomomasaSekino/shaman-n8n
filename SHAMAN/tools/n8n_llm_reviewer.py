import os
import sys
import json
import hashlib
from google import genai
from dotenv import load_dotenv

load_dotenv()

def get_file_hash(content_str):
    """文字列からMD5ハッシュを生成する"""
    return hashlib.md5(content_str.encode('utf-8')).hexdigest()

def main():
    # 1. 引数チェック
    if len(sys.argv) < 2:
        print("エラー: JSONファイルのパスを引数に指定してください。")
        print("使用法: python tools/n8n_llm_reviewer.py <path_to_json>")
        sys.exit(1)

    json_file_path = sys.argv[1]

    # 2. ファイルの存在確認と読み込み
    if not os.path.exists(json_file_path):
        print(f"エラー: ファイルが見つかりません - {json_file_path}")
        sys.exit(1)

    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
            # LLMに渡す用＆ハッシュ計算用に整形された文字列にする
            workflow_json_str = json.dumps(workflow_data, indent=2)
    except json.JSONDecodeError as e:
        print(f"エラー: 無効なJSONファイルです。\n詳細: {e}")
        # JSONとして破綻している時点でClaudeに修正させるためエラー終了
        sys.exit(1)

    # 3. キャッシュ（ハッシュ）チェックによる無駄なAPI呼び出しの防止
    current_hash = get_file_hash(workflow_json_str)
    cache_file = ".n8n_review_cache"

    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            last_hash = f.read().strip()
        
        if current_hash == last_hash:
            print("--- 外部LLMレビュー ---")
            print("【変更なし】前回のレビューからJSONの内容が変わっていないため、API呼び出しをスキップします。")
            print("【問題なし】") # ClaudeCodeに成功を伝えるためのキーワード
            sys.exit(0)

    # 4. APIキーの確認
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("エラー: 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    # 5. LLMクライアントの初期化
    client = genai.Client(api_key=api_key)

    # 6. レビュー用の厳格なプロンプトを作成
    prompt = f"""
あなたはn8nの仕様を完全に熟知した、極めて厳格なQAエンジニアです。
以下のJSONは、自動生成されたn8nのワークフローデータです。

このJSONをn8nの実機にインポートし、実行した際にパースエラーや実行時エラーが発生しないかを厳しく審査してください。

【対象のn8nバージョン】
n8n 2.35.7 (self-hosted, macOS LaunchDaemon)

【typeVersion 別の仕様（誤判定防止 — 以下は正しい仕様です）】
1. splitInBatches (Loop Over Items) typeVersion 3:
   - **出力ポート順は旧バージョンから逆転済み**
   - outputNames: ['done', 'loop']
   - connections.main[0] = done (ループ完了時に1回だけ発火)
   - connections.main[1] = loop (各バッチ処理中に毎回発火)
   - したがって main[0] が空配列 [] で main[1] に後続ノードが接続されているのは正しい構成
   - 逆方向(main[0]に後続, main[1]空)は v1/v2 の古い仕様であり v3 では誤り
   - 参考ソース: SplitInBatchesV3.node.js の execute() は done時 `return [processedItems, []]`、loop時 `return [[], returnItems]`
2. errorTrigger typeVersion 1 (n8n v1系):
   - エラー情報は `$json.execution.lastNodeExecuted` で参照する（`$json.execution.error.node.name` ではない — そのフィールドは存在しない）
3. httpRequest typeVersion 4.2:
   - specifyBody: "json" + jsonBody の組み合わせは、式の結果がオブジェクトでも**既にJSON文字列化された文字列**でも両方受け付ける
   - したがって下記のパターンはいずれも **正しく動作する**（実機テスト検証済、誤判定禁止）：
     (a) Codeノードで `var requestBody = JSON.stringify({{...}});` と文字列化 → httpRequestで `jsonBody: "={{{{ $json.requestBody }}}}"` （Geminiリクエスト全般で使用）
     (b) jsonBody式で直接 `"={{{{ JSON.stringify({{content: ...}}) }}}}"` （Discord通知で使用、2026-04-07ライブテスト合格）
   - 真の二重エンコード問題（400 Bad Request）は、JSON.stringify を**2回かける**場合のみ発生する
4. code typeVersion 2:
   - 戻り値は `return [{{ json: {{...}} }}]` の配列形式が必須

【チェックの観点】
1. ノードのパラメータ構成: 対象ノードの必須パラメータが欠落していないか？
2. データ型: 文字列、数値、ブーリアン、オブジェクト、配列などの型がn8nの仕様と一致しているか？
3. エクスプレッション: ={{{{ }}}} のようなn8n特有の記法が正しくエスケープ・記述されているか？
4. クレデンシャル: 認証情報のID指定などが正しい形式か？
5. JSON構造の破綻: parameters のネストがおかしくないか？

【出力ルール】
- 上記【typeVersion 別の仕様】セクションに記載された挙動を「誤り」として指摘しないでください（それらは正しい仕様です）。
- もしエラーの可能性が少しでもあれば、Claude AIがコードを修正できるように「どのノードの」「どのパラメータが」「なぜ間違っているのか」そして「どう修正すべきか」を具体的に指摘してください。
- もし実機で完全に動作すると確信できる（完璧な）場合のみ、以下の文字列を正確に出力してください:
【問題なし】

【対象のn8nワークフローJSON】
{workflow_json_str}
"""

    # 7. LLMにリクエストを送信
    print(f"--- 外部LLMによる n8n JSON レビューを開始します ({json_file_path}) ---")
    try:
        # モデルは推論能力・文脈長に優れた gemini-2.5-pro を指定
        response = client.models.generate_content(
            model='gemini-3.1-pro-preview',
            contents=prompt,
        )
        
        # 8. 結果を出力
        print("\n--- レビュー結果 ---")
        print(response.text)
        print("--------------------")

        # 9. 結果に応じた終了コードの制御とキャッシュ保存
        if "【問題なし】" not in response.text:
            # 問題があればエラー終了し、Claudeに修正を促す（キャッシュは更新しない）
            sys.exit(1)
        else:
            # 完璧な場合のみ、次回のスキップ用にハッシュを保存して正常終了
            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write(current_hash)
            sys.exit(0)

    except Exception as e:
        print(f"LLM API呼び出し中にエラーが発生しました: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()