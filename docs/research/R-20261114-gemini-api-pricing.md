# Gemini API の公式の値段：Gemini 4 Hydrogen と 3.8 Flash（燃費の換算用）

- 記録：R-20261114-gemini-api-pricing
- 段：verify
- 決めたい判断：X-01〜X-03 の燃費の記録に添える、公式の API の値段での換算の単価（Gemini 4 Hydrogen と 3.8 Flash）
- 調べた日時：2026-11-14T02:20:43+09:00
- 調べ役：seat（claude-opus-5-5）
- 照合：verified 7・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Gemini 4 Hydrogen の API のモデルのコードは gemini-4-hydrogen。 | Gemini 4 Hydrogen | frontier_labs | primary | fact |  | verified | [Gemini 4 Hydrogen（Gemini API のモデルのページ。Markdown 版）](https://aistudio.google.com/docs/models/gemini-4-hydrogen.md) |
| C2 | Gemini 4 Hydrogen の思考の強さは low・medium・high・xhigh から選べる。 | Gemini 4 Hydrogen | frontier_labs | primary | fact |  | verified | [Gemini 4 Hydrogen（Gemini API のモデルのページ。Markdown 版）](https://aistudio.google.com/docs/models/gemini-4-hydrogen.md) |
| C3 | Gemini 4 Hydrogen の Standard の有料の層は、100 万トークンあたり入力 $1.50・出力（思考のトークンを含む）$7.50・キャッシュの読み $0.075（暗黙のキャッシュだけ）。期限つきの値段の記載は無い（この行の並びはページに 1 か所だけで、Hydrogen の節にある）。 | Gemini 4 Hydrogen | frontier_labs | primary | fact |  | verified | [Gemini Developer API pricing（Google の公式の値段のページ。Markdown 版）](https://aistudio.google.com/docs/pricing.md) |
| C4 | Gemini 3.8 Flash の思考の強さは low・medium・high から選べる。 | Gemini 3.8 Flash | frontier_labs | primary | fact |  | verified | [Gemini 3.8 Flash（Gemini API のモデルのページ。Markdown 版）](https://aistudio.google.com/docs/models/gemini-3.8-flash.md) |
| C5 | Gemini 3.8 Flash の Standard の有料の層の入力は、100 万トークンあたり 2026-12-31 まで $0.75、2027-01-01 から $1.50（節の説明の文とつなげて照合した）。 | Gemini 3.8 Flash | frontier_labs | primary | fact |  | verified | [Gemini Developer API pricing（Google の公式の値段のページ。Markdown 版）](https://aistudio.google.com/docs/pricing.md) |
| C6 | Gemini 3.8 Flash の Standard の有料の層の出力（思考のトークンを含む）は 2026-12-31 まで $3.75、2027-01-01 から $7.50。キャッシュの読みは $0.075 から $0.15（同じ行の並びが 3.6 Flash の節にもあり、抜粋だけでは節を区別できない）。 | Gemini 3.8 Flash | frontier_labs | primary | fact |  | verified | [Gemini Developer API pricing（Google の公式の値段のページ。Markdown 版）](https://aistudio.google.com/docs/pricing.md) |
| C7 | この値段は Gemini Developer API のもので、Gemini Enterprise Agent Platform（Google Cloud）の値段とは違いうる。 | 値段の注記 | frontier_labs | primary | fact |  | verified | [Gemini Developer API pricing（Google の公式の値段のページ。Markdown 版）](https://aistudio.google.com/docs/pricing.md) |

## 抜粋

- C1：「Gemini 4 Hydrogen (`gemini-4-hydrogen`) brings cutting-edge intelligence to everyone」
- C2：「Supported (low, medium, high, xhigh)」
- C3：「* Input price * Free of charge * $1.50 --- * Output price (including thinking tokens) * Free of charge * $7.50 --- * Context caching price * Free of charge * $0.075 Implicit caching only (explicit caching is not supported).」
- C4：「Supported (low, medium, high)」
- C5：「complex enterprise workflows. {% tabs %} {% tab title="Standard" %} {% table %} * Feature * Free Tier * Paid Tier, per 1M tokens in USD --- * Input price * Free of charge * $0.75 through December 31, 2026. $1.50 starting January 1, 2027.」
- C6：「* Output price (including thinking tokens) * Free of charge * $3.75 through December 31, 2026. $7.50 starting January 1, 2027. --- * Context caching price * Free of charge * $0.075 through December 31, 2026. $0.15 starting January 1, 2027.」
- C7：「Prices may differ from the prices listed here and the prices offered on Gemini Enterprise Agent Platform.」

## 確かめられなかったこと

- agy のモデルの名（gemini-4-hydrogen-medium・gemini-3.8-flash-medium）が、API のモデルのコードと思考の強さ medium に当たるのは、名の形からの読み。agy の公開の文書では確かめていない
- agy の経路（定額の利用枠。daily-cloudcode-pa.googleapis.com）の利用量の数え方が、API の課金と同じかは確かめていない。換算は「同じトークンを API の Standard の有料の層で払ったら」の目安で、実際の支払いではない
- agy の stream-json の input_tokens をキャッシュでない入力、cache_read_tokens をキャッシュの読み、output_tokens を思考を含む出力として当てた（harness/agy_stream.py の読み）。API の usage_metadata の項目との対応は公開の文書で確かめていない
- 3.8 Flash の出力とキャッシュの行は 3.6 Flash の節にも同じ文がある（C6）。入力の行だけは節の説明の文とつなげて 1 か所に絞った（C5）
- Gemini Enterprise Agent Platform（Google Cloud）の値段は見ていない
- ai.google.dev/gemini-api/docs/pricing は aistudio.google.com/docs/pricing に移った（301）。HTML の版は本文が script の中にあり照合できないので、同じページの Markdown 版（.md）で照合した
- 検索で出た第三者のまとめ（売り手でない値段の集め）は、Hydrogen を公式の発表の無い名としていた。公式の値段のページとモデルのページには載っている（モデルのページの最終更新は 2026 年 11 月）

## 検索語

- Gemini API pricing Gemini 4 Hydrogen per 1M tokens
- Gemini 3.8 Flash API pricing input output cached tokens
- "gemini-4-hydrogen" model

## 問い

# Gemini API の公式の値段：Gemini 4 Hydrogen と 3.8 Flash（燃費の換算用）

## 段
verify

## 決めたい判断
X-01〜X-03 の燃費の記録に添える、公式の API の値段での換算の単価（Gemini 4 Hydrogen と 3.8 Flash）

## 判断を変えうるもの
公式の値段のページに Hydrogen が無い。プロンプトの長さの層や期限つきの値段がある。出力の値段が思考のトークンを含まない。キャッシュの読みの値段が無い

## 問い
Gemini Developer API の公式の値段のページで、gemini-4-hydrogen と gemini-3.8-flash の Standard の有料の層の、100 万トークンあたりの入力・出力・キャッシュの読みの値段はいくらか。期限つきの値段や、思考のトークンの扱いはどう書かれているか。モデルのページで、モデルのコードと選べる思考の強さは何か。
