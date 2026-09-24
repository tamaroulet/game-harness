# v2（Contract-Driven Foundry）：契約駆動の防壁型

- 状態: 設計（方針と仕様）。コードは変更していない
- 前提: B4-RUN v1.0 の評価はスモーク（b4-smoke-01・02）で打ち切り（2026-09-24、オーナー判断）。実測と総括は game-harness#63
- 決定（2026-09-24、オーナー裁定）:
  1. v2 でも条件 A（単一チャット）と比べる。**A にも同じ契約と性質テストを渡す**（同じ情報の原則）
  2. 性質（PBT の正解）は **Claude が宣言を書き**、キック表などの参照データは **GDD から写す**
  3. 読み取り専用の契約（C#）は、**単位定義の `interface`（JSON）から機械的に生成**する。誰も C# を手書きしない
  4. 実装役の TTL と再試行時の道具の解禁は、**v2 の設計の中で決める**（§7）

## 1. v1.0 から持ち込む教訓（実測）

| # | 事実（b4-smoke-01・02） | v2 の扱い |
|:--|:--|:--|
| L1 | 静的な門が `impl_files` の文字列だけからシグネチャを探し、T1 で作った `TickInput.*` を T2・T3 で見つけられなかった（`pipeline.py:603`）。B は 6 回の試行すべてが実装の中身に関わらず落ちた | シグネチャの検査は廃止し、コンパイラに任せる（§3.3） |
| L2 | 実装前から通る受入テストを「偽テスト」として REJECT した（T2 の 7 件中 4 件） | タスクを積み重ねる前提では「実装前から通る」は正常。性質は累積で守らせる（§5.2） |
| L3 | B が落としたタスクのテストが次のタスクの base に残り、ABORT が連鎖した | 既知の失敗を持ち越す（v1 の S2 を標準にする。§5.2） |
| L4 | T4 で実装役の 9 回の呼び出しがすべて TTL（300 秒）で打ち切られ、利用量も失われた | §7 |
| L5 | `TickInput` のような振る舞いを持たないデータ型まで実装役に書かせ、whitelist の外に出たとたんに門と食い違った | データ型は契約の生成物にして、実装役の仕事から外す（§3.1） |
| L6 | A は 2 回のスモークとも、5 タスクを 1 回ずつの呼び出しで通した（4.8132 USD・4.7650 USD） | 比較の相手として十分に強い。v2 の B は、門の誤爆ゼロを最低条件にする |

## 2. 全体の流れ

```
[事前] Claude（分解役）
   ├─ 契約の宣言：単位定義の interface（JSON）…… いまと同じ形。T1〜T5 の累積
   └─ 性質の宣言：properties.json（§4）…… GDD の規則 ID と参照データ（PR-xx）で根拠を示す
          │
          ▼ 機械的な生成（LLM を使わない、費用 0）
   ├─ contractgen：データ型の C# 実装と、振る舞いの型の C# interface（読み取り専用）
   ├─ 契約の探針（ContractProbe.cs）：コンストラクタとメンバーの形をコンパイルで確かめる
   └─ propgen：性質テスト（シード付きのランダム操作列 ＋ GDD の参照モデル）
          │
[各タスク] 実装役（Flash）…… A も B も同じ契約・同じ性質テストを見る
          │
[門（B のみ）] whitelist（git）→ dotnet build（契約の探針を含む）→ 性質テスト（公開シード）
               → 性質テスト（非公開シード）→ 不変条件（Outer）→ 持ち出し
[測定（A・B 共通）] 性質テストの全件（公開＋非公開シード）・不変条件・差分・トークン・秒
```

## 3. 契約（読み取り専用）

### 3.1 型の 2 分類

単位定義の `interface.types` を、振る舞いの有無で 2 つに分ける。`kind` と `members` から機械的に決まる。

| 分類 | 条件 | 生成物 | 実装役 |
|:--|:--|:--|:--|
| データ型 | `enum`、または `struct` でメンバーが ctor と property だけ | **C# の実装そのもの**（ctor が各 property に代入するだけ） | 書かない（読み取り専用） |
| 振る舞いの型 | `class`、または method を持つ型 | **C# の interface**（`I<型名>`）。method と property のシグネチャ | `class <型名> : I<型名>` を実装する |

T1〜T5 の interface（`experiments/b4_ab/units/T5.json` の累積）に当てはめると、次のようになる。

