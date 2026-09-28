// CRM-Client.exe — лаунчер кабинета клиента «Cartridge Engineer».
//
// Открывает страницу /client сервера CRM в отдельном окне браузера
// без адресной строки (режим --app у Edge/Chrome), выглядит как обычное
// приложение Windows: клиенты через него отправляют заявки на сервер.
//
//  * адрес сервера хранится в %APPDATA%\CRM-Kartridzh\client-app.ini;
//  * при первом запуске (или с нажатым Shift) открывается окно настройки
//    адреса с кнопкой «Проверить связь»;
//  * если Edge/Chrome не найдены — страница откроется в браузере по умолчанию.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <winhttp.h>
#include <string>
#include <cstdio>

#ifndef CRM_CLIENT_VERSION
#define CRM_CLIENT_VERSION "1.0"
#endif

static std::wstring iniPath;
static std::wstring g_url;
static bool g_saved = false;

// ------------------------------------------------------------ кодировки
static std::string u8(const std::wstring &w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), s.data(), n, nullptr, nullptr);
    return s;
}
static std::wstring wide(const std::string &s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), w.data(), n);
    return w;
}

// ------------------------------------------------------------ настройки
static void initPaths() {
    wchar_t appdata[MAX_PATH] = L"";
    if (SUCCEEDED(SHGetFolderPathW(nullptr, CSIDL_APPDATA, nullptr, 0, appdata))) {
        std::wstring dir = std::wstring(appdata) + L"\\CRM-Kartridzh";
        CreateDirectoryW(dir.c_str(), nullptr);
        iniPath = dir + L"\\client-app.ini";
    } else {
        iniPath = L"client-app.ini";
    }
}

static std::wstring settingsLoad() {
    wchar_t buf[512] = L"";
    GetPrivateProfileStringW(L"server", L"url", L"", buf, 512, iniPath.c_str());
    return buf;
}
static void settingsSave(const std::wstring &url) {
    WritePrivateProfileStringW(L"server", L"url", url.c_str(), iniPath.c_str());
}

// нормализуем адрес: http:// + без хвостового слэша
static std::wstring normalizeUrl(std::wstring u) {
    while (!u.empty() && (u.back() == L'/' || u.back() == L' ')) u.pop_back();
    if (u.rfind(L"http://", 0) != 0 && u.rfind(L"https://", 0) != 0) u = L"http://" + u;
    if (u.size() < 12) u = L"http://127.0.0.1:8000";
    return u;
}

// ------------------------------------------------------------ проверка связи
static std::wstring probeHealth(const std::wstring &base) {
    std::wstring full = base + L"/api/health";
    wchar_t host[256] = L"", path[512] = L"";
    URL_COMPONENTSW uc{};
    uc.dwStructSize = sizeof(uc);
    uc.lpszHostName = host; uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path; uc.dwUrlPathLength = 512;
    if (!WinHttpCrackUrl(full.c_str(), (DWORD)full.size(), 0, &uc))
        return L"Некорректный адрес";
    std::wstring result = L"Нет связи с сервером";
    HINTERNET sess = WinHttpOpen(L"CRM-Client-Setup", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, NULL, NULL, 0);
    if (sess) {
        HINTERNET conn = WinHttpConnect(sess, host, uc.nPort, 0);
        if (conn) {
            HINTERNET req = WinHttpOpenRequest(conn, L"GET", path, NULL, WINHTTP_NO_REFERER,
                                               WINHTTP_DEFAULT_ACCEPT_TYPES,
                                               uc.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0);
            if (req) {
                if (WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0)
                    && WinHttpReceiveResponse(req, nullptr)) {
                    DWORD code = 0, sz = sizeof(code);
                    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                        NULL, &code, &sz, NULL);
                    char body[2048] = {0};
                    DWORD read = 0, total = 0;
                    while (total < sizeof(body) - 1
                           && WinHttpReadData(req, body + total, sizeof(body) - 1 - total, &read) && read)
                        total += read;
                    if (code == 200) {
                        std::string b(body);
                        auto p = b.find("\"version\":\"");
                        if (p != std::string::npos) {
                            auto e = b.find('"', p + 11);
                            result = L"Связь есть. Версия сервера: " + wide(b.substr(p + 11, e - p - 11));
                        } else {
                            result = L"Связь есть.";
                        }
                    } else {
                        result = L"Сервер ответил ошибкой HTTP " + std::to_wstring(code);
                    }
                }
                WinHttpCloseHandle(req);
            }
            WinHttpCloseHandle(conn);
        }
        WinHttpCloseHandle(sess);
    }
    return result;
}

// ------------------------------------------------------------ окно настройки
#define IDC_URL 2001
#define IDC_OK 2002
#define IDC_CANCEL 2003
#define IDC_TEST 2004

