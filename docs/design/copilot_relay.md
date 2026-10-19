# 副操縦士と総監督の中継（人間のコピペの解消）

- 状態: 起草（2026-10-19）。操縦士の承認まで、§5 の段 R1 以降のコードは書かない
- 関わる文書: `docs/design/foundation_v3_review.md`（改訂 6）§3〜§5（受信箱）、`harness/director_launch.py`（総監督の無人の起動の関所、W-84）、`harness/hline_room.py`（受信箱の設定）

## 1. 今の流れと、なくしたいもの

| 向き | 今 | 中身 |
| --- | --- | --- |
| 副操縦士 → 総監督 | 操縦士が Antigravity の出力をコピーし、受信箱で起こした総監督のセッションに貼る | 指示・検討の依頼・What の素案 |
| 総監督 → 副操縦士 | 操縦士が総監督の応答をコピーし、Antigravity に貼る | 報告・判断・置いた What の名前 |

なくすのは「コピーして貼る」手間だけ。次の 3 つは操縦士の手に残す（§3.4）。

- 承認（`--approve`・`ms4:approved`・設計文書の「操縦士・副操縦士の承認」）
- 受信箱の直下への What の投入の可否（総監督だけが置く。副操縦士は置かない）
- 中継そのものの停止と再開

## 2. 前提として実測したこと

- 総監督の部屋（`C:\src\.local\inbox\.claude\settings.json`）はシェルを拒否し、書き込みは受信箱の中だけに限る。拒否の列は `harness/hline_room.py:13-29` が **`setup` を走らせた時点のディレクトリの一覧** から作る。後から `C:\src` や `C:\src\.local` の下に作ったディレクトリは、拒否の列に入らない
- H ラインは受信箱の **直下** の `*.md`（`report.md`・`TODO.md`・`CLAUDE.md` を除く）だけを What として取る（`harness/hline_queue.py:67`）。下のディレクトリ（`drafts/`・`notes/` など）は取らない
- 無人の起動の関所（`harness/director_launch.py`）がすでにある。起こす前に読む文書の字数を測り、`--max-turns` と TTL を掛け、報告されたモデルを設定と照合する（`model_pin`）
- agy（Antigravity の CLI）は stream-json の `init` にだけ使ったモデルを載せる（`harness/model_pin.py:99-107`）。照合するには `--output-format stream-json` が要る
- H ラインの定期起動はタスク スケジューラ（`harness/hline_cron.py`、10 分ごと、`IgnoreNew`）で、登録は人間の明示の承認の後だけ

## 3. 構成

### 3.1 置き場（郵便受け）

受信箱の下に 1 つのディレクトリを置く。総監督の部屋の書き込みの許しの内側に入り、H ラインの取り込み（直下だけ）には入らない。

```
C:\src\.local\inbox\relay\
  to_director\      副操縦士 → 総監督（未読）
  to_copilot\       総監督 → 副操縦士（未読）
  to_pilot\         どちらからも、操縦士に回すもの（承認・防壁の検出・上限の到達）
  done\             読み終えたもの（中継役だけが動かす）
  STOP              在れば中継は何もしない（操縦士の止めのスイッチ）
C:\src\.local\out\relay\
  state.json        スレッドごとの手数・最後の送り手・本文の sha256（中継役だけが書く）
  <時刻>.log        起こした CLI の生ログ
```

