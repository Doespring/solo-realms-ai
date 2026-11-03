#define MyAppVersion "2.1.4-preview.10-mock"
[Setup]
AppId={{C7847A67-44A0-4B1D-9168-8413D9BC8147}
AppName=Solo Realms AI
AppVersion={#MyAppVersion}
AppPublisher=Solo Realms AI
DefaultDirName={localappdata}\Programs\SoloRealmsAI
DefaultGroupName=Solo Realms AI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=SoloRealmsAI-Setup-2.1.4-preview.10-mock
SetupIconFile=..\assets\solo.ico
UninstallDisplayIcon={app}\SoloRealmsAI.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
AppMutex=SoloRealmsAI.Desktop
DisableProgramGroupPage=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "..\dist\SoloRealmsAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Solo Realms AI"; Filename: "{app}\SoloRealmsAI.exe"
Name: "{autodesktop}\Solo Realms AI"; Filename: "{app}\SoloRealmsAI.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SoloRealmsAI.exe"; Description: "Открыть Solo Realms AI"; Flags: nowait postinstall skipifsilent

; Deliberately no UninstallDelete for the separate {localappdata}\SoloRealmsAI profile.
; New installers with the same AppId update application files. Data is backed up before migrations by the executable.
[Code]
function HasWebView2: Boolean;
var V: String;
begin
  Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
begin
  Result := HasWebView2;
  if not Result then
    MsgBox('Требуется Microsoft Edge WebView2 Runtime. Установите Evergreen Runtime с https://developer.microsoft.com/microsoft-edge/webview2/ и повторите установку. Python устанавливать не нужно.', mbError, MB_OK);
end;
