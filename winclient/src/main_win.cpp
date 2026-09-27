// main_win.cpp — Windows-клиент приёма заявок на заправку картриджей и ремонт оргтехники.
//
// Что делает приложение:
//   1) форма заявки с обязательными полями (УНП, р/с, телефон, контактное лицо,
//      выпадающий список работ, адрес, пояснение);
//   2) телефон автоматически приводится к международному виду +375 XX XXX-XX-XX;
//   3) адрес переводится в координаты и район (Минск и Минский район) —
//      сервер определяет зону и инженера, отвечающего за неё;
//   4) заявка отправляется на сервер по адресу http://<публичный IP>:<порт>;
//   5) поиск по истории (контрагент, УНП, р/с) и повторный заказ в один клик;
//   6) автообновление: при запуске клиент спрашивает у сервера последний релиз,
//      скачивает CRM-Windows.exe с GitHub, проверяет SHA-256 и ставит поверх
//      установленного файла (подпись — постоянным ключом проекта).
//
// Сборка (Linux, кросс-компилятор): см. build_windows.sh
// Сборка (Windows, MinGW): x86_64-w64-mingw32-g++ -std=c++17 -O2 -DUNICODE -D_UNICODE
//                          -Wl,--subsystem,windows src/*.cpp -o CRM-Windows.exe
//                          -lwinhttp -lcomctl32 -lgdi32 -luser32 -lshell32 -lole32 -luuid

#ifdef _WIN32

#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <string>
#include <vector>
#include <cstdio>

#include "api.h"
#include "hash.h"
#include "http.h"
#include "validate.h"

// Версию подставляет build_windows.sh из tools/version.py (MAJOR.MINOR + номер сборки).
#ifndef CRM_CLIENT_VERSION
#define CRM_CLIENT_VERSION "1.0.0"
#endif
#ifndef CRM_CLIENT_BUILD
#define CRM_CLIENT_BUILD 0
#endif

using namespace crm;

// --------------------------------------------------------------- идентификаторы
enum {
    IDC_EDIT_CONTRACTOR = 1001, IDC_EDIT_UNP, IDC_EDIT_ACCOUNT, IDC_EDIT_BANK,
    IDC_EDIT_CONTACT, IDC_EDIT_PHONE, IDC_COMBO_WORK, IDC_COMBO_PRIORITY,
    IDC_EDIT_ADDRESS, IDC_BTN_GEOCODE, IDC_EDIT_EQUIPMENT, IDC_EDIT_SERIAL,
    IDC_EDIT_COMMENT, IDC_BTN_SUBMIT, IDC_BTN_CLEAR, IDC_BTN_SETTINGS,
    IDC_BTN_CHECK, IDC_BTN_OPENMAP, IDC_BTN_BROWSER,
    IDC_STATIC_GEO, IDC_STATIC_STATUS, IDC_STATIC_SERVER,
    IDC_SEARCH_EDIT, IDC_SEARCH_KIND, IDC_BTN_SEARCH, IDC_LIST_HISTORY, IDC_BTN_FILL,
    IDC_STATIC_FORM_ERR, IDC_BTN_UPDATE, IDC_STATIC_VERSION,
    IDC_DLG_URL = 1500, IDC_DLG_URL_OK, IDC_DLG_URL_CANCEL, IDC_DLG_URL_TEST, IDC_DLG_URL_AUTO
};

// Масштаб интерфейса под DPI монитора: все координаты в коде заданы для 96 DPI
// (масштаб 100% в Windows). При 125–150% без масштабирования подписи наползают
// на поля, а кнопки вылезают за край окна.
static double g_dpiScale = 1.0;
static int sx(int v) { return (int)(v * g_dpiScale + 0.5); }

static void initDpiScale(HWND ref) {
    typedef UINT (WINAPI *GetDpiFn)(HWND);
    typedef UINT (WINAPI *GetDpiSysFn)(void);
    HMODULE u32 = GetModuleHandleW(L"user32.dll");
    if (ref) {
        GetDpiFn byWindow = (GetDpiFn)(void*)GetProcAddress(u32, "GetDpiForWindow");
        if (byWindow) {
            UINT d = byWindow(ref);
            if (d) { g_dpiScale = d / 96.0; return; }
        }
    }
    GetDpiSysFn bySystem = (GetDpiSysFn)(void*)GetProcAddress(u32, "GetDpiForSystem");
    if (bySystem) {
        UINT d = bySystem();
        if (d) g_dpiScale = d / 96.0;
    }
}

#define WM_APP_STATUS (WM_APP + 1)
#define WM_APP_INIT   (WM_APP + 2)
#define TIMER_CONNECT 1

struct HistoryItem {
    int id = 0;
    std::string number, created, contractor, unp, account, contact, phone, address, status, work;
};

static struct AppState {
    HWND hwnd = nullptr;
    ApiClient api;
    std::string serverUrl = "http://127.0.0.1:8000";
    std::vector<Work> works;
    std::vector<std::pair<std::string, std::string> > priorities;
    std::vector<HistoryItem> history;
    GeoResult geo;
    std::string geoAddress;
    std::wstring iniPath;
    HFONT font = nullptr;
    bool serverOk = false;
    std::wstring clientName;   // имя оператора (для журнала на сервере)
    bool updateOnStart = true; // проверять обновления при запуске (настройка)
    UpdateInfo update;         // что рассказал сервер о последнем релизе
} app;

// ------------------------------------------------------- преобразования строк
static std::wstring wide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring out((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &out[0], n);
    return out;
}

