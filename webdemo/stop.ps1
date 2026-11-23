# Stop the demo server:  powershell -ExecutionPolicy Bypass -File .\stop.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = Get-Content "$here\server.pid" -ErrorAction SilentlyContinue
if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { Stop-Process -Id $id -Force; Write-Host "Stopped PID $id" } else { Write-Host "Not running" }
Remove-Item "$here\server.pid" -ErrorAction SilentlyContinue
