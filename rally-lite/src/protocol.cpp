#include "protocol.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <random>
#include <sstream>

namespace rally {
using nlohmann::json;
namespace {
const std::array<Flash, 3> defaults{{{"safe", "安全", "#2FBF71", ""}, {"warning", "警戒", "#FFAA1A", ""}, {"danger", "危险", "#FF3B30", ""}}};
std::string text(const json& obj, const char* key) {
    auto it = obj.find(key);
    return it != obj.end() && it->is_string() ? it->get<std::string>() : "";
}
std::string trim(std::string value) {
    auto space = [](unsigned char c) { return std::isspace(c) != 0; };
    value.erase(value.begin(), std::find_if_not(value.begin(), value.end(), space));
    value.erase(std::find_if_not(value.rbegin(), value.rend(), space).base(), value.end());
    return value;
}
std::string identifier() {
    static std::random_device random;
    std::string value;
    for (int i = 0; i < 4; ++i) {
        auto number = random();
        for (int j = 0; j < 8; ++j) { value += "0123456789abcdef"[number & 15]; number >>= 4; }
    }
    return value;
}
bool valid_id(const std::string& value) {
    return !value.empty() && value.size() <= 64 && std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
    });
}
}

bool valid_name(const std::string& value) {
    if (value.size() > 96) return false;
    try { (void)json(value).dump(); } catch (const json::exception&) { return false; }
    size_t length = 0;
    for (unsigned char c : value) {
        if (c < 32 || c == 127) return false;
        if ((c & 0xc0) != 0x80) ++length;
    }
    return length <= 24;
}

std::optional<Hotkey> parse_hotkey(const std::string& input) {
    std::istringstream stream(input);
    std::string part;
    std::vector<std::string> parts;
    while (std::getline(stream, part, '+')) {
        part = trim(part);
        std::transform(part.begin(), part.end(), part.begin(), [](unsigned char c) { return std::tolower(c); });
        parts.push_back(part);
    }
    if (parts.size() < 2 || input.empty() || input.back() == '+') return {};
    Hotkey result;
    for (size_t i = 0; i + 1 < parts.size(); ++i) {
        unsigned bit = parts[i] == "ctrl" ? 1 : parts[i] == "alt" ? 2 : (parts[i] == "cmd" || parts[i] == "win") ? 4 : parts[i] == "shift" ? 8 : 0;
        if (!bit || (result.modifiers & bit)) return {};
        result.modifiers |= bit;
    }
    const auto& key = parts.back();
    if (key.size() == 1 && ((key[0] >= 'a' && key[0] <= 'z') || (key[0] >= '0' && key[0] <= '9')))
        result.key = static_cast<unsigned>(std::toupper(static_cast<unsigned char>(key[0])));
    else if (key.size() >= 2 && key[0] == 'f') {
        int number = 0;
        for (size_t i = 1; i < key.size(); ++i) {
            if (key[i] < '0' || key[i] > '9' || i > 2) return {};
            number = number * 10 + key[i] - '0';
        }
        if (number < 1 || number > 12 || key[1] == '0') return {};
        result.key = 0x6f + number;
    } else return {};
    return result;
}

double alert_intensity(double elapsed, double duration, const std::string& level) {
    if (elapsed < 0 || elapsed >= duration) return 0;
    const double fade = std::min({1.0, elapsed / 0.2, (duration - elapsed) / 0.5});
    double pulse = level == "safe" ? 0.65 : level == "warning" ? 0.60 + 0.15 * std::sin(elapsed * 3.2) : 0.72 + 0.23 * std::sin(elapsed * 5.0);
    return std::clamp(fade * pulse, 0.0, 1.0);
}

