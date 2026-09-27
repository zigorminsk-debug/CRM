// api.cpp — вызовы REST API сервера CRM.
#include "api.h"
#include "hash.h"
#include "http.h"
#include <cstdio>

namespace crm {

std::string urlEncode(const std::string& s) {
    static const char* hex = "0123456789ABCDEF";
    std::string out;
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char c = (unsigned char)s[i];
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') out += (char)c;
        else {
            out += '%';
            out += hex[c >> 4];
            out += hex[c & 15];
        }
    }
    return out;
}

static std::string errText(const HttpResponse& r) {
    if (!r.error.empty()) return r.error;
    Json j = Json::parse(r.body);
    std::string e = j.getStr("error");
    if (e.empty()) e = j.getStr("detail");
    if (e.empty()) {
        char buf[80];
        snprintf(buf, sizeof(buf), "Ошибка сервера (HTTP %d)", r.status);
        e = buf;
    }
    return e;
}

bool ApiClient::health(std::string& info) {
    HttpResponse r = httpRequest(base + "/api/health", "GET", "", std::map<std::string, std::string>(), 6);
    if (!r.ok) { lastError = errText(r); return false; }
    Json j = Json::parse(r.body);
    char buf[256];
    snprintf(buf, sizeof(buf), "Сервер доступен. Версия %s, зон: %d, время: %s",
             j.getStr("version").c_str(), j.getInt("zone_count"), j.getStr("time").c_str());
    info = buf;
    return true;
}

bool ApiClient::listWorks(std::vector<Work>& out) {
    HttpResponse r = httpRequest(base + "/api/works", "GET");
    if (!r.ok) { lastError = errText(r); return false; }
    Json j = Json::parse(r.body);
    out.clear();
    for (size_t i = 0; i < j.arr.size(); ++i) {
        const Json& w = j.arr[i];
        Work it;
        it.id = w.getInt("id");
        it.code = w.getStr("code");
        it.name = w.getStr("name");
        it.category = w.getStr("category");
        it.siteKind = w.getStr("site_kind");
        it.minutes = w.getInt("default_minutes", 60);
        out.push_back(it);
    }
    return true;
}

bool ApiClient::geocode(const std::string& address, GeoResult& out) {
    HttpResponse r = httpRequest(base + "/api/geo/resolve?address=" + urlEncode(address), "GET");
    if (!r.ok) { lastError = errText(r); return false; }
    Json j = Json::parse(r.body);
    out = GeoResult();
    out.ok = j.getBool("ok", false);
    if (!out.ok) { out.message = j.getStr("message"); return false; }
    out.lat = j.getNum("lat");
    out.lon = j.getNum("lon");
    out.zoneId = j.getInt("zone_id");
    out.zoneName = j.getStr("zone_name");
    out.engineerName = j.getStr("engineer_name");
    out.engineerVia = j.getStr("engineer_via");
    out.provider = j.getStr("provider");
    out.precision = j.getStr("precision");
    out.message = j.getStr("message");
    out.navUrl = j.getStr("yandex_url");
    return true;
}

bool ApiClient::submitRequest(const RequestForm& form, const GeoResult& geo, Json& out, std::string& error) {
    Json body = Json::object();
    body.set("contractor", form.contractor);
    body.set("unp", normalizeUnp(form.unp));
    body.set("bank_account", normalizeAccount(form.bankAccount));
    body.set("contact_person", form.contactPerson);
    body.set("phone", normalizePhone(form.phone));
    if (form.workId) body.set("work_id", form.workId);
    if (!form.workCode.empty()) body.set("work_code", form.workCode);
    body.set("priority", form.priority.empty() ? std::string("normal") : form.priority);
    body.set("address", form.address);
    body.set("comment", form.comment);
    if (!form.equipment.empty()) body.set("equipment", form.equipment);
    if (!form.serial.empty()) body.set("serial", form.serial);
    if (!form.email.empty()) body.set("email", form.email);
    if (!form.bankName.empty()) body.set("bank_name", form.bankName);
    if (geo.ok) {
        body.set("lat", geo.lat);
        body.set("lon", geo.lon);
        if (geo.zoneId) body.set("zone_id", geo.zoneId);
    }

    std::map<std::string, std::string> hdrs;
    hdrs["X-Client-Name"] = "windows-client-v1";
    if (!token.empty()) hdrs["Authorization"] = "Bearer " + token;

    HttpResponse r = httpRequest(base + "/api/requests", "POST", body.dump(), hdrs);
    out = Json::parse(r.body);
    if (!r.ok) { error = errText(r); lastError = error; return false; }
    return true;
}

