// hash.h — SHA-256 для проверки целостности файлов обновления.
// Реализация переносимая (без OpenSSL/CryptoAPI): используется и в Windows-клиенте,
// и в консольном автотесте, и при сборке — так подпись и контрольная сумма проверяются одним кодом.
#pragma once
#include <string>

namespace crm {

// SHA-256 произвольной строки, hex в нижнем регистре
std::string sha256Hex(const std::string& data);

// SHA-256 файла; при ошибке чтения возвращает пустую строку и текст в error
std::string sha256File(const std::string& path, std::string& error);

// Сравнение hex-строк без учёта регистра
bool hexEqual(const std::string& a, const std::string& b);

} // namespace crm
