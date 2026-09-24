# v2（Contract-Driven Foundry）：契約駆動の防壁型

- 状態: 設計（方針と仕様）。V2-1（contractgen）・V2-2（propgen）・V2-3（pipeline の門）を実装し、V2-4 で T1〜T5 を宣言し、V2-5 で T1 の乾式の走行を通した（§11）
- 前提: B4-RUN v1.0 の評価はスモーク（b4-smoke-01・02）で打ち切り（2026-09-24、オーナー判断）。実測と総括は game-harness#63
- 決定（2026-09-24、オーナー裁定）:
  1. v2 でも条件 A（単一チャット）と比べる。**A にも同じ契約と性質テストを渡す**（同じ情報の原則）
  2. 性質（PBT の正解）は **Claude が宣言を書き**、キック表などの参照データは **GDD から写す**
  3. 読み取り専用の契約（C#）は、**単位定義の `interface`（JSON）から機械的に生成**する。誰も C# を手書きしない
  4. 実装役の TTL と再試行時の道具の解禁は、**v2 の設計の中で決める**（§7）
- 決定（2026-09-24、game-harness#64 への回答）:
  5. 観測用の API（`IsOccupied` と固定ブロック付きの復元用コンストラクタ）を**足す**（§3.5）
  6. T1〜T5 を v2 用に**定義し直す**。データ型は契約の生成物なので、実装役への入力は振る舞いだけにする
  7. 例示テストは**捨てる**。性質テストと参照モデルに一元化する
  8. 速さと省トークンの原則を 3 つ守る（§10）：事前投資の極小化、実装役は再試行でも道具を使わない、反例は 1 行

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

### 3.5 観測用の API（決定 5）

性質で「盤面外に出ていない」「固定ブロックに重ならない」を確かめるため、契約に次を足す（V2-4 で interface の JSON に書く）。

- データ型 `Cell`（`Cell(int x, int y)`、property `X`・`Y`）。単位定義のスキーマの型の書式（`TYPE_RE`）はタプルを書けないので、座標は `Cell` で表す。データ型なので contractgen が実装まで作る
- `IGameState.IsOccupied(int x, int y)`：固定ブロックがあるか。盤面外は GDD（RL-40）に合わせて true
- 復元用コンストラクタ `GameState(GamePhase phase, ActiveMino? activeMino, IReadOnlyList<Cell> locked)`：固定ブロックのある盤面から始める。性質テストの生成器が使う

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

### 4.3 反例（決定 8 の 3：1 行）

いまの `invgen` と同じく、最短の接頭辞の操作列を出す。実装役に返すのは次の 1 行だけで、ログ・スタックトレース・参照モデルの中身は返さない。

```
PROPERTY_FAIL id=P-T1-01 rule=RL-16 ops=[Right,RotateCw,SoftDrop] expected=ActiveMino.X:4 actual=ActiveMino.X:5
```

- `ops` は `TickInput` の真の項目名の列（1 ティック 1 要素、同時押しは `+` でつなぐ）。最短の接頭辞に縮める
- `expected`・`actual` は、性質の `then` に出てくる値のうち食い違ったものだけ
- 1 回の再試行に載せる反例は、落ちた性質ごとに 1 行、最大 5 行

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

## 7. 実装役の TTL と道具の解禁（決定 8 の 2）

**再試行を含めて、実装役にはビルド・テスト・探索を一律で使わせない。編集だけにする。** 1 回の呼び出しは 30〜60 秒で終わる想定で、TTL の 300 秒は安全弁として残す（打ち切りが出たら、それ自体を異常として記録する）。v1 の `tool_policy.RETRY`（game-harness#63）は V2-3 で消す。

- v2 では、実装役が知りたいこと（どの性質が、どの操作列で、どう破れたか）を門が反例として返す。実装役が自分で `dotnet test` を回して得られる情報は、公開シードの反例と同じもの
- v1 の T4 では、解禁した再試行の 9 回がすべて 300 秒で打ち切られ、利用量も残らなかった（L4）。解禁は費用と時間を読めなくする
- 反例だけでは直せない例が本走で出たら、そのときに解禁を見直す（反例の表示の改善を先に試す）

## 8. 裁定の記録

