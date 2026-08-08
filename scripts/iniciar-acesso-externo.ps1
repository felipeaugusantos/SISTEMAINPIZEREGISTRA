param(
    [switch]$SemWhatsApp
)

$ErrorActionPreference = "Stop"
$Projeto = Split-Path -Parent $PSScriptRoot
$Estado = Join-Path $env:LOCALAPPDATA "ZeRegistra"
$Log = Join-Path $Estado "acesso-externo.log"
$ConfigPath = Join-Path $Estado "whatsapp.env"
$UrlPath = Join-Path $Estado "ultimo-link.txt"
$TunnelOut = Join-Path $Estado "cloudflared.out.log"
$TunnelErr = Join-Path $Estado "cloudflared.err.log"

New-Item -ItemType Directory -Path $Estado -Force | Out-Null

function Registrar([string]$Mensagem) {
    $linha = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Mensagem"
    Add-Content -LiteralPath $Log -Value $linha -Encoding UTF8
}

function Carregar-Config {
    $valores = @{}
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return $valores }
    foreach ($linha in Get-Content -LiteralPath $ConfigPath) {
        $texto = $linha.Trim()
        if (-not $texto -or $texto.StartsWith("#") -or -not $texto.Contains("=")) { continue }
        $partes = $texto.Split("=", 2)
        $valores[$partes[0].Trim()] = $partes[1].Trim()
    }
    return $valores
}

function Obter-Cloudflared {
    $comando = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $conhecidos = @(
        "C:\Program Files (x86)\cloudflared\cloudflared.exe",
        "C:\Program Files\cloudflared\cloudflared.exe"
    )
    foreach ($caminho in $conhecidos) {
        if (Test-Path -LiteralPath $caminho) { return $caminho }
    }
    throw "cloudflared nao foi encontrado. Instale-o ou adicione-o ao PATH."
}

function Aguardar-Docker {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $dockerDesktop)) {
        throw "Docker Desktop nao foi encontrado em $dockerDesktop"
    }
    Registrar "Iniciando Docker Desktop."
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    for ($tentativa = 1; $tentativa -le 60; $tentativa++) {
        Start-Sleep -Seconds 5
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw "Docker Desktop nao ficou disponivel em cinco minutos."
}

function Criar-Tunnel {
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Item -LiteralPath $TunnelOut, $TunnelErr -Force -ErrorAction SilentlyContinue
    $exe = Obter-Cloudflared
    Start-Process -FilePath $exe `
        -ArgumentList @("tunnel", "--url", "http://localhost:8000", "--no-autoupdate") `
        -WindowStyle Hidden -RedirectStandardOutput $TunnelOut -RedirectStandardError $TunnelErr

    for ($tentativa = 1; $tentativa -le 45; $tentativa++) {
        Start-Sleep -Seconds 2
        $conteudo = @()
        if (Test-Path -LiteralPath $TunnelOut) { $conteudo += Get-Content -LiteralPath $TunnelOut -Raw }
        if (Test-Path -LiteralPath $TunnelErr) { $conteudo += Get-Content -LiteralPath $TunnelErr -Raw }
        $encontrado = [regex]::Match(($conteudo -join "`n"), "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($encontrado.Success) { return $encontrado.Value }
    }
    throw "O Cloudflare Tunnel nao informou um link em 90 segundos."
}

function Validar-Link([string]$Url) {
    for ($tentativa = 1; $tentativa -le 15; $tentativa++) {
        try {
            $saude = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 15
            if ($saude.status -eq "ok") { return }
        } catch {
            Start-Sleep -Seconds 4
        }
    }
    throw "O link foi criado, mas o health check externo nao respondeu."
}

function Enviar-WhatsApp([string]$Url, [hashtable]$Config) {
    $obrigatorios = @("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TO")
    foreach ($chave in $obrigatorios) {
        if (-not $Config.ContainsKey($chave) -or -not $Config[$chave]) {
            Registrar "WhatsApp nao enviado: configure $chave em $ConfigPath."
            return
        }
    }
    $versao = if ($Config["WHATSAPP_GRAPH_VERSION"]) { $Config["WHATSAPP_GRAPH_VERSION"] } else { "v23.0" }
    $template = $Config["WHATSAPP_TEMPLATE_NAME"]
    $idioma = if ($Config["WHATSAPP_TEMPLATE_LANGUAGE"]) { $Config["WHATSAPP_TEMPLATE_LANGUAGE"] } else { "pt_BR" }
    if ($template) {
        $corpo = @{
            messaging_product = "whatsapp"
            to = $Config["WHATSAPP_TO"]
            type = "template"
            template = @{
                name = $template
                language = @{ code = $idioma }
                components = @(@{
                    type = "body"
                    parameters = @(@{ type = "text"; text = $Url })
                })
            }
        }
    } else {
        $corpo = @{
            messaging_product = "whatsapp"
            recipient_type = "individual"
            to = $Config["WHATSAPP_TO"]
            type = "text"
            text = @{ preview_url = $true; body = "Novo link externo do Zé Registra: $Url" }
        }
    }
    $headers = @{ Authorization = "Bearer $($Config['WHATSAPP_ACCESS_TOKEN'])" }
    $endpoint = "https://graph.facebook.com/$versao/$($Config['WHATSAPP_PHONE_NUMBER_ID'])/messages"
    $resposta = Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers `
        -ContentType "application/json; charset=utf-8" -Body ($corpo | ConvertTo-Json -Depth 8 -Compress)
    Registrar "WhatsApp enviado. ID: $($resposta.messages[0].id)"
}

try {
    Registrar "Inicio da automacao."
    Aguardar-Docker
    Push-Location $Projeto
    try {
        $erroAnterior = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $saidaDocker = & docker compose up -d 2>&1
        $codigoDocker = $LASTEXITCODE
        $ErrorActionPreference = $erroAnterior
        $saidaDocker | ForEach-Object { Registrar "docker: $_" }
        if ($codigoDocker -ne 0) { throw "docker compose up falhou com codigo $codigoDocker" }
    } finally {
        Pop-Location
    }
    $url = Criar-Tunnel
    Validar-Link $url
    $anterior = if (Test-Path -LiteralPath $UrlPath) { (Get-Content -LiteralPath $UrlPath -Raw).Trim() } else { "" }
    Set-Content -LiteralPath $UrlPath -Value $url -Encoding UTF8
    Registrar "Acesso externo disponivel em $url"
    if (-not $SemWhatsApp -and $url -ne $anterior) {
        Enviar-WhatsApp $url (Carregar-Config)
    } elseif ($url -eq $anterior) {
        Registrar "Link inalterado; notificacao nao reenviada."
    }
    exit 0
} catch {
    Registrar "ERRO: $($_.Exception.Message)"
    exit 1
}