| 型 | 分類 | 生成するファイル（`Game/Assets/Core/Contracts/`） |
|:--|:--|:--|
| `GamePhase`・`MinoType`・`Rotation` | データ型（enum） | `GamePhase.cs`・`MinoType.cs`・`Rotation.cs` |
| `ActiveMino` | データ型（struct。ctor と 4 property） | `ActiveMino.cs` |
| `TickInput` | データ型（struct。ctor と 6 property） | `TickInput.cs` |
| `GameState` | 振る舞いの型（ctor・property・`Tick`・`InjectSeed`） | `IGameState.cs` |

v1 の T2・T3 の失敗（L1）は、`TickInput` が実装役の仕事でなくなるので、構造的に起きなくなる。

### 3.2 生成する C# の形（例）

以下の例の名前空間（`Game.Core`）は仮置き。contractgen は project.json の設定から決める（総監督は Core を見ないので、ここでは確かめていない）。

`TickInput` の生成物。実装役は触れない。

```csharp
// 生成物（contractgen）。手で書き換えない。元は単位定義の interface。
namespace Game.Core
{
    public readonly struct TickInput
    {
        public TickInput(bool left, bool right, bool rotateCw, bool rotateCcw, bool softDrop, bool hardDrop)
        {
            Left = left; Right = right; RotateCw = rotateCw;
            RotateCcw = rotateCcw; SoftDrop = softDrop; HardDrop = hardDrop;
        }
        public bool Left { get; }
        public bool Right { get; }
        public bool RotateCw { get; }
        public bool RotateCcw { get; }
        public bool SoftDrop { get; }
        public bool HardDrop { get; }
    }
}
```

`GameState` の生成物。実装役は `public class GameState : IGameState` を書く。

```csharp
// 生成物（contractgen）。手で書き換えない。
namespace Game.Core
{
    public interface IGameState
    {
        GamePhase Phase { get; }
        ActiveMino? ActiveMino { get; }
        System.Collections.Generic.IReadOnlyList<MinoType> NextQueue { get; }
        int Score { get; }
        int LinesCleared { get; }
        int TickCount { get; }
        int BlockCount { get; }
        int LockedMinoCount { get; }
        void InjectSeed(uint seed);
        void Tick(TickInput input);
    }
}
```

### 3.3 コンストラクタの形の検査：契約の探針

C# の interface はコンストラクタを縛れない。代わりに、テストプロジェクトに**呼ぶだけで何も検査しない**ファイルを生成し、形が違えばコンパイルで落とす。

```csharp
// 生成物（contractgen）。実行しない。コンパイルが通ることが検査。
namespace Game.Core.Tests.Contracts
{
    internal static class ContractProbe
    {
        private static void Shapes()
        {
            IGameState a = new GameState();
            IGameState b = new GameState(GamePhase.Playing, (ActiveMino?)null);
        }
    }
}
```

- `pipeline.gate_static` のシグネチャの照合（`missing_symbols`）はやめる。禁止パターン・skip 属性の検査（`gate_static_common`）は残す（シグネチャとは別のもの）
- ビルドが落ちたときの診断の射影（`diagproj`、ADR-003 §3.11）は、コンパイラのエラーを interface の識別子に射影するので、そのまま使える

### 3.4 読み取り専用の担保

- 生成物は whitelist に入れない。書き換えれば、いまの `gate_whitelist`（`git status` による。文字列の走査ではない）が「許可外の変更」として弾く
- 測定器は、測る前に生成物を凍結したものに戻し（いまの `place_frozen_tests` と同じ）、書き換えを `tests_tampered` と同じく記録する。A は門を通らないので、ここで揃える
- 総監督は生成物を見ない。生成器の入力（JSON）と、生成物の sha256 だけを扱う（ADR-002 の防壁①）

### 3.5 観測用の API（未決。§8 の 1）

性質で「盤面外に出ていない」「固定ブロックに重ならない」を確かめるには、ミノの 4 マスと盤面の占有を外から見る必要がある。いまの interface には、盤面を読むメンバーも、固定ブロックのある盤面から始めるコンストラクタも無い。

- 案：`IGameState` に `bool IsOccupied(int x, int y)` を足す。復元用コンストラクタに固定ブロックの一覧（`IReadOnlyList<(int X, int Y)> locked`）を足す
- 足さなければ、性質はミノの位置と数の保存則だけになり、T1（壁と固定ブロックの判定）・T5（キック）の検査が弱くなる

## 4. 性質の宣言（properties.json）

