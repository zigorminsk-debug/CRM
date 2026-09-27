// json.h — минимальный JSON для клиента CRM (без внешних зависимостей).
// Поддерживает объекты, массивы, строки (UTF-8, экранирование и \uXXXX), числа, bool, null.
#pragma once

#include <string>
#include <vector>
#include <map>
#include <sstream>
#include <stdexcept>
#include <cstdio>

namespace crm {

class Json {
public:
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ };

    Type type = NUL;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<Json> arr;
    std::vector<std::pair<std::string, Json>> obj;   // порядок ключей сохраняется

    Json() {}
    Json(bool v) : type(BOOL), b(v) {}
    Json(double v) : type(NUM), num(v) {}
    Json(int v) : type(NUM), num((double)v) {}
    Json(long long v) : type(NUM), num((double)v) {}
    Json(const char* v) : type(STR), str(v ? v : "") {}
    Json(const std::string& v) : type(STR), str(v) {}

    static Json object() { Json j; j.type = OBJ; return j; }
    static Json array()  { Json j; j.type = ARR; return j; }

    bool isNull() const { return type == NUL; }
    bool has(const std::string& k) const { return find(k) != nullptr; }

    const Json* find(const std::string& k) const {
        if (type != OBJ) return nullptr;
        for (size_t i = 0; i < obj.size(); ++i)
            if (obj[i].first == k) return &obj[i].second;
        return nullptr;
    }

    std::string getStr(const std::string& k, const std::string& def = "") const {
        const Json* v = find(k);
        if (!v || v->type == NUL) return def;
        if (v->type == STR) return v->str;
        if (v->type == NUM) { char buf[64]; snprintf(buf, sizeof(buf), "%.10g", v->num); return buf; }
        if (v->type == BOOL) return v->b ? "true" : "false";
        return def;
    }
    double getNum(const std::string& k, double def = 0) const {
        const Json* v = find(k);
        if (!v) return def;
        if (v->type == NUM) return v->num;
        if (v->type == STR) { try { return atof(v->str.c_str()); } catch (...) { return def; } }
        return def;
    }
    bool getBool(const std::string& k, bool def = false) const {
        const Json* v = find(k);
        if (!v) return def;
        if (v->type == BOOL) return v->b;
        if (v->type == NUM) return v->num != 0;
        return def;
    }
    int getInt(const std::string& k, int def = 0) const { return (int)getNum(k, def); }
    const Json* getArr(const std::string& k) const {
        const Json* v = find(k);
        return (v && v->type == ARR) ? v : nullptr;
    }

    void set(const std::string& k, const Json& v) {
        if (type != OBJ) { type = OBJ; }
        for (size_t i = 0; i < obj.size(); ++i)
            if (obj[i].first == k) { obj[i].second = v; return; }
        obj.push_back(std::make_pair(k, v));
    }
    void set(const std::string& k, const std::string& v) { set(k, Json(v)); }
    void set(const std::string& k, const char* v) { set(k, Json(v ? v : "")); }
    void set(const std::string& k, int v) { set(k, Json(v)); }
    void set(const std::string& k, double v) { set(k, Json(v)); }
    void set(const std::string& k, bool v) { set(k, Json(v)); }
    void push(const Json& v) { if (type != ARR) type = ARR; arr.push_back(v); }

    // ------------------------------------------------------------- сериализация
    std::string dump() const {
        std::string out;
        dumpTo(out);
        return out;
    }

private:
    void dumpTo(std::string& o) const {
        switch (type) {
        case NUL: o += "null"; break;
        case BOOL: o += (b ? "true" : "false"); break;
        case NUM: {
            char buf[64];
            if (num == (long long)num) snprintf(buf, sizeof(buf), "%lld", (long long)num);
            else snprintf(buf, sizeof(buf), "%.6f", num);
            o += buf; break;
        }
        case STR: escape(str, o); break;
        case ARR: {
            o += "[";
            for (size_t i = 0; i < arr.size(); ++i) { if (i) o += ","; arr[i].dumpTo(o); }
            o += "]"; break;
        }
        case OBJ: {
            o += "{";
            for (size_t i = 0; i < obj.size(); ++i) {
                if (i) o += ",";
                escape(obj[i].first, o);
                o += ":";
                obj[i].second.dumpTo(o);
            }
            o += "}"; break;
        }
        }
    }

    static void escape(const std::string& s, std::string& o) {
        o += '"';
        for (size_t i = 0; i < s.size(); ++i) {
            unsigned char c = (unsigned char)s[i];
            switch (c) {
            case '"':  o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char buf[8]; snprintf(buf, sizeof(buf), "\\u%04x", c); o += buf; }
                else o += (char)c;
            }
        }
        o += '"';
    }