bool ApiClient::searchContractors(const std::string& query, std::vector<ContractorCard>& out) {
    HttpResponse r = httpRequest(base + "/api/contractors?limit=25&q=" + urlEncode(query), "GET");
    out.clear();
    if (!r.ok) { lastError = errText(r); return false; }
    Json j = Json::parse(r.body);
    for (size_t i = 0; i < j.arr.size(); ++i) {
        const Json& c = j.arr[i];
        ContractorCard card;
        card.id = c.getInt("id");
        card.name = c.getStr("name");
        card.unp = c.getStr("unp");
        card.bankAccount = c.getStr("bank_account");
        card.bankName = c.getStr("bank_name");
        card.address = c.getStr("address");
        card.contactPerson = c.getStr("contact_person");
        card.phone = c.getStr("phone");
        card.email = c.getStr("email");
        out.push_back(card);
    }
    return true;
}

bool ApiClient::searchRequests(const std::string& query, Json& out) {
    std::string qs;
    if (!query.empty()) qs = "&q=" + urlEncode(query);
    HttpResponse r = httpRequest(base + "/api/requests?limit=25" + qs, "GET");
    if (!r.ok) { lastError = errText(r); return false; }
    out = Json::parse(r.body);
    return true;
}

bool ApiClient::login(const std::string& user, const std::string& password) {
    Json body = Json::object();
    body.set("username", user);
    body.set("password", password);
    body.set("device", "windows-client");
    HttpResponse r = httpRequest(base + "/api/auth/login", "POST", body.dump());
    if (!r.ok) { lastError = errText(r); return false; }
    Json j = Json::parse(r.body);
    token = j.getStr("token");
    return !token.empty();
}

bool ApiClient::checkUpdates(UpdateInfo& out) {
    out = UpdateInfo();
    HttpResponse r = httpRequest(base + "/api/updates", "GET", "", std::map<std::string, std::string>(), 25);
    if (!r.ok) { lastError = errText(r); out.error = lastError; return false; }
    Json j = Json::parse(r.body);
    const Json* cur = j.find("current");
    const Json* latest = j.find("latest");
    if (cur) {
        out.currentVersion = cur->getStr("version");
        out.currentBuild = cur->getInt("build");
    }
    if (!latest || latest->isNull() || latest->type != Json::OBJ) {
        out.error = j.getStr("error");
        if (out.error.empty()) out.error = "Релизы на GitHub ещё не опубликованы";
        return false;
    }
    out.latestVersion = latest->getStr("version");
    out.latestBuild = latest->getInt("build");
    out.notes = latest->getStr("notes");
    out.page = latest->getStr("page");
    const Json* win = latest->find("windows");
    if (win && win->type == Json::OBJ) {
        out.url = win->getStr("url");
        out.sha256 = win->getStr("sha256");
        out.size = (long long)win->getNum("size");
    }
    out.available = j.getBool("update_available", false) && !out.url.empty();
    return true;
}

bool ApiClient::downloadUpdate(const UpdateInfo& u, const std::string& targetPath, std::string& error) {
    if (u.url.empty()) { error = "В релизе нет файла для Windows"; return false; }
    long long size = httpDownloadToFile(u.url, targetPath, error, 300);
    if (size <= 0) { lastError = error; return false; }
    if (!u.sha256.empty()) {
        std::string hashErr;
        std::string got = sha256File(targetPath, hashErr);
        if (got.empty()) { error = hashErr; return false; }
        if (!hexEqual(got, u.sha256)) {
            error = "Контрольная сумма не совпала: ожидалось " + u.sha256.substr(0, 16) +
                    "…, получено " + got.substr(0, 16) + "…";
            return false;
        }
    }
    char buf[128];
    snprintf(buf, sizeof(buf), "Скачано %lld КБ, подпись файла проверена", size / 1024);
    return true;
}

} // namespace crm
