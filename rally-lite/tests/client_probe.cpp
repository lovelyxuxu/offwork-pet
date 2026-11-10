#include "client.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
#include <mutex>

int main() {
    using nlohmann::json;
    try {
        auto settings = rally::load_settings();
        std::mutex output;
        rally::Client client(settings.endpoint, rally::load_token(settings.endpoint), [&](const std::string& token) {
            rally::save_token(settings.endpoint, token);
        }, [&](const rally::Event& event) {
            std::lock_guard<std::mutex> guard(output);
            if (event.kind == rally::EventKind::State) {
                const auto& state = event.state;
                std::cout << json{{"type", "state"}, {"ready", state.ready}, {"rally_id", state.rally_id}, {"code", state.code}, {"status", state.status}}.dump() << std::endl;
            } else if (event.kind == rally::EventKind::Flash) {
                const auto& f = event.flash;
                std::cout << json{{"type", "flash"}, {"level", f.level}, {"label", f.label}, {"color", f.color}, {"from", f.source}}.dump() << std::endl;
            }
        });
        client.start();
        std::string line;
        while (std::getline(std::cin, line)) {
            auto cmd = json::parse(line);
            auto type = cmd.value("type", "");
            if (type == "join") client.join(cmd.value("code", ""), cmd.value("name", ""));
            else if (type == "alert") client.alert(cmd.at("index").get<int>());
            else if (type == "leave") client.leave();
            else if (type == "quit") break;
        }
        client.stop();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