いまの `invgen`（不変条件の宣言 → シード付きのランダム操作列のテスト）を広げる。**Claude が書くのは宣言（JSON）だけ**で、C# は propgen が固定テンプレートから作る。

### 4.1 宣言の形

```json
{
  "schema": 1,
  "gdd_sha256": "<GDD v10 の sha256>",
  "reference": {
    "shapes": {"param": "PR-18..PR-51"},
    "kicks_jlstz": {"param": "PR-xx"},
    "kicks_i": {"param": "PR-xx"},
    "board": {"width": {"param": "PR-06"}, "height": {"param": "PR-08"}}
  },
  "generators": {
    "input": {"TickInput": {"left": "bool", "right": "bool", "rotateCw": "bool", "rotateCcw": "bool",
                            "softDrop": "bool", "hardDrop": "bool"}},
    "start": {"construct": "GameState", "args": {"phase": ["Playing"], "activeMino": "any_in_board"}}
  },
  "steps": 50,
  "seeds": {"public": 5, "hidden": 20},
  "properties": [
    {"id": "P-T1-01", "task": "T1", "rule": "RL-16",
     "given": "input.Left && !input.Right && fits(mino.moved(-1, 0))",
     "then": "after.ActiveMino.X == before.ActiveMino.X - 1"},
    {"id": "P-T1-02", "task": "T1", "rule": "RL-40",
     "given": "true",
     "then": "all_in_board(after.ActiveMino)"},
    {"id": "P-T5-01", "task": "T5", "rule": "RL-xx",
     "given": "input.RotateCw && !fits(mino.rotated(+1))",
     "then": "after.ActiveMino == first_fit(kicks(mino.Type, before.Rotation, +1), mino.rotated(+1)) ?? before.ActiveMino"}
  ]
}
```

- `reference` は GDD の外部パラメーター（PR-xx）から**機械的に読む**。表の値を宣言に書き写さない（写し間違いが入らない。2026-09-17 の前例）
- `fits`・`all_in_board`・`kicks`・`first_fit` は、参照データの上で propgen が生成する**参照モデル**（C# の小さな関数群。テストプロジェクト側に置き、実装役には読み取り専用）
- 性質は **GDD v10 の最終形で真であること**だけを書く。後のタスクで振る舞いが変わる性質（例：T1 の時点では「Y は変わらない」だが、T2 の自然落下で偽になる）は書かない。こうすれば `superseded` が要らない
- 性質は `task` で有効になる時点を持つ。タスク k の受入は「`task` が k の性質の全部」、P2P は「`task` が k より前の性質の全部」

### 4.2 性質の強さの担保

性質だけだと「何もしない実装」が通りうる（例：「左に動いたなら X は 1 減る」は、動かなければ真）。これを防ぐため、宣言に 2 つの規則を課す。

1. **活性の性質を必ず 1 つ持つ**：`given` が成り立つとき結果を一意に決める性質（P-T1-01 のように `==` で終わる）を、タスクごとに 1 つ以上
2. **`given` が一度も成り立たない性質は失敗とみなす**：ランダム系列の中で前提が 0 回しか成り立たなければ、その性質は何も検査していない。propgen は前提の成立回数を数え、下限を下回れば落とす

### 4.3 反例

いまの `invgen` と同じく、最短の接頭辞の操作列を 1 行で出す。実装役に返すのは、性質の ID・規則の ID・操作列・`before` と `after` の差分の要約だけ。参照モデルの中身は返さない。

- **公開シード**（5 本）：実装役に見せる。A・B とも、落ちたらこの反例を受け取る
- **非公開シード**（20 本）：B の門と、両条件の測定だけに使う。公開シードへの過剰適合を検出する

## 5. 門と測定

### 5.1 B の門（pipeline）

| 順 | 門 | v1 から | 落ちたとき実装役に返すもの |
|:--|:--|:--|:--|
| 1 | whitelist（git） | 同じ | 許可外のファイルの一覧 |
| 2 | 禁止パターン・skip 属性 | 同じ（`gate_static_common`） | 該当の行 |
| 3 | `dotnet build`（契約の探針を含む） | シグネチャの照合を置き換える | 診断の射影（`diagproj`） |
| 4 | 性質テスト（公開シード） | 受入テスト（例示）を置き換える | 反例（§4.3） |
| 5 | 性質テスト（非公開シード） | 非開示ゴールデンの位置 | 「非公開の反例あり」だけ |
| 6 | 不変条件（Outer） | 同じ | 反例の行 |
| 7 | 差分の予算 | 同じ | 超過量 |

