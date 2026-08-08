$ErrorActionPreference = "Stop"
$Projeto = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot "backup-banco.ps1"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argumentos = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
$acao = New-ScheduledTaskAction -Execute $powershell -Argument $argumentos -WorkingDirectory $Projeto
$gatilho = New-ScheduledTaskTrigger -Daily -At "20:00"
$configuracao = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ZeRegistra-BackupDiario" -Action $acao -Trigger $gatilho `
    -Settings $configuracao -Principal $principal `
    -Description "Backup diario local do PostgreSQL do Ze Registra, com retencao de 14 dias." -Force | Out-Null
Write-Output "Tarefa ZeRegistra-BackupDiario instalada para 20:00."
