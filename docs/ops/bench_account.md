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

## 5. 確かめ

```powershell
runas /user:bench "cmd /k cd /d C:\src\.local\bench && python -m harness.bench_guard check"
```

終了コードが 0 なら準備は済み。1 なら、出たパスの拒否が効いていないので、段 2 をやり直す。走行の後は、操縦士のアカウントで `python -m harness.bench_guard scan <dir>...` を走らせ、漏れの跡のある走行を無効にする。
