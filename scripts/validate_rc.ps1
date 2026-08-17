$ErrorActionPreference = "Stop"
Write-Host "[RC] testes Python"
uv run pytest -q
Write-Host "[RC] sintaxe JavaScript"
Get-ChildItem app/web/static -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
Write-Host "[RC] E2E (requer API disponível)"
npm run test:e2e
Write-Host "[RC] health"
$health = Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
if ($health.StatusCode -ne 200) { throw "Health check falhou" }
Write-Host "[RC] gates automatizados aprovados"