### 5.2 積み重ねの扱い（v1 の S1・S2 を標準にする）

- 実装前から通っている性質は正常とする（偽テストの概念を性質テストには当てはめない）。例示テストが無いので、「実装前に落ちるはず」という前提がそもそも無い
- 前のタスクの終わりに落ちていた性質は、既知の失敗として次のタスクの base から外す。A の測定器も「前のタスクの終わりに通っていたもの」だけを P2P に数えるので、同じ扱い

### 5.3 A への渡し方（裁定 1）

- A の作業ツリーにも、同じ契約の生成物・契約の探針・性質テスト（公開シード）・参照モデルを置く。初回のプロンプトは「契約の型を実装し、性質テストを通す」
- A の再試行には、公開シードの反例（§4.3）を渡す。B の内側ループと同じ関数で作る
- A と B で違うのは、会話を積むか・試行ごとにリセットして門の下で走るかだけ

## 6. 事前投資の計上

| 事前投資 | 書き手 | 区分 | 測り方 |
|:--|:--|:--|:--|
| 単位定義の interface（契約の宣言） | Claude | shared | 分解役の telemetry（v1 の `precost.py` を流用） |
| 性質の宣言（properties.json） | Claude | shared（A も同じ性質テストを見る） | 同上 |
| contractgen・propgen・参照モデルの生成 | 機械 | — | 0（LLM を使わない） |
| 不変条件の宣言・門の設定 | 既存資産の流用 | B_only = 0（v1 と同じ方針） | — |

v1 の分解役は、受入データ（given・op・期待値の例）を大量に書いたため、出力が 126,549 トークン、8.45305 USD になった（`precost.json`）。v2 の宣言は interface（v1 と同じ）と性質の数十行なので、出力は大きく減る見込み。ただし推測なので、設計の段階では数字を約束しない。V2-4 で測る。

## 7. 実装役の TTL と道具の解禁（裁定 4：設計の中で決める）

推奨：**最初の呼び出しは編集だけ（v1 の裁定 2 のまま）、再試行でも道具は解禁しない。TTL は 300 秒のまま。**

- v2 では、実装役が知りたいこと（どの性質が、どの操作列で、どう破れたか）を門が反例として返す。実装役が自分で `dotnet test` を回して得られる情報は、公開シードの反例と同じもの
- v1 の T4 では、解禁した再試行の 9 回がすべて 300 秒で打ち切られ、利用量も残らなかった（L4）。解禁は費用と時間を読めなくする
- 反例だけでは直せない例が本走で出たら、そのときに解禁を見直す（反例の表示の改善を先に試す）

## 8. 裁定が要ること（人間）

1. **観測用の API（§3.5）**：`IGameState` に `IsOccupied` と、固定ブロック付きの復元用コンストラクタを足すか。足さないと T1 の固定ブロックの判定と T5 のキックを性質で縛れない。推奨は足す
2. **T1〜T5 の分け方**：データ型を契約に移すので、v1 の T1 の仕事（`TickInput` を作る）が消える。T1 は「左右移動の振る舞い」だけになる。タスクの定義を v2 用に作り直してよいか
3. **例示テストの扱い**：裁定 2 は「性質の宣言と GDD の表」。GDD の具体例（§8-4 のような例示）を、性質とは別の少数の例示テストとして残すか、捨てるか。推奨は捨てる（v1 の失敗の半分は例示テストの前提から来た）

## 9. 実装の順序と PR の分け方

| 順 | 内容 | 門・指標に触れるか |
|:--|:--|:--|
| V2-1 | contractgen：interface（JSON）→ データ型の C#・`I<型名>`・契約の探針 | 触れない（生成器） |
| V2-2 | propgen：properties.json → 性質テスト（公開・非公開シード）と参照モデル。`invgen` を広げる | 触れない（生成器） |
| V2-3 | pipeline：シグネチャの照合をやめ、門を §5.1 に置き換える。S1・S2 を標準にする | **触れる**（別 PR、独立レビュー） |
| V2-4 | T1〜T5 の契約と性質の宣言（Claude）。事前投資を測る | 触れない（入力） |
| V2-5 | 乾式の走行（T1、A・B を 1 回ずつ） | — |
| V2-6 | スモーク（T1〜T5、N=1）→ 本走（N=4） | — |