public:
    // -------------------------------------------------------------- разбор
    static Json parse(const std::string& text) {
        size_t i = 0;
        skipWs(text, i);
        Json j = parseValue(text, i);
        return j;
    }

private:
    static void skipWs(const std::string& t, size_t& i) {
        while (i < t.size() && (t[i] == ' ' || t[i] == '\t' || t[i] == '\n' || t[i] == '\r')) ++i;
    }

    static Json parseValue(const std::string& t, size_t& i) {
        skipWs(t, i);
        if (i >= t.size()) return Json();
        char c = t[i];
        if (c == '{') return parseObject(t, i);
        if (c == '[') return parseArray(t, i);
        if (c == '"') return Json(parseString(t, i));
        if (t.compare(i, 4, "true") == 0) { i += 4; return Json(true); }
        if (t.compare(i, 5, "false") == 0) { i += 5; return Json(false); }
        if (t.compare(i, 4, "null") == 0) { i += 4; return Json(); }
        return parseNumber(t, i);
    }

    static Json parseObject(const std::string& t, size_t& i) {
        Json j = Json::object();
        ++i;                       // {
        skipWs(t, i);
        if (i < t.size() && t[i] == '}') { ++i; return j; }
        while (i < t.size()) {
            skipWs(t, i);
            std::string key = parseString(t, i);
            skipWs(t, i);
            if (i < t.size() && t[i] == ':') ++i;
            Json v = parseValue(t, i);
            j.obj.push_back(std::make_pair(key, v));
            skipWs(t, i);
            if (i < t.size() && t[i] == ',') { ++i; continue; }
            if (i < t.size() && t[i] == '}') { ++i; break; }
            break;
        }
        return j;
    }

    static Json parseArray(const std::string& t, size_t& i) {
        Json j = Json::array();
        ++i;                       // [
        skipWs(t, i);
        if (i < t.size() && t[i] == ']') { ++i; return j; }
        while (i < t.size()) {
            Json v = parseValue(t, i);
            j.arr.push_back(v);
            skipWs(t, i);
            if (i < t.size() && t[i] == ',') { ++i; continue; }
            if (i < t.size() && t[i] == ']') { ++i; break; }
            break;
        }
        return j;
    }

    static std::string parseString(const std::string& t, size_t& i) {
        std::string out;
        if (i < t.size() && t[i] == '"') ++i;
        while (i < t.size()) {
            char c = t[i++];
            if (c == '"') break;
            if (c != '\\') { out += c; continue; }
            if (i >= t.size()) break;
            char e = t[i++];
            switch (e) {
            case 'n': out += '\n'; break;
            case 't': out += '\t'; break;
            case 'r': out += '\r'; break;
            case 'b': out += '\b'; break;
            case 'f': out += '\f'; break;
            case '/': out += '/'; break;
            case '"': out += '"'; break;
            case '\\': out += '\\'; break;
            case 'u': {
                if (i + 4 > t.size()) break;
                unsigned cp = 0;
                for (int k = 0; k < 4; ++k) {
                    char h = t[i + k];
                    cp <<= 4;
                    if (h >= '0' && h <= '9') cp |= (unsigned)(h - '0');
                    else if (h >= 'a' && h <= 'f') cp |= (unsigned)(h - 'a' + 10);
                    else if (h >= 'A' && h <= 'F') cp |= (unsigned)(h - 'A' + 10);
                }
                i += 4;
                if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= t.size() && t[i] == '\\' && t[i + 1] == 'u') {
                    unsigned lo = 0;
                    for (int k = 0; k < 4; ++k) {
                        char h = t[i + 2 + k];
                        lo <<= 4;
                        if (h >= '0' && h <= '9') lo |= (unsigned)(h - '0');
                        else if (h >= 'a' && h <= 'f') lo |= (unsigned)(h - 'a' + 10);
                        else if (h >= 'A' && h <= 'F') lo |= (unsigned)(h - 'A' + 10);
                    }
                    if (lo >= 0xDC00 && lo <= 0xDFFF) {
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        i += 6;
                    }
                }
                appendUtf8(out, cp);
                break;
            }
            default: out += e;
            }
        }
        return out;
    }

    static void appendUtf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out += (char)cp;
        else if (cp < 0x800) {
            out += (char)(0xC0 | (cp >> 6));
            out += (char)(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += (char)(0xE0 | (cp >> 12));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18));
            out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        }
    }

    static Json parseNumber(const std::string& t, size_t& i) {
        size_t start = i;
        while (i < t.size() && (isdigit((unsigned char)t[i]) || t[i] == '-' || t[i] == '+' ||
                                t[i] == '.' || t[i] == 'e' || t[i] == 'E')) ++i;
        return Json(atof(t.substr(start, i - start).c_str()));
    }
};

} // namespace crm
