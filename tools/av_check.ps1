# Antivirus check for the packaged app, done the way a stranger would meet it.
#   1. copy dist\ChatReader.zip to a fresh folder and mark it as downloaded from the internet (ZoneId=3)
#   2. extract it with the Windows shell (the code behind Explorer's "Extract all")
#   3. record a fingerprint of every program file
#   4. start ChatReader.exe through ShellExecute (what a double-click does), connected to a live channel,
#      speech muted, window minimised; press the three hotkeys; let it run
#   5. afterwards: was the process still alive, is every file still there and unchanged, did any security
#      program open a window, did Windows log anything about it
# Nothing is excluded from scanning and no security setting is read-write touched.
# -Direct starts the exe with CreateProcess instead of ShellExecute. Windows then shows no "Open File - Security
# Warning" (that dialog belongs to Windows, not to the antivirus, and needs a person to press Run). The files keep
# their download mark and the antivirus watches the process exactly the same.
param([string] $Channel = 'jerma985', [int] $Seconds = 180, [int] $Launches = 1, [string] $Label = 'av', [switch] $Direct)
$ErrorActionPreference = 'Stop'
$app = Split-Path $PSScriptRoot -Parent
$dir = "$app\stranger"
$logDir = "$app\logs"; New-Item -ItemType Directory -Force $logDir, $dir | Out-Null
$started = Get-Date
# Bitdefender writes every quarantined file into this folder; a new entry during the run is a detection.
# (Its two entries from 2026-10-11 17:53 and 17:57 are the rejected renamed-interpreter experiment.)
$qDir = 'C:\ProgramData\Bitdefender\Desktop\Quarantine'
function Quarantine { if (Test-Path $qDir) { $i = Get-ChildItem $qDir -Force -ErrorAction SilentlyContinue; "$(@($i).Count) entries, newest $((($i | Sort-Object LastWriteTime | Select-Object -Last 1).LastWriteTime).ToString('yyyy-MM-dd HH:mm:ss'))" } else { 'no Bitdefender quarantine folder on this PC' } }
$qBefore = Quarantine
"antivirus quarantine before: $qBefore"

Add-Type @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class WinList {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  public static List<string> List() { var r = new List<string>(); EnumWindows((h, l) => { if (IsWindowVisible(h)) { var t = new StringBuilder(512); uint pid; GetWindowText(h, t, 512); GetWindowThreadProcessId(h, out pid); r.Add(h.ToInt64() + "|" + pid + "|" + t); } return true; }, IntPtr.Zero); return r; }
}
'@
function Fingerprint($root) {
    $h = @{}
    Get-ChildItem $root -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length)
        $h[$rel] = if ($_.Extension -in '.exe', '.dll', '.pyd') { (Get-FileHash $_.FullName -Algorithm SHA256).Hash } else { [string]$_.Length }
    }
    $h
}

# 1 + 2
$zip = "$dir\ChatReader.zip"
Copy-Item "$app\dist\ChatReader.zip" $zip -Force
Set-Content -Path $zip -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3`r`nHostUrl=https://github.com/example/chat-reader/releases/download/v0.1.0/ChatReader.zip"
if (Test-Path "$dir\ChatReader") { [IO.Directory]::Delete("$dir\ChatReader", $true) }
$expected = (Get-ChildItem "$app\dist\ChatReader" -Recurse -File).Count
$shell = New-Object -ComObject Shell.Application
$sw = [Diagnostics.Stopwatch]::StartNew()
$shell.NameSpace($dir).CopyHere($shell.NameSpace($zip).Items(), 0x14)
do { Start-Sleep -Milliseconds 500; $n = if (Test-Path "$dir\ChatReader") { (Get-ChildItem "$dir\ChatReader" -Recurse -File -ErrorAction SilentlyContinue).Count } else { 0 } } while ($n -lt $expected -and $sw.Elapsed.TotalSeconds -lt 600)
"extract: $n of $expected files in $([int]$sw.Elapsed.TotalSeconds) s"
$exe = "$dir\ChatReader\ChatReader.exe"
"download mark on ChatReader.exe: " + $(if (Get-Item $exe -Stream Zone.Identifier -ErrorAction SilentlyContinue) { 'present (ZoneId=3)' } else { 'MISSING' })

# 3
$before = Fingerprint "$dir\ChatReader"
"fingerprinted $($before.Count) files"

