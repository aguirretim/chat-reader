# Prove the installer on this PC: install, start the installed copy muted, refuse-while-running, install over,
# uninstall, and check that only the settings folder is left. Ends with no installed copy.
# The user's real settings (%APPDATA%\ChatReader\settings.json) are never opened by the test copy: it runs with
# CR_DATA_DIR pointing into logs\. They are hashed before and after.
param([string] $Setup = '')
$ErrorActionPreference = 'Stop'
$app = Split-Path $PSScriptRoot -Parent
if (-not $Setup) { $Setup = (Get-ChildItem "$app\dist" -Filter 'ChatReader-Setup-*.exe' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName }
$logDir = "$app\logs\installer"; New-Item -ItemType Directory -Force $logDir | Out-Null
$inst = "$env:LOCALAPPDATA\Programs\Chat Reader"
$exe = "$inst\ChatReader.exe"
$startLnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Chat Reader.lnk"
$deskLnk = [Environment]::GetFolderPath('Desktop') + '\Chat Reader.lnk'
$regKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{7E0B5B8C-3F0A-4C55-9D0B-6A6F1C2B9E41}_is1'
$settings = "$env:APPDATA\ChatReader\settings.json"
$qDir = 'C:\ProgramData\Bitdefender\Desktop\Quarantine'
function Quarantine { if (Test-Path $qDir) { $i = Get-ChildItem $qDir -Force -ErrorAction SilentlyContinue; "$(@($i).Count) entries, newest $((($i | Sort-Object LastWriteTime | Select-Object -Last 1).LastWriteTime).ToString('HH:mm:ss'))" } else { 'no quarantine folder' } }
function SettingsHash { if (Test-Path $settings) { (Get-FileHash $settings -Algorithm SHA256).Hash } else { 'absent' } }
function Run($file, $arguments) {      # CreateProcess, wait, return exit code and seconds
    $psi = New-Object Diagnostics.ProcessStartInfo $file, $arguments; $psi.UseShellExecute = $false
    $sw = [Diagnostics.Stopwatch]::StartNew(); $p = [Diagnostics.Process]::Start($psi); $p.WaitForExit()
    [pscustomobject]@{ code = $p.ExitCode; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
}
function Installed { Get-Process -Name ChatReader -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } }
function Check($name, $ok, $detail = '') { $script:rows += [pscustomobject]@{ check = $name; ok = [bool]$ok; detail = $detail }; "{0}  {1}  {2}" -f $(if ($ok) { 'ok  ' } else { 'FAIL' }), $name, $detail }
$rows = @()

"installer: $(Split-Path $Setup -Leaf), $([math]::Round((Get-Item $Setup).Length / 1e6, 1)) MB"
if ((Test-Path $inst) -or (Test-Path $regKey)) { throw "An installed copy already exists ($inst). This check starts from a clean state." }
$q0 = Quarantine; $s0 = SettingsHash
$srcFiles = (Get-ChildItem "$app\dist\ChatReader" -Recurse -File).Count

# 1. install (current user, silent, with the desktop shortcut)
$r = Run $Setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon /LOG=`"$logDir\install.log`""
Check 'install exit code 0' ($r.code -eq 0) "exit $($r.code), $($r.seconds) s"
$files = Get-ChildItem $inst -Recurse -File
Check 'all program files installed' ($files.Count -eq $srcFiles + 2) "$($files.Count) files ($srcFiles from the build + uninstaller exe and data), $([math]::Round(($files | Measure-Object Length -Sum).Sum / 1e6, 1)) MB"
Check 'installed in the user''s own programs folder' ($inst.StartsWith($env:LOCALAPPDATA)) $inst
Check 'licence list installed' (Test-Path "$inst\licenses\COMPONENTS.txt") "$((Get-ChildItem "$inst\licenses" -File).Count) files in licenses\"
Check 'Start-menu shortcut' (Test-Path $startLnk)
Check 'desktop shortcut (optional task ticked)' (Test-Path $deskLnk)
$reg = Get-ItemProperty $regKey -ErrorAction SilentlyContinue
Check 'entry in Windows Apps list (per-user)' ($reg -and $reg.DisplayName -eq 'Chat Reader') "DisplayName '$($reg.DisplayName)', version $($reg.DisplayVersion), publisher '$($reg.Publisher)'"
Check 'nothing registered machine-wide' (-not (Get-ChildItem 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like '*7E0B5B8C*' }))
Check 'uninstaller present and registered' (Test-Path "$inst\unins000.exe") $reg.UninstallString

