; Cartridge Engineer — установщик клиентского приложения (заявки заказчиков)
Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define APP "Cartridge Engineer — заявки (клиент)"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CartridgeEngineerClientApp"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define INIDIR "$APPDATA\CRM-Kartridzh"

Name "${APP}"
OutFile "CRM-Client-Setup.exe"
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

Var UrlH
Var Url
Var AutoH
Var DeskH

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "Подключение к серверу" "Адрес, автозапуск и ярлыки"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0u 0u 100% 10u "Адрес сервера Cartridge Engineer (скажет диспетчер):"
  Pop $1
  ${NSD_CreateText} 0u 12u 120u 12u "http://192.168.1.35:8010"
  Pop $UrlH
  ${NSD_CreateLabel} 0u 28u 100% 10u "Через программу клиенты отправляют заявки на сервер. Позже адрес можно"
  Pop $1
  ${NSD_CreateLabel} 0u 38u 100% 10u "изменить: запуск с нажатым Shift."
  Pop $1
  ${NSD_CreateCheckbox} 0u 54u 100% 10u "Запускать вместе с Windows (автостарт)"
  Pop $AutoH
  ${NSD_CreateCheckbox} 0u 68u 100% 10u "Ярлык «Заявки (Cartridge Engineer)» на рабочем столе"
  Pop $DeskH
  ${NSD_SetState} $DeskH ${BST_CHECKED}
  nsDialogs::Show
FunctionEnd

Function OptionsPageLeave
  ${NSD_GetText} $UrlH $Url
  ${If} $Url == ""
    MessageBox MB_ICONEXCLAMATION "Укажите адрес сервера (например, http://192.168.1.35:8010)."
    Abort
  ${EndIf}
  ${NSD_GetState} $AutoH $AutoH
  ${NSD_GetState} $DeskH $DeskH
FunctionEnd

Section "Клиент"
  SetOutPath "$INSTDIR"
  File "/oname=CRM-Client.exe" "CRM-Client.exe"
  WriteUninstaller "$INSTDIR\Uninstall-ClientApp.exe"

  ; адрес сервера — в настройку лаунчера
  CreateDirectory "${INIDIR}"
  WriteINIStr "${INIDIR}\client-app.ini" "server" "url" "$Url"

  ${If} $AutoH == ${BST_CHECKED}
    WriteRegStr HKCU "${RUNKEY}" "CartridgeEngineerClientApp" '"$INSTDIR\CRM-Client.exe"'
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\Cartridge Engineer"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Заявки (клиент).lnk" "$INSTDIR\CRM-Client.exe"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Удаление клиента (заявки).lnk" "$INSTDIR\Uninstall-ClientApp.exe"
  ${If} $DeskH == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\Заявки (Cartridge Engineer).lnk" "$INSTDIR\CRM-Client.exe"
  ${EndIf}

  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall-ClientApp.exe"'
  WriteRegStr HKCU "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\CRM-Client.exe"
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  DeleteRegValue HKCU "${RUNKEY}" "CartridgeEngineerClientApp"
  Delete "$DESKTOP\Заявки (Cartridge Engineer).lnk"
  RMDir /r "$SMPROGRAMS\Cartridge Engineer"
  Delete "$INSTDIR\CRM-Client.exe"
  Delete "$INSTDIR\Uninstall-ClientApp.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"
  MessageBox MB_ICONINFORMATION "Логин и настройки подключения сохранены в профиле пользователя и не удалены."
SectionEnd
