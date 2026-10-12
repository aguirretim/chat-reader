# Build the double-click package: dist\ChatReader\ChatReader.exe (PyInstaller one-folder) and dist\ChatReader.zip.
# Needs only the project's own .venv (pip install -r requirements.txt -r requirements-dev.txt).
#   build.ps1                  the folder and the zip (as before)
#   build.ps1 -Installer       the same, then dist\ChatReader-Setup-<version>.exe
#   build.ps1 -InstallerOnly   only the installer, from the dist\ChatReader folder that is already there
#                              (reads the folder, changes nothing in it, so it is safe while the app is running)
# The installer needs Inno Setup in tools\innosetup (a portable copy; see installer\README.md).
param([switch] $Installer, [switch] $InstallerOnly)
$ErrorActionPreference = 'Stop'
$app = $PSScriptRoot
$py  = "$app\.venv\Scripts\python.exe"
$out = "$app\dist\ChatReader"
$sw  = [Diagnostics.Stopwatch]::StartNew()

if (-not $InstallerOnly) {

# Never replace the folder under a running copy: on 2026-10-11 a rebuild deleted 1,370 files out from under
# the app while it was in use (the locked exe and DLLs survived; the rest had to be restored from the zip).
$inUse = Get-Process -Name ChatReader -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith("$app\dist\") }
if ($inUse) { throw "Chat Reader is running from dist\ (process $($inUse.Id -join ', ')). Close it, then build again." }
if (Test-Path $out) { [IO.Directory]::Delete($out, $true) }
if (Test-Path "$app\dist\ChatReader.zip") { [IO.File]::Delete("$app\dist\ChatReader.zip") }
New-Item -ItemType Directory -Force "$app\build", "$app\dist" | Out-Null

# Icon and version details. An exe with no icon, no product name and no version is what throwaway malware looks like.
& $py "$app\tools\make_icon.py" "$app\build\chatreader.ico"
$ver = (& $py -c "import sys; sys.path.insert(0, r'$app'); from chatreader.config import VERSION; print(VERSION)").Trim()
$v4 = (($ver -split '\.') + '0', '0', '0', '0')[0..3] -join ', '
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v4), prodvers=($v4), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Chat Reader project'),
      StringStruct('FileDescription', 'Chat Reader - reads Twitch chat aloud'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'ChatReader'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Tim Aguirre. Free software under the GNU GPL version 3 or later.'),
      StringStruct('OriginalFilename', 'ChatReader.exe'),
      StringStruct('ProductName', 'Chat Reader'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content "$app\build\version_info.txt" -Encoding utf8

& $py -m PyInstaller --noconfirm --clean --windowed --name ChatReader `
    --icon "$app\build\chatreader.ico" --version-file "$app\build\version_info.txt" `
    --distpath "$app\build\pyi-dist" --workpath "$app\build\pyi-work" --specpath "$app\build" `
    --paths "$app" `
    --add-data "$app\chatreader\textclean\data;chatreader\textclean\data" `
    --add-data "$app\chatreader\assets;chatreader\assets" `
    --collect-all espeakng_loader `
    --collect-data kokoro_onnx --collect-data phonemizer `
    --hidden-import pystray._win32 `
    --exclude-module pytest --exclude-module PyInstaller `
    "$app\run.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

Move-Item "$app\build\pyi-dist\ChatReader" $out
New-Item -ItemType Directory "$out\models" | Out-Null
Copy-Item "$app\models\kokoro-v1.0.int8.onnx", "$app\models\voices-v1.0.bin" "$out\models\"
Copy-Item "$app\third_party_licenses" "$out\licenses" -Recurse
[IO.Directory]::Delete("$out\licenses\texts", $true)   # the canonical texts are copied below under their final names
& $py "$app\tools\components.py" "$out\licenses\COMPONENTS.txt"
& $py "$app\tools\collect_licenses.py" "$out\licenses"
if ($LASTEXITCODE -ne 0) { throw "a licence text is missing (collect_licenses.py)" }
Copy-Item "$app\README.txt" $out
Copy-Item "$app\LICENSE" "$out\LICENSE.txt"

Compress-Archive -Path $out -DestinationPath "$app\dist\ChatReader.zip" -CompressionLevel Optimal
$files = Get-ChildItem $out -Recurse -File
"built ChatReader $ver in {0:N0} s: {1} files, {2:N1} MB unpacked, zip {3:N1} MB" -f $sw.Elapsed.TotalSeconds, $files.Count, (($files | Measure-Object Length -Sum).Sum / 1e6), ((Get-Item "$app\dist\ChatReader.zip").Length / 1e6)
}

if ($Installer -or $InstallerOnly) {
    $iscc = "$app\tools\innosetup\ISCC.exe"
    if (-not (Test-Path $iscc)) { throw "Inno Setup is not in tools\innosetup. See installer\README.md." }
    if (-not (Test-Path "$out\ChatReader.exe")) { throw "dist\ChatReader is missing. Run build.ps1 without -InstallerOnly first." }
    if (-not (Test-Path "$app\build\chatreader.ico")) { & $py "$app\tools\make_icon.py" "$app\build\chatreader.ico" }
    $exeVer = (Get-Item "$out\ChatReader.exe").VersionInfo.ProductVersion   # the installer carries the version of the exe it wraps
    $swi = [Diagnostics.Stopwatch]::StartNew()
    & $iscc /Qp "/DAppVersion=$exeVer" "/DSourceDir=$out" "/DOutDir=$app\dist" "/DIconFile=$app\build\chatreader.ico" "$app\installer\chatreader.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
    $setup = Get-Item "$app\dist\ChatReader-Setup-$exeVer.exe"
    "built {0} in {1:N0} s: {2:N1} MB, sha256 {3}" -f $setup.Name, $swi.Elapsed.TotalSeconds, ($setup.Length / 1e6), (Get-FileHash $setup.FullName -Algorithm SHA256).Hash
}
