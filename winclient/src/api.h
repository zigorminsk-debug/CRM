// api.h — клиент API сервера CRM (общий для Windows-GUI и консольного автотеста).
#pragma once
#include <string>
#include <vector>
#include "json.h"
#include "validate.h"

namespace crm {

struct Work {
    int id = 0;
    std::string code, name, category, siteKind;
    int minutes = 60;
    std::string label() const { return name + " — " + category; }
};

struct GeoResult {
    bool ok = false;
    double lat = 0, lon = 0;
    int zoneId = 0;
    std::string zoneName, engineerName, engineerVia, provider, precision, message, navUrl;
};

struct ContractorCard {
    int id = 0;
    std::string name, unp, bankAccount, bankName, address, contactPerson, phone, email;
};

// Сведения об обновлении, полученные от сервера (он читает манифест релиза на GitHub)
struct UpdateInfo {
    bool available = false;          // на GitHub есть сборка новее установленной
    int currentBuild = 0, latestBuild = 0;
    std::string currentVersion, latestVersion, notes, page, url, sha256;
    long long size = 0;
    std::string error;               // почему не удалось проверить (нет связи/GitHub)

    bool ok() const { return error.empty(); }
};

class ApiClient {
public:
    std::string base;          // например, http://192.168.1.10:8000
    std::string token;         // необязательный токен (для поиска в истории)
    std::string lastError;

    ApiClient() : base("http://127.0.0.1:8000") {}
    explicit ApiClient(const std::string& baseUrl) : base(baseUrl) {}

    bool health(std::string& info);
    bool listWorks(std::vector<Work>& out);
    bool geocode(const std::string& address, GeoResult& out);
    bool submitRequest(const RequestForm& form, const GeoResult& geo, Json& out, std::string& error);
    bool searchContractors(const std::string& query, std::vector<ContractorCard>& out);
    bool searchRequests(const std::string& query, Json& out);
    bool login(const std::string& user, const std::string& password);
    bool checkUpdates(UpdateInfo& out);                       // GET /api/updates
    bool downloadUpdate(const UpdateInfo& u, const std::string& targetPath, std::string& error);
};

// Утилита: склейка URL с экранированием параметров
std::string urlEncode(const std::string& s);

} // namespace crm
