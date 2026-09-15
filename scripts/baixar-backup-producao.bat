@echo off
setlocal enabledelayedexpansion

rem Achado FASE6-12 da auditoria (04/09/2026): o backup do banco de producao
rem fica so na propria VPS -- se o disco morrer ou a VPS for comprometida, o
rem backup vai junto. Este script baixa o dump mais recente para esta
rem maquina local, servindo como copia fora da VPS sem custo de S3/storage
rem externo. Roda por cima do OpenSSH Client do Windows (ssh/scp) -- se nao
rem tiver instalado: Configuracoes > Aplicativos > Recursos opcionais >
rem Adicionar um recurso > Cliente OpenSSH.

set VPS_HOST=root@2.25.79.231
set VPS_KEY=%USERPROFILE%\.ssh\inpi_vps
set VPS_BACKUPS_DIR=/opt/zeregistra/backups
set DESTINO=%~dp0..\backups-producao
set RETENCAO_DIAS=30

if not exist "%VPS_KEY%" (
    echo [ERRO] Chave SSH nao encontrada em "%VPS_KEY%".
    echo Ajuste a variavel VPS_KEY no inicio deste script se a chave estiver em outro lugar.
    exit /b 1
)

if not exist "%DESTINO%" mkdir "%DESTINO%"

echo Consultando o backup mais recente na VPS...
rem Achado (14/09/2026): "for /f ... usebackq" com comando em backtick
rem quebra o parsing de "-o BatchMode=yes" quando o caminho da chave SSH
rem tem espaco (ex.: "Felipe Santos") -- funciona rodando o ssh direto,
rem so falha dentro dessa substituicao de comando do cmd.exe. Redirecionar
rem para um arquivo temporario e ler com "set /p" evita o problema.
set TEMPFILE=%TEMP%\zeregistra-ultimo-backup.txt
ssh -i "%VPS_KEY%" -o BatchMode=yes -o ConnectTimeout=10 %VPS_HOST% "ls -t %VPS_BACKUPS_DIR%/inpi-*.dump 2>/dev/null | head -1" > "%TEMPFILE%"
set ULTIMO=
set /p ULTIMO=<"%TEMPFILE%"
del "%TEMPFILE%" 2>nul

if "%ULTIMO%"=="" (
    echo [ERRO] Nao foi possivel encontrar nenhum backup em %VPS_BACKUPS_DIR% na VPS.
    echo Verifique a conexao SSH e se o cron de backup ja rodou pelo menos uma vez.
    exit /b 1
)

for %%F in ("%ULTIMO%") do set NOME=%%~nxF

if exist "%DESTINO%\%NOME%" (
    echo Backup mais recente ja esta local: %NOME%
    goto :limpeza
)

echo Baixando %NOME% de producao...
scp -i "%VPS_KEY%" -o BatchMode=yes "%VPS_HOST%:%ULTIMO%" "%DESTINO%\%NOME%"
if errorlevel 1 (
    echo [ERRO] Falha ao baixar o backup via scp.
    exit /b 1
)
echo Backup salvo em: %DESTINO%\%NOME%

:limpeza
rem Remove copias locais mais antigas que RETENCAO_DIAS -- mesma politica do
rem backup local (scripts/backup-banco.ps1), so que aplicada aqui a esta
rem pasta separada de copias de producao.
forfiles /p "%DESTINO%" /m inpi-*.dump /d -%RETENCAO_DIAS% /c "cmd /c del @path" 2>nul

echo.
echo Concluido.
endlocal
