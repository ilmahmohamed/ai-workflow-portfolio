@echo off
rem Runs the Outlook triage once and adds its output to triage-log.txt (same folder as this file).
rem Used by the Windows scheduled task "Nova Wellness triage". Safe to run by hand too.
rem It finds the project folder from its own location, so no user or folder names are written here.
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
set LOG=%~dp0triage-log.txt
rem Use python from PATH; if it is not found, use the py launcher instead.
set PY=python
where python >nul 2>&1 || set PY=py -3
echo ===== %date% %time% ===== >> "%LOG%"
%PY% workflows\02-email-to-attio\outlook_triage.py --live >> "%LOG%" 2>&1
echo exit code: %ERRORLEVEL% >> "%LOG%"
