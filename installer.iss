; ContextPulse Windows Installer -- Inno Setup 6 script
; Packs the PyInstaller onedir output (dist\ContextPulse\) into one setup exe.
; Build with scripts\build_windows_installer.ps1, which passes the version:
;   ISCC.exe /DMyAppVersion=0.1.2 installer.iss
;
; Installs per-user (no admin): %LOCALAPPDATA%\Programs\ContextPulse by default.
; Silent install for scripted tests / deployment:
;   ContextPulseSetup-<ver>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\path" /MERGETASKS="!autostart"
; Silent uninstall:
;   "<dir>\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
;
; Installed programs:
;   ContextPulse.exe     tray daemon (capture + its own MCP server on 127.0.0.1:8420)
;   ContextPulseCLI.exe  console twin: --license-status, --activate-license FILE

#define MyAppName "ContextPulse"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.2"
#endif
#define MyAppPublisher "Jerard Ventures LLC"
#define MyAppURL "https://contextpulse.ai"
#define MyAppExeName "ContextPulse.exe"
#define MyCliExeName "ContextPulseCLI.exe"

[Setup]
AppId={{A3F7E2B1-6D4C-4E8A-B2C1-5D9F3A7E1B3C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ContextPulseSetup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=no
RestartApplications=no
SetupIconFile=assets\contextpulse.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start ContextPulse when Windows starts"; GroupDescription: "Startup:"

[Files]
; Everything PyInstaller produced: both exes and the shared _internal\ folder.
Source: "dist\ContextPulse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; AGPL-3.0: the licence text and notices travel with the binary.
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "NOTICE"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion
Source: "THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Auto-start on login (current user only, no admin needed)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ContextPulse"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopRunningCopies();
var
  ResultCode: Integer;
begin
  // The tray daemon and its MCP server child are both ContextPulse.exe.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyCliExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  // Upgrade in place: a running copy would hold its DLLs open.
  StopRunningCopies();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  // Otherwise files in use survive the uninstall.
  StopRunningCopies();
  Result := True;
end;