§8 の 3 点（観測用の API・タスクの定義し直し・例示テスト）は、2026-09-24 に決定 5〜7 として決まった（冒頭）。

## 9. 実装の順序と PR の分け方

| 順 | 内容 | 門・指標に触れるか |
|:--|:--|:--|
| V2-1 | contractgen：interface（JSON）→ データ型の C#・`I<型名>`・契約の探針 | 触れない（生成器） |
| V2-2 | propgen：properties.json → 性質テスト（公開・非公開シード）と参照モデル。`invgen` を広げる | 触れない（生成器） |
| V2-3 | pipeline：シグネチャの照合をやめ、門を §5.1 に置き換える。S1・S2 を標準にする | **触れる**（別 PR、独立レビュー） |
| V2-4 | T1〜T5 の契約と性質の宣言（Claude）。事前投資を測る | 触れない（入力） |
| V2-5 | 乾式の走行（T1、A・B を 1 回ずつ） | — |
| V2-6 | スモーク（T1〜T5、N=1）→ 本走（N=4） | — |

## 10. 速さと省トークンの原則（決定 8）

| # | 原則 | 守り方 |
|:--|:--|:--|
| 1 | 事前投資を極小にする | Claude が書くのは、interface の JSON と `properties.json`（規則 ID・パラメーター参照・短い式）だけ。長い文章と個別のケースは書かない。C# は 100% 機械で作る（contractgen・propgen）。目標は 1 USD 前後・数分。V2-4 で `precost.py` と同じ方法で測り、超えたら宣言の形を見直す |
| 2 | 実装役は編集だけ | 再試行でも道具を使わせない（§7）。目標は 1 回 30〜60 秒 |
| 3 | 反例は 1 行 | §4.3 の形だけを返す。再試行の入力を小さく保ち、実装役の文脈キャッシュを効かせる |

## 11. 実装の記録

- V2-1（contractgen）：`harness/contractgen.py`・`tests/test_contractgen.py`。生成物は、C# 9・警告をエラー扱いのクラスライブラリで、正しい形の `GameState` の仮実装とともにコンパイルが通る。コンストラクタの引数の型を変えた仮実装は、契約の探針が CS1503 で落とす（使い捨ての確認。手順は game-harness の PR 本文）
- V2-2（propgen）：`harness/propgen.py`・`tests/test_propgen.py`。参照データは、構造化仕様 §5 の PR-xx の**名前**（「形状 I 向き 0」「回転補正候補 J・L・S・T・Z 0 → R」「回転補正候補 O 全向き遷移」）から組み立て、欠けた形・遷移があれば生成を拒絶する。§4.1 の式に加えて `occupied_before`・`occupied_after` を足した。非公開シードは生成物に書かず、環境変数 `HARNESS_PROPERTY_SEEDS` で渡す（無ければ Ignore）。反例は、最初に破れた手で切ったうえで、手を 1 つずつ抜いても破れるなら抜いて縮める。開始状態の半分は壁際から始め、壁やキックに当たる操作を系列に含める
- V2-2 で分かったこと：ランダムな開始状態では「キックが要る回転」がまれで、`given` が回転全般の性質はキックしない実装を通しうる。キックが要る場合だけを前提にした性質（`!fits(rotated(m, 1)) && kick(m, 1) != null`）を足せば、成り立つ回数が 0 のとき `PROPERTY_VACUOUS` で落ちるので、網羅の不足が表に出る。V2-4 の宣言では、分岐ごとに前提を分けた性質を書く
- V2-3（pipeline の門）：
  - シグネチャの文字列照合（`gate_static` の `required_symbols`）を、テスト駆動の単位では廃止した。残すのは禁止パターンと skip 属性の検査だけ。形は、契約の探針を含む高速検査のビルドが決め、落ちたら `diagproj` の射影を返す。旧来のゴールデンの単位（unity-2d の MS3 の `core_impl`・`so_impl`）の検査は、この改修の対象外として残した
  - 実装役への失敗の知らせは、TRX から `PROPERTY_FAIL`・`PROPERTY_VACUOUS` の行だけを取り出し、同じ行をまとめて最大 5 行（`adapters/dotnet.failure_detail`）。性質テストでない失敗だけのときは、従来の名前と本文の要約
  - 非公開シードの段（`attempt_hidden_properties`）：公開シードの内側ループと受入の後、不変条件の前。`*_Hidden` のテストを、単位・試行・差分から決めたシード（既定 20 本、`gates.hidden_seeds` で変えられる）で走らせる。環境変数が効かずに Ignore のままなら合格としない。落ちたら「非公開シードで性質が破れました（反例は開示しません）」だけを返す
  - S1（実装前から通る受入テストは P2P として守る）と S2（`--known-failures`）を、`--local-only` に限らない標準の扱いにした
  - 実装役は再試行を含めて編集だけ（`tool_policy.EDIT_ONLY`。`RETRY` は削除）。A（ドライバ）も同じ文面。TTL は 300 秒のまま
  - 測定器（`harness/ab/measure.py`）は、まだ非公開シードを渡さない。A・B の測定で非公開シードを使うのは V2-5 で入れる
