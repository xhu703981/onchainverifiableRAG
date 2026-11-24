# Start the demo servers in the background:
#   powershell -ExecutionPolicy Bypass -File .\start.ps1 [-Port 8765] [-NewsPort 8766]
param([int]$Port = 8765, [int]$NewsPort = 8766)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Missing $py"; exit 1 }

function Get-AlivePid($pidFile) {
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { return [int]$old }
  }
  return $null
}

function Get-ListenPid([int]$port) {
  foreach ($line in (netstat -ano -p tcp)) {
    if ($line -match ("^\s*TCP\s+(127\.0\.0\.1|\[::1\]):" + $port + "\s+\S+\s+LISTENING\s+(\d+)\s*$")) {
      return [int]$Matches[2]
    }
  }
  return $null
}

$pidFile = Join-Path $here "server.pid"
$alive = Get-AlivePid $pidFile
if ($alive) {
  Write-Host "Demo already running (PID $alive) -> http://127.0.0.1:$Port"
} elseif (Get-ListenPid $Port) {
  Write-Host "Port $Port is busy; pass -Port <other> for the existing demo"
} else {
  $p = Start-Process -FilePath $py -ArgumentList "app.py","--port",$Port -WorkingDirectory $here `
       -RedirectStandardOutput (Join-Path $here "server.log") -RedirectStandardError (Join-Path $here "server.err.log") -WindowStyle Hidden -PassThru
  $p.Id | Set-Content $pidFile
  Write-Host "Started demo PID $($p.Id) -> http://127.0.0.1:$Port  (first start loads the index, ~10-20 s)"
}

$newsApp = Join-Path $here "news\app.py"
if (Test-Path $newsApp) {
  if (-not $env:NEWS_DATA) { $env:NEWS_DATA = Join-Path $here "news-data" }
  $newsPidFile = Join-Path $here "news-server.pid"
  $nalive = Get-AlivePid $newsPidFile
  if ($nalive) {
    Write-Host "News already running (PID $nalive) -> http://127.0.0.1:$NewsPort"
  } elseif (Get-ListenPid $NewsPort) {
    Write-Host "Port $NewsPort is busy; pass -NewsPort <other> for the news page"
  } else {
    $p = Start-Process -FilePath $py -ArgumentList "news\app.py","--host","127.0.0.1","--port",$NewsPort -WorkingDirectory $here `
         -RedirectStandardOutput (Join-Path $here "news-server.log") -RedirectStandardError (Join-Path $here "news-server.err.log") -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $newsPidFile
    Write-Host "Started news PID $($p.Id) -> http://127.0.0.1:$NewsPort"
  }
}
