@echo off
rem Runs the Outlook triage once and adds its output to triage-log.txt (same folder as this file).
rem Used by the Windows scheduled task "Nova Wellness triage". Safe to run by hand too.
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\.."
set LOG=%~dp0triage-log.txt
echo ===== %date% %time% ===== >> "%LOG%"
"C:\Users\Ilmah Mohamed\AppData\Local\Programs\Python\Python312\python.exe" workflows\02-email-to-attio\outlook_triage.py --live >> "%LOG%" 2>&1
echo exit code: %ERRORLEVEL% >> "%LOG%"
