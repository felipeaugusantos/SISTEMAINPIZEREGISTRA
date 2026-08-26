<#
Aplica e valida os gates de escala e qualidade do Zé Registra.

Uso:
  .\scripts\aplicar-p2.ps1
  .\scripts\aplicar-p2.ps1 -AplicarMigrations -ExecutarE2E
  .\scripts\aplicar-p2.ps1 -BackendStorage s3 -ExecutarE2E
#>
[CmdletBinding()]
param(
    [switch]$AplicarMigrations,
    [switch]$ExecutarE2E,
    [ValidateSet("local", "s3")]
    [string]$BackendStorage = "local"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Invoke-Step([string]$Nome, [scriptblock]$Acao) {
    Write-Host "`n==> $Nome" -ForegroundColor Cyan
    & $Acao
    if ($LASTEXITCODE -ne 0) { throw "Falha em: $Nome (código $LASTEXITCODE)" }
}

if ($BackendStorage -eq "s3") {
    $obrigatorias = @("STORAGE_S3_BUCKET", "STORAGE_S3_ACCESS_KEY", "STORAGE_S3_SECRET_KEY")
    $ausentes = @($obrigatorias | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_)) })
    if ($ausentes.Count -gt 0) {
        throw "Backend S3 selecionado. Configure: $($ausentes -join ', ')"
    }
}
$env:STORAGE_BACKEND = $BackendStorage

if ($AplicarMigrations) {
    Invoke-Step "Aplicar migrations" { uv run alembic upgrade head }
}

Invoke-Step "Verificar migrations" { uv run alembic heads }
Invoke-Step "Ruff" { uv run ruff check app tests migrations }
Invoke-Step "Formatação" { uv run ruff format --check app tests }
Invoke-Step "Testes Python" { uv run pytest -q }

if ($ExecutarE2E) {
    if (-not (Test-Path (Join-Path $repo "node_modules"))) {
        Invoke-Step "Instalar dependências E2E" { npm.cmd ci }
    }
    Invoke-Step "Listar testes E2E" { npm.cmd run test:e2e -- --list }
    Invoke-Step "Executar testes E2E" { npm.cmd run test:e2e }
}

Write-Host "`nP2 validado com sucesso." -ForegroundColor Green
Write-Host "Backend de storage: $BackendStorage"
Write-Host "Tracing distribuído depende da infraestrutura OpenTelemetry configurada no ambiente."
