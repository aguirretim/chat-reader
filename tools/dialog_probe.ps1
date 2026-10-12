# Start a file the way a double-click does (from a separate PowerShell, so a blocking dialog cannot hang this one),
# report whether Windows shows "Open File - Security Warning", close that dialog with Cancel, and stop the app.
param([Parameter(Mandatory)] [string] $Path, [int] $WaitSeconds = 8)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace DP -Name U -MemberDefinition '[DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr h, uint m, System.IntPtr w, System.IntPtr l);'
$env:CR_NULL_AUDIO = '1'; $env:CR_START_MINIMIZED = '1'; $env:CR_DATA_DIR = "$env:TEMP\cr-dialog-probe"
$zone = Get-Content $Path -Stream Zone.Identifier -ErrorAction SilentlyContinue
$child = Start-Process pwsh -ArgumentList '-NoProfile', '-Command', "Start-Process -FilePath '$Path'" -PassThru -WindowStyle Minimized
$dialog = $false
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline -and -not $dialog) {
    Start-Sleep -Milliseconds 500
    foreach ($w in [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)) {
        if ($w.Current.Name -eq 'Open File - Security Warning') {
            $dialog = $true
            [DP.U]::PostMessage([IntPtr]$w.Current.NativeWindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        }
    }
}
Start-Sleep -Seconds 1
$running = Get-Process | Where-Object { $_.Path -eq $Path }
"{0}: download mark {1}; security dialog {2}; app started {3}" -f (Split-Path $Path -Leaf), $(if ($zone) { 'present' } else { 'absent' }), $(if ($dialog) { 'SHOWN (closed with Cancel)' } else { 'not shown' }), [bool]$running
$running | ForEach-Object { $_.Kill() }
if (-not $child.HasExited) { $child.Kill() }
