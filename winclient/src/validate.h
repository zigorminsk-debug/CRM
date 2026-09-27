// validate.h — проверка обязательных реквизитов заявки (та же логика, что на сервере).
#pragma once
#include <string>
#include <vector>

namespace crm {

// УНП: 9 цифр, контрольная цифра по алгоритму МНС РБ
std::string normalizeUnp(const std::string& raw);
bool unpIsValid(const std::string& unp);
std::string unpError(const std::string& raw);

// Расчётный счёт: IBAN BY (28 знаков, mod-97) либо 13-значный счёт
std::string normalizeAccount(const std::string& raw);
bool accountIsValid(const std::string& acc);
std::string accountError(const std::string& raw);

// Телефон: приведение к +375 XX XXX-XX-XX
std::string normalizePhone(const std::string& raw);
std::string formatPhone(const std::string& normalized);
std::string phoneError(const std::string& raw);

// Общая проверка формы: возвращает список ошибок (пусто = форма корректна)
struct RequestForm {
    std::string contractor, unp, bankAccount, contactPerson, phone, address, comment;
    std::string equipment, serial, email, bankName;
    std::string workCode, priority;
    int workId = 0;
    bool hasCoords = false;
    double lat = 0, lon = 0;
    int zoneId = 0;
};
std::vector<std::string> validateForm(const RequestForm& f);

} // namespace crm