static std::string u8(const std::wstring& s) {
    if (s.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0, nullptr, nullptr);
    std::string out((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.c_str(), (int)s.size(), &out[0], n, nullptr, nullptr);
    return out;
}

static std::string getText(HWND h) {
    int len = GetWindowTextLengthW(h);
    std::wstring buf((size_t)len + 1, L'\0');
    GetWindowTextW(h, &buf[0], len + 1);
    buf.resize((size_t)len);
    return u8(buf);
}

static void setText(HWND h, const std::string& text) {
    SetWindowTextW(h, wide(text).c_str());
}

// ------------------------------------------------------------------- настройки
static void settingsLoad() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"APPDATA", buf, MAX_PATH);
    std::wstring dir = n ? std::wstring(buf) : L".";
    CreateDirectoryW(dir.c_str(), nullptr);
    std::wstring sub = dir + L"\\CRM-Kartridzh";
    CreateDirectoryW(sub.c_str(), nullptr);
    app.iniPath = sub + L"\\client.ini";

    wchar_t url[512] = L"";
    GetPrivateProfileStringW(L"server", L"url", L"http://127.0.0.1:8000", url, 512, app.iniPath.c_str());
    app.serverUrl = u8(url);
    app.api.base = app.serverUrl;

    wchar_t name[128] = L"";
    GetPrivateProfileStringW(L"client", L"operator", L"Оператор", name, 128, app.iniPath.c_str());
    app.clientName = name;

    app.updateOnStart = GetPrivateProfileIntW(L"update", L"check_on_start", 1, app.iniPath.c_str()) != 0;
}

static void settingsSave() {
    WritePrivateProfileStringW(L"server", L"url", wide(app.serverUrl).c_str(), app.iniPath.c_str());
    WritePrivateProfileStringW(L"update", L"check_on_start", app.updateOnStart ? L"1" : L"0",
                               app.iniPath.c_str());
}

static void setStatus(const std::wstring& text, bool error = false) {
    HWND h = GetDlgItem(app.hwnd, IDC_STATIC_STATUS);
    if (!h) return;
    SetWindowTextW(h, text.c_str());
    InvalidateRect(h, nullptr, TRUE);
}

static void msgBox(const std::wstring& text, const std::wstring& title, UINT flags = MB_OK | MB_ICONINFORMATION) {
    MessageBoxW(app.hwnd, text.c_str(), title.c_str(), flags);
}

static bool askYes(const std::wstring& text, const std::wstring& title) {
    return MessageBoxW(app.hwnd, text.c_str(), title.c_str(), MB_YESNO | MB_ICONQUESTION) == IDYES;
}

// ------------------------------------------------------------------ заполнение
static void fillWorksCombo() {
    HWND c = GetDlgItem(app.hwnd, IDC_COMBO_WORK);
    SendMessageW(c, CB_RESETCONTENT, 0, 0);
    size_t curCat = std::string::npos;
    for (size_t i = 0; i < app.works.size(); ++i) {
        std::string label = app.works[i].name;
        if (app.works[i].minutes) {
            char buf[32];
            snprintf(buf, sizeof(buf), "  (~%d мин)", app.works[i].minutes);
            label += buf;
        }
        int idx = (int)SendMessageW(c, CB_ADDSTRING, 0, (LPARAM)wide(label).c_str());
        SendMessageW(c, CB_SETITEMDATA, idx, (LPARAM)i);
    }
    if (!app.works.empty()) SendMessageW(c, CB_SETCURSEL, 0, 0);
}

static void fillPriorityCombo() {
    HWND c = GetDlgItem(app.hwnd, IDC_COMBO_PRIORITY);
    SendMessageW(c, CB_RESETCONTENT, 0, 0);
    static const wchar_t* def[][2] = {
        {L"emergency", L"Аварийная (вне очереди)"},
        {L"urgent", L"Срочная (в течение дня)"},
        {L"normal", L"Обычная"},
        {L"planned", L"Плановая (по графику)"}
    };
    for (int i = 0; i < 4; ++i) {
        int idx = (int)SendMessageW(c, CB_ADDSTRING, 0, (LPARAM)def[i][1]);
        SendMessageW(c, CB_SETITEMDATA, idx, (LPARAM)i);
    }
    SendMessageW(c, CB_SETCURSEL, 2, 0);   // обычная
    app.priorities.clear();
    for (int i = 0; i < 4; ++i) app.priorities.push_back(std::make_pair(u8(def[i][0]), u8(def[i][1])));
}

static std::string currentPriority() {
    HWND c = GetDlgItem(app.hwnd, IDC_COMBO_PRIORITY);
    int sel = (int)SendMessageW(c, CB_GETCURSEL, 0, 0);
    if (sel < 0 || sel >= (int)app.priorities.size()) return "normal";
    return app.priorities[(size_t)sel].first;
}

static Work currentWork() {
    HWND c = GetDlgItem(app.hwnd, IDC_COMBO_WORK);
    int sel = (int)SendMessageW(c, CB_GETCURSEL, 0, 0);
    Work w;
    if (sel < 0) return w;
    int dataIdx = (int)SendMessageW(c, CB_GETITEMDATA, sel, 0);
    if (dataIdx >= 0 && dataIdx < (int)app.works.size()) w = app.works[(size_t)dataIdx];
    return w;
}

static RequestForm readForm() {
    RequestForm f;
    f.contractor = getText(GetDlgItem(app.hwnd, IDC_EDIT_CONTRACTOR));
    f.unp = getText(GetDlgItem(app.hwnd, IDC_EDIT_UNP));
    f.bankAccount = getText(GetDlgItem(app.hwnd, IDC_EDIT_ACCOUNT));
    f.bankName = getText(GetDlgItem(app.hwnd, IDC_EDIT_BANK));
    f.contactPerson = getText(GetDlgItem(app.hwnd, IDC_EDIT_CONTACT));
    f.phone = getText(GetDlgItem(app.hwnd, IDC_EDIT_PHONE));
    f.address = getText(GetDlgItem(app.hwnd, IDC_EDIT_ADDRESS));
    f.comment = getText(GetDlgItem(app.hwnd, IDC_EDIT_COMMENT));
    f.equipment = getText(GetDlgItem(app.hwnd, IDC_EDIT_EQUIPMENT));
    f.serial = getText(GetDlgItem(app.hwnd, IDC_EDIT_SERIAL));
    f.priority = currentPriority();
    Work w = currentWork();
    f.workId = w.id;
    f.workCode = w.code;
    if (app.geo.ok && !app.geoAddress.empty() && app.geoAddress == f.address) {
        f.hasCoords = true;
        f.lat = app.geo.lat;
        f.lon = app.geo.lon;
        f.zoneId = app.geo.zoneId;
    }
    return f;
}

