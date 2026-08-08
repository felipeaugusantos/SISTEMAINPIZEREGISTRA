param([switch]$IniciarAgora)

$ErrorActionPreference = "Stop"
$Projeto = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot "iniciar-acesso-externo.ps1"
$Estado = Join-Path $env:LOCALAPPDATA "ZeRegistra"
$Config = Join-Path $Estado "whatsapp.env"
New-Item -ItemType Directory -Path $Estado -Force | Out-Null

if (-not (Test-Path -LiteralPath $Config)) {
    @"
# Credenciais do WhatsApp Cloud API da Meta. Nao envie este arquivo para o Git.
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_TO=55DDDNUMERO
WHATSAPP_GRAPH_VERSION=v23.0
# Para notificacao proativa, informe um modelo aprovado com um parametro no corpo.
WHATSAPP_TEMPLATE_NAME=link_acesso_externo
WHATSAPP_TEMPLATE_LANGUAGE=pt_BR
"@ | Set-Content -LiteralPath $Config -Encoding UTF8
}

$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argumentos = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
$acao = New-ScheduledTaskAction -Execute $powershell -Argument $argumentos -WorkingDirectory $Projeto
$gatilho = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$configuracao = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ZeRegistra-AcessoExterno" -Action $acao -Trigger $gatilho `
    -Settings $configuracao -Principal $principal `
    -Description "Inicia Docker, API INPI, Cloudflare Tunnel e envia o novo link por WhatsApp." -Force | Out-Null

Write-Host "Tarefa ZeRegistra-AcessoExterno instalada."
Write-Host "Configure as credenciais em: $Config"
if ($IniciarAgora) { Start-ScheduledTask -TaskName "ZeRegistra-AcessoExterno" }
