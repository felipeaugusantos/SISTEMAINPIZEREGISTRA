@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

rem Achado (30/09/2026): a Receita Federal recusa conexões vindas da VPS
rem (curl devolve 000), então a importação do cache nacional falha logo no
rem início. Este script roda NESTE computador (agendado pelo
rem instalar-envio-cnpj-rfb-agendado.bat): descobre o período mais recente
rem publicado pela Receita, baixa os arquivos (~4,5 GB), envia para o cache em
rem disco da VPS e grava o marcador ENVIO_COMPLETO. O worker da VPS dispara a
rem importação sozinho em até 1 hora (app/prospeccao_cache_rfb.py).
rem Período já enviado é pulado; download interrompido continua de onde parou.

set "VPS_HOST=root@2.25.79.231"
set "VPS_KEY=%USERPROFILE%\.ssh\inpi_vps"
set "RAIZ=https://arquivos.receitafederal.gov.br/public.php/webdav"
set "TOKEN=YggdBLfdninEJX9"
set "PASTA_LOCAL=%USERPROFILE%\Downloads\cnpj-rfb"
set "REGISTRO=%PASTA_LOCAL%\ultimo-periodo-enviado.txt"
set "LOG=%PASTA_LOCAL%\envio.log"

if not exist "%PASTA_LOCAL%" mkdir "%PASTA_LOCAL%"
echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"

if not exist "%VPS_KEY%" (
    call :registrar "[ERRO] Chave SSH não encontrada em %VPS_KEY%."
    exit /b 1
)

rem --- 1. Período mais recente publicado pela Receita ----------------------
set "PERIODO="
for /f %%P in ('powershell -NoProfile -Command "$r = & curl.exe -s -m 120 -X PROPFIND -H 'Depth: 1' -u '%TOKEN%:' '%RAIZ%/'; ([regex]::Matches($r, '\d{4}-\d{2}') | ForEach-Object Value | Sort-Object -Unique | Select-Object -Last 1)"') do set "PERIODO=%%P"
if "%PERIODO%"=="" (
    call :registrar "[ERRO] Não foi possível consultar a Receita Federal."
    exit /b 1
)
call :registrar "Período mais recente na Receita: %PERIODO%"

set "ULTIMO="
if exist "%REGISTRO%" set /p ULTIMO=<"%REGISTRO%"
if "%ULTIMO%"=="%PERIODO%" (
    call :registrar "Período %PERIODO% já foi enviado. Nada a fazer."
    exit /b 0
)

set "BASE=%RAIZ%/%PERIODO%"
set "LOCAL=%PASTA_LOCAL%\%PERIODO%"
set "REMOTO=/opt/zeregistra/data/rfb_cnpj_cache/%PERIODO%"
if not exist "%LOCAL%" mkdir "%LOCAL%"

set "ARQUIVOS=Municipios"
for /l %%i in (0,1,9) do set "ARQUIVOS=!ARQUIVOS! Empresas%%i Estabelecimentos%%i"

rem --- 2. Download ------------------------------------------------------------
for %%A in (%ARQUIVOS%) do (
    if not exist "%LOCAL%\%%A.zip" (
        call :registrar "Baixando %%A.zip ..."
        curl -f -L -s -S --retry 5 --retry-delay 10 -u "%TOKEN%:" -o "%LOCAL%\%%A.zip.parcial" "%BASE%/%%A.zip"
        if errorlevel 1 (
            del "%LOCAL%\%%A.zip.parcial" 2>nul
            call :registrar "[ERRO] Falha ao baixar %%A.zip. A próxima execução continua de onde parou."
            exit /b 1
        )
        move /y "%LOCAL%\%%A.zip.parcial" "%LOCAL%\%%A.zip" >nul
    )
)

rem --- 3. Envio para a VPS (o marcador vai por último) ----------------------
ssh -i "%VPS_KEY%" -o BatchMode=yes -o ConnectTimeout=30 %VPS_HOST% "mkdir -p %REMOTO% && rm -f %REMOTO%/ENVIO_COMPLETO"
if errorlevel 1 (
    call :registrar "[ERRO] Não foi possível conectar na VPS."
    exit /b 1
)
for %%A in (%ARQUIVOS%) do (
    call :registrar "Enviando %%A.zip ..."
    scp -q -i "%VPS_KEY%" -o BatchMode=yes "%LOCAL%\%%A.zip" "%VPS_HOST%:%REMOTO%/%%A.zip"
    if errorlevel 1 (
        call :registrar "[ERRO] Falha ao enviar %%A.zip. A próxima execução tenta de novo."
        exit /b 1
    )
)
ssh -i "%VPS_KEY%" -o BatchMode=yes %VPS_HOST% "chmod -R a+rwX /opt/zeregistra/data/rfb_cnpj_cache && touch %REMOTO%/ENVIO_COMPLETO"
if errorlevel 1 (
    call :registrar "[ERRO] Falha ao gravar o marcador de envio completo."
    exit /b 1
)

rem --- 4. Registro e limpeza local ------------------------------------------
> "%REGISTRO%" echo %PERIODO%
rd /s /q "%LOCAL%"
call :registrar "Período %PERIODO% enviado. A VPS inicia a importação em até 1 hora."
exit /b 0

:registrar
echo %~1
echo %~1 >> "%LOG%"
exit /b 0
