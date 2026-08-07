; ─────────────────────────────────────────────────────────────────────
; LuckyD Browser — Windows installer script (Inno Setup 6)
;
; Produces a single, shareable setup file:
;   browser\installer\output\LuckyDBrowserSetup-1.5.0.exe
;
; Anyone can run it — it installs per-user (no admin needed) to
; %LOCALAPPDATA%\Programs\LuckyDBrowser with Start Menu / Desktop
; shortcuts and a Settings > Apps uninstall entry.
;
; Build (from repo root):
;   cd browser
;   python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
;   cd ..
;   iscc browser\installer\LuckyDBrowser.iss
; (or just run: powershell -File browser\installer\build_installer.ps1)
; ─────────────────────────────────────────────────────────────────────

#define AppName      "LuckyD Browser"
#define AppVersion   "1.5.0"
#define AppPublisher "LuckyD"
#define AppExeName   "LuckyDBrowser.exe"
#define AppURL       "https://github.com/luckyd/coding-agent"

[Setup]
AppId={{A69ECBB6-BDCC-4A32-B71A-A4F13A1569B2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion=1.5.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion=1.5.0.0
; Per-user install — no admin rights required (admin users may opt into all-users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\LuckyDBrowser
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=LuckyDBrowserSetup-1.5.0
SetupIconFile=..\assets\icon.ico
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Everything PyInstaller produced — LuckyDBrowser.exe plus the _internal
; folder (Qt WebEngine runtime, assets, bundled luckyd-code.exe backend).
Source: "..\dist\LuckyDBrowser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Free local AI bootstrap — run post-install (see [Run]) so every user gets
; Ollama + a default model without lifting a finger.
Source: "ollama_setup.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "{#AppName} — Chromium-based AI browser by LuckyD"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Free, unlimited local AI: installs Ollama (if missing) and pulls the default
; model (~3 GB one-time download). Checked by default; runs detached in a
; console window so the download progress stays visible after Setup closes.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ollama_setup.ps1"""; Description: "Set up free unlimited local AI (Ollama + llama3.2:3b, one-time download)"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// A running browser would lock files during install/upgrade — stop it first.
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
