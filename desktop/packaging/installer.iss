; Inno Setup script for the GameVault Sync installer.
; Build the app first (python packaging/build.py), then:
;   iscc /DAppVersion=1.1.0 packaging\installer.iss
; Output: dist\GameVaultSync-Setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{9C3F5B2E-6A41-4E8B-9D1F-2B7A6C0E4F13}
AppName=GameVault Sync
AppVersion={#AppVersion}
AppVerName=GameVault Sync {#AppVersion}
AppPublisher=GameVault
AppPublisherURL=https://github.com/adiuv0/gamevault
; Per-user install: no admin prompt, and updates don't need one either.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\GameVault Sync
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GameVaultSync-Setup
SetupIconFile=..\build\GameVaultSync.ico
UninstallDisplayIcon={app}\GameVaultSync.exe
UninstallDisplayName=GameVault Sync
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\app\GameVaultSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; PyInstaller's folder changes between versions; don't leave old pieces behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\GameVault Sync"; Filename: "{app}\GameVaultSync.exe"
Name: "{autodesktop}\GameVault Sync"; Filename: "{app}\GameVaultSync.exe"; Tasks: desktopicon

[Registry]
; The app adds its own "start with Windows" entry; remove it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "GameVault Sync"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\GameVaultSync.exe"; Description: "Open GameVault Sync"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings and the sync memory live in %LOCALAPPDATA%\GameVault Sync and are kept,
; so reinstalling doesn't re-check every screenshot. Only the log goes.
Type: files; Name: "{localappdata}\GameVault Sync\gamevault-sync.log*"

[Code]
procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM GameVaultSync.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // A running copy holds its files open; an update would fail to replace them.
  StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;