static void clearForm(bool keepContractor = false) {
    std::string contractor = keepContractor ? getText(GetDlgItem(app.hwnd, IDC_EDIT_CONTRACTOR)) : "";
    setText(GetDlgItem(app.hwnd, IDC_EDIT_CONTRACTOR), contractor);
    setText(GetDlgItem(app.hwnd, IDC_EDIT_UNP), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_ACCOUNT), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_BANK), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_CONTACT), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_PHONE), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_ADDRESS), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_EQUIPMENT), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_SERIAL), "");
    setText(GetDlgItem(app.hwnd, IDC_EDIT_COMMENT), "");
    app.geo = GeoResult();
    app.geoAddress.clear();
    setText(GetDlgItem(app.hwnd, IDC_STATIC_GEO), "Адрес → координаты: не определены");
}

// --------------------------------------------------------------- проверка связи
static void checkServer() {
    std::string info;
    bool ok = app.api.health(info);
    app.serverOk = ok;
    if (ok) {
        setStatus(L"● " + wide(info), false);
    } else {
        setStatus(L"● Нет связи с сервером " + wide(app.serverUrl) + L" — " + wide(app.api.lastError), true);
    }
    SetWindowTextW(GetDlgItem(app.hwnd, IDC_STATIC_SERVER),
                   (L"Сервер: " + wide(app.serverUrl)).c_str());
}

static void loadDictionaries() {
    if (!app.api.listWorks(app.works)) {
        setStatus(L"Не удалось загрузить справочник работ: " + wide(app.api.lastError), true);
        return;
    }
    fillWorksCombo();
}

// ------------------------------------------------------------ автообновление
// Как работает: сервер знает о последнем релизе на GitHub (/api/updates),
// клиент сравнивает номера сборок, скачивает exe из релиза, проверяет SHA-256
// и подменяет файл на диске после своего закрытия — установка «поверх» без мастера.
static std::wstring tempDir() {
    wchar_t buf[MAX_PATH + 1] = L"";
    DWORD n = GetTempPathW(MAX_PATH, buf);
    if (!n) return L".\\";
    return std::wstring(buf);
}

static void setVersionLabel(const std::wstring& extra) {
    HWND h = GetDlgItem(app.hwnd, IDC_STATIC_VERSION);
    if (!h) return;
    std::wstring text = L"Версия " + wide(CRM_CLIENT_VERSION);
    if (!extra.empty()) text += L"  ·  " + extra;
    SetWindowTextW(h, text.c_str());
}

static bool installUpdate() {
    if (app.update.url.empty()) {
        msgBox(L"В релизе нет файла для Windows. Скачайте сборку со страницы релизов.", L"Обновление",
               MB_OK | MB_ICONWARNING);
        return false;
    }
    std::wstring tmp = tempDir();
    std::wstring newExe = tmp + L"CRM-Windows-" + wide(app.update.latestVersion) + L".new.exe";

    setStatus(L"● Скачиваем версию " + wide(app.update.latestVersion) + L" с GitHub…");
    SetCursor(LoadCursorW(nullptr, IDC_WAIT));
    std::string err;
    bool ok = app.api.downloadUpdate(app.update, u8(newExe), err);
    SetCursor(LoadCursorW(nullptr, IDC_ARROW));
    if (!ok) {
        setStatus(L"● Обновление не установлено: " + wide(err), true);
        msgBox(L"Не удалось скачать обновление.\n\n" + wide(err) +
               L"\n\nПродолжаем работу на текущей версии " + wide(CRM_CLIENT_VERSION) + L".", L"Обновление",
               MB_OK | MB_ICONERROR);
        return false;
    }

    wchar_t cur[MAX_PATH + 1] = L"";
    GetModuleFileNameW(nullptr, cur, MAX_PATH);

    // Скрипт-установщик: дожидается нашего закрытия, копирует новый exe поверх старого
    // и запускает клиент заново. Пути передаются аргументами, поэтому кириллица в них безопасна.
    std::wstring batPath = tmp + L"crm-update.cmd";
    const char* bat =
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        ":wait\r\n"
        "tasklist /FI \"IMAGENAME eq CRM-Windows.exe\" 2>nul | find /I \"CRM-Windows.exe\" >nul\r\n"
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        "copy /y \"%~1\" \"%~2\" >nul\r\n"
        "start \"\" \"%~2\"\r\n"
        "del \"%~1\" >nul 2>nul\r\n"
        "(goto) 2>nul & del \"%~f0\"\r\n";
    FILE* f = _wfopen(batPath.c_str(), L"wb");
    if (!f) {
        msgBox(L"Не удалось подготовить установку обновления (нет доступа к " + tmp + L").", L"Обновление",
               MB_OK | MB_ICONERROR);
        return false;
    }
    fwrite(bat, 1, strlen(bat), f);
    fclose(f);

    std::wstring args = L"\"" + newExe + L"\" \"" + std::wstring(cur) + L"\"";
    HINSTANCE rc = ShellExecuteW(nullptr, L"open", batPath.c_str(), args.c_str(), nullptr, SW_HIDE);
    if ((INT_PTR)rc <= 32) {
        msgBox(L"Не удалось запустить установку обновления. Скачайте сборку вручную со страницы релизов.",
               L"Обновление", MB_OK | MB_ICONERROR);
        return false;
    }
    setStatus(L"● Устанавливаем версию " + wide(app.update.latestVersion) + L" — клиент перезапустится…");
    DestroyWindow(app.hwnd);   // выходим: скрипт подменит файл и запустит клиент заново
    return true;
}

