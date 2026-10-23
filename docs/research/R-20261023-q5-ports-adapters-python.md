# Python の小さな自動化のコードで、外部のプロセス・文字コード・OS の差を境界（ports and adapters）に閉じ込める設計の、いまの実践と費用対効果

- 記録：R-20261023-q5-ports-adapters-python
- 調べた日時：2026-10-23T12:21:43+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 8・quote_not_found 4・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|
| C1 | subprocess の universal_newlines（text=True と同じ扱い）が True のとき、既定の文字コードは locale.getpreferredencoding(False) になる（3.3 で変更された記述） | fact | 不明 | quote_not_found | [subprocess — Subprocess management (Python 3 documentation)](https://docs.python.org/3/library/subprocess.html) |
| C2 | PEP 686（UTF-8 mode を既定にする提案）は Final で、対象版は Python 3.15 である | fact | 不明 | verified | [PEP 686 – Make UTF-8 mode default](https://peps.python.org/pep-0686/) |
| C3 | PEP 686 が採られた後も、環境変数 PYTHONUTF8=0 で UTF-8 mode を無効にできる | fact | 不明 | quote_not_found | [PEP 686 – Make UTF-8 mode default](https://peps.python.org/pep-0686/) |
| C4 | PEP 686 の議論では、UTF-8 mode は locale.getpreferredencoding(False) に影響するため、UTF-8 mode に関係なくロケールの文字コードを得る API が必要とされている | fact | 不明 | quote_not_found | [PEP 686 – Make UTF-8 mode default](https://peps.python.org/pep-0686/) |
| C5 | import-linter の独立性（independence）契約は、一連のモジュール同士が互いに依存しないことを確かめる | fact | 不明 | verified | [Contract types — Import Linter v2.0 documentation](https://import-linter.readthedocs.io/en/v2.0/contract_types.html) |
| C6 | import-linter のレイヤー（layers）契約は、上位のレイヤーが下位のレイヤーに依存してよく、その逆は許さない階層化を強制する | fact | 不明 | verified | [Contract types — Import Linter v2.0 documentation](https://import-linter.readthedocs.io/en/v2.0/contract_types.html) |
| C7 | Cockburn の 2005 年の記事は、アプリケーションが利用者・プログラム・自動テスト・バッチ処理に等しく駆動され、実行時のデバイスや DB から切り離して開発・テストできることを目標に挙げている | fact | 2005-09-04 | verified | [Hexagonal architecture — Alistair Cockburn](https://alistair.cockburn.us/hexagonal-architecture/) |
| C8 | 小規模で人の入れ替わりがあるチームでは、ヘキサゴナル構造の意思決定を理解する読み込みの費用（参入コスト）が高くなる、と JDriven の著者は主張している | opinion | 2022-10-04 | verified | [Why you should NOT use Hexagonal architecture — Kees Nederkoorn, JDriven Blog](https://jdriven.com/blog/2022/10/Why-you-should-NOT-use-Hexagonal-architecture) |
| C9 | 同記事の著者は、期限のプレッシャーで実装が層と隔離を飛ばし始める危険があると主張している | opinion | 2022-10-04 | quote_not_found | [Why you should NOT use Hexagonal architecture — Kees Nederkoorn, JDriven Blog](https://jdriven.com/blog/2022/10/Why-you-should-NOT-use-Hexagonal-architecture) |
| C10 | arXiv の論文は、2,042 件のオープンソースのリポジトリを、2 つの相補的な方法で分析したと述べている | fact | 2026-09-22 | verified | [An Empirical Analysis of Cross-OS Portability Issues in Python Projects (Silva, Farahat, d'Amorim)](https://arxiv.org/abs/2609.25531) |
| C11 | 同論文は、500 件のプロジェクトを OS をまたいでテストし、11.2% に OS 依存のテスト失敗があったと報告している | fact | 2026-09-22 | verified | [An Empirical Analysis of Cross-OS Portability Issues in Python Projects (Silva, Farahat, d'Amorim)](https://arxiv.org/abs/2609.25531) |
| C12 | 同論文は、OS 依存の不具合の種類として、ファイルやディレクトリの操作、プロセス管理、ライブラリの依存が最も多いと報告している | fact | 2026-09-22 | verified | [An Empirical Analysis of Cross-OS Portability Issues in Python Projects (Silva, Farahat, d'Amorim)](https://arxiv.org/abs/2609.25531) |

## 抜粋

- C1：「Changed in version 3.3: When universal_newlines is True, the class uses the encoding locale.getpreferredencoding(False) instead of locale.getpreferredencoding(). See the io.TextIOWrapper class for more information on this change.」
- C2：「With this change, Python consistently uses UTF-8 for default encoding of files, stdio, and pipes.」
- C3：「Users can still disable UTF-8 mode by setting `PYTHONUTF8=0` or `-X utf8=0`.」
- C4：「Since UTF-8 mode affects `locale.getpreferredencoding(False)`, we need an API to get locale encoding regardless of UTF-8 mode.」
- C5：「Independence contracts check that a set of modules do not depend on each other.」
- C6：「Layers contracts enforce a ‘layered architecture’, where higher layers may depend on lower layers」
- C7：「Allow an application to equally be driven by users, programs, automated test or batch scripts」
- C8：「the extra cost of entry to the team will require a lot of reading up on the design decisions」
- C9：「the implementation starts skipping layers and isolation.」
- C10：「analyzing 2,042 open-source repositories using two complementary approaches」
- C11：「Our cross-platform testing of 500 projects reveals that 11.2% exhibit OS-dependent test failures.」
- C12：「file/directory operations, process management, and library dependencies being most prevalent」

## 確かめられなかったこと

- 小規模コードで ports and adapters の境界が費用対効果に見合うかについて、定量的なデータ（保守時間・変更コストの実測）は見つからなかった。反対意見は個人の経験談が中心で、対照実験は確認できなかった。
- 境界を採る側の実践報告（規模・効果の実測）は、本文を取得できず未確認。Manning の章は検索結果の要約のみで、本文の主張は確認していない。
- Herbert Graca の記事が述べるとされる「原典はレイヤーに言及しない」という指摘は、本文を取得していないため未確認。
- subprocess の encoding="locale" オプションが Python 3.10 で追加されたという情報は、公式の subprocess ページには記載がなく、確認できなかった。
- Windows（コードページ 932、GetConsoleOutputCP 等）での subprocess の挙動について、一次の公式文書は見つからなかった。見つかったのはメーリングリストと CPython の issue の記述のみで、いずれも本文を取得していない。
- PEP 686 本文で subprocess の既定がどう変わるかは、取得した範囲では明示されていなかった。PEP の「Windows に影響が大きい」という記述は、議論の一部を含む可能性がある。
- PEP 686 の公開日は取得できず、不明。subprocess ページは生きた文書で、取得時点の版の内容である。
- import-linter の stable のページは 404 だったため、v2.0 のページを使った。現行版との文言の差は未確認。
- Linux と Windows の両方の CI で見つかった不具合の種類について、arXiv 論文の本文の内訳（カテゴリ別の件数）は取得できなかった。2026 年の Silva et al. の研究とされる分類は、別の記事での言及のみで、論文本体は確認できなかった。
- import-linter を実プロジェクトで採用した事例や、境界の検査を CI に組み込んだ実践は調査していない。

## 問い

# Python の小さな自動化のコードで、外部のプロセス・文字コード・OS の差を境界（ports and adapters）に閉じ込める設計の、いまの実践と費用対効果

1 台の Windows の PC で動く、数万行の Python の自動化のコード（git・gh・各種 CLI を subprocess で呼ぶ）について、次のことの一次の出典や実測を集めてください。

- 外部のプロセスの呼び出し・文字コード（UTF-8 とコードページ 932）・OS の差を、ports and adapters（hexagonal architecture）の境界に閉じ込める設計の実践
- 規模の小さいコードでは境界が過剰設計になる、という反対の意見や、費用対効果を論じたもの
- Python の subprocess の文字コードの既定（Windows のコードページ、PYTHONUTF8、Python 3.15 の UTF-8 モードの既定化）についての公式の文書
- import の向きを機械で検査する道具（import-linter など）の実践
- Linux と Windows の両方の CI を回すことで見つかった不具合の種類についての実測
