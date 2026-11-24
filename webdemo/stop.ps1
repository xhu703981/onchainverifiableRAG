# Stop the demo servers:  powershell -ExecutionPolicy Bypass -File .\stop.ps1
param([int]$Port = 8765, [int]$NewsPort = 8766)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-PidFile($pidFile, $label) {
  $id = Get-Content $pidFile -ErrorAction SilentlyContinue
  if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
    # The venv python.exe launcher often has a child interpreter; stop the tree.
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -ErrorAction SilentlyContinue |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped $label PID $id"
  } else {
    Write-Host "$label not running"
  }
  Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Stop-Port([int]$port, $label) {
  $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq "127.0.0.1" -or $_.LocalAddress -eq "::1" }
  foreach ($c in $conns) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped $label listener on $port (PID $($c.OwningProcess))"
  }
}

Stop-PidFile (Join-Path $here "server.pid") "demo"
Stop-Port $Port "demo"
Stop-PidFile (Join-Path $here "news-server.pid") "news"
Stop-Port $NewsPort "news"
