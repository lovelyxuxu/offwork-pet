#include "client.hpp"
#include "win_common.hpp"
#include <wincrypt.h>
#include <shlobj.h>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <cstdlib>

namespace rally {
namespace {
using nlohmann::json;
std::filesystem::path home() {
    if (auto override = _wgetenv(L"RALLY_LITE_HOME")) return std::filesystem::path(override);
    wchar_t folder[MAX_PATH];
    if (FAILED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, folder)))
        throw std::runtime_error("无法定位本地配置目录");
    return std::filesystem::path(folder) / L"RallyLite";
}
json read(const wchar_t* name) {
    auto path = home() / name;
    if (!std::filesystem::exists(path)) return json::object();
    if (std::filesystem::file_size(path) > 32768) throw std::runtime_error("本地配置文件过大");
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("无法读取本地配置");
    auto value = json::parse(file);
    if (!value.is_object()) throw std::runtime_error("本地配置格式无效");
    return value;
}
void write(const wchar_t* name, const json& value) {
    auto directory = home();
    std::filesystem::create_directories(directory);
    auto path = directory / name, temporary = path;
    temporary += L".tmp";
    {
        std::ofstream file(temporary, std::ios::binary | std::ios::trunc);
        file << value.dump(2);
        file.close();
        if (!file) throw std::runtime_error("无法写入本地配置");
    }
    if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("无法保存本地配置");
}
std::string protect(const std::string& data, bool decrypt) {
    DATA_BLOB source{static_cast<DWORD>(data.size()), reinterpret_cast<BYTE*>(const_cast<char*>(data.data()))}, result{};
    bool ok = decrypt ? CryptUnprotectData(&source, nullptr, nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &result)
                      : CryptProtectData(&source, L"RallyLite", nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &result);
    if (!ok) throw std::runtime_error("无法访问安全设备身份，请使用原 Windows 账户");
    std::string value(reinterpret_cast<char*>(result.pbData), result.cbData);
    SecureZeroMemory(result.pbData, result.cbData);
    LocalFree(result.pbData);
    return value;
}
}
Settings load_settings() {
    Settings result;
    auto value = read(L"settings.json");
    if (value.contains("nickname") && value["nickname"].is_string()) result.nickname = value["nickname"].get<std::string>();
    if (!valid_name(result.nickname)) throw std::runtime_error("保存的昵称格式无效");
    if (value.contains("hotkeys")) {
        if (!value["hotkeys"].is_array() || value["hotkeys"].size() != 3) throw std::runtime_error("保存的快捷键格式无效");
        for (size_t i = 0; i < 3; ++i) {
            if (!value["hotkeys"][i].is_string()) throw std::runtime_error("保存的快捷键格式无效");
            result.hotkeys[i] = value["hotkeys"][i].get<std::string>();
        }
    }
    if (auto endpoint = std::getenv("RALLY_LITE_ENDPOINT")) result.endpoint = endpoint;
    return result;
}
void save_settings(const Settings& settings) {
    if (!valid_name(settings.nickname)) throw std::runtime_error("昵称最多 24 字，不能包含换行");
    for (const auto& key : settings.hotkeys) if (!parse_hotkey(key)) throw std::runtime_error("快捷键格式无效");
    write(L"settings.json", {{"nickname", settings.nickname}, {"hotkeys", settings.hotkeys}});
}
std::string load_token(const std::string& endpoint) {
    auto value = read(L"identity.json");
    if (value.value("endpoint", std::string()) != endpoint) return {};
    auto encoded = value.value("protected_token", std::string());
    if (encoded.empty() || encoded.size() % 2) throw std::runtime_error("保存的设备身份格式无效");
    std::string data;
    auto digit = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        throw std::runtime_error("保存的设备身份格式无效");
    };
    for (size_t i = 0; i < encoded.size(); i += 2) data += static_cast<char>((digit(encoded[i]) << 4) | digit(encoded[i + 1]));
    return protect(data, true);
}
void save_token(const std::string& endpoint, const std::string& token) {
    auto protected_data = protect(token, false);
    std::string encoded;
    for (unsigned char c : protected_data) { encoded += "0123456789abcdef"[c >> 4]; encoded += "0123456789abcdef"[c & 15]; }
    write(L"identity.json", {{"endpoint", endpoint}, {"protected_token", encoded}});
}
}
