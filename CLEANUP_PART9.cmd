@echo off
setlocal
echo [Synergy Part 9] Removing legacy daily-stat files...

if exist data\latest.json del /q data\latest.json
if exist data\archive rmdir /s /q data\archive
if exist data\archive_corrections_applied.json del /q data\archive_corrections_applied.json
if exist data\archive_month_confirmed.json del /q data\archive_month_confirmed.json
if exist docs\data\daily rmdir /s /q docs\data\daily
if exist docs\data\dates.js del /q docs\data\dates.js

if exist scripts\sync_members.py del /q scripts\sync_members.py
if exist scripts\fetch_eloboard_data.py del /q scripts\fetch_eloboard_data.py
if exist scripts\fetch_poonggo_data.py del /q scripts\fetch_poonggo_data.py
if exist scripts\update_data.py del /q scripts\update_data.py

echo.
echo Finished.
echo git status
echo git add -A
echo git commit -m "Remove Synergy legacy fallbacks"
echo git push origin main
endlocal
