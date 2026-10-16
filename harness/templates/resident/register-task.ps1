<#
スケジューラの常駐を、Windows のタスクスケジューラ（ログオン時）に登録する。

**実行すると OS に設定が残る。** 人間が内容を確認してから実行する（-WhatIf で表示だけ）。

    powershell -ExecutionPolicy Bypass -File harness\templates\resident\register-task.ps1 -Project unity-2d -WhatIf
    powershell -ExecutionPolicy Bypass -File harness\templates\resident\register-task.ps1 -Project unity-2d

取り消し:

    Unregister-ScheduledTask -TaskName "game-harness scheduler (unity-2d)" -Confirm:$false

常駐の状態を見る・止める:

    python harness\scheduler.py --project unity-2d --status
    python harness\scheduler.py --project unity-2d --stop

pythonw を直接起動する（.cmd を挟むとコンソールの窓が一瞬出るため）。
出力は <out_dir>\scheduler-YYYYMMDD.log に出る（scheduler.py が付け替える）。
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Pythonw = "",
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"

if (-not $Pythonw) {
    $Pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
}
$harness = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$scheduler = Join-Path $harness "scheduler.py"
if (-not (Test-Path $scheduler)) { throw "scheduler.py がありません: $scheduler" }
if (-not (Test-Path (Join-Path (Split-Path $harness -Parent) "projects\$Project\project.json"))) {
    throw "プロジェクト $Project がありません（projects\$Project\project.json）"
}

$taskName = "game-harness scheduler ($Project)"
$arguments = "`"$scheduler`" --project $Project --watch"
$commandLine = "`"$Pythonw`" $arguments"
# タスクスケジューラの実行コマンドは 261 字までしか保存されない（超えると黙って切れる）
if ($commandLine.Length -gt 261) {
    throw "コマンドが $($commandLine.Length) 字で、261 字を超えます。harness を短いパスに置いてください: $commandLine"
}

Write-Host "タスク名 : $taskName"
Write-Host "起動     : ログオン時（$env:USERDOMAIN\$env:USERNAME）"
Write-Host "コマンド : $commandLine"
if ($WhatIf) {
    Write-Host "（-WhatIf: 何も登録していません）"
    return
}

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument $arguments -WorkingDirectory (Split-Path $harness -Parent)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# 実行時間の上限を外す（既定の 72 時間で止められないように）。多重起動はロックでも防いでいる
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "game-harness の常駐スケジューラ（--watch）。停止は scheduler.py --stop" | Out-Null
Write-Host "登録しました。次のログオンから起動します（今すぐ起動するなら Start-ScheduledTask -TaskName `"$taskName`"）"
