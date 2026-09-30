@echo off
chcp 65001 >nul
setlocal

rem Cadastra no Agendador de Tarefas do Windows o envio automático dos
rem arquivos do CNPJ para a VPS (enviar-cnpj-rfb-para-vps.bat). Roda toda
rem segunda-feira às 10h; se o computador estiver desligado nesse horário,
rem roda assim que for ligado. O script só baixa quando a Receita publica
rem um período novo -- nas outras semanas termina em segundos.
rem Para remover: schtasks /delete /tn "Ze Registra - Envio CNPJ RFB" /f

set "TAREFA=Ze Registra - Envio CNPJ RFB"
set "SCRIPT=%~dp0enviar-cnpj-rfb-para-vps.bat"

if not exist "%SCRIPT%" (
    echo [ERRO] Não encontrei "%SCRIPT%".
    pause
    exit /b 1
)

powershell -NoProfile -Command ^
  "$acao = New-ScheduledTaskAction -Execute $env:SCRIPT -WorkingDirectory (Split-Path $env:SCRIPT);" ^
  "$gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 10:00;" ^
  "$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;" ^
  "Register-ScheduledTask -TaskName $env:TAREFA -Action $acao -Trigger $gatilho -Settings $config -Description 'Baixa os dados abertos do CNPJ da Receita e envia para a VPS do Zé Registra.' -Force | Out-Null"
if errorlevel 1 (
    echo [ERRO] Não foi possível cadastrar a tarefa.
    pause
    exit /b 1
)

echo Tarefa "%TAREFA%" cadastrada: toda segunda às 10h (ou assim que o computador ligar).
echo O histórico de cada execução fica em %USERPROFILE%\Downloads\cnpj-rfb\envio.log
echo.
set /p "AGORA=Rodar o primeiro envio agora? (S/N): "
if /i "%AGORA%"=="S" schtasks /run /tn "%TAREFA%"
pause
