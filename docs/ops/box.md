# 箱（WSL2 の Docker）の用意と確かめ

箱は、ハーネスの実行環境のアダプタの 1 つです（[設計と仮説](../v4/design.md)の §5-1）。エージェントが書いたコードは、WSL2 の Docker の箱の中だけで走らせます。回す側は Windows のままで、箱とのやり取りは標準入出力の tar だけです。
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
- エージェントの箱で agy の認証が読め、試験の箱には認証が無い。
- 中継が、許した宛先を通し、許していない宛先を拒む。

## 箱の決まり

| 箱 | 入れるもの | ネット | 外へ返すもの |
|:--|:--|:--|:--|
| エージェント | 作業の木の写し（`.git` を除く）、agy の認証 | 中継だけ（`box/proxy/allow`） | 変更したファイル |
| 試験 | 作業の木の写し | なし | 試験の出力 |

- どの箱も 1 回ごとに使い捨てです。ホームと `/tmp` は tmpfs で、エージェントの作業の木は名の無いボリュームです（容器と一緒に消えます）。
- エージェントの箱には使い捨ての git があります（開始の木を 1 コミット）。外の本当の `.git` は見せません。
- 受け取らないもの：`.git` の下、リンク、特殊なファイル、木の外を指す名前、Windows で作れない名前、大文字と小文字だけが違う 2 つ目、大きすぎるファイル、呼び手が許していない新しいファイル。
- agy の `--print-timeout` は、stream-json の入力では終了コード 0 のまま、標準エラーの `AGY_ERROR` の `reason` が `print_timeout` になります。時間切れとして扱います。

## 分かっている限界

- エージェントのコードは試験の箱の中で試験と同じプロセスで走るので、結果を偽れます。敵対的なエージェントは想定していません。
- エージェントは箱の中で自分の agy の認証を読めます。出せる宛先は中継で絞っています。
- WSL2 の root は、Windows のドライブをマウントできます（`automount` を切っても、root なら戻せる）。箱は権限を落としているので届きません。Docker の操作は root だけです。
- 像（`box/Dockerfile`）は、agy と git だけを入れた土台です。公式のベンチの課題の像に agy を載せる形は、[実験と計画](../v4/experiments.md)の 0-b で作ります。v3 の鎖のベンチを箱で走らせた道具と手順は、タグ `archive/pre-v4` にあります。
