; The Butler installer for Windows (#40), built by build-installer.ps1 with Inno Setup.
;
; It installs into the household's own profile, %USERPROFILE%\Butler, without an admin password,
; as the Mac's package installs into ~/Butler. It carries only the repo's tracked files, so an
; upgrade never touches the household's config, data, projects or budget (all git-ignored), and
; uninstalling leaves them where they are. After copying, it makes Butler's Python
; (scripts\bootstrap.ps1, logged to logs\bootstrap.log; without it the install fails with exit code 9),
; then butler.installed names the butler from this installer's filename
; and opens the guided first run, or, over an existing Butler, the choice to resume or start fresh.
;
; build-installer.ps1 passes /DSource=<staged files> /DVersion=<1.2.3> /DOutput=<folder>.

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{6B3F2A5E-8C41-4D7B-9E2A-B5C1D0F3A471}
AppName=Butler
AppVersion={#Version}
AppPublisher=AELaboratories
AppPublisherURL=https://buildyourbutler.com
DefaultDirName={%USERPROFILE}\Butler
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=Butler
PrivilegesRequired=lowest
OutputDir={#Output}
OutputBaseFilename=Butler-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayName=Butler
; Butler's files are its own; the household's are never in this list, so they stay.
UninstallFilesDir={app}\.uninstall

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Butler"; Filename: "{cmd}"; Parameters: "/k ""{app}\bin\butler.cmd"" resume"; WorkingDir: "{app}"; Comment: "Back to where you left off"

[Registry]
; `butler` in any new terminal, as the Mac's installer adds Butler to the PATH in ~/.zprofile.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\bin"; Check: NeedsAddPath(ExpandConstant('{app}\bin'))

[Run]
; Butler's Python first. cmd carries every line of its output, errors too, into the log, and the
; 64-bit PowerShell ({sysnative}) sees the computer's real processor (the installer is 32-bit).
Filename: "{cmd}"; Parameters: "/c """"{sysnative}\WindowsPowerShell\v1.0\powershell.exe"" -NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\bootstrap.ps1"" > ""{app}\logs\bootstrap.log"" 2>&1"""; WorkingDir: "{app}"; StatusMsg: "Getting Butler's own Python. This takes a minute or two."; Flags: runhidden waituntilterminated; BeforeInstall: MakeLogs
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-X utf8 ""{app}\run.py"" installed --installer ""{srcexe}"" --silent"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: WizardSilent and HasPython
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-X utf8 ""{app}\run.py"" installed --installer ""{srcexe}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: (not WizardSilent) and HasPython

[Code]
var
  NoPython: Boolean;

procedure MakeLogs;
begin
  ForceDirectories(ExpandConstant('{app}\logs'));
end;

// Called as each [Run] step comes up, so after the bootstrap has run.
function HasPython: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.venv\Scripts\python.exe'));
  NoPython := not Result;
end;

// An install without Python says so and fails (exit code 9), instead of finishing quietly.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssDone) and not HasPython then
    SuppressibleMsgBox('Butler couldn''t get its Python, so it isn''t ready yet. Check the internet connection and run this installer again. ' +
      'What went wrong is in ' + ExpandConstant('{app}\logs\bootstrap.log') + '.', mbError, MB_OK, IDOK);
end;

function GetCustomSetupExitCode: Integer;
begin
  if NoPython then Result := 9 else Result := 0;
end;

function NeedsAddPath(Dir: string): Boolean;
var
  Path: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Result := True
  else
    Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') = 0;
end;
