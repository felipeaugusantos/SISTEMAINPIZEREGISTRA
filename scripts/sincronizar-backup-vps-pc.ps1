$ErrorActionPreference = "Stop"

$vps = "root@2.25.79.231"
$chave = "C:\Users\Felipe Santos\Documents\Codex\vps-deploy"
$remoto = "/opt/zeregistra/backups"
$destino = "C:\ZeRegistraBackup\arquivos"
$log = "C:\ZeRegistraBackup\backup-vps.log"
$retencao = 7

function Registrar($mensagem) {
    $linha = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $mensagem"
    Write-Host $linha
    Add-Content -LiteralPath $log -Value $linha
}

function SSH($comando) {
    $saida = & ssh.exe -i $chave -o BatchMode=yes `
        -o StrictHostKeyChecking=yes $vps $comando 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "Falha no SSH: $($saida -join ' ')"
    }

    return @($saida)
}

try {
    Registrar "Inicio da sincronizacao"

    # Mais antigo primeiro (sort por nome = ordem cronologica). Dumps com menos
    # de 30 minutos ainda podem estar sendo gravados e sao ignorados.
    $arquivos = @(
        SSH "find '$remoto' -maxdepth 1 -type f -name 'inpi-*.dump' -mmin +30 -printf '%f\n' | sort" |
        ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ }
    )

    # MUDANCA 1 (auditoria de seguranca 13/10/2026): o dump mais recente NUNCA
    # e apagado da VPS. Ele e o que o simulado de restauracao dominical e o
    # alerta "backup com mais de 26 h" do sistema enxergam em /app/backups.
    $maisRecente = if ($arquivos.Count -gt 0) { $arquivos[-1] } else { $null }

    foreach ($nome in $arquivos) {
        if ($nome -notmatch '^inpi-\d{8}-\d{6}\.dump$') {
            Registrar "Nome ignorado por seguranca: $nome"
            continue
        }

        $origem = "$remoto/$nome"
        $arquivoLocal = Join-Path $destino $nome
        $temporario = "$arquivoLocal.part"

        $hashRemoto = ((SSH "sha256sum '$origem'" |
            Select-Object -First 1) -split '\s+')[0].ToLowerInvariant()

        if ($hashRemoto -notmatch '^[a-f0-9]{64}$') {
            throw "Hash remoto invalido: $nome"
        }

        if (-not (Test-Path -LiteralPath $arquivoLocal)) {
            Remove-Item -LiteralPath $temporario -Force -ErrorAction SilentlyContinue
            Registrar "Baixando $nome"

            & scp.exe -i $chave -o BatchMode=yes `
                -o StrictHostKeyChecking=yes `
                "${vps}:$origem" $temporario

            if ($LASTEXITCODE -ne 0) {
                throw "Falha no download: $nome"
            }

            $hashLocal = (Get-FileHash -LiteralPath $temporario -Algorithm SHA256).Hash.ToLowerInvariant()

            if ($hashLocal -ne $hashRemoto) {
                throw "Falha de integridade: $nome. Remoto preservado."
            }

            Move-Item -LiteralPath $temporario -Destination $arquivoLocal
        }
        else {
            $hashLocal = (Get-FileHash -LiteralPath $arquivoLocal -Algorithm SHA256).Hash.ToLowerInvariant()

            if ($hashLocal -ne $hashRemoto) {
                throw "Copia local divergente: $nome"
            }
        }

        $hashFinal = ((SSH "sha256sum '$origem'" |
            Select-Object -First 1) -split '\s+')[0].ToLowerInvariant()

        if ($hashFinal -ne $hashRemoto) {
            throw "Arquivo remoto mudou durante a transferencia: $nome"
        }

        # MUDANCA 3: confirma a copia no PC para o painel de saude dos backups
        # (superadmin). O marcador so e gravado depois do hash conferir.
        SSH "touch '$remoto/.pc-$nome'" | Out-Null

        # MUDANCA 1: nao apaga o dump mais recente da VPS.
        if ($nome -eq $maisRecente) {
            Registrar "$nome validado e MANTIDO na VPS (e o dump mais recente)"
            continue
        }

        # MUDANCA 2: so apaga se o envio externo cifrado (Google Drive) ja foi
        # confirmado. O marcador .offsite-<dump> e criado por
        # docker/backup-offsite.sh somente depois do upload bem-sucedido.
        $enviado = (SSH "test -f '$remoto/.offsite-$nome' && echo sim || echo nao") |
            Select-Object -First 1

        if ("$enviado".Trim() -ne "sim") {
            Registrar "$nome baixado e validado; MANTIDO na VPS (envio externo ainda nao confirmado)"
            continue
        }

        SSH "rm -f -- '$origem'" | Out-Null
        Registrar "$nome validado, com copia externa confirmada e removido da VPS"
    }

    $locais = @(Get-ChildItem -LiteralPath $destino -Filter "inpi-*.dump" -File |
        Sort-Object LastWriteTime -Descending)

    if ($locais.Count -gt $retencao) {
        $locais | Select-Object -Skip $retencao | ForEach-Object {
            Registrar "Retencao local: removendo $($_.Name)"
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }

    Registrar "Concluido. Retencao: $retencao backups locais"
    exit 0
}
catch {
    Registrar "ERRO: $($_.Exception.Message)"
    exit 1
}