static void checkUpdates(bool manual) {
    UpdateInfo u;
    if (!app.api.checkUpdates(u)) {
        app.update = u;
        setVersionLabel(L"обновления недоступны");
        if (app.serverOk) setStatus(L"● Версия " + wide(CRM_CLIENT_VERSION) + L" (последний релиз на GitHub не получен)");
        if (manual) {
            msgBox(L"Не удалось проверить обновления.\n\n" + wide(u.error) +
                   L"\n\nСборки публикуются автоматически на GitHub; проверьте доступ в интернет.", L"Обновление",
                   MB_OK | MB_ICONINFORMATION);
        }
        return;
    }
    app.update = u;
    if (!u.available) {
        setVersionLabel(L"последняя");
        setStatus(L"● Версия " + wide(CRM_CLIENT_VERSION) + L" — установлена последняя версия");
        if (manual) {
            msgBox(L"Установлена последняя версия: " + wide(CRM_CLIENT_VERSION) +
                   L"\n\nНа GitHub опубликована " + wide(u.latestVersion.empty() ? CRM_CLIENT_VERSION : u.latestVersion) + L".",
                   L"Обновление", MB_OK | MB_ICONINFORMATION);
        }
        return;
    }

    wchar_t sizebuf[64] = L"";
    if (u.size > 0) _snwprintf(sizebuf, 64, L"%.0f КБ", (double)u.size / 1024.0);
    setVersionLabel(L"доступна " + wide(u.latestVersion));
    setStatus(L"● Доступна версия " + wide(u.latestVersion) + L" — нажмите «Обновление клиента»");
    if (!manual && !app.updateOnStart) return;

    std::wstring text = L"Опубликована новая версия " + wide(u.latestVersion) +
                        L" (у вас " + wide(CRM_CLIENT_VERSION) + L").\n\n";
    if (!u.notes.empty()) text += L"Что нового:\n" + wide(u.notes) + L"\n\n";
    if (sizebuf[0]) text += std::wstring(L"Размер файла: ") + sizebuf + L"\n";
    text += L"Скачать и установить поверх установленной версии?";
    if (askYes(text, L"Доступно обновление")) installUpdate();
}

// ------------------------------------------------------------ адрес → координаты
static void onGeocode(bool silent = false) {
    std::string addr = getText(GetDlgItem(app.hwnd, IDC_EDIT_ADDRESS));
    if (addr.size() < 4) {
        setStatus(L"Укажите адрес (улица, дом, населённый пункт) для определения координат", true);
        return;
    }
    setStatus(L"Определяем координаты адреса…");
    HWND h = GetDlgItem(app.hwnd, IDC_STATIC_GEO);
    SetWindowTextW(h, L"Адрес → координаты: поиск…");
    GeoResult g;
    bool ok = app.api.geocode(addr, g);
    if (!ok || !g.ok) {
        std::wstring err = g.message.empty() ? wide(app.api.lastError) : wide(g.message);
        SetWindowTextW(h, (L"Адрес → координаты: не определён — " + err).c_str());
        setStatus(L"Адрес не определён: " + err, true);
        app.geo = GeoResult();
        app.geoAddress.clear();
        if (!silent) msgBox(L"Не удалось определить адрес:\n\n" + err +
                            L"\n\nУкажите район Минска или Минской области в адресе.", L"Адрес", MB_OK | MB_ICONWARNING);
        return;
    }
    app.geo = g;
    app.geoAddress = addr;
    wchar_t buf[512];
    _snwprintf(buf, 512, L"Адрес → координаты: %.5f, %.5f | район: %s | инженер: %s",
               g.lat, g.lon, wide(g.zoneName).c_str(),
               g.engineerName.empty() ? L"— не назначен —" : wide(g.engineerName).c_str());
    SetWindowTextW(h, buf);
    std::wstring status = L"Заявка будет передана: " + wide(g.zoneName.empty() ? "зона не определена" : g.zoneName);
    if (!g.engineerName.empty()) status += L" → инженер " + wide(g.engineerName);
    if (!g.message.empty()) status += L" (" + wide(g.message) + L")";
    setStatus(status);
}

// ------------------------------------------------------------------- отправка
static void onSubmit() {
    RequestForm f = readForm();

    std::vector<std::string> errs = validateForm(f);
    if (!errs.empty()) {
        std::wstring text = L"Заявка не отправлена. Проверьте обязательные поля:\n\n";
        for (size_t i = 0; i < errs.size(); ++i) text += L"• " + wide(errs[i]) + L"\n";
        setText(GetDlgItem(app.hwnd, IDC_STATIC_FORM_ERR), errs[0]);
        msgBox(text, L"Проверка формы", MB_OK | MB_ICONWARNING);
        setStatus(L"Форма заполнена не полностью", true);
        return;
    }
    setText(GetDlgItem(app.hwnd, IDC_STATIC_FORM_ERR), "");

    // если адрес менялся после геокодирования — пересчитываем координаты
    if (!(app.geo.ok && app.geoAddress == f.address)) {
        onGeocode(true);
        f = readForm();
    }

    setStatus(L"Отправляем заявку на сервер…");
    SetCursor(LoadCursorW(nullptr, IDC_WAIT));

    Json out;
    std::string error;
    bool ok = app.api.submitRequest(f, app.geo, out, error);
    SetCursor(LoadCursorW(nullptr, IDC_ARROW));

    if (!ok) {
        setStatus(L"Ошибка отправки: " + wide(error), true);
        msgBox(L"Сервер отклонил заявку:\n\n" + wide(error) +
               L"\n\nПроверьте связь и данные формы.", L"Ошибка", MB_OK | MB_ICONERROR);
        return;
    }

    std::string number = out.getStr("number");
    std::string engineer = out.getStr("engineer_name");
    std::string zone = out.getStr("zone_name");
    std::string status = out.getStr("status_label");
    std::wstring text = L"Заявка принята сервером.\n\n";
    text += L"Номер: " + wide(number) + L"\n";
    text += L"Статус: " + wide(status) + L"\n";
    if (!zone.empty()) text += L"Район (зона): " + wide(zone) + L"\n";
    text += L"Инженер: " + wide(engineer.empty() ? "будет назначен диспетчером" : engineer) + L"\n";
    std::string note = out.getStr("_assignment_message");
    if (!note.empty()) text += L"\n" + wide(note);
    std::string geoNote = out.getStr("_geo_message");
    if (!geoNote.empty()) text += L"\n\n" + wide(geoNote);
    if (app.geo.ok) text += L"\n\nПроверка в Яндекс.Навигаторе: " + wide(app.geo.navUrl);

    msgBox(text, L"Заявка принята", MB_OK | MB_ICONINFORMATION);
    setStatus(L"Заявка " + wide(number) + L" принята сервером (инженер: " +
              wide(engineer.empty() ? "не назначен" : engineer) + L")");
    clearForm(true);   // оставляем только контрагента — удобно для повторных заявок
}

