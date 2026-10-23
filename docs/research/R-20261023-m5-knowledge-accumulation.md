# 開発の中で外部の知識を集め、溜め、使う仕組み

- 記録：R-20261023-m5-knowledge-accumulation
- 段：map
- 決めたい判断：調べもの（外部の最先端の情報）を、どう集めて、どう溜め、開発と判断の中でどう引くか（いまは調べ役の CLI・出典の機械の照合・記録の索引）
- 調べた日時：2026-10-23T14:10:03+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 16・quote_not_found 1・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic の調査システムは、主エージェントが副エージェントを並列に動かす orchestrator-worker 型で作られている。 | 多エージェント型の調査 | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C2 | 多エージェント構成は社内の調査評価で、単一エージェントの Claude Opus 4 を 90.2% 上回った。ただしトークン消費は会話の約 15 倍である。 | 多エージェント型の調査 | frontier_labs | primary | vendor_claim | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C3 | 評価は LLM 判定者が基準表で行う。見る項目は、主張が出典と合うか（事実の正確さ）、引用が主張と合うか（引用の正確さ）、一次の出典を選んだか（出典の質）である。 | LLM 判定者による評価 | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C4 | 調査エージェントは初期に、権威ある出典より SEO 目的のコンテンツ農場を選びがちだった。出典の質を見る指針を指示に加えて直した。 | 出典の質の指針 | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C5 | 報告書ができた後に、引用の位置を探す専用の引用エージェントを別に通す作りがある。 | 引用の後付け照合 | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C6 | ALCE は、引用つきの文章生成を測る最初のベンチマークで、流暢さ・正しさ・引用の質を測る。最良のモデルでも ELI5 で半分は引用の裏付けが欠ける。 | 引用の自動評価ベンチマーク | academia | primary | fact | 2023-05-24 | verified | [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627) |
| C7 | 既存の生成検索エンジンでは、生成文のうち引用で完全に裏付けられるのは平均 51.5% にとどまる。 | 引用の自動評価ベンチマーク | academia | primary | fact | 2023-04-19 | verified | [Evaluating Verifiability in Generative Search Engines](https://arxiv.org/abs/2304.09848) |
| C8 | Self-RAG は、モデル自身が検索の要否と出力の批評を反省トークンで行う。事実性と引用の正確さが上がると報告している。 | 生成中の自己批評 | academia | primary | fact | 2023-10-17 | verified | [Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection](https://arxiv.org/abs/2310.11511) |
| C9 | FreshQA は、変化の速い知識と誤った前提を含む質問の動的ベンチマークである。モデルは大きさによらずこれに弱い。検索結果を指示に入れる FreshPrompt で改善する。 | 情報の鮮度の評価 | academia | primary | fact | 2023-10-05 | verified | [FreshLLMs: Refreshing Large Language Models with Search Engine Augmentation](https://arxiv.org/abs/2310.03214) |
| C10 | Contextual Retrieval（文書片ごとに文脈を足した埋め込みと BM25、さらに再順位付け）は、上位 20 件の取り出し失敗率を 67% 減らした。 | 検索の索引（埋め込み＋語彙＋再順位） | frontier_labs | primary | vendor_claim | 2024-09-19 | verified | [Introducing Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) |
| C11 | GraphRAG は、文書集合からグラフと共同体の要約を作り、全体を問う質問（主な主題は何か）に答える。通常の RAG が苦手な型に当たる。 | グラフ型の知識ベース | frontier_labs | primary | fact | 2024-04-24 | verified | [From Local to Global: A Graph RAG Approach to Query-Focused Summarization](https://arxiv.org/abs/2404.16130) |
| C12 | MemGPT は、OS の階層記憶に倣い、速い記憶と遅い記憶の間でデータを動かして長い文脈に見せかける。 | 階層型のエージェント記憶 | academia | primary | fact | 2023-10-12 | verified | [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) |
| C13 | Mem0 は、長期記憶を抽出して保存し、全文脈方式に比べ p95 遅延 91% 減、トークン費 90% 超減を報告している。著者自身の測定である。 | 階層型のエージェント記憶 | academia | primary | vendor_claim | 2025-04-28 | verified | [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413) |
| C14 | Anthropic は、エージェントが文脈の外に書き残す覚え書きを後で引き戻す方式（構造化されたメモ取り）を、文脈が溢れたときの手段の 1 つに挙げている。 | ファイル・覚え書きによる記憶 | frontier_labs | primary | fact | 2025-09-29 | verified | [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| C15 | 同じ記事は、全部を先に読み込まず、ファイルパスや保存した問い合わせ・リンクなどの軽い識別子を持たせ、実行時に道具で読み込む「必要な時に取る」方式を述べている。 | 必要な時に取る検索 | frontier_labs | primary | fact | 2025-09-29 | verified | [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| C16 | 調査エージェント（Deep Research）のサーベイは、静的な流れと動的な流れを分ける分類を提案している。 | 調査エージェントの分類 | academia | primary | fact | 2025-06-22 | verified | [Deep Research Agents: A Systematic Examination And Roadmap](https://arxiv.org/abs/2506.18096) |
| C17 | ADR は 1 つの設計決定とその理由を記録するもので、プロジェクトの ADR の集まりが決定の記録簿になる。根拠となる出典を結ぶ決まりは、このページには明記されていない。 | 決定記録（ADR） | standards_tools | primary | fact | 不明 | quote_not_found | [Architectural Decision Records](https://adr.github.io/) |

## 抜粋

- C1：「uses a multi-agent architecture with an orchestrator-worker pattern」
- C2：「outperformed single-agent Claude Opus 4 by 90.2% on our internal research eval」
- C3：「citation accuracy (do the cited sources match the claims?)」
- C4：「consistently chose SEO-optimized content farms over authoritative」
- C5：「processes the documents and research report」
- C6：「even the best models lack complete citation support 50% of the time」
- C7：「on average, a mere 51.5% of generated sentences are fully supported by citations」
- C8：「shows significant gains in improving factuality and citation accuracy for long-form generations」
- C9：「all models (regardless of model size) struggle on questions that involve fast-changing knowledge and false premises」
- C10：「Reranked Contextual Embedding and Contextual BM25 reduced the top-20-chunk retrieval failure rate by 67%」
- C11：「a graph-based approach to question answering over private text corpora」
- C12：「virtual context management, a technique drawing inspiration from hierarchical memory systems」
- C13：「Mem0 attains a 91% lower p95 latency」
- C14：「regularly writes notes persisted to memory outside of the context window」
- C15：「lightweight identifiers (file paths, stored queries, web links, etc.)」
- C16：「propose a taxonomy that differentiates between static and dynamic workflows」
- C17：「The collection of ADRs created and maintained in a project constitute its decision log.」

## 確かめられなかったこと

- OpenAI の deep research 紹介ページは 403 で読めず、OpenAI・Google DeepMind・Microsoft/GitHub・Meta の公式の調査エージェントの評価値は集められなかった
- practitioners_at_scale（大規模運用企業の事後検証）と practitioners（名の通った実務家）の出典は、まだ取れていない。Simon Willison の記事は URL が 404 で、確かめられなかった
- standards_tools では ADR の公式ページしか取れず、ADR に調べた根拠を結び付ける実践の文書（MADR の参照欄、OpenSSF など）は未確認
- 古くなった情報の扱い（有効期限・再検証・版管理）の実測は FreshQA だけで、知識ベースの陳腐化を測った研究は未確認
- 出典の機械照合（引用文が本文に実在するかの自動検証）の専用手法は、ALCE と Verifiability の評価指標以外は未確認
- 本文の引用は WebFetch の要約モデルを経由して得たため、一字一句の一致は機械照合で確かめる必要がある。Mem0 と Anthropic の数字は著者自身の測定である

## 検索語

- Anthropic multi-agent research system
- Anthropic contextual retrieval
- ALCE citation benchmark
- Self-RAG
- Evaluating Verifiability in Generative Search Engines
- MemGPT
- GraphRAG
- Mem0
- FreshLLMs FreshQA
- Deep Research Agents survey
- Anthropic effective context engineering
- adr.github.io
- OpenAI introducing deep research
- Simon Willison deep research

## 問い

# 開発の中で外部の知識を集め、溜め、使う仕組み

## 段
map

## 決めたい判断
調べもの（外部の最先端の情報）を、どう集めて、どう溜め、開発と判断の中でどう引くか（いまは調べ役の CLI・出典の機械の照合・記録の索引）

## 判断を変えうるもの
エージェントが外部の知識を集める仕組み（調査のエージェント・出典の検証・知識の索引・記憶）で、確立した形や評価の結果があれば、それに寄せる。情報の鮮度と誤りの扱いについての実測

## 分野
frontier_labs
practitioners_at_scale
academia
standards_tools
practitioners

## 問い
AI エージェントに外部の情報を調べさせ、その結果を溜めて後から使う仕組みの手法の系統を集めてください。

- 調査のエージェント（deep research の類）の作り方と、その評価（正確さ・出典の正しさ・幻覚）
- 出典の検証・引用の照合の手法
- 調べた知識を溜めて引く仕組み（索引・検索・記憶・知識ベース）と、古くなった情報の扱い
- 開発の判断（設計の決定記録など）に、調べた根拠を結び付ける実践
