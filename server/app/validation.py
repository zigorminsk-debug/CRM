"""Проверка и нормализация обязательных реквизитов заявки.

* УНП — 9 цифр, контрольная цифра по алгоритму МНС РБ.
* Расчётный счёт — белорусский IBAN (28 знаков, mod-97) либо 13-значный счёт.
* Телефон — приведение к международному формату +375 XX XXX-XX-XX.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- УНП

_UNP_WEIGHTS = (29, 23, 19, 17, 13, 7, 5, 3)


def normalize_unp(raw: str) -> str:
    """Оставляет только цифры."""
    return re.sub(r"\D", "", raw or "")


def unp_is_valid(unp: str) -> bool:
    """Проверка контрольной цифры УНП (алгоритм МНС Республики Беларусь)."""
    unp = normalize_unp(unp)
    if len(unp) != 9:
        return False
    digits = [int(c) for c in unp]
    s = sum(w * d for w, d in zip(_UNP_WEIGHTS, digits[:8]))
    control = s % 11
    if control == 10:
        return False
    return control == digits[8]


def unp_error(unp: str) -> str | None:
    unp = normalize_unp(unp)
    if not unp:
        return "УНП обязателен для заполнения"
    if len(unp) != 9:
        return "УНП должен содержать 9 цифр (получено %d)" % len(unp)
    if not unp_is_valid(unp):
        return "Неверная контрольная цифра УНП — проверьте номер"
    return None


# ------------------------------------------------------------ р/с

_IBAN_BY = re.compile(r"^BY[0-9]{2}[A-Z0-9]{24}$")   # 28 знаков: BY + 2 контрольных + 4 банк + 20 счёта


def normalize_account(raw: str) -> str:
    return re.sub(r"\s", "", (raw or "")).upper()


def _iban_mod97(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    rem = 0
    for ch in digits:
        rem = (rem * 10 + int(ch)) % 97
    return rem == 1


def account_is_valid(acc: str) -> bool:
    acc = normalize_account(acc)
    if _IBAN_BY.match(acc):
        return _iban_mod97(acc)
    # 13-значный расчётный счёт (старый формат без IBAN)
    return bool(re.fullmatch(r"\d{13}", acc))


def account_error(acc: str, required: bool = True) -> str | None:
    acc = normalize_account(acc)
    if not acc:
        return "Расчётный счёт обязателен для заполнения" if required else None
    if _IBAN_BY.match(acc):
        if not _iban_mod97(acc):
            return "Неверная контрольная сумма р/с (IBAN BY, mod-97)"
        return None
    if re.fullmatch(r"\d{13}", acc):
        return None
    if acc.startswith("BY"):
        return "IBAN BY должен содержать 28 знаков: BY + 2 контрольные цифры + код банка (4 знака) + 20 знаков счёта"
    return "р/с должен быть IBAN формата BY… (28 знаков) или 13-значным счётом"


# ---------------------------------------------------------- телефон

MOBILE_CODES = {"25", "29", "33", "44"}
CITY_CODES = {"17", "162", "163", "164", "165", "166", "171", "172", "173", "174", "175", "176", "177", "178", "179"}


def normalize_phone(raw: str) -> str:
    """Любой ввод -> +375XXXXXXXXX (или +<цифры> для иностранного номера)."""
    s = re.sub(r"[^\d+]", "", raw or "")
    digits = s.lstrip("+")
    if not digits:
        return ""
    if digits.startswith("375"):
        digits = digits[3:]
    elif digits.startswith("80") and len(digits) == 11:
        digits = digits[2:]
    elif digits.startswith("8") and len(digits) == 10:
        digits = digits[1:]
    if len(digits) == 9 and digits.isdigit():
        return "+375" + digits
    if len(digits) == 7 and digits.startswith("0"):           # городской без кода
        return "+37517" + digits[1:]
    return "+" + digits


def format_phone(phone: str) -> str:
    """+375291234567 -> +375 29 123-45-67"""
    if not phone:
        return ""
    if phone.startswith("+375") and len(phone) == 13:
        d = phone[4:]
        return f"+375 {d[:2]} {d[2:5]}-{d[5:7]}-{d[7:9]}"
    return phone


def phone_error(raw: str) -> str | None:
    if not (raw or "").strip():
        return "Телефон обязателен для заполнения"
    p = normalize_phone(raw)
    if not p.startswith("+375"):
        return "Телефон должен быть белорусским: +375 …"
    if len(p) != 13:
        return "Номер неполный: ожидается +375 XX XXX-XX-XX"
    code = p[4:6]
    if code not in MOBILE_CODES and code not in CITY_CODES:
        return f"Неизвестный код оператора/города: {code}"
    return None


# --------------------------------------------------- прочие поля

def require(value: str, field: str) -> str | None:
    if not (value or "").strip():
        return f"Поле «{field}» обязательно для заполнения"
    return None


def validate_request_form(data: dict, works_codes: set[str] | None = None) -> tuple[dict, list[str]]:
    """Проверяет форму заявки. Возвращает (нормализованные данные, список ошибок)."""
    errors: list[str] = []
    out = dict(data)

    unp = normalize_unp(data.get("unp", ""))
    err = unp_error(unp)
    if err:
        errors.append(err)
    out["unp"] = unp

    acc = normalize_account(data.get("bank_account", ""))
    err = account_error(acc)
    if err:
        errors.append(err)
    out["bank_account"] = acc

    phone = normalize_phone(data.get("phone", ""))
    err = phone_error(data.get("phone", ""))
    if err:
        errors.append(err)
    out["phone"] = phone

    for f, label in (("contractor", "Контрагент"), ("contact_person", "Контактное лицо"), ("address", "Адрес")):
        err = require(data.get(f, ""), label)
        if err:
            errors.append(err)

    if not data.get("work_id") and not data.get("work_code"):
        errors.append("Не выбрана работа (выпадающий список)")
    if works_codes is not None and data.get("work_code") and data["work_code"] not in works_codes:
        errors.append("Выбранная работа отсутствует в справочнике")

    priority = data.get("priority", "normal")
    if priority not in ("emergency", "urgent", "normal", "planned"):
        errors.append("Некорректный приоритет срочности")
    out["priority"] = priority

    if data.get("lat") is not None and data.get("lon") is not None:
        try:
            lat, lon = float(data["lat"]), float(data["lon"])
            if not (51.0 <= lat <= 56.5 and 23.0 <= lon <= 33.0):
                errors.append("Координаты вне зоны обслуживания (Минск и Минская область)")
        except (TypeError, ValueError):
            errors.append("Координаты должны быть числами")

    return out, errors
