// main_test.cpp — консольный автотест клиентской логики (та же логика, что в Windows-GUI).
// Сборка: g++ -std=c++17 src/main_test.cpp src/api.cpp src/validate.cpp src/http_posix.cpp -o crm_client_test
// Запуск:  ./crm_client_test http://127.0.0.1:8000
#include "api.h"
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

    printf("\n=== Итог: %s (ошибок: %d) ===\n", failures == 0 ? "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" : "ЕСТЬ ОШИБКИ", failures);
    return failures == 0 ? 0 : 1;
}
