; Cartridge Engineer — установщик клиента диспетчера (NSIS)
Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define APP "Cartridge Engineer — диспетчер (клиент)"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CartridgeEngineerClient"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"

Name "${APP}"
OutFile "CRM-Windows-Setup.exe"
InstallDir "$LOCALAPPDATA\CartridgeEngineer"
InstallDirRegKey HKCU "${REGKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ICON "icons\app.ico"
!define MUI_UNICON "icons\app.ico"
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Var AutoH
Var DeskH

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "Параметры клиента" "Автозапуск и ярлыки"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0u 0u 100% 10u "Адрес сервера задаётся в самой программе: кнопка «Настройки сервера»"
  Pop $1
  ${NSD_CreateLabel} 0u 10u 100% 10u "или запуск приложения с нажатой клавишей Shift."
  Pop $1
  ${NSD_CreateCheckbox} 0u 28u 100% 10u "Запускать клиент вместе с Windows (автостарт)"
  Pop $AutoH
  ${NSD_CreateCheckbox} 0u 42u 100% 10u "Ярлык на рабочем столе"
  Pop $DeskH
  ${NSD_SetState} $DeskH ${BST_CHECKED}
  nsDialogs::Show
FunctionEnd

Function OptionsPageLeave
  ${NSD_GetState} $AutoH $AutoH
  ${NSD_GetState} $DeskH $DeskH
FunctionEnd

Section "Клиент"
  SetOutPath "$INSTDIR"
  File "/oname=CRM-Windows.exe" "CRM-Windows.exe"
  WriteUninstaller "$INSTDIR\Uninstall-Client.exe"

  ${If} $AutoH == ${BST_CHECKED}
    WriteRegStr HKCU "${RUNKEY}" "CartridgeEngineerClient" '"$INSTDIR\CRM-Windows.exe"'
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\Cartridge Engineer"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Диспетчер (клиент).lnk" "$INSTDIR\CRM-Windows.exe"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Удаление клиента.lnk" "$INSTDIR\Uninstall-Client.exe"
  ${If} $DeskH == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\Cartridge Engineer — Диспетчер.lnk" "$INSTDIR\CRM-Windows.exe"
  ${EndIf}

  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall-Client.exe"'
  WriteRegStr HKCU "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\CRM-Windows.exe"
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  DeleteRegValue HKCU "${RUNKEY}" "CartridgeEngineerClient"
  Delete "$DESKTOP\Cartridge Engineer — Диспетчер.lnk"
  RMDir /r "$SMPROGRAMS\Cartridge Engineer"
  Delete "$INSTDIR\CRM-Windows.exe"
  Delete "$INSTDIR\Uninstall-Client.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"
  MessageBox MB_ICONINFORMATION "Настройки подключения сохранены в профиле пользователя и не удалены."
SectionEnd
