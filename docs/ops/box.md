# 箱（実装役・門・採点）の用意と確かめ

実装役が書いたコードは、WSL2 の Docker の箱の中だけで走らせます（v3 §8 の E4）。回す側は Windows のままで、箱とのやり取りは標準入出力の tar だけです。
部品は `harness/box.py`・`harness/box_tree.py`・`box/`・`config/box.json` です。

## 用意（1 回だけ。やり直せる）

作業ディレクトリはリポジトリの根です。

```
python -m harness.box setup
wsl --terminate Ubuntu
python -m harness.box build
```

- `setup` は WSL2 の中を root で整えます：bench 利用者（uid 1000、docker の組に入れない）、agy（`config/box.json` の版、root の持ち物の置き場）、Docker Engine、`/etc/wsl.conf`（Windows のフォルダと実行ファイルを見えなくする）。
- `/etc/wsl.conf` は `wsl --terminate` の後に効きます。効いた後は、WSL2 から `/mnt/c` は見えません。Windows から `wsl.exe` で呼ぶ向きは動きます。
- `build` は像を 2 つ造ります（箱と中継）。`box/` か `config/box.json` の agy の値を変えたら、造り直します。造り直さないと、走りの前の確かめで止まります。

## ログイン（人の作業）

agy の認証は WSL2 の bench 利用者が持ちます。定額の方を選びます（Pay As You Go は選ばない）。

```
wsl -d Ubuntu -u bench --cd /home/bench -- bash -lc "agy"
```

Google のサインインが済んだら `/quit` か Ctrl+C で抜けます。

## 確かめ（利用枠を使わない）

```
python -m harness.box check
```

合格と出れば、次のことを確かめています。

- 像が今の `box/` と設定から造られた。
- 箱の利用者は bench で、ルートは読み取り専用で、`/mnt` は空で、WSL の部品が見えない。
- 直の接続が届かず、名前の解決もできない。
- 実装役の箱で agy の認証が読め、試験の箱には認証が無い。
- 中継が、許した宛先を通し、許していない宛先を拒む。

## 箱の決まり

| 箱 | 入れるもの | ネット | 外へ返すもの |
|:--|:--|:--|:--|
| 実装役 | 作業の木の写し（`.git` を除く）、agy の認証 | 中継だけ（`box/proxy/allow`） | 変更したファイル |
| 門 | 作業の木の写し | なし | 試験の出力 |
| 採点 | 段の木の写し、隠しテスト | なし | 結果の 1 行 |

- どの箱も 1 回ごとに使い捨てです。ホームと `/tmp` は tmpfs で、実装役の作業の木は名の無いボリュームです（容器と一緒に消えます）。
- 実装役の箱には使い捨ての git があります（開始の木を 1 コミット）。外の本当の `.git` は見せません。
- 受け取らないもの：`.git` の下、リンク、特殊なファイル、木の外を指す名前、Windows で作れない名前、大文字と小文字だけが違う 2 つ目、大きすぎるファイル、課題のパッケージと試験の外に新しく作ったファイル。

## 分かっている限界

- 実装役のコードは採点の箱の中で隠しテストと同じプロセスで走るので、結果の行を偽れます。敵対的な実装役は想定していません。
- 実装役は箱の中で自分の agy の認証を読めます。出せる宛先は中継で絞っています。
- WSL2 の root は、Windows のドライブをマウントできます（`automount` を切っても、root なら戻せる）。箱は権限を落としているので届きません。Docker の操作は root だけです。

## 走りの通し方

`stage --box` の印があるタグは、run・finish・bench_pair が印に従って箱で走ります。今の Windows の基準のタグは、印が無いのでそのまま採点し直せます。

```
python -m harness.bench_chain stage --tag <t> --set dev --bases B07,B08,B09 --box
python -m harness.bench_chain run --tag <t> --repo <クローン> --base <rev> --staged <stage が出した置き場> --out <置き場> --implementer config/bench_implementer_agy.json
python -m harness.bench_chain finish --tag <t> --repo <クローン> --out <置き場>
```

- 走りは操縦士のアカウントで回します。実装役が書いたコードは箱の外で走りません（実装役・門・積む前の全件・進捗の記録の検証・採点の 5 か所）。
- 実装役はコマンドを使えます（`--sandbox` は付けません。1.5.3 の `--sandbox` はコマンドを止めないので、コマンド無しの走りは作りません）。
- 新しく作ったファイルのうち、課題のパッケージと `tests/test_<パッケージ>*` の外のものは受け取りません。受け取らなかった名は試行の記録（`box_dropped`）に残り、門に落ちたときは次の試行の入力で実装役に知らせます。
- agy の `--print-timeout` は、stream-json の入力では終了コード 0 のまま、標準エラーの `AGY_ERROR` の `reason` が `print_timeout` になります。時間切れとして扱います。
