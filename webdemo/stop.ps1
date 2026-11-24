# Stop the demo servers:  powershell -ExecutionPolicy Bypass -File .\stop.ps1
param([int]$Port = 8765, [int]$NewsPort = 8766)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-ListenPid([int]$port) {
  foreach ($line in (netstat -ano -p tcp)) {
    if ($line -match ("^\s*TCP\s+(127\.0\.0\.1|\[::1\]):" + $port + "\s+\S+\s+LISTENING\s+(\d+)\s*$")) {
      return [int]$Matches[2]
    }
  }
  return $null
}

function Stop-Tree([int]$id, $label) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  Write-Host "Stopped $label PID $id"
}

function Stop-PidFile($pidFile, $label) {
  $id = Get-Content $pidFile -ErrorAction SilentlyContinue
  if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
    Stop-Tree ([int]$id) $label
  } else {
    Write-Host "$label not running"
  }
  Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Stop-Port([int]$port, $label) {
  $listenPid = Get-ListenPid $port
  if ($listenPid) {
    Stop-Tree $listenPid "$label listener on $port"
  }
}

Stop-PidFile (Join-Path $here "server.pid") "demo"
Stop-Port $Port "demo"
Stop-PidFile (Join-Path $here "news-server.pid") "news"
Stop-Port $NewsPort "news"
