# Run the app against a real live channel for a while, minimised and with the speech muted, and report counts.
# No chat text is written anywhere: the timing log holds marks and counters only.
# Usage: live_run.ps1 -Exe <python.exe or ChatReader.exe> [-ExeArgs 'run.py'] -Channel <name> -Seconds 90 -Label src
param(
    [Parameter(Mandatory)] [string] $Exe,
    [string] $ExeArgs = '',
    [Parameter(Mandatory)] [string] $Channel,
    [int] $Seconds = 90,
    [string] $Label = 'live',
    [switch] $PressHotkeys
)
$app = Split-Path $PSScriptRoot -Parent
$logDir = "$app\logs"; New-Item -ItemType Directory -Force $logDir | Out-Null
$log = "$logDir\$Label.jsonl"; if (Test-Path $log) { [IO.File]::Delete($log) }
$env:CR_TIMING_LOG = $log; $env:CR_NULL_AUDIO = '1'; $env:CR_START_MINIMIZED = '1'
$env:CR_AUTOCONNECT = $Channel; $env:CR_DATA_DIR = "$logDir\data-$Label"
$t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
$psi = @{ FilePath = $Exe; PassThru = $true; WorkingDirectory = $app }
if ($ExeArgs) { $psi.ArgumentList = $ExeArgs }
$p = Start-Process @psi
Start-Sleep -Seconds $Seconds
if ($PressHotkeys) {
    Add-Type -Namespace W -Name K -MemberDefinition '[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, System.UIntPtr extra);'
    foreach ($fk in 0x7A, 0x79, 0x79, 0x7B) {   # skip, pause, resume, clear
        foreach ($vk in 0x11, 0x12, $fk) { [W.K]::keybd_event($vk, 0, 0, [UIntPtr]::Zero) }
        foreach ($vk in $fk, 0x12, 0x11) { [W.K]::keybd_event($vk, 0, 2, [UIntPtr]::Zero) }
        Start-Sleep -Milliseconds 700
    }
}
$alive = -not $p.HasExited
$priv = $null
# a venv's pythonw.exe is a small launcher that starts the real interpreter as a child: measure and close that one
$real = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | Where-Object Name -like 'python*' | Select-Object -First 1
$target = if ($real) { Get-Process -Id $real.ProcessId } else { $p }
if (-not $target.HasExited) { $target.Refresh(); $priv = [math]::Round($target.PrivateMemorySize64 / 1MB); $target.CloseMainWindow() | Out-Null; if (-not $target.WaitForExit(6000)) { $target.Kill() } }
if (-not $p.HasExited) { $p.Kill() }
$marks = if (Test-Path $log) { Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json } } else { @() }
$get = { param($n) ($marks | Where-Object mark -eq $n | Select-Object -First 1) }
$spoken = @($marks | Where-Object mark -eq 'spoken').Count
$quit = & $get 'quitting'
[pscustomobject]@{
    label = $Label; alive_at_end = $alive; private_mb = $priv
    window_s = if (& $get 'window_ready') { [math]::Round((& $get 'window_ready').t - $t0, 2) }
    usable_s = if (& $get 'usable') { [math]::Round((& $get 'usable').t - $t0, 2) }
    reading_chat_s = if (& $get 'reading_chat') { [math]::Round((& $get 'reading_chat').t - $t0, 2) }
    first_spoken_s = if ($spoken) { [math]::Round((($marks | Where-Object mark -eq 'spoken')[0]).t - $t0, 2) }
    spoken = $spoken; not_read = @($marks | Where-Object mark -eq 'not_read').Count
    hotkeys_failed = if (& $get 'hotkeys') { (& $get 'hotkeys').failed }
    hotkey_presses_seen = @($marks | Where-Object mark -eq 'hotkey').Count
    tray = [bool](& $get 'tray_started'); clean_exit = [bool](& $get 'exited')
    voice_failed = [bool](& $get 'voice_failed')
} | Format-List
if (Test-Path "$env:CR_DATA_DIR\error.log") { 'ERROR LOG:'; Get-Content "$env:CR_DATA_DIR\error.log" }
