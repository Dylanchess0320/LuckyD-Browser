; -----------------------------------------------------------------------------
; LuckyD Browser - Windows installer script (Inno Setup 6)
;
; Produces a single, shareable setup file:
;   browser\installer\output\LuckyDBrowserSetup-10.2.3.exe
;
; Anyone can run it - it installs per-user (no admin needed) to
; %LOCALAPPDATA%\Programs\LuckyDBrowser with Start Menu / Desktop
; shortcuts and a Settings > Apps uninstall entry.
;
; Build (from repo root):
;   powershell -File browser\installer\build_installer.ps1
; -----------------------------------------------------------------------------

#define AppName      "LuckyD Browser"
#define AppVersion   "10.2.3"
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
VersionInfoVersion=10.2.3.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion=10.2.3.0
; Per-user install â€” no admin rights required (admin users may opt into all-users).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\LuckyDBrowser
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=LuckyDBrowserSetup-10.2.3
SetupIconFile=..\assets\professional_icon.ico
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
; Everything PyInstaller produced â€” LuckyDBrowser.exe plus the _internal
; folder (Qt WebEngine runtime, assets, bundled luckyd-code.exe backend).
Source: "..\dist\LuckyDBrowser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Free local AI bootstrap â€” bundled for MANUAL opt-in only (double-click it in
; the install folder or run it from PowerShell). It is never executed by the
; installer itself: silent post-install downloads that install third-party
; software are a classic behavioral anti-ransomware flag.
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
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "{#AppName} â€” Chromium-based AI browser by LuckyD"
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
; NOTE (9.9): the Ollama bootstrap is intentionally NOT auto-run here.
; A post-install step that downloads and installs third-party software is a
; classic behavioral anti-ransomware flag (Halcyon/Defender). Users who want
; free local AI can run {app}\ollama_setup.ps1 manually at any time.
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// A running browser would lock files during install/upgrade â€” stop it first.
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





