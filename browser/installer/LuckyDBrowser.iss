; -----------------------------------------------------------------------------
; LuckyD Browser - Windows installer script (Inno Setup 6)
;
; Produces a single, shareable setup file:
;   browser\installer\output\LuckyDBrowserSetup-10.9.1.exe
;
; Anyone can run it - it installs per-user (no admin needed) to
; %LOCALAPPDATA%\Programs\LuckyDBrowser with Start Menu / Desktop
; shortcuts and a Settings > Apps uninstall entry.
;
; Build (from repo root):
;   powershell -File browser\installer\build_installer.ps1
; -----------------------------------------------------------------------------

#define AppName      "LuckyD Browser"
#define AppVersion   "10.9.1"
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
VersionInfoVersion=10.9.1.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion=10.9.1.0
; Per-user install - no admin rights required (admin users may opt into all-users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\LuckyDBrowser
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=LuckyDBrowserSetup-10.9.1
SetupIconFile=..\assets\professional_icon.ico
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
; 10.9.1: checked by default so a brand-new install is ready on first
; launch. The browser reads .first-run-setup and runs the bundled,
; idempotent bootstrap once. Unchecking writes "0" and setup stays manual.
Name: "agentsetup"; Description: "Set up agent tools automatically on first launch (recommended)"; GroupDescription: "New-user setup:"; Flags: checkedonce

[Files]
; Everything PyInstaller produced - LuckyDBrowser.exe plus the _internal
; folder (Qt WebEngine runtime, assets, bundled luckyd-code.exe backend).
Source: "..\dist\LuckyDBrowser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Free local AI bootstrap - bundled and run by the browser's one-time
; first-launch setup (installer task "agentsetup"), never by the installer
; itself.
Source: "ollama_setup.ps1"; DestDir: "{app}"; Flags: ignoreversion
; New-user automation (10.8.9 behavior restored in 10.9.1): the browser runs
; this idempotent bootstrap once on first launch. No secrets are stored, and
; Start Menu > Agent Setup re-runs it any time.
Source: "LuckyD-Bootstrap.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "LuckyD-Browser-Setup-Guide.md"; DestDir: "{app}"; Flags: ignoreversion

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
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "{#AppName} - Chromium-based AI browser by LuckyD"
Name: "{group}\Agent Setup"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy RemoteSigned -File ""{app}\LuckyD-Bootstrap.ps1"" -Groups all"; WorkingDir: "{app}"; Comment: "Install or repair optional agent tools (Ollama, Node, mesh CLIs)"
Name: "{group}\Set up Local AI"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy RemoteSigned -File ""{app}\LuckyD-Bootstrap.ps1"" -Groups ollama"; WorkingDir: "{app}"; Comment: "Install Ollama and a small local model (about 2 GB download)"
Name: "{group}\Install Agent Mesh CLIs"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy RemoteSigned -File ""{app}\LuckyD-Bootstrap.ps1"" -Groups core,free,keyed"; WorkingDir: "{app}"; Comment: "Install Node, Git, GitHub CLI, Python and the agent CLIs (several GB)"
Name: "{group}\Setup Guide"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\LuckyD-Browser-Setup-Guide.md"""; Comment: "LuckyD Browser new-user setup guide"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Default-browser registration (per-user HKCU only — no admin needed).
; This makes LuckyD appear in Settings > Apps > Default apps; Windows
; still requires the user to pick it there (no silent takeover).
; Activation passes the URL as argv[1], routed by startup_urls_from_argv.
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "{#AppName} — Chromium-based AI browser"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities\Startmenu"; ValueType: string; ValueName: "StartMenuInternet"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities\URLAssociations"; ValueType: string; ValueName: "http"; ValueData: "LuckyDHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities\URLAssociations"; ValueType: string; ValueName: "https"; ValueData: "LuckyDHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".htm"; ValueData: "LuckyDHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".html"; ValueData: "LuckyDHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\Clients\StartMenuInternet\{#AppName}\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\LuckyDHTML"; ValueType: string; ValueData: "LuckyD HTML Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\LuckyDHTML\DefaultIcon"; ValueType: string; ValueName: "DefaultIcon"; ValueData: "{app}\{#AppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\LuckyDHTML\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
; 10.9.1: the installer itself still downloads nothing and runs no
; PowerShell (a post-install fetch is a behavioral security flag). Agent
; setup is queued for the browser's first launch via the "agentsetup" task,
; and the Start Menu shortcuts above re-run it any time.
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// A running browser would lock files during install/upgrade - stop it first.
procedure KillRunningApp;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM QtWebEngineProcess.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { Orphaned agent backends keep _internal files locked — the 10.2.3
    upgrades failed twice on DeleteFile code 5 until these were added. }
  Exec('taskkill.exe', '/F /IM luckyd-code.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM luckyd-cli.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp;
  Result := '';
end;

{ 10.9.1: persist the new-user setup choice for the browser's first launch.
  "1" = run the bundled bootstrap once; "0" = stay manual. }
procedure WriteFirstRunMarker;
var
  MarkerPath: String;
  Value: String;
begin
  MarkerPath := ExpandConstant('{app}\.first-run-setup');
  if WizardIsTaskSelected('agentsetup') then
    Value := '1'
  else
    Value := '0';
  SaveStringToFile(MarkerPath, Value, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteFirstRunMarker;
end;





