# agy の導入先を書き換えられなくする（ACL）

人間が 1 回だけ実行する手順です。agy の導入先（`config/agy_digests.json` の `install_dir`。以下 `<導入先>`）の
ファイルが、実装役に書き換えられたり差し替えられたりしないようにします。

ACL が未適用でも、実装役を起動する前の SHA-256 の照合（`harness/agy_pinned.py`）は単独で働きます。

## 適用

現在のユーザーに、書き込み（`WD`）・ディレクトリへのファイルの追加（`AD`）・削除（`DE`）・子の削除（`DC`）を拒否します。
読み取りと実行は拒否しないので、agy は今までどおり起動できます。

```
icacls "<導入先>" /deny "%USERNAME%:(OI)(CI)(WD,AD,DE,DC)"
```

## 確認

```
icacls "<導入先>"
python -m harness.agy_pinned
```

`icacls` の出力に `(DENY)` の行があり `WD`・`AD`・`DE`・`DC` が並んでいること、照合が `合格` と出ることを確かめます。

## 外し方

agy を更新するときは、先に拒否を外します。

```
icacls "<導入先>" /remove:d "%USERNAME%"
```

更新したあと、`python -m harness.agy_pinned --measure "<導入先>" <ファイル名>...` の出力で
`config/agy_digests.json` を承認つきの PR で書き直し、「適用」と「確認」を掛け直します。
期待値を書き換えるのは人間だけで、ハーネスのコードは書き換えません。
