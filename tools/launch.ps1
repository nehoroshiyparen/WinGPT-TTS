# Short console entry point; all paths are independent of the working directory.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectRoot '.venv'
$projectPython = Join-Path $environmentPath 'Scripts\python.exe'
$installationProbe = Join-Path $PSScriptRoot 'check_install.py'

try {
    if (-not (Test-Path -LiteralPath $projectPython)) {
        Write-Host 'WinGPT TTS: preparing Python environment...'
        $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        $basePython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonLauncher) {
            & $pythonLauncher.Source -3 -m venv $environmentPath
        } elseif ($basePython) {
            & $basePython.Source -m venv $environmentPath
        } else {
            throw 'Install Python 3.11 or newer from python.org (include Tcl/Tk), then run .\wgtts again.'
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $projectPython)) {
            throw 'Could not create the Python environment. Install Python 3.11 or newer with Tcl/Tk, then try again.'
        }
    }

    & $projectPython $installationProbe $projectRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'WinGPT TTS: installing the application and dependencies...'
        & $projectPython -m pip install --editable $projectRoot
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not install the application. Check your internet connection and try again.'
        }
        & $projectPython -c 'import tkinter, PIL, pywinauto, sounddevice, prompt_toolkit, wgtts'
        if ($LASTEXITCODE -ne 0) {
            throw 'Python dependencies or Tcl/Tk are unavailable. Repair your Python installation and try again.'
        }
        & $projectPython $installationProbe $projectRoot --write-stamp
        if ($LASTEXITCODE -ne 0) {
            throw 'The application could not be installed into the local environment.'
        }
    }

    & $projectPython -m wgtts @args
    exit $LASTEXITCODE
} catch {
    Write-Host "WinGPT TTS: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
