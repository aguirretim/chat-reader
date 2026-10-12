# Installer

`chatreader.iss` builds `dist\ChatReader-Setup-<version>.exe`: a per-user installer (no administrator prompt)
that puts the app in `%LOCALAPPDATA%\Programs\Chat Reader`, adds a Start-menu shortcut, an optional desktop
shortcut and an entry in Windows "Apps". Uninstalling removes the program and shortcuts and keeps the settings
in `%APPDATA%\ChatReader` unless the person answers Yes to "delete your settings as well?" (default No; a silent
uninstall never asks and always keeps them).

```
.\build.ps1 -Installer        # build the app, then the installer
.\build.ps1 -InstallerOnly    # installer from the dist\ChatReader folder that is already there
.\tools\installer_check.ps1   # install, start, refuse-while-running, install over, uninstall, verify (25 checks)
```

## The build tool: Inno Setup 7.1.0, portable copy in `tools\innosetup` (not in git)

| | |
|---|---|
| File | `innosetup-7.1.0-x64.exe`, 14,304,168 bytes |
| Source | https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe (release `is-7_1_0`, published 2026-08-12) |
| SHA-256 | `0362A383ED217D4C4239B5933866DD96D3EB2102737DA92F80F6057A4B40DF2F` |
| Signature | valid, signed by Pyrsys B.V. (checked with `Get-AuthenticodeSignature`) |
| How it was unpacked | `innosetup-7.1.0-x64.exe /PORTABLE=1 /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /NOICONS /DIR="<app>\tools\innosetup"` |
| System changes | none: no uninstall entry, no Start-menu entry, no registry key (checked after unpacking) |

Licence note: Inno Setup is free to use; its authors ask commercial users (revenue above USD 5,000 a year,
donations included) to buy a licence. See https://jrsoftware.org/isorder.php

## Do not change

- `AppId` in `chatreader.iss`. Windows uses it to find the older copy when installing over it.
- The install is per-user on purpose. A machine-wide install would need an administrator prompt.