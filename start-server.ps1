$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$pythonPath = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-Not (Test-Path $pythonPath)) {
    Write-Error "Python executable not found at $pythonPath. Please create the virtual environment first."
    exit 1
}
& $pythonPath -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
