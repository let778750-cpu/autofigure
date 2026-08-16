@echo off
setlocal

set "AUTOFIGURE_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%AUTOFIGURE_POWERSHELL%" (
    >&2 echo AI AutoFigure could not find Windows PowerShell.
    endlocal
    exit /b 9009
)

"%AUTOFIGURE_POWERSHELL%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0tools\run_perception_gate.ps1" %*
set "AUTOFIGURE_EXIT=%ERRORLEVEL%"

endlocal & exit /b %AUTOFIGURE_EXIT%
