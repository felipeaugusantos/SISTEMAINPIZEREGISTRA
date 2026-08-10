param(
    [int]$RetencaoDias = 14
)

$ErrorActionPreference = "Stop"
$Projeto = Split-Path -Parent $PSScriptRoot
$Destino = Join-Path $Projeto "backups"
New-Item -ItemType Directory -Path $Destino -Force | Out-Null
$data = Get-Date -Format "yyyyMMdd-HHmmss"
$nome = "inpi-$data.dump"
$temporario = "/tmp/$nome"

Push-Location $Projeto
try {
    $container = (& docker compose ps -q db).Trim()
    if (-not $container) { throw "Container do PostgreSQL nao esta em execucao." }
    & docker compose exec -T db pg_dump -U inpi -d inpi -Fc -f $temporario
    if ($LASTEXITCODE -ne 0) { throw "pg_dump falhou com codigo $LASTEXITCODE" }
    & docker compose exec -T db pg_restore -l $temporario | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "O arquivo gerado nao passou na validacao do pg_restore." }
    & docker cp "${container}:$temporario" (Join-Path $Destino $nome)
    if ($LASTEXITCODE -ne 0) { throw "docker cp falhou com codigo $LASTEXITCODE" }
} finally {
    if ($container) {
        & docker compose exec -T db rm -f $temporario 2>$null
    }
    Pop-Location
}

$destinoResolvido = (Resolve-Path -LiteralPath $Destino).Path
if (-not $destinoResolvido.StartsWith($Projeto, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Diretorio de backup fora do projeto: $destinoResolvido"
}
$limite = (Get-Date).AddDays(-$RetencaoDias)
Get-ChildItem -LiteralPath $destinoResolvido -Filter "inpi-*.dump" -File |
    Where-Object LastWriteTime -lt $limite |
    Remove-Item -Force

$arquivo = Get-Item -LiteralPath (Join-Path $Destino $nome)
if ($arquivo.Length -le 0) { throw "O arquivo de backup foi criado vazio." }
Write-Output $arquivo.FullName
