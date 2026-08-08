param(
    [Parameter(Mandatory = $true)][string]$Arquivo,
    [switch]$Confirmar
)

$ErrorActionPreference = "Stop"
if (-not $Confirmar) {
    throw "Restauracao cancelada. Execute novamente com -Confirmar."
}
$Projeto = Split-Path -Parent $PSScriptRoot
$origem = (Resolve-Path -LiteralPath $Arquivo).Path
$nome = [IO.Path]::GetFileName($origem)
$temporario = "/tmp/restore-$nome"

Push-Location $Projeto
$servicosParados = $false
try {
    Write-Output "Criando backup de seguranca antes da restauracao..."
    & (Join-Path $PSScriptRoot "backup-banco.ps1") -RetencaoDias 30 | Out-Null
    $container = (& docker compose ps -q db).Trim()
    if (-not $container) { throw "Container do PostgreSQL nao esta em execucao." }
    & docker cp $origem "${container}:$temporario"
    if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel copiar o backup para o container." }
    & docker compose stop api rpi-sync worker | Out-Null
    $servicosParados = $true
    & docker compose exec -T db pg_restore -U inpi -d inpi --clean --if-exists --no-owner $temporario
    if ($LASTEXITCODE -ne 0) { throw "pg_restore falhou com codigo $LASTEXITCODE" }
} finally {
    if ($servicosParados) {
        & docker compose up -d api rpi-sync worker | Out-Null
    }
    Pop-Location
}
Write-Output "Restauracao concluida e servicos reiniciados."
