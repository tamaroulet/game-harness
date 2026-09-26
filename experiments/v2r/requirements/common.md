## 全タスク共通の制約（再実験 v2r。この節は T1〜T10 のすべてに同じ文面で付く）

- 仕様は GDD v10（`docs/gdd/source.md`）と構造化仕様（`docs/spec/spec.md`）に従う。一般的な落ちものパズルの常識で仕様を補わない
- 入力は、1 ティック分の入力をまとめた struct `TickInput` を引数に取る 1 つの操作 `GameState.Tick(TickInput input)` で渡す。`TickInput` のコンストラクタの引数は `left`・`right`・`rotateCw`・`rotateCcw`・`softDrop`・`hardDrop`（すべて bool）の 6 つで、この名前と順序は全タスクで変えない。まだ振る舞いを持たない入力も、この形で最初から受け取る
- 入力の無いティックは、`TickInput` のすべてを false にして `GameState.Tick` を呼ぶ
- 事前状態は、`GameState` の復元用コンストラクタで作る
- 既存の公開メンバーの名前・型・引数を変えない。消さない
- 保存則（RL-36）が常に成り立つこと。`GameState.BlockCount`・`GameState.LockedMinoCount`・`GameState.LinesCleared` を公開し続ける
- **後のタスクは、前のタスクで作った処理（固定・得点・落下・入力の処理）を書き換える。前のタスクの性質は、書き換えた後も成り立ち続けなければならない**
- 差分は追加 250 行以下かつ削除 100 行以下（feature）
