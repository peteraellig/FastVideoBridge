#ifndef PayloadDir
  #define PayloadDir SourcePath + "stage"
#endif
#ifndef OutputDir
  #define OutputDir SourcePath + "..\dist"
#endif
#define AppVersion "0.0.5.14"

[Setup]
AppId={{05AE6B2A-1031-4DBE-9251-52507DE5E804}
AppName=FastVideo Bridge
AppVersion={#AppVersion}
AppPublisher=Peter Aellig
DefaultDirName={sd}\FastVideoBridge
DefaultGroupName=FastVideo Bridge
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
OutputDir={#OutputDir}
#ifdef Lean
OutputBaseFilename=FastVideoBridge-Setup-{#AppVersion}-lean
#else
OutputBaseFilename=FastVideoBridge-Setup-{#AppVersion}
#endif
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=FastVideo Bridge
UninstallDisplayIcon={app}\images\fastvideobridge.ico
UninstallFilesDir={app}\scripts
SetupIconFile={#PayloadDir}\images\fastvideobridge.ico
CloseApplications=no
RestartApplications=no
#ifndef Lean
InfoBeforeFile={#PayloadDir}\documents\INSTALLATION.txt
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create Start and Stop desktop shortcuts"

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{app}\work"; Attribs: hidden; Flags: uninsneveruninstall
Name: "{app}\user"; Attribs: hidden; Flags: uninsneveruninstall

Name: "{app}\.runtime"; Attribs: hidden
Name: "{app}\fastvideo"; Attribs: hidden
Name: "{app}\static"; Attribs: hidden
Name: "{app}\images"; Attribs: hidden
Name: "{app}\vendor"; Attribs: hidden
Name: "{app}\gateway"; Attribs: hidden
Name: "{app}\licenses"; Attribs: hidden

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "Start-Bridge.bat,Stop-Bridge.bat,scripts\*,fastvideo.settings.json,work\*,user\*,*\__pycache__\*,*.pyc,*.pyo"; Attribs: hidden; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\fastvideo.settings.json"; DestDir: "{app}"; Attribs: hidden; Flags: onlyifdoesntexist uninsneveruninstall

Source: "{#PayloadDir}\Start-Bridge.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\Stop-Bridge.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion

[InstallDelete]
#ifdef Lean
; Lean setup over an earlier installation: its documents and notes go as well.
Type: filesandordirs; Name: "{app}\documents"
Type: files; Name: "{app}\vendor\omt\README.txt"
Type: files; Name: "{app}\vendor\omt\libomt.h"
Type: files; Name: "{app}\static\connection-guide.html"
Type: files; Name: "{app}\static\connection-guide.css"
Type: files; Name: "{app}\images\Thumbs.db"
Type: files; Name: "{app}\images\fastvideobridge.png"
Type: files; Name: "{app}\scripts\Update-Bridge.ps1"
Type: files; Name: "{app}\scripts\Update-Bridge.vbs"
#endif
; Earlier versions carried the NDI Tools installer (not redistributable).
Type: filesandordirs; Name: "{app}\installers"
Type: files; Name: "{app}\app.py"
Type: files; Name: "{app}\bridge.ico"
Type: files; Name: "{app}\static\bridge.ico"
Type: files; Name: "{app}\BRIDGE-README.md"
Type: files; Name: "{app}\requirements.lock.txt"
Type: files; Name: "{app}\requirements.txt"
Type: files; Name: "{app}\INSTALLATION.txt"
Type: files; Name: "{app}\PAYLOAD-MANIFEST.json"
Type: files; Name: "{app}\Start-Bridge.vbs"
Type: files; Name: "{app}\Stop-Bridge.vbs"
Type: files; Name: "{app}\Start-Whip.ps1"
Type: files; Name: "{app}\Stop-Whip.ps1"
Type: files; Name: "{app}\Stop-FastVideo.ps1"

[Icons]
Name: "{group}\FastVideo Bridge"; Filename: "{app}\Start-Bridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\images\fastvideobridge.ico"
Name: "{group}\Stop FastVideo Bridge"; Filename: "{app}\Stop-Bridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\images\fastvideobridge.ico"
Name: "{group}\Uninstall FastVideo Bridge"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FastVideo Bridge"; Filename: "{app}\Start-Bridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\images\fastvideobridge.ico"; Tasks: desktopicon

Name: "{autodesktop}\Stop FastVideo Bridge"; Filename: "{app}\Stop-Bridge.bat"; WorkingDir: "{app}"; IconFilename: "{app}\images\fastvideobridge.ico"; Tasks: desktopicon

[Run]
Filename: "https://ndi.video/tools/"; Description: "Open the NDI Tools download page (needed for NDI input or output)"; Flags: postinstall unchecked skipifsilent shellexec nowait
Filename: "{app}\Start-Bridge.bat"; Description: "Start FastVideo Bridge"; Flags: postinstall unchecked skipifsilent shellexec

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and
     (CompareText(ExtractFileName(RemoveBackslashUnlessRoot(WizardDirValue)), 'Fastvideo') = 0) then begin
    MsgBox('Choose a separate FastVideo Bridge folder. The existing FastVideo project must remain separate.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure StopInstalledBridge;
var ExitCode: Integer; StopScript: String;
begin
  StopScript := ExpandConstant('{app}\scripts\Stop-Bridge.ps1');
  if not FileExists(StopScript) then StopScript := ExpandConstant('{app}\Stop-Whip.ps1');
  if FileExists(ExpandConstant('{app}\work\pid-server.txt')) and FileExists(StopScript) then
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -File "' + StopScript + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopInstalledBridge;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then StopInstalledBridge;
end;

function BridgeGetFileAttributes(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function BridgeSetFileAttributes(FileName: String; Attributes: LongWord): Boolean;
  external 'SetFileAttributesW@kernel32.dll stdcall';
procedure HideLegacyFile(Name: String);
var Attributes: LongWord; FileName: String;
begin
  FileName := ExpandConstant('{app}\') + Name;
  Attributes := BridgeGetFileAttributes(FileName);
  if Attributes <> $FFFFFFFF then BridgeSetFileAttributes(FileName, Attributes or 2);
end;
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    HideLegacyFile('fastvideo.settings.json');
    HideLegacyFile('unins000.exe');
    HideLegacyFile('unins000.dat');
  end;
end;
