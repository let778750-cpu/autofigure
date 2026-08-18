@echo off
setlocal

rem AI AutoFigure v2 - VLM-first, verify-light
rem Usage: autofigure ^<prepare^|convert^|check^|math^> [args...]

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    >&2 echo AI AutoFigure could not find the v2 venv: %VENV_PY%
    >&2 echo Create it with: D:\anaconda\python.exe -m venv .venv ^&^& .venv\Scripts\pip install -r requirements-v2.txt
    endlocal
    exit /b 9009
)

cd /d "%~dp0"
rem No -X utf8 here: let stdio follow the console codepage so Chinese prints
rem correctly in a GBK console. File I/O uses explicit encoding in code.
"%VENV_PY%" -B -m tools.v2 %*
set "AUTOFIGURE_EXIT=%ERRORLEVEL%"

endlocal & exit /b %AUTOFIGURE_EXIT%
