; Cartridge Engineer — установщик сервера (NSIS)
Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define APP "Cartridge Engineer — сервер заявок"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CartridgeEngineerServer"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"

Name "${APP}"
OutFile "CRM-Server-Setup.exe"
InstallDir "C:\CartridgeEngineer"
InstallDirRegKey HKLM "${REGKEY}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "icons\app.ico"
!define MUI_UNICON "icons\app.ico"
!insertmacro MUI_PAGE_DIRECTORY
Page custom SettingsPage SettingsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Var PortH
Var Port
Var AutoH
Var FireH
Var DeskH

Function SettingsPage
  !insertmacro MUI_HEADER_TEXT "Параметры сервера" "Порт, автозапуск, брандмауэр и ярлыки"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0u 0u 100% 10u "Порт сервера (позже можно изменить в окне CRM-Server.exe):"
  Pop $1
  ${NSD_CreateText} 0u 12u 60u 12u "8010"
  Pop $PortH
  ${NSD_CreateCheckbox} 0u 32u 100% 10u "Запускать сервер вместе с Windows (автостарт)"
  Pop $AutoH
  ${NSD_SetState} $AutoH ${BST_CHECKED}
  ${NSD_CreateCheckbox} 0u 46u 100% 10u "Разрешить порт в брандмауэре Windows (доступ с телефонов и других ПК)"
  Pop $FireH
  ${NSD_SetState} $FireH ${BST_CHECKED}
  ${NSD_CreateCheckbox} 0u 60u 100% 10u "Ярлыки на рабочем столе: Админка и Диспетчер (открываются в браузере)"
  Pop $DeskH
  ${NSD_SetState} $DeskH ${BST_CHECKED}
  ${NSD_CreateLabel} 0u 80u 100% 24u "База данных — в папке data рядом с программой. При переезде или обновлении сервера просто скопируйте эту папку: новый сервер сам подхватит все данные."
  Pop $1
  nsDialogs::Show
FunctionEnd

; вход: строка на стеке; выход: 1 если порт 1..65535, иначе 0
Function ValidatePort
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  StrLen $2 $0
  StrCpy $4 0
  ${If} $2 == 0
  ${OrIf} $2 > 5
    Goto done
  ${EndIf}
  StrCpy $1 0
loop:
  ${If} $1 >= $2
    Goto done
  ${EndIf}
  StrCpy $3 $0 1 $1
  ${If} $3 < "0"
  ${OrIf} $3 > "9"
    StrCpy $4 0
    Goto done
  ${EndIf}
  IntOp $4 $4 * 10
  IntOp $4 $4 + $3
  IntOp $1 $1 + 1
  Goto loop
done:
  ${If} $4 < 1
  ${OrIf} $4 > 65535
    StrCpy $4 0
  ${EndIf}
  StrCpy $0 $4
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function SettingsPageLeave
  ${NSD_GetText} $PortH $Port
  Push "$Port"
  Call ValidatePort
  Pop $0
  ${If} $0 == 0
    MessageBox MB_ICONEXCLAMATION "Порт должен быть числом от 1 до 65535."
    Abort
  ${EndIf}
  ${NSD_GetState} $AutoH $AutoH
  ${NSD_GetState} $FireH $FireH
  ${NSD_GetState} $DeskH $DeskH
FunctionEnd

Section "Сервер"
  SetOutPath "$INSTDIR"
  File "/oname=CRM-Server.exe" "CRM-Server.exe"
  File "/oname=CRM-Web.exe" "CRM-Web.exe"
  WriteUninstaller "$INSTDIR\Uninstall-Server.exe"

  ; порт в data\port.txt — сервер сам подхватит его при запуске
  CreateDirectory "$INSTDIR\data"
  FileOpen $0 "$INSTDIR\data\port.txt" w
  FileWrite $0 "$Port"
  FileClose $0

  ; автозапуск с Windows
  ${If} $AutoH == ${BST_CHECKED}
    WriteRegStr HKCU "${RUNKEY}" "CartridgeEngineerServer" '"$INSTDIR\CRM-Server.exe"'
  ${EndIf}

  ; брандмауэр (установщик запущен с правами администратора)
  ${If} $FireH == ${BST_CHECKED}
    nsExec::Exec 'netsh advfirewall firewall delete rule name="Cartridge Engineer $Port"'
    nsExec::ExecToLog 'netsh advfirewall firewall add rule name="Cartridge Engineer $Port" dir=in action=allow protocol=TCP localport=$Port'
  ${EndIf}

  ; меню «Пуск»
  CreateDirectory "$SMPROGRAMS\Cartridge Engineer"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Сервер (окно управления).lnk" "$INSTDIR\CRM-Server.exe"
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Админка (веб).lnk" "$INSTDIR\CRM-Web.exe" "admin" "$INSTDIR\CRM-Server.exe" 0
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Диспетчер (веб).lnk" "$INSTDIR\CRM-Web.exe" "dispatcher" "$INSTDIR\CRM-Server.exe" 0
  CreateShortCut "$SMPROGRAMS\Cartridge Engineer\Удаление сервера.lnk" "$INSTDIR\Uninstall-Server.exe"

  ; рабочий стол
  CreateShortCut "$DESKTOP\Cartridge Engineer — Сервер.lnk" "$INSTDIR\CRM-Server.exe"
  ${If} $DeskH == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\Админка (Cartridge Engineer).lnk" "$INSTDIR\CRM-Web.exe" "admin" "$INSTDIR\CRM-Server.exe" 0 "" "" "Админка Cartridge Engineer"
    CreateShortCut "$DESKTOP\Диспетчер (Cartridge Engineer).lnk" "$INSTDIR\CRM-Web.exe" "dispatcher" "$INSTDIR\CRM-Server.exe" 0 "" "" "Диспетчерская Cartridge Engineer"
  ${EndIf}

  ; сведения об установке (для «Установка и удаление программ»)
  WriteRegStr HKLM "${REGKEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall-Server.exe"'
  WriteRegStr HKLM "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${REGKEY}" "DisplayIcon" "$INSTDIR\CRM-Server.exe"
  WriteRegDWORD HKLM "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  DeleteRegValue HKCU "${RUNKEY}" "CartridgeEngineerServer"
  ; читаем порт и снимаем правило брандмауэра
  ClearErrors
  FileOpen $0 "$INSTDIR\data\port.txt" r
  ${If} ${Errors}
    StrCpy $1 ""
  ${Else}
    FileRead $0 $1
    FileClose $0
    nsExec::Exec 'netsh advfirewall firewall delete rule name="Cartridge Engineer $1"'
  ${EndIf}
  Delete "$DESKTOP\Cartridge Engineer — Сервер.lnk"
  Delete "$DESKTOP\Админка (Cartridge Engineer).lnk"
  Delete "$DESKTOP\Диспетчер (Cartridge Engineer).lnk"
  RMDir /r "$SMPROGRAMS\Cartridge Engineer"
  Delete "$INSTDIR\CRM-Server.exe"
  Delete "$INSTDIR\CRM-Web.exe"
  Delete "$INSTDIR\Uninstall-Server.exe"
  Delete "$INSTDIR\data\port.txt"
  RMDir "$INSTDIR\data"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${REGKEY}"
  MessageBox MB_ICONINFORMATION "База данных в папке data сохранена — так её можно перенести на новый сервер. Чтобы удалить её вместе со всеми заявками, сотрите папку вручную."
SectionEnd
