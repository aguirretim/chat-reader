# Prove the first-start download for real, from this source folder:
#   move the two voice files aside (they are not deleted), start the app muted with scratch settings,
#   wait for it to download them and become usable, compare the downloaded files with the originals,
#   then put the originals back (the downloaded copies are removed).
# Downloads about 115 MB once from the kokoro-onnx release on GitHub.
param([int] $TimeoutSeconds = 420)
$ErrorActionPreference = 'Stop'
$app = Split-Path $PSScriptRoot -Parent
$models = "$app\models"
$aside = "$app\logs\models-aside"
$names = 'kokoro-v1.0.int8.onnx', 'voices-v1.0.bin'
$logDir = "$app\logs"; $log = "$logDir\first-start.jsonl"
if (Test-Path $aside) { throw "$aside already exists: an earlier run did not finish. Put the files back by hand first." }
foreach ($n in $names) { if (-not (Test-Path "$models\$n")) { throw "missing $models\$n" } }
$original = @{}; foreach ($n in $names) { $original[$n] = (Get-FileHash "$models\$n" -Algorithm SHA256).Hash }
New-Item -ItemType Directory $aside | Out-Null
foreach ($n in $names) { Move-Item "$models\$n" "$aside\$n" }
"moved aside: $($names -join ', ')"
$result = $null
try {
    if (Test-Path $log) { [IO.File]::Delete($log) }
    $data = "$logDir\data-first-start"; if (Test-Path $data) { [IO.Directory]::Delete($data, $true) }
    $env:CR_TIMING_LOG = $log; $env:CR_NULL_AUDIO = '1'; $env:CR_START_MINIMIZED = '1'; $env:CR_AUTOTEST = '1'
    $env:CR_DATA_DIR = $data; $env:CR_AUTOCONNECT = $null
    $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $p = Start-Process "$app\.venv\Scripts\pythonw.exe" -ArgumentList 'run.py' -WorkingDirectory $app -PassThru
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline -and -not ((Test-Path $log) -and (Select-String -Path $log -Pattern '"spoken"|"voice_download_failed"|"voice_failed"' -Quiet))) { Start-Sleep -Milliseconds 500 }
    Start-Sleep -Seconds 1
    $marks = if (Test-Path $log) { Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json } } else { @() }
    $at = { param($n) $m = $marks | Where-Object mark -eq $n | Select-Object -First 1; if ($m) { [math]::Round($m.t - $t0, 1) } }
    $downloaded = @{}; foreach ($n in $names) { $downloaded[$n] = if (Test-Path "$models\$n") { (Get-FileHash "$models\$n" -Algorithm SHA256).Hash } else { 'absent' } }
    $result = [pscustomobject]@{
        window_s = (& $at 'window_ready'); download_started_s = (& $at 'voice_download_started'); download_done_s = (& $at 'voice_download_done')
        usable_s = (& $at 'usable'); spoke_test_sentence = [bool]($marks | Where-Object mark -eq 'spoken')
        failed = (($marks | Where-Object { $_.mark -like '*failed' } | ForEach-Object { "$($_.mark) $($_.kind)" }) -join '; ')
        model_identical = ($downloaded[$names[0]] -eq $original[$names[0]]); voices_identical = ($downloaded[$names[1]] -eq $original[$names[1]])
        part_files_left = @(Get-ChildItem $models -Filter '*.part' -ErrorAction SilentlyContinue).Count
        check_memo_written = (Test-Path "$data\voice-check.json")
    }
    # the app's own process is the child of the venv launcher
    $real = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | Where-Object Name -like 'python*' | Select-Object -First 1
    $target = if ($real) { Get-Process -Id $real.ProcessId -ErrorAction SilentlyContinue } else { $p }
    if ($target -and -not $target.HasExited) { $target.CloseMainWindow() | Out-Null; if (-not $target.WaitForExit(8000)) { $target.Kill() } }
    if (-not $p.HasExited) { $p.Kill() }
}
finally {
    # always put Tim's originals back; the downloaded copies were only for the comparison
    foreach ($n in $names) {
        if (Test-Path "$models\$n") { [IO.File]::Delete("$models\$n") }
        if (Test-Path "$models\$n.part") { [IO.File]::Delete("$models\$n.part") }
        Move-Item "$aside\$n" "$models\$n"
    }
    [IO.Directory]::Delete($aside)
    $back = $true; foreach ($n in $names) { if ((Get-FileHash "$models\$n" -Algorithm SHA256).Hash -ne $original[$n]) { $back = $false } }
    "originals back in models\ and unchanged: $back"
}
$result | Format-List
