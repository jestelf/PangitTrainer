param([string]$Dist = '')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $Dist) { $Dist = Join-Path $root ('dist\' + (Get-Content -LiteralPath (Join-Path $root 'version.txt') -Raw).Trim()) }
$work = Join-Path $env:TEMP 'PangitTrainerBuild'
python -m PyInstaller --noconfirm --clean --onefile --windowed --name PangitTrainer `
  --icon "$root\assets\Pangit.ico" `
  --add-data "$root\assets\Pangit.ico;assets" --add-data "$root\assets\enabled.wav;assets" --add-data "$root\assets\disabled.wav;assets" `
  --version-file "$root\version_info.txt" `
  --paths "$root\src" --distpath "$Dist" --workpath $work --specpath $work `
  "$root\src\app.py"
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Get-Item "$Dist\PangitTrainer.exe" | Select-Object Name, Length, LastWriteTime
