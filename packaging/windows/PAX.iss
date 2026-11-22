; PAX installer. Compile after the PyInstaller build.
#define AppName "PAX"
#define AppVersion "1.0.0"
#ifndef PackageMode
  #define PackageMode "onedir"
#endif

[Setup]
AppId={{B51F6A1B-2A58-4D29-A1E8-6E8AB0B3C4A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PAX Project
DefaultDirName={autopf}\PAX
DefaultGroupName=PAX
OutputDir=..\..\dist\installer
OutputBaseFilename=PAX-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\PAX.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
#if PackageMode == "onefile"
Source: "..\..\dist\PAX.exe"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "..\..\dist\PAX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\PAX"; Filename: "{app}\PAX.exe"
Name: "{autodesktop}\PAX"; Filename: "{app}\PAX.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PAX.exe"; Description: "Launch PAX"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Deliberately retain %LOCALAPPDATA%\PAX: credentials, Gmail token and memory survive uninstall.