// --------------------------------------------------------------- история заявок
static void fillHistoryList() {
    HWND lb = GetDlgItem(app.hwnd, IDC_LIST_HISTORY);
    SendMessageW(lb, LB_RESETCONTENT, 0, 0);
    for (size_t i = 0; i < app.history.size(); ++i) {
        const HistoryItem& h = app.history[i];
        std::string line = h.number + "  " + (h.created.size() > 10 ? h.created.substr(0, 10) : h.created)
                           + "  " + h.contractor + "  [" + h.status + "]";
        SendMessageW(lb, LB_ADDSTRING, 0, (LPARAM)wide(line).c_str());
    }
}

static void onSearch() {
    int kind = (int)SendMessageW(GetDlgItem(app.hwnd, IDC_SEARCH_KIND), CB_GETCURSEL, 0, 0);
    std::string q = getText(GetDlgItem(app.hwnd, IDC_SEARCH_EDIT));
    std::string url = "/api/requests?limit=100";
    if (!q.empty()) {
        if (kind == 1) url += "&unp=" + urlEncode(q);
        else if (kind == 2) url += "&bank_account=" + urlEncode(q);
        else if (kind == 3) url += "&q=" + urlEncode(q);
        else url += "&contractor=" + urlEncode(q);
    }
    HttpResponse r = httpRequest(app.api.base + url, "GET", "", std::map<std::string, std::string>(), 10);
    if (!r.ok) { setStatus(L"Поиск не удался: " + wide(app.api.lastError), true); return; }
    Json j = Json::parse(r.body);
    app.history.clear();
    const Json* items = j.getArr("items");
    if (items) {
        for (size_t i = 0; i < items->arr.size(); ++i) {
            const Json& x = items->arr[i];
            HistoryItem h;
            h.id = x.getInt("id");
            h.number = x.getStr("number");
            h.created = x.getStr("created_at");
            h.contractor = x.getStr("contractor");
            h.unp = x.getStr("unp");
            h.account = x.getStr("bank_account");
            h.contact = x.getStr("contact_person");
            h.phone = x.getStr("phone");
            h.address = x.getStr("address");
            h.status = x.getStr("status_label");
            h.work = x.getStr("work_name");
            app.history.push_back(h);
        }
    }
    fillHistoryList();
    char buf[128];
    snprintf(buf, sizeof(buf), "Найдено заявок: %d", j.getInt("total"));
    setText(GetDlgItem(app.hwnd, IDC_STATIC_FORM_ERR), buf);
    setStatus(wide(std::string("История: ") + buf));
}

static void prefillFromHistory() {
    HWND lb = GetDlgItem(app.hwnd, IDC_LIST_HISTORY);
    int sel = (int)SendMessageW(lb, LB_GETCURSEL, 0, 0);
    if (sel < 0 || sel >= (int)app.history.size()) {
        msgBox(L"Выберите заявку в списке истории слева.", L"История", MB_OK | MB_ICONINFORMATION);
        return;
    }
    const HistoryItem& h = app.history[(size_t)sel];
    setText(GetDlgItem(app.hwnd, IDC_EDIT_CONTRACTOR), h.contractor);
    setText(GetDlgItem(app.hwnd, IDC_EDIT_UNP), h.unp);
    setText(GetDlgItem(app.hwnd, IDC_EDIT_ACCOUNT), h.account);
    setText(GetDlgItem(app.hwnd, IDC_EDIT_CONTACT), h.contact);
    setText(GetDlgItem(app.hwnd, IDC_EDIT_PHONE), formatPhone(h.phone));
    setText(GetDlgItem(app.hwnd, IDC_EDIT_ADDRESS), h.address);
    app.geo = GeoResult();
    app.geoAddress.clear();
    onGeocode(true);
    setStatus(L"Форма заполнена данными заявки " + wide(h.number) + L" — проверьте пояснение и отправьте");
}

// ------------------------------------------------------------------ настройки
static void showSettingsDialog();

