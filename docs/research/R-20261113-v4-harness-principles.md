# 長く走る自律開発のハーネスの原理とコスト：状態の外在化・検証の背圧・文脈とキャッシュ

- 記録：R-20261113-v4-harness-principles
- 段：map
- 決めたい判断：v4 のハーネスの設計の原理（中くらいの仕事の入口・内部・出口）と、区切り（会話を続けるか新しくするか）の決め方
- 調べた日時：2026-11-13T18:00:12+09:00
- 調べ役：seat（claude-opus-5-5）
- 照合：verified 31・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | 長く走るエージェントの核の課題は、各セッションが前の記憶なしに始まること。Anthropic は compaction だけでは足りないとした。 | 外の構造の状態で引き継ぐ多セッション | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C2 | 機能の一覧は JSON にした。モデルは Markdown より JSON を不適切に書き換えにくい。 | 外の構造の状態で引き継ぐ多セッション | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C3 | 実装役には 1 回に 1 機能だけを頼んだ（一気に作ろうとする失敗への対策）。 | 外の構造の状態で引き継ぐ多セッション | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C4 | 初期化役と実装役の違いは最初の頼みだけで、システムの頼み・道具・ハーネスは同じ。 | 外の構造の状態で引き継ぐ多セッション | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C5 | Sonnet 4.5 は context anxiety が強く、compaction だけでは足りず、文脈のリセットが要った。 | リセットの要否はモデル次第 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C6 | Opus 4.5 ではその振る舞いが消え、文脈のリセットを外して 1 本の続くセッションにした。 | リセットの要否はモデル次第 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C7 | ハーネスの部品は、モデルが単独でできないことの仮定で、確かめ直す価値がある。 | 部品は外す条件つきの仮説 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C8 | 評価役は、課題が今のモデルの単独で確実にできる範囲の外にあるときだけ費用に見合う。 | 部品は外す条件つきの仮説 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C9 | 3 役のハーネスは単独の 20 倍超の費用だったが、出来の差はすぐ分かるほど大きかった（1 例）。 | 費用の構造 | frontier_labs | primary | opinion | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C10 | 文脈のトークンが増えるほど、文脈から情報を正しく思い出す力が落ちる（context rot）。 | 文脈の腐り | frontier_labs | primary | fact | 2025-09-29 | verified | [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| C11 | 最も安全で軽い compaction は、道具の結果の消去。 | 文脈の管理 | frontier_labs | primary | fact | 2025-09-29 | verified | [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| C12 | 100 ターンの web 検索の評価で、context editing は文脈の尽きで失敗する流れを完了させ、トークンを 84% 減らした。 | 文脈の管理 | frontier_labs | primary | vendor_claim |  | verified | [Managing context on the Claude Developer Platform](https://www.anthropic.com/news/context-management) |
| C13 | エージェントはチャットの約 4 倍、多役のシステムは約 15 倍のトークンを使う。 | 費用の構造 | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C14 | 自律の長い開発では、検証器がほぼ完璧でないと違う問題を解く。 | 検証の背圧 | frontier_labs | primary | fact | 2026-02-05 | verified | [Building a C compiler with a team of parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler) |
| C15 | 大きいプロジェクトの終盤、新しい機能のたびに既存の機能を壊すようになった（CI と強制で止めた）。 | 検証の背圧 | frontier_labs | primary | fact | 2026-02-05 | verified | [Building a C compiler with a team of parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler) |
| C16 | 新しいコンテナに文脈なしで入るエージェントは、向き直りにかなりの時間を使う。 | 外の構造の状態で引き継ぐ多セッション | frontier_labs | primary | fact | 2026-02-05 | verified | [Building a C compiler with a team of parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler) |
| C17 | Claude のプロンプトキャッシュの読みは、基本の入力の 0.1 倍の値段。 | キャッシュの費用 | standards_tools | primary | fact |  | verified | [Prompt caching（Claude の文書）](https://docs.claude.com/en/docs/build-with-claude/prompt-caching.md) |
| C18 | Gemini API の implicit caching は 2.5 以降のモデルで既定で有効。 | キャッシュの費用 | standards_tools | primary | fact |  | verified | [Context caching（Gemini API の文書）](https://ai.google.dev/gemini-api/docs/caching.md.txt) |
| C19 | Unity のテストをコマンドラインで走らせるときは -batchmode で人の入力を要らなくする。 | 検証の背圧（Unity） | standards_tools | primary | fact |  | verified | [Running tests from the command line（Unity Test Framework）](https://docs.unity3d.com/Packages/com.unity.test-framework@1.4/manual/reference-command-line.html) |
| C20 | 本番のエージェントで最も大事な数は KV キャッシュの当たり率。 | キャッシュの費用 | practitioners_at_scale | primary | fact | 2025-07-18 | verified | [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) |
| C21 | 文脈は追記だけにする。 | キャッシュの費用 | practitioners_at_scale | primary | fact | 2025-07-18 | verified | [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) |
| C22 | Manus の入力と出力のトークンの比は平均で約 100：1。 | 費用の構造 | practitioners_at_scale | primary | fact | 2025-07-18 | verified | [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) |
| C23 | 圧縮は常に戻せる形にする（ファイルシステムを外の記憶にする）。 | 文脈の管理 | practitioners_at_scale | primary | fact | 2025-07-18 | verified | [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) |
| C24 | 多役は、書き込みを 1 本の流れに保ち、ほかの役が知恵を足すときに最もよく効く。 | 単一の書き手と独立の検証 | practitioners_at_scale | primary | fact | 2026-04-22 | verified | [Multi-Agents: What's Actually Working](https://cognition.ai/blog/multi-agents-working) |
| C25 | コードの検証役は、書き手と文脈を事前に共有しないときに最もよく効いた。 | 単一の書き手と独立の検証 | practitioners_at_scale | primary | fact | 2026-04-22 | verified | [Multi-Agents: What's Actually Working](https://cognition.ai/blog/multi-agents-working) |
| C26 | 派手な長い自律開発の例は、どれも単純で確かめられる成功の基準を持つ。ふつうのソフトはそうでない。 | 検証の背圧 | practitioners_at_scale | primary | opinion | 2026-04-22 | verified | [Multi-Agents: What's Actually Working](https://cognition.ai/blog/multi-agents-working) |
| C27 | 18 のモデルで、入力が長くなるほど性能は一様でなく不安定になる。 | 文脈の腐り | practitioners_at_scale | primary | fact | 2025-07-14 | verified | [Context Rot: How Increasing Input Tokens Impacts LLM Performance](https://research.trychroma.com/context-rot) |
| C28 | 毎ループ仕様を読み直すのは無駄と承知のうえで、文脈を少なく使うほうが結果が良いとして新しい文脈で回す。 | 外の構造の状態で引き継ぐ多セッション | practitioners | secondary | opinion | 2025-07-14 | verified | [Ralph Wiggum as a "software engineer"](https://ghuntley.com/ralph/) |
| C29 | テストや型や静的解析など、何でも背圧として配線して不正な生成を拒む。 | 検証の背圧 | practitioners | secondary | opinion | 2025-07-14 | verified | [Ralph Wiggum as a "software engineer"](https://ghuntley.com/ralph/) |
| C30 | Context-Folding は、畳みの振る舞いを RL（FoldGRPO）で学ばせる方式（学習が前提）。 | 学習した文脈の畳み | academia | secondary | fact | 2025-10-13 | verified | [Scaling Long-Horizon LLM Agent via Context-Folding](https://arxiv.org/abs/2510.11967) |
| C31 | 古い観測を伏せるだけで、生のエージェントの費用を半分にし、LLM の要約と同じかやや上の解決率（SWE-agent・SWE-bench Verified）。 | 文脈の管理 | academia | secondary | fact | 2025 | verified | [The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management](https://arxiv.org/html/2508.21433v3) |

## 抜粋

- C1：「The core challenge of long-running agents is that they must work in discrete sessions, and each new session begins with no memory of what came before.」
- C2：「After some experimentation, we landed on using JSON for this, as the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files.」
- C3：「the next iteration of the coding agent was then asked to work on only one feature at a time.」
- C4：「The system prompt, set of tools, and overall agent harness was otherwise identical.」
- C5：「In our earlier testing, we found Claude Sonnet 4.5 exhibited context anxiety strongly enough that compaction alone wasn't sufficient to enable strong long task performance, so context resets became essential to the harness design.」
- C6：「Opus 4.5 largely removed that behavior on its own, so I was able to drop context resets from this harness entirely.」
- C7：「every component in a harness encodes an assumption about what the model can't do on its own, and those assumptions are worth stress testing」
- C8：「It is worth the cost when the task sits beyond what the current model does reliably solo.」
- C9：「The harness was over 20x more expensive, but the difference in output quality was immediately apparent.」
- C10：「as the number of tokens in the context window increases, the model’s ability to accurately recall information from that context decreases.」
- C11：「One of the safest lightest touch forms of compaction is tool result clearing」
- C12：「In a 100-turn web search evaluation, context editing enabled agents to complete workflows that would otherwise fail due to context exhaustion」
- C13：「agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats.」
- C14：「So it’s important that the task verifier is nearly perfect, otherwise Claude will solve the wrong problem.」
- C15：「near the end of the project, Claude started to frequently break existing functionality each time it implemented a new feature.」
- C16：「each agent is dropped into a fresh container with no context and will spend significant time orienting itself, especially on large projects.」
- C17：「Cache read tokens are 0.1 times the base input tokens price」
- C18：「Implicit caching is enabled by default for all Gemini 2.5 and newer models.」
- C19：「Use the -batchmode option when running tests on the command line to remove the need for manual user inputs.」
- C20：「I'd argue that the KV-cache hit rate is the single most important metric for a production-stage AI agent.」
- C21：「Make your context append-only.」
- C22：「In Manus, for example, the average input-to-output token ratio is around 100:1」
- C23：「Our compression strategies are always designed to be restorable」
- C24：「multi-agent systems work best today when writes stay single-threaded and the additional agents contribute intelligence rather than actions.」
- C25：「we found this technique to work best when the coding and review agents do not share any context beforehand.」
- C26：「These are exciting but they all share a property most real software doesn't: a simple, verifiable success criterion.」
- C27：「Our results reveal that models do not use their context uniformly; instead, their performance grows increasingly unreliable as input length grows.」
- C28：「Yes, this is wasteful because you're effectively burning the allocation of the specifications every loop and not reusing the allocation.」
- C29：「Anything can be wired in as back pressure to reject invalid code generation.」
- C30：「To make this behavior learnable, we develop an end-to-end reinforcement learning framework FoldGRPO with specific process rewards to encourage effective task decomposition and context management.」
- C31：「We find that a simple environment Observation Masking strategy halves cost relative to the Raw Agent while matching, and sometimes slightly exceeding, the solve rate of LLM-Summary.」

## 確かめられなかったこと

- 反証の段（docs/design/research_protocol.md §2。別の会社のモデル Argon で行う）はまだ行っていない。この記録は席（Claude）が自分で集めた反する証拠を含むが、規則の反証の代わりにはならない。
- 最前線（frontier_labs）の主張は Anthropic と Google の文書に偏った。OpenAI・Google DeepMind・Microsoft と GitHub・Meta の長く走るハーネスの一次の記事は、今回は読んでいない。
- agy（Antigravity の CLI）のキャッシュの仕組みは公開されていない。新しい会話で約 1.6 万〜2.1 万トークンまでキャッシュが当たらない、は手元の実測（docs/v4/experiments.md の M-01）で、この記録の主張ではない。
- Context-Folding の表 1（RL なしの畳みは長文脈の ReAct より下）は PDF から読んだ。PDF は機械の照合ができないので、主張には要旨の文だけを入れた。
- SWE-bench Verified が最小の足場で飽和しているという順位（Vals.ai）は、検索の結果でしか見ていない（ページは JavaScript で取れない）。
- Unity の公式の MCP の今の状態（2026-05 のベータの後、CLI に移ったという第三者の記事）は確かめていない。

## 検索語

- Anthropic engineering "Effective harnesses for long-running agents"
- Anthropic engineering "Harness design for long-running application development"
- Manus blog "Context Engineering for AI Agents: Lessons from Building Manus" KV-cache hit rate
- Scaling Long-Horizon LLM Agent via Context-Folding arXiv
- "The Complexity Trap" observation masking LLM summarization agent context management SWE-bench
- Gemini API implicit caching minimum token count cached token discount Gemini 3
- Chroma research "Context Rot" how increasing input tokens impacts LLM performance
- Cognition "Don't Build Multi-Agents" context sharing principles Walden Yan
- mini-SWE-agent bash only scaffold matches complex agent scaffolds SWE-bench Verified results
- Unity Test Framework command line batchmode -runTests PlayMode EditMode documentation
- Unity official MCP server AI agents Unity Editor 2026 Model Context Protocol

## 問い

# 長く走る自律開発のハーネスの原理とコスト：状態の外在化・検証の背圧・文脈とキャッシュ

## 段
map

## 決めたい判断
v4 のハーネスの設計の原理（中くらいの仕事の入口・内部・出口）と、区切り（会話を続けるか新しくするか）の決め方

## 判断を変えうるもの
強いモデルでは外の状態も検証も要らないという実測。区切りを細かくするほど良いという実測。キャッシュの費用の構造が提供元によらず同じという事実

## 分野
frontier_labs
practitioners_at_scale
academia
standards_tools
practitioners

## 問い
長く走るコーディングのエージェントで、最前線の企業・大規模に運用する当事者・研究・標準と道具の文書・実務家は、状態の引き継ぎ・検証・文脈の管理・プロンプトのキャッシュをどう扱っているか。費用の構造はどうなっているか。
