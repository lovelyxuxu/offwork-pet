#pragma once
#include <array>
#include <functional>
#include <memory>
#include <optional>
#include <string>

namespace rally {
constexpr const char* default_endpoint = "http://106.54.27.8";
struct Flash {
    std::string level, label, color, source;
};
struct Snapshot {
    bool ready = false;
    bool owner = false;
    std::string status, rally_id, rally_name, code;
    int members = 0;
};
enum class EventKind { State, Flash, Send, Fatal, Reconnect };
struct Event {
    EventKind kind = EventKind::State;
    Snapshot state;
    Flash flash;
    std::string wire;
};
struct Hotkey {
    unsigned modifiers = 0;
    unsigned key = 0;
};
std::optional<Hotkey> parse_hotkey(const std::string& text);
bool valid_name(const std::string& text);
double alert_intensity(double elapsed, double duration, const std::string& level);

struct Settings {
    std::string endpoint = default_endpoint;
    std::string nickname;
    std::array<std::string, 3> hotkeys{{"Ctrl+Alt+G", "Ctrl+Alt+Y", "Ctrl+Alt+R"}};
};
Settings load_settings();
void save_settings(const Settings& settings);
std::string load_token(const std::string& endpoint);
void save_token(const std::string& endpoint, const std::string& token);

class Client {
public:
    using Sink = std::function<void(const Event&)>;
    Client(std::string endpoint, std::string token, std::function<void(const std::string&)> persist_token, Sink sink);
    ~Client();
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    void start();
    void stop();
    void join(std::string code, std::string name);
    void leave();
    void alert(int index);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