// --------------------------------------------------------------- обработка команд
static void onCommand(int id, int code) {
    switch (id) {
    case IDC_BTN_CHECK:
        checkServer(); loadDictionaries();
        break;
    case IDC_BTN_GEOCODE:
        onGeocode();
        break;
    case IDC_BTN_SUBMIT:
        onSubmit();
        break;
    case IDC_BTN_CLEAR:
        if (askYes(L"Очистить поля формы?", L"Очистка")) clearForm(false);
        break;
    case IDC_BTN_BROWSER: {
        // новый интерфейс — браузерная консоль диспетчера на том же сервере
        std::string url = app.serverUrl;
        while (!url.empty() && url.back() == '/') url.pop_back();
        url += "/dispatcher";
        ShellExecuteW(app.hwnd, L"open", wide(url).c_str(), nullptr, nullptr, SW_SHOWNORMAL);
        break;
    }
    case IDC_BTN_SETTINGS:
        showSettingsDialog();
        break;
    case IDC_BTN_SEARCH:
        onSearch();
        break;
    case IDC_BTN_FILL:
        prefillFromHistory();
        break;
    case IDC_BTN_UPDATE:
        checkUpdates(true);
        break;
    case IDC_BTN_OPENMAP:
        if (app.geo.ok) ShellExecuteW(nullptr, L"open", wide(app.geo.navUrl).c_str(), nullptr, nullptr, SW_SHOWNORMAL);
        else msgBox(L"Сначала определите координаты адреса.", L"Карта", MB_OK | MB_ICONINFORMATION);
        break;
    case IDC_EDIT_PHONE:
        if (code == EN_KILLFOCUS) {
            std::string raw = getText(GetDlgItem(app.hwnd, IDC_EDIT_PHONE));
            if (!raw.empty()) {
                std::string norm = normalizePhone(raw);
                std::string err = phoneError(raw);
                setText(GetDlgItem(app.hwnd, IDC_EDIT_PHONE), formatPhone(norm));
                if (!err.empty()) { setStatus(wide(err), true); }
                else { setText(GetDlgItem(app.hwnd, IDC_STATIC_FORM_ERR), ""); }
            }
        }
        break;
    case IDC_EDIT_UNP:
        if (code == EN_KILLFOCUS) {
            std::string raw = getText(GetDlgItem(app.hwnd, IDC_EDIT_UNP));
            if (!raw.empty()) {
                std::string err = unpError(raw);
                if (raw != normalizeUnp(raw)) setText(GetDlgItem(app.hwnd, IDC_EDIT_UNP), normalizeUnp(raw));
                if (!err.empty()) setStatus(wide(err), true);
            }
        }
        break;
    case IDC_EDIT_ACCOUNT:
        if (code == EN_KILLFOCUS) {
            std::string raw = getText(GetDlgItem(app.hwnd, IDC_EDIT_ACCOUNT));
            if (!raw.empty()) {
                std::string err = accountError(raw);
                if (raw != normalizeAccount(raw)) setText(GetDlgItem(app.hwnd, IDC_EDIT_ACCOUNT), normalizeAccount(raw));
                if (!err.empty()) setStatus(wide(err), true);
            }
        }
        break;
    case IDC_EDIT_ADDRESS:
        if (code == EN_KILLFOCUS) {
            std::string addr = getText(GetDlgItem(app.hwnd, IDC_EDIT_ADDRESS));
            if (addr.size() >= 4 && (app.geoAddress != addr)) onGeocode(true);
        }
        if (code == EN_CHANGE) app.geo = GeoResult();   // адрес изменён — координаты устарели
        break;
    case IDC_LIST_HISTORY:
        if (code == LBN_DBLCLK) prefillFromHistory();
        break;
    default: break;
    }
}

// --------------------------------------------------------------- создание окна
static HWND mkControl(const wchar_t* cls, const wchar_t* text, DWORD style, int x, int y, int w, int h,
                      int id, DWORD exStyle = 0) {
    HWND hw = CreateWindowExW(exStyle, cls, text, WS_CHILD | WS_VISIBLE | style,
                              sx(x), sx(y), sx(w), sx(h),
                              app.hwnd, (HMENU)(INT_PTR)id, GetModuleHandleW(nullptr), nullptr);
    if (hw && app.font) SendMessageW(hw, WM_SETFONT, (WPARAM)app.font, TRUE);
    return hw;
}

