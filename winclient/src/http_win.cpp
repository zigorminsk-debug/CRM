// http_win.cpp — HTTP-клиент для Windows на WinHTTP (входит в состав Windows).
#ifdef _WIN32

#include "http.h"
#include <windows.h>
#include <winhttp.h>
#include <sstream>

#pragma comment(lib, "winhttp.lib")

namespace crm {

UrlParts parseUrl(const std::string& url) {
    UrlParts p;
    std::string rest = url;
    size_t pos = rest.find("://");
    if (pos != std::string::npos) {
        p.scheme = rest.substr(0, pos);
        rest = rest.substr(pos + 3);
    } else {
        p.scheme = "http";
    }
    p.https = (p.scheme == "https");
    size_t slash = rest.find('/');
    std::string hostport = (slash == std::string::npos) ? rest : rest.substr(0, slash);
    p.path = (slash == std::string::npos) ? "/" : rest.substr(slash);
    size_t colon = hostport.find(':');
    if (colon == std::string::npos) {
        p.host = hostport;
        p.port = p.https ? 443 : 80;
    } else {
        p.host = hostport.substr(0, colon);
        p.port = atoi(hostport.substr(colon + 1).c_str());
    }
    return p;
}

static std::wstring w(const std::string& s) {
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

HttpResponse httpRequest(const std::string& url, const std::string& method, const std::string& body,
                         const std::map<std::string, std::string>& headers, int timeoutSec) {
    HttpResponse res;
    UrlParts p = parseUrl(url);

    HINTERNET session = WinHttpOpen(L"CRM-Windows-Client/1.0",
                                    WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) { res.error = "WinHttpOpen: не удалось инициализировать сеть"; return res; }

    int tmo = timeoutSec * 1000;
    WinHttpSetTimeouts(session, tmo, tmo, tmo, tmo);

    HINTERNET conn = WinHttpConnect(session, w(p.host).c_str(), (INTERNET_PORT)p.port, 0);
    if (!conn) {
        res.error = "Не удалось подключиться к серверу " + p.host + ":" + std::to_string(p.port);
        WinHttpCloseHandle(session);
        return res;
    }

    DWORD flags = p.https ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET req = WinHttpOpenRequest(conn, w(method).c_str(), w(p.path).c_str(), nullptr,
                                       WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!req) {
        res.error = "Не удалось создать HTTP-запрос";
        WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return res;
    }

    // Не проверять прокси-аутентификацию вручную, поддержать нестандартные порты
    WinHttpSetOption(req, WINHTTP_OPTION_DISABLE_FEATURE, nullptr, 0);

    std::wstring hdrs = L"Content-Type: application/json; charset=utf-8\r\nAccept: application/json\r\n";
    for (std::map<std::string, std::string>::const_iterator it = headers.begin(); it != headers.end(); ++it)
        hdrs += w(it->first) + L": " + w(it->second) + L"\r\n";

    BOOL sent = WinHttpSendRequest(req, hdrs.c_str(), (DWORD)-1L,
                                   body.empty() ? WINHTTP_NO_REQUEST_DATA : (LPVOID)body.data(),
                                   (DWORD)body.size(), (DWORD)body.size(), 0);
    if (!sent) {
        DWORD err = GetLastError();
        std::string msg = "Ошибка отправки запроса (код " + std::to_string((int)err) + "). Проверьте адрес сервера и сеть.";
        if (err == 12029 || err == 12007) msg = "Сервер недоступен: " + p.host + ":" + std::to_string(p.port);
        res.error = msg;
        WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return res;
    }

    if (!WinHttpReceiveResponse(req, nullptr)) {
        res.error = "Сервер не ответил (таймаут " + std::to_string(timeoutSec) + " с)";
        WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return res;
    }

    DWORD status = 0, len = sizeof(status);
    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &len, WINHTTP_NO_HEADER_INDEX);
    res.status = (int)status;

    std::string data;
    for (;;) {
        DWORD avail = 0;
        if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0) break;
        std::string chunk(avail, '\0');
        DWORD read = 0;
        if (!WinHttpReadData(req, &chunk[0], avail, &read) || read == 0) break;
        chunk.resize(read);
        data += chunk;
    }
    res.body = data;
    res.ok = (res.status >= 200 && res.status < 300);

    WinHttpCloseHandle(req);
    WinHttpCloseHandle(conn);
    WinHttpCloseHandle(session);
    return res;
}

long long httpDownloadToFile(const std::string& url, const std::string& path,
                            std::string& error, int timeoutSec) {
    UrlParts p = parseUrl(url);
    HINTERNET session = WinHttpOpen(L"CRM-Windows-Client/1.0",
                                    WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) { error = "WinHttpOpen: не удалось инициализировать сеть"; return -1; }

    int tmo = timeoutSec * 1000;
    WinHttpSetTimeouts(session, tmo, tmo, tmo, tmo);

    HINTERNET conn = WinHttpConnect(session, w(p.host).c_str(), (INTERNET_PORT)p.port, 0);
    if (!conn) {
        error = "Не удалось подключиться к " + p.host + ":" + std::to_string(p.port);
        WinHttpCloseHandle(session);
        return -1;
    }
    DWORD flags = p.https ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET req = WinHttpOpenRequest(conn, L"GET", w(p.path).c_str(), nullptr,
                                       WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!req) {
        error = "Не удалось создать HTTP-запрос";
        WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return -1;
    }
    if (!WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(req, nullptr)) {
        error = "Сервер не отдал файл (таймаут " + std::to_string(timeoutSec) + " с)";
        WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return -1;
    }
    DWORD status = 0, len = sizeof(status);
    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &len, WINHTTP_NO_HEADER_INDEX);
    if (status < 200 || status >= 300) {
        error = "Сервер вернул код " + std::to_string((int)status) + " вместо файла";
        WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return -1;
    }

    FILE* f = fopen(path.c_str(), "wb");
    if (!f) {
        error = "Не удалось создать файл " + path;
        WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return -1;
    }
    long long total = 0;
    for (;;) {
        DWORD avail = 0;
        if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0) break;
        std::string chunk(avail, '\0');
        DWORD read = 0;
        if (!WinHttpReadData(req, &chunk[0], avail, &read) || read == 0) break;
        if (fwrite(chunk.data(), 1, (size_t)read, f) != (size_t)read) {
            error = "Ошибка записи файла " + path;
            fclose(f);
            WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
            return -1;
        }
        total += read;
    }
    fclose(f);
    WinHttpCloseHandle(req); WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
    if (total == 0) { error = "Получен пустой файл — обновление отменено"; return -1; }
    return total;
}

} // namespace crm

#endif // _WIN32
