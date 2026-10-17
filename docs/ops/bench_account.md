# ベンチ用の制限のアカウント（人が 1 回だけ行う）

ベンチの走行を、非公開オラクル・受信箱・本線の生ログを OS が読ませないアカウントで回す。管理者の PowerShell で、上から順に行う。以下の `bench` はアカウント名の例。

## 1. ローカルのアカウントの作成

```powershell
$pw = Read-Host -AsSecureString "bench のパスワード"
New-LocalUser -Name bench -Password $pw -PasswordNeverExpires -UserMayNotChangePassword
```

## 2. 3 つの場所への読みの拒否

`config/hline.json` の `oracles`・`inbox`・`out` に、読みの拒否を付ける。

```powershell
foreach ($p in 'C:\src\.local\oracles', 'C:\src\.local\inbox', 'C:\src\.local\out') {
    if (Test-Path $p) { icacls $p /deny "bench:(OI)(CI)(R)" }
}
```

## 3. ベンチの clone への読み書きの許し

```powershell
icacls C:\src\.local\bench /grant "bench:(OI)(CI)(M)"
```

## 4. そのアカウントでの claude の CLI のログイン

```powershell
runas /user:bench "cmd /k claude"
```

開いた窓で、claude のログインを 1 回済ませる。

### 4a. git の所有者の検査

ベンチの clone の持ち主は操縦士のアカウントなので、制限のアカウントの git は `detected dubious ownership` で止まる。
制限のアカウントで開いた窓（§4 と同じ `runas`）で、次を 1 回だけ実行する。そのアカウントの全体の設定にだけ書く。

    git config --global --add safe.directory *

`HOME`・`USERPROFILE` は替えない（そのアカウントの既定のプロファイルのまま。`harness/bench_run.py` の `child_env` は `GIT_CONFIG_GLOBAL` を設定しないので、この全体の設定が子プロセスの git にも効く）。

### 4b. agy のログイン

    runas /user:bench "cmd /k agy"

開いた窓で、agy の認証を 1 回済ませる。その後は窓なしで動く。工廠の走行は、計画役と監修役に claude、実装役に agy を使うので、§4 と §4b の両方のログインが要る。

### 4c. 止まった走行の後

止まった走行の run-id は使い回さない。次は新しい run-id で走らせる（同じ run-id は、残った作業の場所と `origin.git` の枝がぶつかる）。

## 5. 確かめ

```powershell
runas /user:bench "cmd /k cd /d C:\src\.local\bench && python -m harness.bench_guard check"
```

終了コードが 0 なら準備は済み。1 なら、出たパスの拒否が効いていないので、段 2 をやり直す。走行の後は、操縦士のアカウントで `python -m harness.bench_guard scan <dir>...` を走らせ、漏れの跡のある走行を無効にする。
