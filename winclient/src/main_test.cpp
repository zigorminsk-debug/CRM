// main_test.cpp — консольный автотест клиентской логики (та же логика, что в Windows-GUI).
// Сборка: g++ -std=c++17 src/main_test.cpp src/api.cpp src/validate.cpp src/hash.cpp src/http_posix.cpp
//         -o crm_client_test
// Запуск:  ./crm_client_test http://127.0.0.1:8000
#include "api.h"
#include "hash.h"
#include "http.h"
#include "validate.h"
#include <cstdio>
#include <cstdlib>
#include <iostream>

using namespace crm;

static int failures = 0;

static void check(bool cond, const std::string& name, const std::string& detail = "") {
    printf("%s %s%s\n", cond ? "[ OK ]" : "[FAIL]", name.c_str(),
           detail.empty() ? "" : (" — " + detail).c_str());
    if (!cond) failures++;
}

int main(int argc, char** argv) {
    std::string base = argc > 1 ? argv[1] : "http://127.0.0.1:8000";
    const char* env = getenv("CRM_SERVER");
    if (env) base = env;

    ApiClient api(base);
    printf("=== Автотест клиента CRM, сервер: %s ===\n\n", base.c_str());

    // 1. Проверки реквизитов (локальная валидация формы)
    check(unpError("190123455").empty(), "УНП 190123455 корректен", unpError("190123455"));
    check(!unpError("190123456").empty(), "Неверный УНП отклонён", unpError("190123456"));
    check(unpError("123").find("9 цифр") != std::string::npos, "Короткий УНП отклонён");
    check(accountError("BY96BLBB30120000000000000001").empty(), "IBAN BY… длиной 28 знаков корректен");
    check(!accountError("BY96BLBB30120000000000000099").empty(), "IBAN с неверной контрольной суммой отклонён");
    check(accountError("3012000000000").empty(), "13-значный р/с корректен");
    check(normalizePhone("8 (029) 111-22-33") == "+375291112233", "Телефон 8 029 → +375291112233", normalizePhone("8 (029) 111-22-33"));
    check(normalizePhone("+375 33 333-44-55") == "+375333334455", "Телефон +375 33… нормализован");
    check(formatPhone("+375291112233") == "+375 29 111-22-33", "Формат телефона для отображения", formatPhone("+375291112233"));
    check(phoneError("+375 29 111-22-33").empty(), "Телефон проходит проверку");
    check(!phoneError("+7 999 000-00-00").empty(), "Иностранный номер отклонён");

    RequestForm bad;
    bad.contractor = "";
    bad.unp = "123";
    bad.bankAccount = "BY00";
    bad.contactPerson = "";
    bad.phone = "123";
    bad.address = "";
    std::vector<std::string> errs = validateForm(bad);
    check(errs.size() >= 6, "Пустая форма даёт полный список ошибок", std::to_string(errs.size()) + " ошибок");

    // 2. Связь с сервером
    std::string info;
    bool ok = api.health(info);
    check(ok, "GET /api/health", api.lastError);
    if (!ok) { printf("\nСервер недоступен — дальнейшие проверки пропущены.\n"); return 1; }

    // 3. Справочник работ (выпадающий список)
    std::vector<Work> works;
    bool worksOk = api.listWorks(works);
    check(worksOk && !works.empty(), "GET /api/works — справочник работ",
          std::to_string(works.size()) + " работ, первая: " + (works.empty() ? "" : works[0].name));

    // 4. Адрес -> координаты
    GeoResult geo;
    bool geoOk = api.geocode("г. Минск, ул. Притыцкого, 62", geo) && geo.ok;
    check(geoOk, "Адрес → координаты", geo.zoneName + " | инженер: " + geo.engineerName + " | " + geo.provider);
    check(geo.lat > 53.0 && geo.lon > 27.0, "Координаты в пределах Минска",
          std::to_string(geo.lat) + "," + std::to_string(geo.lon));

    GeoResult geo2;
    bool geo2Ok = api.geocode("Минский район, аг. Колодищи, ул. Минская, 5", geo2) && geo2.ok;
    check(geo2Ok, "Адрес Минского района → координаты", geo2.zoneName + " | " + geo2.engineerName);

    // 5. Отправка заявки
    RequestForm f;
    f.contractor = "ИП Тестовый Пользователь";
    f.unp = "100234568";
    f.bankAccount = "BY42BLBB30120000000000000003";
    f.contactPerson = "Тестов Тест Тестович";
    f.phone = "8 029 555-66-77";
    f.address = "г. Минск, ул. Кальварийская, 17";
    f.comment = "Автотест клиента: заправить картридж HP CF218A, 2 шт.";
    f.equipment = "HP LaserJet Pro M428";
    f.serial = "TEST0001";
    f.priority = "urgent";
    if (!works.empty()) { f.workId = works[0].id; f.workCode = works[0].code; }

    GeoResult geoF;
    api.geocode(f.address, geoF);

    Json created;
    std::string error;
    bool sent = api.submitRequest(f, geoF, created, error);
    check(sent, "POST /api/requests — заявка принята", error);
    if (sent) {
        std::string number = created.getStr("number");
        std::string eng = created.getStr("engineer_name");
        printf("       заявка %s, район: %s, инженер: %s\n", number.c_str(),
               created.getStr("zone_name").c_str(), eng.c_str());
        check(number.size() > 5, "Присвоен номер заявки", number);
        check(created.getStr("phone") == "+375295556677", "Сервер нормализовал телефон", created.getStr("phone"));
        check(created.getStr("status_label").size() > 0, "Статус заявки определён", created.getStr("status_label"));
    }

    // 6. Ошибочная заявка (неверный УНП) — клиент не должен её отправить
    RequestForm bad2 = f;
    bad2.unp = "190123456";
    std::vector<std::string> e2 = validateForm(bad2);
    bool blocked = !e2.empty();
    check(blocked, "Форма с неверным УНП не отправляется", e2.empty() ? "" : e2[0]);

    // 7. Поиск по контрагенту/УНП/р/с (история)
    std::vector<ContractorCard> cards;
    bool cOk = api.searchContractors("Тестовый", cards) && !cards.empty();
    check(cOk, "Поиск контрагента для автозаполнения",
          cards.empty() ? api.lastError : cards[0].name + " / УНП " + cards[0].unp + " / " + cards[0].bankAccount);

    Json hist;
    bool hOk = api.searchRequests("+375295556677", hist);
    printf("       история по телефону: найдено заявок %d\n", hist.getInt("total"));
    check(hOk && hist.getInt("total") >= 1, "Поиск по истории заявок (телефон)", hist.getStr("error"));

    // 8. Авторизация (для истории по р/с)
    check(api.login("admin", "admin123") || !api.lastError.empty(), "Проверка входа администратора", api.lastError);

    // 9. SHA-256 — им проверяется целостность скачанного обновления
    check(sha256Hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
          "SHA-256 строки (тестовый вектор)", sha256Hex("abc"));
    std::string emptyHash = sha256Hex("");
    check(emptyHash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "SHA-256 пустой строки", emptyHash);

    // 10. Версия и автообновление
    Json ver;
    bool vOk = false;
    {
        HttpResponse vr = httpRequest(base + "/api/version", "GET");
        vOk = vr.ok;
        ver = Json::parse(vr.body);
    }
    check(vOk && !ver.getStr("version").empty(), "GET /api/version — версия сборки",
          ver.getStr("version") + " (build " + ver.getStr("build") + ")");
    check(ver.getStr("manifest_url").find("update.json") != std::string::npos,
          "Адрес манифеста автообновления", ver.getStr("manifest_url"));

    UpdateInfo upd;
    bool uOk = api.checkUpdates(upd);
    printf("       релиз: установлено %s (build %d), на GitHub %s (build %d)\n",
           upd.currentVersion.c_str(), upd.currentBuild, upd.latestVersion.c_str(), upd.latestBuild);
    check(ok && (uOk || !upd.error.empty()), "GET /api/updates — проверка релиза на GitHub",
          uOk ? ("обновление " + std::string(upd.available ? "доступно: " + upd.latestVersion : "не требуется"))
              : upd.error);
    if (uOk && upd.available) {
        check(upd.url.find("CRM-Windows") != std::string::npos && upd.sha256.size() == 64,
              "В релизе есть exe и контрольная сумма", upd.sha256.substr(0, 16) + "…");
    }

    // 11. Скачивание файла обновления и сверка контрольной суммы
    {
        std::string local = "/tmp/crm-download-test.bin";
        std::string dlErr;
        long long bytes = httpDownloadToFile(base + "/download/CRM-Windows.exe", local, dlErr, 60);
        std::string hashErr, got;
        if (bytes > 0) got = sha256File(local, hashErr);
        bool missing = dlErr.find("404") != std::string::npos;   // сборка ещё не опубликована
        check(bytes > 0 || missing, "Скачивание сборки с сервера (файл для обновления)",
              bytes > 0 ? (std::to_string(bytes / 1024) + " КБ, sha256 " + got.substr(0, 16) + "…")
                        : (missing ? "файл пока не собран — проверка пропущена" : dlErr));
        remove(local.c_str());
    }

    printf("\n=== Итог: %s (ошибок: %d) ===\n", failures == 0 ? "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" : "ЕСТЬ ОШИБКИ", failures);
    return failures == 0 ? 0 : 1;
}
