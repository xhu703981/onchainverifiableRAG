# Start the demo server in the background:  powershell -ExecutionPolicy Bypass -File .\start.ps1 [-Port 8765]
param([int]$Port = 8765)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path "$here\server.pid") {
  $old = Get-Content "$here\server.pid" -ErrorAction SilentlyContinue
  if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { Write-Host "Already running (PID $old) - run stop.ps1 first"; exit 0 }
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { Write-Host "Port $Port is busy; pass -Port <other>"; exit 1 }
$p = Start-Process -FilePath "$here\.venv\Scripts\python.exe" -ArgumentList "app.py","--port",$Port -WorkingDirectory $here `
     -RedirectStandardOutput "$here\server.log" -RedirectStandardError "$here\server.err.log" -WindowStyle Hidden -PassThru
$p.Id | Set-Content "$here\server.pid"
Write-Host "Started PID $($p.Id) -> http://127.0.0.1:$Port  (first start loads the index, ~10-20 s)"
