# game-harness

ゲーム開発のための自律開発ハーネスの研究と実装です。人はゲームの創作の上流（企画・世界観・設計の大枠・創作の判断）に専念し、下の層の実装と検証をエージェントに任せます。そのためのハーネスを、素のチャットのエージェントと公平に比べ、どこまで素のチャットで足り、どこからハーネスが効くか（損益分岐点）を、公式のベンチで確かめて公開します。

- 正本は [docs/v4/](docs/v4/README.md) の 3 層です：[憲章](docs/v4/charter.md)（目的と変えない条件）・[設計と仮説](docs/v4/design.md)・[実験と計画](docs/v4/experiments.md)。
- 実戦（実際のゲーム開発）では、その時点で最も賢いモデルに、まとまった仕事を 1 つの会話で一括で任せます。公開する比べと証明は、agy の Gemini のモデル 1 つで行います（標準は Gemini 4 Hydrogen の medium。校正 X-01 の 1 課題目で燃費を測って決めました）。
- ベンチで証明するのは、どの賢いモデルでも要る共通骨格（入口の要件の台帳・1 つの単位を 1 つの会話で続ける・出口の機械の背圧）だけです。実戦では、受け入れテストを先に見せる・任せる粒度を大きくする・Unity 専用の多段の関門を使う形に専用化します（[設計と仮説](docs/v4/design.md)の §4-4）。

## 中身

| 置き場 | 中身 |
|:--|:--|
| `docs/v4/` | 正本の 3 層と、整理の計画 |
| `docs/research/` | 調べものの記録。主張ごとに出典と抜粋があり、抜粋は出典の本文で機械に照合する |
| `docs/design/` | 調べ方の規則（`research_protocol.md`）と前提の台帳（`premises.yaml`） |
| `docs/ops/` | 箱（WSL2 の Docker）の用意と確かめ |
| `harness/` | 部品：調べもの（`research`・`research_verify`・`premises`）、モデルの固定（`model_pin`）、実行環境のアダプタ（`box`・`box_tree`）、エージェントの実行口（`agy_stream`・`agy_pinned`・`agent_cli`）、土台（`proc`・`job_object`・`exitcode`・`machine`） |
| `tests/` | 部品の試験 |
| `box/` | 箱の像（Dockerfile と中継） |
| `config/` | 部品の設定 |
| `.github/` | CI（試験・前提の台帳・モデルの固定）と、承認のチェック（`ms4:approved` のラベル） |

ハーネスの制御核（要件の台帳・検証の手順の形・1 つの単位を 1 つの会話で進める周回・検証の関門）は、これから作ります。制御核は実行環境とモデルを知らない形にし、アダプタだけを替えて Unity の実開発へ持ち込みます（[設計と仮説](docs/v4/design.md)の §5-1）。

## 確かめ方

Python 3.12 と `pip install -r requirements.lock` の後に、リポジトリの根で次を走らせます。CI も同じものを Windows と Linux で走らせます。

```
python -m unittest discover -s tests
python -m harness.premises check --strict
python -m harness.model_pin
```

## v1〜v3 の記録

2026-09〜11 の v1〜v3（自作の課題での比べ、H ライン、総監督、古いゲームの線など）の文書・コード・ランの記録は、タグ [`archive/pre-v4`](https://github.com/tamaroulet/game-harness/tree/archive/pre-v4) にあります。main には戻しません。v3 の最後の計画は [direction_and_plan_v3.md](https://github.com/tamaroulet/game-harness/blob/archive/pre-v4/docs/design/direction_and_plan_v3.md)（2026-11-13 に凍結）です。