static void createMainControls() {
    int L = 380;             // x левой колонки формы
    int C = 22;              // ширина подписи
    int y = 54;
    const int H = 24, GAP = 32;
    int W = 300;             // ширина полей

    // --- левая панель: поиск и история
    mkControl(L"STATIC", L"История заявок (поиск по контрагенту, УНП, р/с)", 0, 14, 44, 340, 18, -1);
    HWND kind = mkControl(L"COMBOBOX", nullptr, CBS_DROPDOWNLIST | WS_VSCROLL, 14, 66, 150, 200, IDC_SEARCH_KIND);
    const wchar_t* kinds[] = {L"Контрагент", L"УНП", L"Расчётный счёт", L"Текст/адрес"};
    for (int i = 0; i < 4; ++i) SendMessageW(kind, CB_ADDSTRING, 0, (LPARAM)kinds[i]);
    SendMessageW(kind, CB_SETCURSEL, 0, 0);
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER, 170, 66, 110, H, IDC_SEARCH_EDIT);
    mkControl(L"BUTTON", L"Найти", BS_PUSHBUTTON, 286, 66, 68, H, IDC_BTN_SEARCH);
    mkControl(L"LISTBOX", nullptr, LBS_NOTIFY | WS_BORDER | WS_VSCROLL | LBS_NOINTEGRALHEIGHT,
              14, 96, 340, 400, IDC_LIST_HISTORY);
    mkControl(L"BUTTON", L"↺ Заполнить форму из выбранной заявки", BS_PUSHBUTTON, 14, 502, 340, 30, IDC_BTN_FILL);

    // --- правая панель: форма заявки
    mkControl(L"STATIC", L"Контрагент *", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, W, H, IDC_EDIT_CONTRACTOR); y += GAP;

    mkControl(L"STATIC", L"УНП * (9 цифр, контрольная цифра проверяется)", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | ES_NUMBER | WS_TABSTOP, L, y, 160, H, IDC_EDIT_UNP);
    mkControl(L"STATIC", L"  Р/с * (IBAN BY… 28 знаков или 13 цифр)", 0, L + 165, y, W - 165, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, W, H, IDC_EDIT_ACCOUNT); y += GAP;

    mkControl(L"STATIC", L"Банк (необязательно)", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, W, H, IDC_EDIT_BANK); y += GAP;

    mkControl(L"STATIC", L"Контактное лицо *", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, 180, H, IDC_EDIT_CONTACT);
    mkControl(L"STATIC", L"  Телефон * (+375 …)", 0, L + 185, y, W - 185, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, 180, H, IDC_EDIT_PHONE);
    mkControl(L"STATIC", L"  приводится к +375 XX XXX-XX-XX", 0, L + 185, y, W - 185, 18, -1); y += GAP;

    mkControl(L"STATIC", L"Работа * (выпадающий список)", 0, L, y, W, 18, -1);
    mkControl(L"STATIC", L"Срочность", 0, L + W + 6, y, 200, 18, -1); y += C;
    mkControl(L"COMBOBOX", nullptr, CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, L, y, W, 320, IDC_COMBO_WORK);
    mkControl(L"COMBOBOX", nullptr, CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, L + W + 6, y, 200, 200, IDC_COMBO_PRIORITY);
    y += GAP;

    mkControl(L"STATIC", L"Адрес * (Минск и Минский район — переводится в координаты)", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, W - 150, H, IDC_EDIT_ADDRESS);
    mkControl(L"BUTTON", L"Определить координаты", BS_PUSHBUTTON, L + W - 145, y, 145, H, IDC_BTN_GEOCODE);
    y += GAP;
    mkControl(L"STATIC", L"Адрес → координаты: не определены", SS_LEFT, L, y, W, 18, IDC_STATIC_GEO);
    mkControl(L"BUTTON", L"Открыть в Яндекс.Картах", BS_PUSHBUTTON, L + W + 6, y - 24, 150, H, IDC_BTN_OPENMAP);
    y += GAP;

    mkControl(L"STATIC", L"Оборудование", 0, L, y, 150, 18, -1);
    mkControl(L"STATIC", L"  Серийный номер", 0, L + 155, y, 150, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L, y, 150, H, IDC_EDIT_EQUIPMENT);
    mkControl(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, L + 155, y, 145, H, IDC_EDIT_SERIAL);
    y += GAP;

    mkControl(L"STATIC", L"Пояснение (что сделать, какие расходники)", 0, L, y, W, 18, -1); y += C;
    mkControl(L"EDIT", L"", ES_MULTILINE | ES_AUTOVSCROLL | WS_BORDER | WS_VSCROLL | WS_TABSTOP,
              L, y, W + 160, 96, IDC_EDIT_COMMENT);
    y += 108;

    mkControl(L"BUTTON", L"Отправить заявку на сервер", BS_DEFPUSHBUTTON, L, y, 200, 34, IDC_BTN_SUBMIT);
    mkControl(L"BUTTON", L"Очистить форму", BS_PUSHBUTTON, L + 210, y, 130, 34, IDC_BTN_CLEAR);
    mkControl(L"BUTTON", L"Проверить связь", BS_PUSHBUTTON, L + 350, y, 120, 34, IDC_BTN_CHECK);
    mkControl(L"BUTTON", L"Настройки сервера", BS_PUSHBUTTON, L + 480, y, 140, 34, IDC_BTN_SETTINGS);
    mkControl(L"BUTTON", L"Открыть в браузере", BS_PUSHBUTTON, L + 630, y, 170, 34, IDC_BTN_BROWSER);
    y += 44;

    mkControl(L"BUTTON", L"Обновление клиента", BS_PUSHBUTTON, L, y, 180, 26, IDC_BTN_UPDATE);
    mkControl(L"STATIC", L"", SS_LEFT, L + 190, y + 4, W + 60, 18, IDC_STATIC_FORM_ERR);

    // статус-бар
    mkControl(L"STATIC", L"Версия " CRM_CLIENT_VERSION, SS_LEFT, 520, 640, 400, 18, IDC_STATIC_VERSION);
    mkControl(L"STATIC", L"Сервер: —", SS_LEFT, 14, 640, 500, 18, IDC_STATIC_SERVER);
    HWND st = mkControl(L"STATIC", L"● Проверка связи с сервером…", SS_LEFT, 14, 660, 1180, 18, IDC_STATIC_STATUS);
    SendMessageW(st, WM_SETFONT, (WPARAM)app.font, TRUE);
}

// ------------------------------------------------------------ окно настроек
static std::wstring dlgUrl;
static bool dlgResult = false;

static LRESULT CALLBACK SettingsProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_CREATE: {
        CreateWindowExW(0, L"STATIC", L"Адрес сервера (публичный IP или домен и порт):",
                        WS_CHILD | WS_VISIBLE, 16, 14, 380, 18, h, nullptr, nullptr, nullptr);
        HWND e = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", dlgUrl.c_str(),
                                 WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL | WS_TABSTOP,
                                 sx(16), sx(36), sx(380), sx(24), h, (HMENU)(INT_PTR)IDC_DLG_URL, nullptr, nullptr);
        CreateWindowExW(0, L"STATIC", L"Пример: http://10.20.30.40:8000   (порт по умолчанию 8000)",
                        WS_CHILD | WS_VISIBLE, 16, 64, 380, 18, h, nullptr, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Проверять обновления при запуске (брать сборки с GitHub)",
                        WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_TABSTOP,
                        sx(16), sx(84), sx(380), sx(20), h, (HMENU)(INT_PTR)IDC_DLG_URL_AUTO, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Сохранить", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
                        sx(120), sx(116), sx(110), sx(28), h, (HMENU)(INT_PTR)IDC_DLG_URL_OK, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Проверить связь", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                        sx(236), sx(116), sx(120), sx(28), h, (HMENU)(INT_PTR)IDC_DLG_URL_TEST, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Отмена", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                        sx(16), sx(116), sx(96), sx(28), h, (HMENU)(INT_PTR)IDC_DLG_URL_CANCEL, nullptr, nullptr);
        SendMessageW(GetDlgItem(h, IDC_DLG_URL_AUTO), BM_SETCHECK,
                     app.updateOnStart ? BST_CHECKED : BST_UNCHECKED, 0);
        SendMessageW(e, WM_SETFONT, (WPARAM)app.font, TRUE);
        SetFocus(e);
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_DLG_URL_OK: {
            wchar_t buf[512];
            GetWindowTextW(GetDlgItem(h, IDC_DLG_URL), buf, 512);
            dlgUrl = buf;
            app.updateOnStart = SendMessageW(GetDlgItem(h, IDC_DLG_URL_AUTO), BM_GETCHECK, 0, 0) == BST_CHECKED;
            dlgResult = true;
            DestroyWindow(h);
            return 0;
        }
        case IDC_DLG_URL_CANCEL:
            DestroyWindow(h);
            return 0;
        case IDC_DLG_URL_TEST: {
            wchar_t buf[512];
            GetWindowTextW(GetDlgItem(h, IDC_DLG_URL), buf, 512);
            ApiClient probe(u8(std::wstring(buf)));
            std::string info;
            bool ok = probe.health(info);
            MessageBoxW(h, ok ? (L"Связь есть.\n\n" + wide(info)).c_str()
                              : (L"Нет связи: " + wide(probe.lastError)).c_str(),
                        L"Проверка", MB_OK | (ok ? MB_ICONINFORMATION : MB_ICONERROR));
            return 0;
        }
        default: break;
        }
        break;
    case WM_CLOSE:
        DestroyWindow(h);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

