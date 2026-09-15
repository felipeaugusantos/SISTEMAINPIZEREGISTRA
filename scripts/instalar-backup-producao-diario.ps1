$ErrorActionPreference = "Stop"
$Projeto = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot "baixar-backup-producao.bat"
if (-not (Test-Path -LiteralPath $Script)) {
    throw "scripts/baixar-backup-producao.bat nao encontrado -- crie/copie o arquivo antes de agendar."
}
# Achado FASE6-12 da auditoria (04/09/2026): o cron de producao na VPS roda
# as 05:00 UTC (02:00 Brasilia) -- 03:00 Brasilia da margem de sobra para o
# pg_dump terminar (banco de ~30GB, ~10-20min observados) antes de tentar
# baixar o dump mais recente para esta maquina.
$acao = New-ScheduledTaskAction -Execute $Script -WorkingDirectory $Projeto
$gatilho = New-ScheduledTaskTrigger -Daily -At "03:00"
$configuracao = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ZeRegistra-BackupProducaoDiario" -Action $acao -Trigger $gatilho `
    -Settings $configuracao -Principal $principal `
    -Description "Baixa diariamente o backup mais recente do banco de producao (VPS) para esta maquina -- copia fora da VPS, achado FASE6-12 da auditoria." `
    -Force | Out-Null
Write-Output "Tarefa ZeRegistra-BackupProducaoDiario instalada para 03:00 (roda mesmo se o PC estiver desligado no horario -- StartWhenAvailable dispara assim que ligar)."
