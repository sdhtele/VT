; https://jrsoftware.org/ishelp/index.php

#define AppName "vinetrimmer"
#define Version "0.0.7"

[Setup]
AppId={#AppName}
AppName={#AppName}
AppPublisher=PHOENiX
AppPublisherURL=https://github.com/rlaphoenix/vinetrimmer
AppReadmeFile=https://github.com/rlaphoenix/vinetrimmer/blob/master/README.md
AppSupportURL=https://github.com/rlaphoenix/vinetrimmer/discussions
AppUpdatesURL=https://github.com/rlaphoenix/vinetrimmer/releases
AppVerName={#AppName} {#Version}
AppVersion={#Version}
Compression=lzma2/max
DefaultDirName={autopf}\{#AppName}
LicenseFile=LICENSE
; Python 3.9 has dropped support for <= Windows 7/Server 2008 R2 SP1. https://jrsoftware.org/ishelp/index.php?topic=winvernotes
MinVersion=6.2
OutputBaseFilename=vinetrimmer-setup
OutputDir=dist
OutputManifestFile=vinetrimmer-setup-manifest.txt
PrivilegesRequiredOverridesAllowed=dialog commandline
SetupIconFile=assets/temp-icon.ico
SolidCompression=yes
VersionInfoVersion=0.0.1
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: dist\vinetrimmer\{#AppName}.exe; DestDir: {app}; Flags: ignoreversion
Source: dist\vinetrimmer\*; DestDir: {app}; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKLM; \
  Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
  ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(
    HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath
  )
  then begin
    Result := True;
    exit;
  end;
  { look for the path with leading and trailing semicolon }
  { Pos() returns 0 if not found }
  Result := 
    (Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0) and
    (Pos(';' + UpperCase(Param) + '\;', ';' + UpperCase(OrigPath) + ';') = 0); 
end;