void Protocol::status(const std::string& value) {
    state_.status = value;
    Event event; event.state = state_;
    sink_(event);
}
void Protocol::request(json message, double mono) {
    auto id = identifier();
    pending_[id] = {text(message, "type"), mono + 8};
    message["request_id"] = id;
    Event event; event.kind = EventKind::Send; event.wire = message.dump(); sink_(event);
}
void Protocol::flash(const Flash& value) {
    Event event; event.kind = EventKind::Flash; event.flash = value; sink_(event);
}
void Protocol::connected(const std::vector<std::string>& features, double mono) {
    pending_.clear(); state_ = {}; clock_synced_ = false;
    if (std::find(features.begin(), features.end(), "rally/v1") == features.end()) {
        status("服务不支持集会提醒，请使用当前下班搭子服务");
        Event event; event.kind = EventKind::Fatal; event.state = state_; sink_(event); return;
    }
    signals_ = std::find(features.begin(), features.end(), "rally-signals/v1") != features.end();
    status("正在同步集会…");
    request({{"type", "profile"}, {"paused", false}, {"visible", true}, {"capabilities", nullptr}, {"visitSupport", false}, {"allowVisits", false}}, mono);
}
void Protocol::disconnected(const std::string& message) {
    state_ = {}; pending_.clear(); status(message);
}
void Protocol::sync_rally(const json& value) {
    if (value.is_null()) {
        state_.rally_id.clear(); state_.rally_name.clear(); state_.code.clear(); state_.members = 0; state_.owner = false;
    } else if (value.is_object() && !text(value, "id").empty()) {
        state_.rally_id = text(value, "id"); state_.rally_name = text(value, "name"); state_.code = text(value, "code");
        state_.owner = value.value("owner_self", json(false)) == true;
        state_.members = value.contains("members") && value["members"].is_array() ? static_cast<int>(value["members"].size()) : 0;
    } else return;
    status(state_.rally_id.empty() ? "已连接，请输入 8 位集会码" : "已加入集会 · " + std::to_string(state_.members) + " 位成员");
}
void Protocol::receive(const std::string& wire, double mono) {
    if (wire.size() > 32768) return;
    const auto msg = json::parse(wire, nullptr, false);
    if (!msg.is_object()) return;
    const auto type = text(msg, "type");
    auto time = msg.find("server_time");
    if ((type == "state" || type == "heartbeat") && time != msg.end() && time->is_number() && std::isfinite(time->get<double>())) {
        double estimate = time->get<double>();
        if (!clock_synced_ || estimate > server_epoch_ + mono - sync_mono_) {
            server_epoch_ = estimate; sync_mono_ = mono; clock_synced_ = true;
        }
    }
    if (type == "rally_state" && msg.contains("rally")) sync_rally(msg["rally"]);
    else if (type == "result") {
        auto found = pending_.find(text(msg, "request_id"));
        if (found == pending_.end()) return;
        auto kind = found->second.kind;
        pending_.erase(found);
        if (msg.value("ok", json(false)) != true) {
            status(text(msg, "message").empty() ? "操作未成功，请重试" : text(msg, "message"));
            if (kind == "profile") { Event event; event.kind = EventKind::Fatal; event.state = state_; sink_(event); }
            return;
        }
        const auto data = msg.value("data", json::object());
        if (kind == "profile") { state_.ready = true; status(state_.rally_id.empty() ? "已连接，请输入 8 位集会码" : "已重新连接集会"); }
        if (data.is_object() && data.contains("rally")) sync_rally(data["rally"]);
        if (kind == "rally_alert") {
            const auto delivered = data.is_object() ? data.value("delivered", json(0)) : json(0);
            status(delivered.is_number_integer() && delivered.get<int>() > 0 ? "已广播给 " + std::to_string(delivered.get<int>()) + " 位在线成员" : "其他成员当前不在线");
        }
    } else if (type == "rally_alert" && state_.ready && !state_.rally_id.empty()) {
        auto level = text(msg, "level"), id = text(msg, "alert_id");
        auto found = std::find_if(defaults.begin(), defaults.end(), [&](const Flash& f) { return f.level == level; });
        auto expires = msg.find("expires_at");
        if (found == defaults.end() || !valid_id(id) || text(msg, "rally_id") != state_.rally_id || expires == msg.end() || !expires->is_number()) return;
        if (!clock_synced_) return;
        double ttl = expires->get<double>() - server_epoch_ - (mono - sync_mono_);
        if (!std::isfinite(ttl) || ttl <= 0 || ttl > 7) return;
        Flash value = *found;
        if (msg.contains("label") || msg.contains("color")) {
            value.label = text(msg, "label"); value.color = text(msg, "color");
            if (trim(value.label).empty() || !valid_name(value.label) || value.color.size() != 7 || value.color[0] != '#' ||
                !std::all_of(value.color.begin() + 1, value.color.end(), [](unsigned char c) { return std::isxdigit(c) != 0; })) return;
        }
        value.source = text(msg, "from");
        if (!valid_name(value.source)) value.source.clear();
        for (auto it = seen_.begin(); it != seen_.end();) {
            if (it->second <= mono) it = seen_.erase(it); else ++it;
        }
        auto key = state_.rally_id + ":" + id;
        if (seen_.count(key)) return;
        seen_[key] = mono + 30;
        flash(value);
    }
}
void Protocol::join(const std::string& input, const std::string& name, double mono) {
    if (!state_.ready) { status("尚未连接服务，请稍后再试"); return; }
    auto code = trim(input);
    std::transform(code.begin(), code.end(), code.begin(), [](unsigned char c) { return std::toupper(c); });
    if (code.size() != 8 || code.find_first_not_of("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") != std::string::npos) { status("请输入 8 位集会码，不是双人搭子配对码"); return; }
    if (!valid_name(name)) { status("昵称最多 24 字，不能包含换行"); return; }
    request({{"type", "rally_join"}, {"code", code}, {"name", trim(name)}}, mono);
}
void Protocol::leave(double mono) {
    if (!state_.ready || state_.rally_id.empty()) { status("当前没有已连接的集会"); return; }
    request({{"type", "rally_leave"}}, mono);
}
void Protocol::alert(int index, double mono) {
    if (!state_.ready || state_.rally_id.empty()) { status("未连接集会，提醒没有发送"); return; }
    if (index < 0 || index >= 3) return;
    const auto& value = defaults[index];
    json msg{{"type", "rally_alert"}, {"level", value.level}, {"alert_id", identifier()}};
    if (signals_) { msg["label"] = value.label; msg["color"] = value.color; }
    flash(value);
    request(std::move(msg), mono);
}
void Protocol::tick(double mono) {
    for (auto it = pending_.begin(); it != pending_.end();) {
        if (it->second.deadline > mono) { ++it; continue; }
        bool initial = it->second.kind == "profile";
        it = pending_.erase(it);
        status("服务未确认操作结果，请检查连接后重试");
        if (initial) { Event event; event.kind = EventKind::Reconnect; event.state = state_; sink_(event); }
    }
}
}
