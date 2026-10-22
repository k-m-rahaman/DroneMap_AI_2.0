[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [switch]$InstallAI,
    [switch]$InstallDrone,
    [switch]$PrepareOnly,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$workspacePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $workspacePython)) {
    $pythonCandidates = @()
    if ($Python) { $pythonCandidates += $Python }
    foreach ($commandName in @('python', 'python3')) {
        $resolvedCommand = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($resolvedCommand -and $resolvedCommand.Source -notlike '*\Microsoft\WindowsApps\*') {
            $pythonCandidates += $resolvedCommand.Source
        }
    }
    foreach ($pythonVersion in @('3.12', '3.11', '3.10')) {
        foreach ($registryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
            $registryKey = Join-Path $registryRoot "$pythonVersion\InstallPath"
            if (Test-Path -LiteralPath $registryKey) {
                $installedPython = (Get-Item -LiteralPath $registryKey).GetValue('ExecutablePath')
                if (-not $installedPython) {
                    $installedDirectory = (Get-Item -LiteralPath $registryKey).GetValue('')
                    if ($installedDirectory) { $installedPython = Join-Path $installedDirectory 'python.exe' }
                }
                if ($installedPython) { $pythonCandidates += $installedPython }
            }
        }
    }
    $selectedPython = $null
    foreach ($pythonCandidate in ($pythonCandidates | Select-Object -Unique)) {
        try {
            & $pythonCandidate -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,12) and sys.maxsize > 2**32 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $selectedPython = $pythonCandidate; break }
        } catch { }
    }
    if (-not $selectedPython) {
        throw 'Install 64-bit Python 3.10-3.12, or pass its executable explicitly: .\start.ps1 -Python C:\Path\To\python.exe'
    }
    & $selectedPython -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the virtual environment. Check that Python includes the venv and pip modules.'
    }
}

& $workspacePython -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,12), "Python 3.10-3.12 is required"'
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python version.' }

# A working local environment starts without contacting a package index. Check
# the complete pinned requirements (including nested -r files), their fingerprint
# and imports; repair only an absent, changed or broken dependency set.
$dependencyCheck = @'
import hashlib, importlib, importlib.metadata, json, pathlib, re, sys
requirements = pathlib.Path(sys.argv[1]).resolve()
stamp = pathlib.Path(sys.argv[2])
modules = sys.argv[3].split(',')
seen, pins, digests = set(), {}, []
def collect(path):
    path = path.resolve()
    if path in seen:
        return
    seen.add(path)
    contents = path.read_bytes()
    digests.append(path.name.encode() + b'\0' + contents)
    for raw in contents.decode('utf-8-sig').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('-r '):
            collect(path.parent / line[3:].strip())
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', line)
        if not match:
            raise RuntimeError('Unsupported requirement: ' + line)
        name, version = match.groups()
        pins[name] = version
collect(requirements)
fingerprint = hashlib.sha256(b'\n'.join(digests) + sys.version.encode()).hexdigest()
try:
    for name, version in pins.items():
        # CPU wheels may have a +cpu local suffix while satisfying ==version.
        installed = importlib.metadata.version(name)
        if installed.split('+', 1)[0] != version:
            raise RuntimeError(name + ' version differs from the pinned requirement')
    for module in modules:
        importlib.import_module(module)
except Exception as exc:
    print('Dependency check: ' + str(exc))
    raise SystemExit(1)
try:
    current = json.loads(stamp.read_text())
except (OSError, ValueError):
    current = {}
if current.get('fingerprint') != fingerprint:
    stamp.write_text(json.dumps({'fingerprint': fingerprint, 'requirements': requirements.name}), encoding='utf-8')
'@

function Test-DependencySet([string]$Requirements, [string]$Name, [string[]]$Modules) {
    $stampPath = Join-Path $PSScriptRoot ".venv\.dronemap-$Name-dependencies.json"
    $checkOutput = & $workspacePython -c $dependencyCheck (Join-Path $PSScriptRoot $Requirements) $stampPath ($Modules -join ',')
    $dependenciesReady = ($LASTEXITCODE -eq 0)
    foreach ($line in $checkOutput) { Write-Host $line }
    return $dependenciesReady
}

$coreModules = @('fastapi', 'uvicorn', 'multipart', 'numpy', 'PIL', 'shapely', 'pyproj', 'rasterio', 'fpdf', 'qrcode', 'uharfbuzz')
if (-not (Test-DependencySet 'requirements.txt' 'core' $coreModules)) {
    & $workspacePython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Core dependency installation failed. First installation requires internet access.' }
    if (-not (Test-DependencySet 'requirements.txt' 'core' $coreModules)) { throw 'Installed core dependencies did not pass their startup check.' }
} else {
    Write-Host 'Core dependencies verified; using the local environment.'
}
if ($InstallAI) {
    $aiModules = @('torch', 'torchvision', 'ultralytics')
    if (-not (Test-DependencySet 'requirements-ai.txt' 'ai' $aiModules)) {
        & $workspacePython -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
        if ($LASTEXITCODE -ne 0) { throw 'CPU inference engine installation failed.' }
        & $workspacePython -m pip install -r requirements-ai.txt
        if ($LASTEXITCODE -ne 0) { throw 'Optional AI dependency installation failed.' }
        if (-not (Test-DependencySet 'requirements-ai.txt' 'ai' $aiModules)) { throw 'Installed AI dependencies did not pass their startup check.' }
    } else {
        Write-Host 'AI dependencies verified; using the local environment.'
    }
}
if ($InstallDrone) {
    $droneModules = @('pymavlink.mavutil', 'serial')
    if (-not (Test-DependencySet 'requirements-drone.txt' 'drone' $droneModules)) {
        & $workspacePython -m pip install -r requirements-drone.txt
        if ($LASTEXITCODE -ne 0) { throw 'Drone telemetry dependency installation failed.' }
        if (-not (Test-DependencySet 'requirements-drone.txt' 'drone' $droneModules)) { throw 'Installed drone dependencies did not pass their startup check.' }
    } else {
        Write-Host 'Drone dependencies verified; using the local environment.'
    }
}

if ($PrepareOnly) { return }
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:YOLO_AUTOINSTALL = 'false'
Write-Host "Open http://127.0.0.1:$Port and sign in. Create an account only on a new workspace."
& $workspacePython -m uvicorn main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
