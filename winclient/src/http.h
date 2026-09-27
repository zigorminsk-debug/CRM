// http.h — единый интерфейс HTTP-клиента.
// Windows: WinHTTP (системная библиотека, ничего ставить не нужно).
// Linux/тест: POSIX-сокеты (используется для автотеста клиента на CI).
#pragma once
#include <string>
#include <map>

namespace crm {

struct HttpResponse {
    int status = 0;
    std::string body;
    bool ok = false;
    std::string error;
};

// url вида "http://host:port/path?query" либо "https://host/path"
HttpResponse httpRequest(const std::string& url, const std::string& method,
                         const std::string& body = "",
                         const std::map<std::string, std::string>& headers = std::map<std::string, std::string>(),
                         int timeoutSec = 20);

// Разбор URL: host, port, path, https
struct UrlParts {
    std::string scheme, host, path;
    int port = 0;
    bool https = false;
};
UrlParts parseUrl(const std::string& url);

} // namespace crm
