; -----------------------------------------------------------------------------
; LuckyD Browser - Windows installer script (Inno Setup 6)
;
; Produces a single, shareable setup file:
;   browser\installer\output\LuckyDBrowserSetup-3.6.0.exe
;
; Anyone can run it - it installs per-user (no admin needed) to
; %LOCALAPPDATA%\Programs\LuckyDBrowser with Start Menu / Desktop
; shortcuts and a Settings > Apps uninstall entry.
;
; Build (from repo root):
;   powershell -File browser\installer\build_installer.ps1
; -----------------------------------------------------------------------------

#define AppName      "LuckyD Browser"
#define AppVersion   "3.6.0"
#define AppPublisher "LuckyD"
#define AppExeName   "LuckyDBrowser.exe"
#define AppURL       "https://github.com/Dylanchess0320/LuckyD-Browser"

[Setup]
AppId={{A69ECBB6-BDCC-4A32-B71A-A4F13A1569B2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion=3.6.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion=3.6.0.0
; Per-user install ??? no admin rights required (admin users may opt into all-users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\LuckyDBrowser
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=LuckyDBrowserSetup-3.6.0
SetupIconFile=..\assets\professional_icon.ico
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
; Everything PyInstaller produced ??? LuckyDBrowser.exe plus the _internal
; folder (Qt WebEngine runtime, assets, bundled luckyd-code.exe backend).
Source: "..\dist\LuckyDBrowser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Free local AI bootstrap ??? run post-install (see [Run]) so every user gets
; Ollama + a default model without lifting a finger.
Source: "ollama_setup.ps1"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Pre-2.5.8 installers used this legacy filename.  Inno only updates a
; shortcut whose name it owns, so it left the old link alongside the current
; "LuckyD Browser.lnk" link on upgrades.
Type: files; Name: "{autodesktop}\LuckyDBrowser.lnk"
Type: files; Name: "{userprograms}\LuckyDBrowser\LuckyDBrowser.lnk"
; An older build created the app link at the Start Menu root instead of in
; the product group. Keep the group-owned shortcut below and remove this
; stale duplicate during every upgrade.
Type: files; Name: "{userprograms}\LuckyD Browser.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "{#AppName} ??? Chromium-based AI browser by LuckyD"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Free, unlimited local AI: installs Ollama (if missing) and pulls the default
; model (~3 GB one-time download). Checked by default; runs detached in a
; console window so the download progress stays visible after Setup closes.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ollama_setup.ps1"""; Description: "Set up free unlimited local AI (Ollama + llama3.2:3b, one-time download)"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// A running browser would lock files during install/upgrade ??? stop it first.
procedure KillRunningApp;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM QtWebEngineProcess.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp;
  Result := '';
end;



