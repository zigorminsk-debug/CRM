// http_posix.cpp — HTTP-клиент для тестовой сборки (Linux/CI), POSIX-сокеты.
#ifndef _WIN32

#include "http.h"
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include <cstring>
#include <cstdio>
#include <sstream>
#include <cstdlib>

namespace crm {

UrlParts parseUrl(const std::string& url) {
    UrlParts p;
    std::string rest = url;
    size_t pos = rest.find("://");
    if (pos != std::string::npos) { p.scheme = rest.substr(0, pos); rest = rest.substr(pos + 3); }
    else p.scheme = "http";
    p.https = (p.scheme == "https");
    size_t slash = rest.find('/');
    std::string hostport = (slash == std::string::npos) ? rest : rest.substr(0, slash);
    p.path = (slash == std::string::npos) ? "/" : rest.substr(slash);
    size_t colon = hostport.find(':');
    if (colon == std::string::npos) { p.host = hostport; p.port = p.https ? 443 : 80; }
    else { p.host = hostport.substr(0, colon); p.port = atoi(hostport.substr(colon + 1).c_str()); }
    return p;
}

static bool readAll(int fd, std::string& out) {
    char buf[8192];
    for (;;) {
        ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        out.append(buf, (size_t)n);
        if (out.size() > 8u * 1024 * 1024) break;
    }
    return true;
}

HttpResponse httpRequest(const std::string& url, const std::string& method, const std::string& body,
                         const std::map<std::string, std::string>& headers, int timeoutSec) {
    HttpResponse res;
    UrlParts p = parseUrl(url);
    if (p.https) { res.error = "TLS в тестовой сборке не поддерживается (используйте http://)"; return res; }

    struct addrinfo hints, *info = nullptr;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    std::string port = std::to_string(p.port);
    if (getaddrinfo(p.host.c_str(), port.c_str(), &hints, &info) != 0 || !info) {
        res.error = "Не удалось разрешить имя " + p.host;
        return res;
    }
    int fd = ::socket(info->ai_family, info->ai_socktype, info->ai_protocol);
    if (fd < 0) { freeaddrinfo(info); res.error = "socket() failed"; return res; }
    struct timeval tv;
    tv.tv_sec = timeoutSec; tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (::connect(fd, info->ai_addr, info->ai_addrlen) != 0) {
        freeaddrinfo(info);
        ::close(fd);
        res.error = "Сервер недоступен: " + p.host + ":" + port;
        return res;
    }
    freeaddrinfo(info);

    std::ostringstream req;
    req << method << " " << p.path << " HTTP/1.1\r\n"
        << "Host: " << p.host << ":" << p.port << "\r\n"
        << "Connection: close\r\n"
        << "Content-Type: application/json; charset=utf-8\r\n"
        << "Content-Length: " << body.size() << "\r\n";
    for (std::map<std::string, std::string>::const_iterator it = headers.begin(); it != headers.end(); ++it)
        req << it->first << ": " << it->second << "\r\n";
    req << "\r\n" << body;
    std::string raw = req.str();
    if (::send(fd, raw.data(), raw.size(), 0) < 0) {
        ::close(fd);
        res.error = "Ошибка отправки";
        return res;
    }
    std::string response;
    readAll(fd, response);
    ::close(fd);

    size_t hdrEnd = response.find("\r\n\r\n");
    if (hdrEnd == std::string::npos) { res.error = "Пустой ответ сервера"; return res; }
    std::string head = response.substr(0, hdrEnd);
    std::string payload = response.substr(hdrEnd + 4);

    size_t sp = head.find(' ');
    if (sp != std::string::npos) res.status = atoi(head.substr(sp + 1, 4).c_str());
    // chunked?
    std::string lower = head;
    for (size_t i = 0; i < lower.size(); ++i) lower[i] = (char)tolower((unsigned char)lower[i]);
    if (lower.find("transfer-encoding: chunked") != std::string::npos) {
        std::string out;
        size_t i = 0;
        while (i < payload.size()) {
            size_t nl = payload.find("\r\n", i);
            if (nl == std::string::npos) break;
            long len = strtol(payload.substr(i, nl - i).c_str(), nullptr, 16);
            if (len <= 0) break;
            out += payload.substr(nl + 2, (size_t)len);
            i = nl + 2 + (size_t)len + 2;
        }
        payload = out;
    }
    res.body = payload;
    res.ok = (res.status >= 200 && res.status < 300);
    return res;
}

// Тестовая сборка: качаем тем же POSIX-клиентом (проверка автообновления на CI).
long long httpDownloadToFile(const std::string& url, const std::string& path,
                            std::string& error, int timeoutSec) {
    HttpResponse r = httpRequest(url, "GET", "", std::map<std::string, std::string>(), timeoutSec);
    if (!r.ok) { error = r.error.empty() ? ("HTTP " + std::to_string(r.status)) : r.error; return -1; }
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { error = "Не удалось создать файл " + path; return -1; }
    size_t n = fwrite(r.body.data(), 1, r.body.size(), f);
    fclose(f);
    if (n != r.body.size() || n == 0) { error = "Ошибка записи файла " + path; return -1; }
    return (long long)n;
}

} // namespace crm

#endif // !_WIN32