static LRESULT CALLBACK SettingsProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_CREATE: {
        CreateWindowExW(0, L"STATIC",
                        L"Адрес сервера Cartridge Engineer (у диспетчера):\n"
                        L"пример: http://192.168.1.35:8010",
                        WS_CHILD | WS_VISIBLE, 14, 12, 400, 34, h, nullptr, nullptr, nullptr);
        HWND e = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g_url.c_str(),
                                 WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL | WS_TABSTOP,
                                 14, 52, 400, 24, h, (HMENU)(INT_PTR)IDC_URL, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Сохранить", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON | WS_TABSTOP,
                        14, 88, 120, 28, h, (HMENU)(INT_PTR)IDC_OK, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Проверить связь", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | WS_TABSTOP,
                        144, 88, 140, 28, h, (HMENU)(INT_PTR)IDC_TEST, nullptr, nullptr);
        CreateWindowExW(0, L"BUTTON", L"Отмена", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON | WS_TABSTOP,
                        294, 88, 120, 28, h, (HMENU)(INT_PTR)IDC_CANCEL, nullptr, nullptr);
        SendMessageW(e, WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), TRUE);
        SetFocus(e);
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_OK: {
            wchar_t buf[512] = L"";
            GetWindowTextW(GetDlgItem(h, IDC_URL), buf, 512);
            g_url = buf;
            g_saved = true;
            DestroyWindow(h);
            return 0;
        }
        case IDC_CANCEL:
            g_saved = false;
            DestroyWindow(h);
            return 0;
        case IDC_TEST: {
            wchar_t buf[512] = L"";
            GetWindowTextW(GetDlgItem(h, IDC_URL), buf, 512);
            std::wstring res = probeHealth(normalizeUrl(buf));
            MessageBoxW(h, res.c_str(), L"Проверка связи", MB_OK | MB_ICONINFORMATION);
            return 0;
        }
        default: break;
        }
        break;
    case WM_CLOSE:
        g_saved = false;
        DestroyWindow(h);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

static void showSettings(HINSTANCE inst) {
    WNDCLASSW wc{};
    wc.lpfnWndProc = SettingsProc;
    wc.hInstance = inst;
    wc.lpszClassName = L"CRMClientAppSettings";
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    RegisterClassW(&wc);
    HWND dlg = CreateWindowExW(WS_EX_DLGMODALFRAME, L"CRMClientAppSettings",
                               L"Заявки Cartridge Engineer — настройка сервера",
                               WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
                               CW_USEDEFAULT, CW_USEDEFAULT, 450, 170,
                               nullptr, nullptr, inst, nullptr);
    if (!dlg) return;
    MSG msg;
    while (IsWindow(dlg) && GetMessageW(&msg, nullptr, 0, 0)) {
        if (!IsDialogMessageW(dlg, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

// ------------------------------------------------------------ поиск браузера
static std::wstring findAppBrowser() {
    wchar_t pf[MAX_PATH] = L"", pf86[MAX_PATH] = L"", la[MAX_PATH] = L"";
    SHGetFolderPathW(nullptr, CSIDL_PROGRAM_FILES, nullptr, 0, pf);
    SHGetFolderPathW(nullptr, CSIDL_PROGRAM_FILESX86, nullptr, 0, pf86);
    SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, la);
    const wchar_t *rel[] = {
        L"\\Microsoft\\Edge\\Application\\msedge.exe",
        L"\\Google\\Chrome\\Application\\chrome.exe",
        L"\\Chromium\\Application\\chrome.exe",
    };
    const wchar_t *roots[] = { pf86, pf, la };
    for (auto *root : roots)
        for (auto *r : rel) {
            std::wstring p = std::wstring(root) + r;
            if (GetFileAttributesW(p.c_str()) != INVALID_FILE_ATTRIBUTES) return p;
        }
    return L"";
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
    initPaths();
    g_url = settingsLoad();

    bool shift = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;   // Shift при запуске — настройка
    if (g_url.empty() || shift) {
        showSettings(inst);
        if (!g_saved) return 0;            // отменили — приложение не открываем
        settingsSave(normalizeUrl(g_url));
    }
    std::wstring url = normalizeUrl(g_url);
    std::wstring page = url + L"/client";

    std::wstring browser = findAppBrowser();
    if (!browser.empty()) {
        std::wstring params = L"--app=\"" + page + L"\" --window-size=1100,860";
        ShellExecuteW(nullptr, L"open", browser.c_str(), params.c_str(), nullptr, SW_SHOWNORMAL);
    } else {
        ShellExecuteW(nullptr, L"open", page.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    }
    return 0;
}
#endif // _WIN32
