; Inno Setup 6 script: a Windows installer for the folder that packaging/build.py makes.
; Built by ``python packaging/build.py --installer`` (it passes AppVersion, SourceDir and OutputDir).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\iyiCAM"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{6B1B6C58-3C55-4C77-9E7B-1F5A0E1D2A47}
AppName=iyiCAM
AppVersion={#AppVersion}
AppVerName=iyiCAM {#AppVersion}
AppPublisher=iyiCAM
DefaultDirName={autopf}\iyiCAM
DefaultGroupName=iyiCAM
UninstallDisplayIcon={app}\iyiCAM.exe
SetupIconFile=iyicam.ico
OutputDir={#OutputDir}
OutputBaseFilename=iyiCAM-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "Open .icam project files with iyiCAM"; GroupDescription: "File types:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\iyiCAM"; Filename: "{app}\iyiCAM.exe"
Name: "{group}\Uninstall iyiCAM"; Filename: "{uninstallexe}"
Name: "{autodesktop}\iyiCAM"; Filename: "{app}\iyiCAM.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.icam"; ValueType: string; ValueName: ""; ValueData: "iyiCAM.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\iyiCAM.Project"; ValueType: string; ValueName: ""; ValueData: "iyiCAM project"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\iyiCAM.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\iyiCAM.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\iyiCAM.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\iyiCAM.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\iyiCAM.exe"; Description: "{cm:LaunchProgram,iyiCAM}"; Flags: nowait postinstall skipifsilent