受信箱の外（`C:\src\.local\relay\` など）には置かない。§2 のとおり後から作ったディレクトリは総監督の拒否の列に入らないので、置き場の外に書けてしまう穴を作らないため。`out\relay\` は作った後に `python -m harness.hline setup` を走らせ直し、総監督の書き込みの拒否に入れる。

### 3.2 手紙の形

1 通 1 ファイル。ファイル名は `<連番 6 桁>-<送り手>.md`（例 `000012-copilot.md`）。書き手は `.tmp` に書いてから名前を変える（読み手が書きかけを読まない）。

```
---
thread: T-20261019-01
seq: 12
from: copilot          # copilot | director | pilot
kind: request          # request（返事が要る）| report（返事は要らない）| stop
hop: 3                 # このスレッドで何通目か
reply_to: 11
---
本文
```

返事を起こすのは `kind: request` だけ。`report` と `stop` には返事をしない。

### 3.3 中継役（`harness/relay.py`）

置き場と CLI のあいだに立つ唯一の書き手。段 R2 から使う（§5）。

1. `STOP` があれば何もせずに終える
2. ロック（`out\relay\relay.lock`。H ラインの `.hline.lock` と同じ作り）を取る。取れなければ終える
3. `to_director\` と `to_copilot\` の未読を連番の順に 1 通だけ取る
4. §4 の検査に掛ける。落ちたら `to_pilot\` に移して、そのスレッドを止める
5. 宛先の CLI を 1 回だけ起こす。手紙は標準入力で渡す（`director_launch` と同じ。cmd.exe が引数の改行で切るため）
   - 総監督：`claude -p --model claude-opus-5-5 --output-format json`、cwd は受信箱、`--max-turns` と TTL は `config/hline.json` の `director.director_budget` を流用する
   - 副操縦士：`agy` を `model_flag` と正確なモデルの ID と `--output-format stream-json` で、cwd は空の作業ディレクトリ（`out\relay\copilot_cwd\`）で起こす
6. 報告されたモデルを設定と照合する（`model_pin.require`・`pinned_models`）。報告が無い・違うときは返事を捨て、`to_pilot\` に回して止める
7. CLI の **標準出力の最後の応答** を、中継役が次の手紙として反対の受け箱に書く。CLI 自身には置き場を書かせない
8. 読んだ手紙を `done\` に移し、`state.json` を書く

CLI に置き場を書かせないので、Antigravity の権限の作りに頼らずに済む。副操縦士の書き込みの範囲を中継役が受け持つ。

起動はタスク スケジューラ（`hline_cron.py` と同じ作り。5 分ごと、`IgnoreNew`）。登録は人間の明示の承認の後だけ。

### 3.4 人間の手に残すもの（機械の判定）

次のどれかに当たる手紙は、宛先に渡さずに `to_pilot\` に回し、スレッドを止める。

- 本文に `--approve`・`ms4:approved`・「承認します」「承認した」のいずれかがある（エージェントは承認しない）
- 受信箱の直下に What を置くことを副操縦士が求めている（`マイルストーン:` と `タスク:` の行を持つ本文が `from: copilot` で来た）。副操縦士の What の素案は、総監督が `drafts/what/` で直して直下に置く
- `kind: request` で、宛先が `pilot`

## 4. ループを止める仕組み

| 仕組み | 条件 | 止めたときの動き |
| --- | --- | --- |
| 手数の上限 | 1 スレッドの `hop` が 6 を超える | `to_pilot\` に回して止める |
| 交互の強制 | 同じ送り手が続けて 2 通送る | 2 通目を `to_pilot\` に回す |
| 返事の要否 | `kind` が `report`・`stop` | 宛先の CLI を起こさない（届けるだけ） |
| 同じ本文 | 本文の sha256 が、同じスレッドの前の 2 通のどれかと同じ | 止める（H ラインの「前の試行と同じ差分」と同じ考え） |
| 1 日の上限 | 1 日に起こす回数が、総監督 4 回・副操縦士 8 回を超える | その日はその側を起こさない |
| 間隔 | 同じ側を前に起こしてから 10 分未満 | 次の起動に回す |
| 時間 | 1 回の起動の TTL（総監督 3600 秒、副操縦士 900 秒） | 返事を捨て、`to_pilot\` に回す |
| 止めのスイッチ | `relay\STOP` がある | 何もしない |
| H ラインとの排他 | `.hline.lock` が生きていて、手紙が受信箱の直下の What に触れる | 次の起動に回す |

数（6・4・8・10 分）は起草の値。段 R1 の 1 週の実測（1 スレッドの往復の数）を見て決め直し、`config/relay.json` に置く。

## 5. 実装の段

| 段 | 何を | 誰が回す | 必要なファイル |
| --- | --- | --- | --- |
| R0 | この文書の承認 | 操縦士 | この文書 |
| R1 | 置き場と手紙の形だけ。両者が自分で置き場を読み書きする。操縦士は「relay を見て」と言うだけ（コピペは無くなるが、起こすのは人間） | 操縦士が両者を起こす | `harness/templates/director_room/CLAUDE.md` に「中継」の節、Antigravity のワークスペースの規則（`~/.gemini/GEMINI.md` の例外か、ワークスペースの規則のファイル）、`harness/hline_room.py` の `setup` が `relay\` の下を作る |
| R2 | 中継役（§3.3）を手で走らせる。`python -m harness.relay once` | 操縦士が 1 回ずつ | `harness/relay.py`、`config/relay.json`、`tests/test_relay.py`（§4 の表の各行を 1 つずつ試す） |
| R3 | タスク スケジューラへの登録 | 登録は操縦士の承認の後 | `harness/relay.py` に `register`・`unregister`・`xml`（`hline_cron.py` と同じ形） |

R1 だけでコピペは無くなり、自動で起こすものが無いのでループも起きない。R2 と R3 は R1 の 1 週の実測（往復の数・止まった理由）を見てから進める。

## 6. 防壁①との関係

総監督は Core・テストコード・`dotnet test` の生ログを見ない。副操縦士はこの制限を持たないので、手紙がそれを運ぶ道になりうる。中継役は手紙を渡す前に次を検査し、当たれば `to_pilot\` に回す。

- `Game/Assets/Core/`・`tests/` の下のパス、`*.cs` のパス
- `dotnet test` の出力の目印（`Passed!`・`Failed!`・`Total tests:`）
- C# の宣言の形（`namespace `・`public class `）

文字列の検査なので漏れはある。言い換えられた中身は止められない。止められない分は、副操縦士の側の規則（R1）に「Core とテストの中身を手紙に書かない」と書いて補う。

## 7. 未決

1. Antigravity の IDE（対話）のセッションを、中継役から起こした agy（無人）で置き換えてよいか。IDE の会話の文脈は agy の無人の起動に引き継がれない。R1 は IDE のまま、R2 から無人にする案
2. 副操縦士の承認（`docs/design/v2r_protocol.md` などの「操縦士・副操縦士の承認」）を、中継で運んでよいか。§3.4 では運ばない（`to_pilot\` に回す）とした
3. 手数・回数の上限の値（§4）
