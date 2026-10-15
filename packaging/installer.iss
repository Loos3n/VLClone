; Inno Setup script: builds VLClone-<version>-Setup.exe from dist\VLClone.
;   python packaging\make_associations.py        (writes build\associations.iss)
;   iscc /DAppVersion=1.0.0 packaging\installer.iss
; AppVersion comes from vlclone/__init__.py via make_version_info.py --print in CI.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C3F1E0B-5D3A-4C2B-9B7E-7A1C0B2E9F31}
AppName=VLClone
AppVersion={#AppVersion}
AppVerName=VLClone {#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=VLClone
AppPublisherURL=https://github.com/Loos3n/VLClone
AppUpdatesURL=https://github.com/Loos3n/VLClone/releases
DefaultDirName={autopf}\VLClone
DefaultGroupName=VLClone
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=VLClone-{#AppVersion}-Setup
SetupIconFile=..\build\vlclone.ico
UninstallDisplayIcon={app}\VLClone.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
LicenseFile=..\build\vlc\VLC-LICENSE.txt
; Tell Explorer to reload file associations after install and uninstall.
ChangesAssociations=yes
; A running VLClone keeps its files open: offer to close it.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; File types are opt-in. Windows still asks the user to confirm the default player
; (Settings > Default apps); VLClone only becomes the default where none was set.
Name: "assoc"; Description: "Open these file types with VLClone:"; GroupDescription: "File types:"; Flags: unchecked
Name: "assoc\video"; Description: "Video files (.mkv, .mp4, .avi, .mov, .webm, …)"; Flags: unchecked
Name: "assoc\audio"; Description: "Audio files (.mp3, .flac, .m4a, .ogg, .wav, …)"; Flags: unchecked
Name: "assoc\playlist"; Description: "Playlists (.m3u, .m3u8)"; Flags: unchecked
Name: "contextmenu"; Description: """Play with VLClone"" and ""Add to VLClone playlist"" in Explorer's right-click menu"; GroupDescription: "Explorer:"

[Files]
Source: "..\dist\VLClone\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VLClone"; Filename: "{app}\VLClone.exe"
Name: "{autodesktop}\VLClone"; Filename: "{app}\VLClone.exe"; Tasks: desktopicon

[Registry]
; Shows VLClone in Explorer's "Open with" list for media files (always; removed on uninstall).
Root: HKA; Subkey: "Software\Classes\Applications\VLClone.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "VLClone"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\VLClone.exe\shell\open\command"; ValueType: string; ValueData: """{app}\VLClone.exe"" ""%1"""; Flags: uninsdeletekey

[Code]
function AssocRoot(): Integer;
begin
  if IsAdminInstallMode then Result := HKLM else Result := HKCU;
end;

// True when no program is the default for Ext yet (or it already is VLClone).
function NoDefaultFor(Ext: String): Boolean;
var
  Value: String;
begin
  Result := True;
  if RegQueryStringValue(HKCR, Ext, '', Value) then
    Result := (Value = '') or (Pos('VLClone.', Value) = 1);
end;

// RemoveOurDefaults (generated, included at the end) calls this for every extension.
// On uninstall, an extension's default is removed only if it still points at VLClone.
procedure RemoveDefaultIfOurs(Ext, ProgId: String);
var
  Value: String;
begin
  if RegQueryStringValue(AssocRoot(), 'Software\Classes\' + Ext, '', Value) and (Value = ProgId) then
    RegDeleteValue(AssocRoot(), 'Software\Classes\' + Ext, '');
end;

[Run]
Filename: "{app}\VLClone.exe"; Description: "{cm:LaunchProgram,VLClone}"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps"; Description: "Open Windows Default apps to choose VLClone as the default player"; Flags: postinstall shellexec skipifsilent unchecked; Tasks: assoc

; File types and Explorer verbs, generated from vlclone/core/associations.py by
; packaging/make_associations.py. Pascal needs declarations before use, so the order is:
; helpers ([Code] above) -> generated RemoveOurDefaults (include) -> the uninstall hook (below).
#include "..\build\associations.iss"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveOurDefaults();
end;
