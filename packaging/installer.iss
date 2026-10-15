; Inno Setup script: builds VLClone-<version>-Setup.exe from dist\VLClone.
; iscc /DAppVersion=0.1.0 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C3F1E0B-5D3A-4C2B-9B7E-7A1C0B2E9F31}
AppName=VLClone
AppVersion={#AppVersion}
AppPublisher=VLClone
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

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\VLClone\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VLClone"; Filename: "{app}\VLClone.exe"
Name: "{autodesktop}\VLClone"; Filename: "{app}\VLClone.exe"; Tasks: desktopicon

[Registry]
; Shows VLClone in Explorer's "Open with" list for media files.
Root: HKA; Subkey: "Software\Classes\Applications\VLClone.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "VLClone"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\VLClone.exe\shell\open\command"; ValueType: string; ValueData: """{app}\VLClone.exe"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\VLClone.exe"; Description: "{cm:LaunchProgram,VLClone}"; Flags: nowait postinstall skipifsilent
