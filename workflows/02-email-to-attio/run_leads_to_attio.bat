@echo off
rem Runs the Leads-to-Attio script once and adds its output to leads-to-attio-log.txt (same folder as this file).
rem Used by the Windows scheduled task "Nova Wellness Attio sync". Safe to run by hand too.
rem It finds the project folder from its own location, so no user or folder names are written here.
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
set LOG=%~dp0leads-to-attio-log.txt
rem Use python from PATH; if it is not found, use the py launcher instead.
set PY=python
where python >nul 2>&1 || set PY=py -3
rem Keep the log short: only its latest lines are kept (this never stops the run).
%PY% workflows\02-email-to-attio\trim_log.py "%LOG%"
echo ===== %date% %time% ===== >> "%LOG%"
%PY% workflows\02-email-to-attio\leads_to_attio.py --live >> "%LOG%" 2>&1
echo exit code: %ERRORLEVEL% >> "%LOG%"