$securityNames = 'bd', 'bitdefender', 'seccenter', 'vsserv', 'smartscreen', 'MsMpEng', 'SecurityHealth', 'consent'
$results = @()
for ($i = 1; $i -le $Launches; $i++) {
    $log = "$logDir\$Label-$i.jsonl"; if (Test-Path $log) { [IO.File]::Delete($log) }
    $env:CR_TIMING_LOG = $log; $env:CR_NULL_AUDIO = '1'; $env:CR_START_MINIMIZED = '1'
    $env:CR_AUTOCONNECT = $Channel; $env:CR_DATA_DIR = "$logDir\data-$Label"
    $winBefore = [WinList]::List() | ForEach-Object { ($_ -split '\|')[0] }
    $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    if ($Direct) {
        $psi = New-Object Diagnostics.ProcessStartInfo $exe; $psi.UseShellExecute = $false; $psi.WorkingDirectory = Split-Path $exe
        [Diagnostics.Process]::Start($psi) | Out-Null
    } else {
        Start-Process -FilePath $exe      # ShellExecute, as a double-click
    }
    # 4
    $deadline = (Get-Date).AddSeconds($Seconds); $proc = $null; $diedAt = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $proc = Get-Process -Name ChatReader -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Select-Object -First 1
        if (-not $proc -and ((Get-Date) - $started).TotalSeconds -gt 8 -and -not $diedAt) { $diedAt = [math]::Round(([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0) - $t0, 1) }
    }
    foreach ($fk in 0x7A, 0x79, 0x79, 0x7B) {
        foreach ($vk in 0x11, 0x12, $fk) { [WinList]::keybd_event($vk, 0, 0, [UIntPtr]::Zero) }
        foreach ($vk in $fk, 0x12, 0x11) { [WinList]::keybd_event($vk, 0, 2, [UIntPtr]::Zero) }
        Start-Sleep -Milliseconds 700
    }
    $proc = Get-Process -Name ChatReader -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Select-Object -First 1
    $alive = [bool]$proc; $priv = $null
    # 5: windows opened by security software during the run
    $newWins = [WinList]::List() | Where-Object { $winBefore -notcontains ($_ -split '\|')[0] } | ForEach-Object {
        $h, $wpid, $title = $_ -split '\|', 3; $pn = (Get-Process -Id $wpid -ErrorAction SilentlyContinue).Name
        if ($pn -and ($securityNames | Where-Object { $pn -like "*$_*" })) { "$pn : '$title'" }
    }
    if ($alive) { $proc.Refresh(); $priv = [math]::Round($proc.PrivateMemorySize64 / 1MB); $proc.CloseMainWindow() | Out-Null; if (-not $proc.WaitForExit(6000)) { $proc.Kill() } }
    $marks = if (Test-Path $log) { Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json } } else { @() }
    $results += [pscustomobject]@{
        launch = $i; started = [bool]($marks | Where-Object mark -eq 'python_started'); alive_at_end = $alive; died_after_s = $diedAt
        usable_s = $(if (($marks | Where-Object mark -eq 'usable') -and ($marks | Where-Object mark -eq 'python_started')) { [math]::Round((($marks | Where-Object mark -eq 'usable')[0].t) - (($marks | Where-Object mark -eq 'python_started')[0].t), 1) })
        reading_chat = [bool]($marks | Where-Object mark -eq 'reading_chat')
        spoken = @($marks | Where-Object mark -eq 'spoken').Count; not_read = @($marks | Where-Object mark -eq 'not_read').Count
        hotkeys_seen = @($marks | Where-Object mark -eq 'hotkey').Count; private_mb = $priv
        clean_exit = [bool]($marks | Where-Object mark -eq 'exited'); security_windows = ($newWins -join '; ')
    }
}
$results | Format-List

$after = Fingerprint "$dir\ChatReader"
$missing = $before.Keys | Where-Object { -not $after.ContainsKey($_) }
$changed = $before.Keys | Where-Object { $after.ContainsKey($_) -and $after[$_] -ne $before[$_] }
$added = $after.Keys | Where-Object { -not $before.ContainsKey($_) }
"files after the run: $($after.Count) of $($before.Count); missing $(@($missing).Count); changed $(@($changed).Count); new $(@($added).Count)"
if ($missing) { 'MISSING: ' + (($missing | Select-Object -First 10) -join ', ') }
if ($changed) { 'CHANGED: ' + (($changed | Select-Object -First 10) -join ', ') }
if ($added) { 'NEW: ' + (($added | Select-Object -First 10) -join ', ') }
$ev = Get-WinEvent -FilterHashtable @{ LogName = 'Application', 'System'; StartTime = $started } -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match 'ChatReader' -or $_.ProviderName -match 'Bitdefender|Defender|SmartScreen|Application Error|Windows Error Reporting' }
"Windows event-log entries about the app or from security software since the start: $(@($ev).Count)"
$ev | Select-Object -First 5 | ForEach-Object { "  $($_.TimeCreated) $($_.ProviderName): $($_.Message.Substring(0, [Math]::Min(200, $_.Message.Length)))" }
$qAfter = Quarantine
"antivirus quarantine after:  $qAfter  ->  " + $(if ($qAfter -eq $qBefore) { 'UNCHANGED (nothing quarantined)' } else { 'CHANGED: something was quarantined during this run' })
$results | ConvertTo-Json | Set-Content "$logDir\$Label-summary.json"
