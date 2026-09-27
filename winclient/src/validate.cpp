// validate.cpp — реализация проверок (портирована с серверной логики).
#include "validate.h"
#include <cctype>
#include <cstdio>
#include <cstring>

namespace crm {

static const int UNP_W[8] = {29, 23, 19, 17, 13, 7, 5, 3};

std::string normalizeUnp(const std::string& raw) {
    std::string out;
    for (size_t i = 0; i < raw.size(); ++i)
        if (isdigit((unsigned char)raw[i])) out += raw[i];
    return out;
}

bool unpIsValid(const std::string& unp) {
    if (unp.size() != 9) return false;
    int sum = 0;
    for (int i = 0; i < 8; ++i) sum += UNP_W[i] * (unp[i] - '0');
    int c = sum % 11;
    if (c == 10) return false;
    return c == (unp[8] - '0');
}

std::string unpError(const std::string& raw) {
    std::string u = normalizeUnp(raw);
    if (u.empty()) return "УНП обязателен для заполнения";
    if (u.size() != 9) {
        char buf[128];
        snprintf(buf, sizeof(buf), "УНП должен содержать 9 цифр (введено %d)", (int)u.size());
        return buf;
    }
    if (!unpIsValid(u)) return "Неверная контрольная цифра УНП — проверьте номер";
    return "";
}

// ------------------------------------------------------------------ р/с

std::string normalizeAccount(const std::string& raw) {
    std::string out;
    for (size_t i = 0; i < raw.size(); ++i)
        if (!isspace((unsigned char)raw[i])) out += (char)toupper((unsigned char)raw[i]);
    return out;
}

static bool ibanMod97(const std::string& iban) {
    std::string re = iban.substr(4) + iban.substr(0, 4);
    long long rem = 0;
    for (size_t i = 0; i < re.size(); ++i) {
        char c = re[i];
        int val;
        if (c >= '0' && c <= '9') val = c - '0';
        else if (c >= 'A' && c <= 'Z') val = c - 'A' + 10;
        else return false;
        if (val >= 10) rem = (rem * 100 + val) % 97;
        else rem = (rem * 10 + val) % 97;
    }
    return rem == 1;
}

bool accountIsValid(const std::string& acc) {
    if (acc.size() == 28 && acc.compare(0, 2, "BY") == 0) return ibanMod97(acc);
    if (acc.size() == 13) {
        for (size_t i = 0; i < acc.size(); ++i)
            if (!isdigit((unsigned char)acc[i])) return false;
        return true;
    }
    return false;
}

std::string accountError(const std::string& raw) {
    std::string a = normalizeAccount(raw);
    if (a.empty()) return "Расчётный счёт обязателен для заполнения";
    if (a.size() == 28 && a.compare(0, 2, "BY") == 0)
        return ibanMod97(a) ? "" : "Неверная контрольная сумма р/с (IBAN, mod-97)";
    if (a.size() == 13) {
        for (size_t i = 0; i < a.size(); ++i)
            if (!isdigit((unsigned char)a[i])) return "13-значный р/с должен состоять только из цифр";
        return "";
    }
    if (a.compare(0, 2, "BY") == 0)
        return "IBAN BY должен содержать 28 знаков: BY + 2 контрольные цифры + код банка (4) + 20 знаков счёта";
    return "р/с должен быть IBAN формата BY… (28 знаков) или 13-значным счётом";
}

// -------------------------------------------------------------- телефон

std::string normalizePhone(const std::string& raw) {
    std::string digits;
    for (size_t i = 0; i < raw.size(); ++i)
        if (isdigit((unsigned char)raw[i])) digits += raw[i];
    if (digits.empty()) return "";
    if (digits.size() >= 12 && digits.compare(0, 3, "375") == 0) digits = digits.substr(3);
    else if (digits.size() == 11 && digits.compare(0, 2, "80") == 0) digits = digits.substr(2);
    else if (digits.size() == 10 && digits[0] == '8') digits = digits.substr(1);
    if (digits.size() == 9) return "+375" + digits;
    if (digits.size() == 7 && digits[0] == '0') return "+37517" + digits.substr(1);
    return "+" + digits;
}

std::string formatPhone(const std::string& phone) {
    if (phone.size() == 13 && phone.compare(0, 4, "+375") == 0) {
        std::string d = phone.substr(4);
        return "+375 " + d.substr(0, 2) + " " + d.substr(2, 3) + "-" + d.substr(5, 2) + "-" + d.substr(7, 2);
    }
    return phone;
}

std::string phoneError(const std::string& raw) {
    std::string p = normalizePhone(raw);
    if (p.empty() || p == "+") return "Телефон обязателен для заполнения";
    if (p.compare(0, 4, "+375") != 0) return "Телефон должен быть белорусским: +375 …";
    if (p.size() != 13) return "Номер неполный: ожидается +375 XX XXX-XX-XX";
    std::string code = p.substr(4, 2);
    const char* codes[] = {"25", "29", "33", "44", "17", "16", "21", "22", "23", "31", "32", "37", "35", "41", "42"};
    for (size_t i = 0; i < sizeof(codes) / sizeof(codes[0]); ++i)
        if (code == codes[i]) return "";
    return "Неизвестный код оператора/города: " + code;
}

// ----------------------------------------------------------- проверка формы

static std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && isspace((unsigned char)s[a])) ++a;
    while (b > a && isspace((unsigned char)s[b - 1])) --b;
    return s.substr(a, b - a);
}

std::vector<std::string> validateForm(const RequestForm& f) {
    std::vector<std::string> errs;
    std::string e;

    e = trim(f.contractor).empty() ? "Поле «Контрагент» обязательно для заполнения" : "";
    if (!e.empty()) errs.push_back(e);

    e = unpError(f.unp);
    if (!e.empty()) errs.push_back(e);

    e = accountError(f.bankAccount);
    if (!e.empty()) errs.push_back(e);

    e = trim(f.contactPerson).empty() ? "Поле «Контактное лицо» обязательно для заполнения" : "";
    if (!e.empty()) errs.push_back(e);

    e = phoneError(f.phone);
    if (!e.empty()) errs.push_back(e);

    if (trim(f.address).empty()) errs.push_back("Поле «Адрес» обязательно для заполнения");
    if (trim(f.workCode).empty() && f.workId == 0)
        errs.push_back("Не выбрана работа из выпадающего списка");

    if (f.hasCoords) {
        if (!(f.lat >= 51.0 && f.lat <= 56.5 && f.lon >= 23.0 && f.lon <= 33.0))
            errs.push_back("Координаты вне зоны обслуживания (Минск и Минская область)");
    }
    return errs;
}

} // namespace crm
