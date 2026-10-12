; Chat Reader: per-user installer (Inno Setup 7.1). Built by build.ps1 -Installer, which passes
;   /DAppVersion=<x.y.z> /DSourceDir=<dist\ChatReader> /DOutDir=<dist> /DIconFile=<build\chatreader.ico>
; Installs for the current user only: no administrator prompt, nothing written outside the user's profile.

#ifndef AppVersion
  #error AppVersion is not defined. Build through build.ps1 -Installer.
#endif

[Setup]
; Never change AppId: Windows uses it to recognise an older copy when installing over it.
AppId={{7E0B5B8C-3F0A-4C55-9D0B-6A6F1C2B9E41}
AppName=Chat Reader
AppVersion={#AppVersion}
AppVerName=Chat Reader {#AppVersion}
AppPublisher=Chat Reader project
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Chat Reader
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=Chat Reader Setup
VersionInfoCompany=Chat Reader project
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ChatReader.exe
UninstallDisplayName=Chat Reader
; {autopf} is %LOCALAPPDATA%\Programs when installing for the current user
PrivilegesRequired=lowest
DefaultDirName={autopf}\Chat Reader
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=ChatReader-Setup-{#AppVersion}
Compression=lzma2/normal
SolidCompression=no
WizardStyle=modern
; The running-copy check below gives a plainer message than the Restart Manager page.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[InstallDelete]
; Installing over an older copy: files the new version no longer ships must not stay behind.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Chat Reader"; Filename: "{app}\ChatReader.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Chat Reader"; Filename: "{app}\ChatReader.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ChatReader.exe"; Description: "Start Chat Reader now"; Flags: nowait postinstall skipifsilent

[Code]
// True when the installed copy (this folder's ChatReader.exe) is running. A copy started from
// somewhere else, for example a developer's build folder, does not count.
function InstalledCopyIsRunning(): Boolean;
var
  Locator, Services, Procs, P: Variant;
  I: Integer;
  Target: String;
begin
  Result := False;
  Target := Lowercase(ExpandConstant('{app}\ChatReader.exe'));
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('.', 'root\CIMV2');
    Procs := Services.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name = "ChatReader.exe"');
    for I := 0 to Procs.Count - 1 do
    begin
      P := Procs.ItemIndex(I);
      if (not VarIsNull(P.ExecutablePath)) and (Lowercase(P.ExecutablePath) = Target) then
      begin
        Result := True;
        Exit;
      end;
    end;
  except
    // If Windows cannot answer, carry on: a file in use then fails with Setup's own "file in use" message.
  end;
end;

// Installing over a copy that is running: say so and let the person close it and press Next again.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if InstalledCopyIsRunning() then
    Result := 'Chat Reader is running. Close it first (right-click its icon near the clock and choose Quit), then try again.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if InstalledCopyIsRunning() then
  begin
    MsgBox('Chat Reader is running. Close it first (right-click its icon near the clock and choose Quit), then uninstall again.', mbError, MB_OK);
    Result := False;
  end;
end;

// Settings (voice, speaker, channel, blocked-word choices) live in %APPDATA%\ChatReader and are kept unless the
// person says otherwise. A silent uninstall never asks and always keeps them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  SettingsDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  SettingsDir := ExpandConstant('{userappdata}\ChatReader');
  if UninstallSilent() or (not DirExists(SettingsDir)) then
    Exit;
  if MsgBox('Chat Reader has been removed.' + #13#10#13#10 +
            'Your settings were kept, so they will be there if you install it again.' + #13#10 +
            'Do you want to delete your settings as well?',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(SettingsDir, True, True, True);
end;