static void showSettingsDialog() {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = SettingsProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"CRMClientSettings";
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    RegisterClassW(&wc);

    dlgUrl = wide(app.serverUrl);
    dlgResult = false;

    HWND dlg = CreateWindowExW(WS_EX_DLGMODALFRAME, L"CRMClientSettings", L"Настройки подключения к серверу",
                               WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
                               CW_USEDEFAULT, CW_USEDEFAULT, sx(430), sx(210), app.hwnd, nullptr,
                               GetModuleHandleW(nullptr), nullptr);
    if (!dlg) return;
    EnableWindow(app.hwnd, FALSE);
    MSG msg;
    while (IsWindow(dlg) && GetMessageW(&msg, nullptr, 0, 0)) {
        if (!IsDialogMessageW(dlg, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    EnableWindow(app.hwnd, TRUE);
    SetForegroundWindow(app.hwnd);
    if (dlgResult) {
        app.serverUrl = u8(dlgUrl);
        if (app.serverUrl.empty()) app.serverUrl = "http://127.0.0.1:8000";
        app.api.base = app.serverUrl;
        settingsSave();
        checkServer();
        loadDictionaries();
    }
}

// ------------------------------------------------------------- оконная процедура
static LRESULT CALLBACK WndProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_CREATE:
        app.hwnd = h;
        app.font = CreateFontW(-(int)(15 * g_dpiScale + 0.5), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                               OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                               DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        createMainControls();
        fillPriorityCombo();
        setStatus(L"● Проверка связи с сервером…");
        PostMessageW(h, WM_APP_INIT, 0, 0);   // сеть — после отрисовки окна
        return 0;

    case WM_APP_INIT:
        checkServer();
        loadDictionaries();
        onSearch();
        setVersionLabel(L"");
        if (app.updateOnStart) checkUpdates(false);
        return 0;

    case WM_COMMAND:
        onCommand(LOWORD(wp), HIWORD(wp));
        return 0;

    case WM_TIMER:
        if (wp == TIMER_CONNECT) checkServer();
        return 0;

    case WM_DRAWITEM: {
        // статус-текст красным при ошибке
        LPDRAWITEMSTRUCT d = (LPDRAWITEMSTRUCT)lp;
        if (d->CtlID == IDC_STATIC_STATUS) return 0;
        break;
    }

    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)wp;
        HWND ctl = (HWND)lp;
        if (GetDlgCtrlID(ctl) == IDC_STATIC_FORM_ERR) {
            SetTextColor(dc, RGB(180, 0, 0));
            SetBkMode(dc, TRANSPARENT);
            return (LRESULT)GetStockObject(NULL_BRUSH);
        }
        if (GetDlgCtrlID(ctl) == IDC_STATIC_GEO) {
            SetTextColor(dc, app.geo.ok ? RGB(0, 110, 0) : RGB(120, 120, 120));
            SetBkMode(dc, TRANSPARENT);
            return (LRESULT)GetStockObject(NULL_BRUSH);
        }
        break;
    }

    case WM_SIZE: {
        int w = LOWORD(lp), hgt = HIWORD(lp);
        HWND st = GetDlgItem(h, IDC_STATIC_STATUS);
        if (st) SetWindowPos(st, nullptr, 0, 0, w - sx(30), sx(18), SWP_NOMOVE | SWP_NOZORDER);
        HWND lb = GetDlgItem(h, IDC_LIST_HISTORY);
        if (lb) SetWindowPos(lb, nullptr, 0, 0, sx(340), hgt - sx(220), SWP_NOMOVE | SWP_NOZORDER);
        HWND fill = GetDlgItem(h, IDC_BTN_FILL);
        if (fill) SetWindowPos(fill, nullptr, 0, hgt - sx(120), sx(340), sx(30), SWP_NOMOVE | SWP_NOZORDER);
        return 0;
    }

    case WM_GETMINMAXINFO: {
        MINMAXINFO* m = (MINMAXINFO*)lp;
        m->ptMinTrackSize.x = sx(1200);
        m->ptMinTrackSize.y = sx(720);
        return 0;
    }

    case WM_CLOSE:
        if (askYes(L"Закрыть клиент?", L"Выход")) DestroyWindow(h);
        return 0;

    case WM_DESTROY:
        KillTimer(h, TIMER_CONNECT);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

// ------------------------------------------------------------------- точка входа
int WINAPI WinMain(HINSTANCE inst, HINSTANCE, LPSTR, int show) {
    INITCOMMONCONTROLSEX icc;
    icc.dwSize = sizeof(icc);
    icc.dwICC = ICC_STANDARD_CLASSES | ICC_BAR_CLASSES;
    InitCommonControlsEx(&icc);

    settingsLoad();
    app.api.base = app.serverUrl;

    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.lpszClassName = L"CRMClientMain";
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.lpszMenuName = nullptr;
    RegisterClassW(&wc);

    initDpiScale(nullptr);
    std::wstring title = L"CRM — приём заявок на заправку картриджей и ремонт оргтехники — версия " +
                         wide(CRM_CLIENT_VERSION);
    HWND h = CreateWindowExW(0, L"CRMClientMain", title.c_str(),
                             WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                             CW_USEDEFAULT, CW_USEDEFAULT, sx(1210), sx(740),
                             nullptr, nullptr, inst, nullptr);
    if (!h) return 1;

    SetTimer(h, TIMER_CONNECT, 60000, nullptr);
    SetFocus(GetDlgItem(h, IDC_EDIT_CONTRACTOR));

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (!IsDialogMessageW(h, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    return (int)msg.wParam;
}

#endif // _WIN32