# 2. start the installed copy from its Start-menu shortcut, muted, with its settings in logs\
$mark = "$logDir\run.jsonl"; if (Test-Path $mark) { [IO.File]::Delete($mark) }
$env:CR_TIMING_LOG = $mark; $env:CR_NULL_AUDIO = '1'; $env:CR_START_MINIMIZED = '1'; $env:CR_AUTOTEST = '1'; $env:CR_DATA_DIR = "$logDir\data"
Start-Process -FilePath $startLnk
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline -and -not ((Test-Path $mark) -and (Select-String -Path $mark -Pattern '"spoken"|_failed"' -Quiet))) { Start-Sleep -Milliseconds 300 }
$marks = if (Test-Path $mark) { Get-Content $mark | ForEach-Object { $_ | ConvertFrom-Json } } else { @() }
$t0 = ($marks | Where-Object mark -eq 'python_started' | Select-Object -First 1).t
$usable = ($marks | Where-Object mark -eq 'usable' | Select-Object -First 1).t
Check 'installed copy starts and becomes usable' ([bool]$usable) $(if ($usable) { "usable after $([math]::Round($usable - $t0, 1)) s" })
Check 'installed copy speaks the test sentence (muted)' ([bool]($marks | Where-Object mark -eq 'spoken'))
$hk = $marks | Where-Object mark -eq 'hotkeys' | Select-Object -First 1
"      note: hotkeys refused by Windows in the test copy: $($hk.failed) (expected when another Chat Reader is already running and owns them)"
$proc = Installed
Check 'process runs from the installed folder' ([bool]$proc) $proc.Path

# 3. with the installed copy running: installing over it and uninstalling must both refuse and change nothing
$r = Run $Setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$logDir\install-while-running.log`""
Check 'install over a running copy is refused' ($r.code -ne 0 -and (Installed)) "exit $($r.code); app still running"
$r = Run "$inst\unins000.exe" "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
Start-Sleep -Seconds 3
Check 'uninstall of a running copy is refused' ((Installed) -and (Test-Path $exe) -and (Test-Path $regKey)) "exit $($r.code); files and Apps entry still there"

# 4. close it, then install over the existing copy (a leftover file from an "older version" must disappear)
$proc = Installed; $proc.CloseMainWindow() | Out-Null; if (-not $proc.WaitForExit(8000)) { $proc.Kill() }
Check 'installed copy closes cleanly' ([bool](Get-Content $mark | Select-String '"exited"'))
Set-Content "$inst\_internal\file-from-an-older-version.txt" 'stale'
$r = Run $Setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$logDir\install-over.log`""
$files2 = Get-ChildItem $inst -Recurse -File
Check 'install over an older copy' ($r.code -eq 0 -and $files2.Count -eq $srcFiles + 2 -and -not (Test-Path "$inst\_internal\file-from-an-older-version.txt")) "exit $($r.code), $($r.seconds) s, $($files2.Count) files, stale file removed"
Check 'desktop shortcut kept by the second install' (Test-Path $deskLnk)

# 5. uninstall (silent: keeps the settings without asking)
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = Run "$inst\unins000.exe" "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
while ((Test-Path $inst) -and $sw.Elapsed.TotalSeconds -lt 90) { Start-Sleep -Milliseconds 500 }   # the uninstaller finishes from a temporary copy
Check 'uninstall exit code 0' ($r.code -eq 0) "exit $($r.code), folder gone after $([math]::Round($sw.Elapsed.TotalSeconds, 1)) s"
Check 'program folder removed' (-not (Test-Path $inst)) $(if (Test-Path $inst) { "left: " + ((Get-ChildItem $inst -Recurse -File | Select-Object -First 5 | ForEach-Object Name) -join ', ') })
Check 'Start-menu shortcut removed' (-not (Test-Path $startLnk))
Check 'desktop shortcut removed' (-not (Test-Path $deskLnk))
Check 'Apps entry removed' (-not (Test-Path $regKey))
Check 'no Chat Reader process from the installed folder' (-not (Installed))

# 6. the user's own settings and the antivirus
$s1 = SettingsHash
Check 'real settings file untouched' ($s1 -eq $s0) "sha256 $($s1.Substring(0, 16))... before and after; folder %APPDATA%\ChatReader kept: $(Test-Path (Split-Path $settings))"
$q1 = Quarantine
Check 'antivirus quarantine unchanged' ($q1 -eq $q0) $q1
$rows | ConvertTo-Json | Set-Content "$logDir\summary.json"
"{0} of {1} checks passed" -f @($rows | Where-Object ok).Count, $rows.Count
