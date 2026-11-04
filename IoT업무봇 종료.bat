@echo off
rem Stop the Bot Hub server listening on 127.0.0.1:8765 (started by either launcher).
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; 'Work bot stopped.' } else { 'Work bot is not running.' }"
ping -n 3 127.0.0.1 >nul