- V2-4（T1〜T5 の宣言）：`harness/declare.py`・`tests/test_declare.py`。分解役（claude）に道具を与えず（`--tools ""`）、要求文・構造化仕様 §1〜§5・v1 の interface・式の文法をプロンプト（約 18,000 字、標準入力）で渡した。宣言は `experiments/v2/contract.json`（v1 の interface ＋ `Cell`・`IsOccupied`・固定ブロック付きの復元用コンストラクタ）と `experiments/v2/properties.json`（T1〜T5 に 4 つずつ、計 20）
  - 事前投資の実測（`experiments/v2/results/precost.json`）：呼び出し 2 回（1 回目は式の書き方の誤り 2 件で検査に落ち、同じ会話で出し直し）、266.8 秒、1.202043 USD。各回 1 ターン（道具を使っていない）。v1 の分解役（8.45305 USD、1457.4 秒）の約 14%
  - 総監督のレビューで、前提の除外が足りない性質を 4 つ直した（P-T3-02・P-T3-03・P-T4-01・P-T4-02。LP-07 の ② の中で、左右移動と回転がハードドロップ・回転より先に効くのに、前提の `fits` が移動前の位置で見ていた）。直し方と理由は precost.json の `director_edits`。この手直しの費用は計測に入っていない。次の宣言で同じ誤りが出ないよう、`declare.py` の指示に ② の中の順序を足した（計測の後の変更）
  - propgen が確かめるのは式の形と型までで、性質が GDD の上で真かどうかは確かめない。正しい実装が無いうちは、性質の誤り（前提の除外の漏れ）は V2-5 の乾式の走行で、実装が正しいのに落ちる性質として初めて表に出る
- V2-5（乾式の走行 T1）：配管は `harness/ab/v2prep.py`（単位定義・テンプレート・マニフェストを機械的に作る）、`harness/ab/common.py`・`measure.py`・`driver.py`（v2 のマニフェスト、生成物の凍結と配置、測定での非公開シード、A の再試行の反例）、`unit_schema`（task_kind `property`）、`contractgen`（`existing`・`subdir`）、`propgen`（`tasks`）、`pipeline.new_test_files`（基準の確立で生成物も除く）。結果は `experiments/v2/results/dry_run_t1/`
  - 設計からの変更：契約は `Game/Assets/Core/Contracts/` ではなく Core の直下に置く（テストプロジェクトが Core の下位フォルダを拾うかを、総監督は確かめられないため）。base（falling-blocks#12・#17 の後）に既にあるデータ型（GamePhase・MinoType・Rotation・ActiveMino）は生成しない。単位定義の whitelist は `GameState.cs` だけ
  - v2-dry-02：A・B とも T1 を通した（受入 8/8、公開 4/4、P2P の破壊 0、不変条件の違反 0）。B は whitelist の門（実装役の `Board.cs` の編集）で試行 1 を捨て、試行 2 で実装役の 3 回のうち 2 回が TTL（300 秒）で打ち切られた。条件の仕事の秒は A 149.6、B 1267.1。B の費用は、打ち切られた 2 回の利用量が残らないので不明
  - 残る課題：実装役の 1 回の呼び出しが 245〜300 秒で、§10 の 2 の目標（30〜60 秒）に届いていない。TTL の打ち切りは利用量を失い、費用の比較を不能にする
