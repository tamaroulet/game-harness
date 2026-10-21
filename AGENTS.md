# game-harness の約束事（H ラインの実装役向け）

## 構成
- `harness/`：H ライン（`hline*.py`）、工廠とベンチ（`bench_*.py`・`planner.py`）、外側の周期（`hline_outer.py`・`bench_regress.py`・`weekly_report.py`・`outer_cycles.py`）。
- `tests/`：unittest の試験。`config/`：設定（`hline.json` など）。`docs/`：文書と台帳。

## コード
- H ラインのモジュールは `harness.hline` を import しない。依存は第 1 引数 `host` の属性として読む（試験の差し替えが効くように。循環を作らない）。
- `harness/` に `python -m` の入口を作るときは `sys.exit(exitcode.normalized(main))` にする。

## 試験
- 試験が `harness` のモジュールの属性を書き換えたら、試験の終わりに元に戻す（戻さないと、Gate 1 で無関係の試験が落ちる）。
- 試験はモジュールごとに別のプロセスで並列に走る。固定の一時ディレクトリ・ポート・順番に依らせない。gh・ネットワーク・実装役は差し替える。

## 触らないもの
- 保護パス（`config/protected_paths.json`）は変えない。`docs/progress.yaml` は `harness.progress` だけが変える。

## 実行環境
- Windows で、命令はシェルを通さない（`&&` を使わない）。
