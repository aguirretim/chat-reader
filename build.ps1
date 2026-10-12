# Build the double-click package: dist\ChatReader\ChatReader.exe (PyInstaller one-folder) and dist\ChatReader.zip.
# Needs only the project's own .venv (pip install -r requirements.txt -r requirements-dev.txt).
$ErrorActionPreference = 'Stop'
$app = $PSScriptRoot
$py  = "$app\.venv\Scripts\python.exe"
$out = "$app\dist\ChatReader"
$sw  = [Diagnostics.Stopwatch]::StartNew()

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
Copy-Item "$app\README.txt" $out

Compress-Archive -Path $out -DestinationPath "$app\dist\ChatReader.zip" -CompressionLevel Optimal
$files = Get-ChildItem $out -Recurse -File
"built ChatReader $ver in {0:N0} s: {1} files, {2:N1} MB unpacked, zip {3:N1} MB" -f $sw.Elapsed.TotalSeconds, $files.Count, (($files | Measure-Object Length -Sum).Sum / 1e6), ((Get-Item "$app\dist\ChatReader.zip").Length / 1e6)
