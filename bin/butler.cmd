@echo off
rem The `butler` command on Windows, bin/butler's twin: each course lesson is one line that calls
rem it. Everything runs in Butler's own Python, in UTF-8 mode, through run.py: `butler setup` is
rem the guided first run (src/butler/first_run.py), and the rest is butler.cli.
setlocal
set "BUTLER_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%BUTLER_PYTHON%" (
  echo Butler isn't installed completely yet. Run the Butler installer again.
  exit /b 1
)
"%BUTLER_PYTHON%" -X utf8 "%~dp0..\run.py" cli %*
exit /b %ERRORLEVEL%
