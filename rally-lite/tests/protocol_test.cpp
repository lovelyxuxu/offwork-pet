#include "protocol.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace rally;
using nlohmann::json;

void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Fixture {
    std::vector<Event> events;
    Protocol protocol{[this](const Event& event) { events.push_back(event); }};
    double wall = 1000, mono = 20;
    json last_sent() {
        for (auto it = events.rbegin(); it != events.rend(); ++it)
            if (it->kind == EventKind::Send) return json::parse(it->wire);
        throw std::runtime_error("no outbound message");
    }
    void receive(json value) { protocol.receive(value.dump(), mono); }
    void connect() {
        protocol.connected({"rally/v1", "rally-signals/v1"}, mono);
        auto profile = last_sent();
        check(profile["type"] == "profile" && profile["capabilities"].is_null(), "headless profile");
        check(profile["visible"] == true && profile["paused"] == false, "background receiver stays available");
        check(profile["visitSupport"] == false && profile["allowVisits"] == false, "no pet capabilities");
        receive({{"type", "state"}, {"server_time", 2000.0}});
        receive({{"type", "result"}, {"request_id", profile["request_id"]}, {"ok", true}, {"data", json::object()}});
        receive({{"type", "rally_state"}, {"rally", {{"id", "group"}, {"code", "ABCDEFGH"}, {"name", "集会"}, {"owner_self", false}, {"members", json::array({json::object(), json::object()})}}}});
        check(protocol.snapshot().ready && protocol.snapshot().rally_id == "group", "ready and membership synced");
    }
    json alert(std::string id = "a") {
        return {{"type", "rally_alert"}, {"rally_id", "group"}, {"alert_id", id}, {"level", "warning"}, {"from", "小伙伴"}, {"expires_at", 2006.0}};
    }
    int flashes() const {
        int count = 0;
        for (const auto& event : events) if (event.kind == EventKind::Flash) ++count;
        return count;
    }
};

int main() {
    try {
        Fixture f;
        f.connect();
        f.receive(f.alert());
        check(f.flashes() == 1, "clock offset permits fresh signal");
        f.receive(f.alert());
        check(f.flashes() == 1, "duplicate suppressed");
        auto wrong = f.alert("wrong"); wrong["rally_id"] = "other"; f.receive(wrong);
        auto old = f.alert("old"); old["expires_at"] = 1999; f.receive(old);
        auto far = f.alert("far"); far["expires_at"] = 2010; f.receive(far);
        auto invalid = f.alert("invalid"); invalid["level"] = "bad"; f.receive(invalid);
        auto custom = f.alert("custom"); custom["label"] = "开会啦"; custom["color"] = "#7964CB";
        f.receive(custom);
        check(f.flashes() == 2, "reject stale and foreign signals, accept custom label");
        check(f.events.back().flash.label == "开会啦", "preserve remote custom label");
        custom["alert_id"] = "bad-color"; custom["color"] = "red"; f.receive(custom);
        check(f.flashes() == 2, "reject malformed custom signal");
        f.protocol.receive("{", f.mono);
        f.protocol.receive("[]", f.mono);
        f.receive({{"type", "rally_alert"}, {"expires_at", true}});
        f.protocol.alert(2, f.mono);
        auto outbound = f.last_sent();
        check(outbound["type"] == "rally_alert" && outbound["level"] == "danger", "hotkey maps to protocol");
        check(outbound.contains("alert_id") && outbound.contains("request_id"), "unique request and alert IDs");
        check(f.flashes() == 3, "sender sees local edge");
        f.protocol.disconnected("断线");
        f.receive(f.alert("late"));
        f.protocol.alert(0, f.mono);
        check(f.flashes() == 3 && !f.protocol.snapshot().ready, "offline signals neither replay nor flash");
        Fixture join;
        join.protocol.connected({"rally/v1"}, join.mono);
        auto profile = join.last_sent();
        join.receive({{"type", "result"}, {"request_id", profile["request_id"]}, {"ok", true}, {"data", json::object()}});
        join.protocol.join(" abcdefgh ", "我", join.mono);
        check(join.last_sent()["code"] == "ABCDEFGH", "normalize rally invitation code");
        auto count = join.events.size();
        join.protocol.join("123456", "我", join.mono);
        check(join.events.size() == count + 1 && join.events.back().kind == EventKind::State, "reject pair invite code");
        Fixture delayed;
        delayed.connect();
        delayed.mono += 40; delayed.wall += 40;
        auto replay = delayed.alert("replayed"); replay["server_time"] = 2000;
        delayed.receive(replay);
        check(delayed.flashes() == 0, "delayed alert must not reset the clock and resurrect itself");
        Fixture timeout;
        timeout.protocol.connected({"rally/v1"}, timeout.mono);
        timeout.protocol.tick(timeout.mono + 9);
        check(timeout.events.back().kind != EventKind::Fatal, "profile timeout is retryable, not a permanent shutdown");
        Fixture missing;
        missing.protocol.connected({}, missing.mono);
        check(!missing.protocol.snapshot().ready && missing.events.back().kind == EventKind::Fatal, "require rally capability");
        auto hotkey = parse_hotkey("Ctrl+Alt+R");
        check(hotkey && hotkey->key == 'R' && hotkey->modifiers == 3, "portable hotkey modifiers");
        check(!parse_hotkey("R") && !parse_hotkey("Ctrl+Ctrl+R") && !parse_hotkey("Alt+F13"), "validate hotkeys");
        check(parse_hotkey("Cmd+Shift+F12").has_value(), "mac command modifier");
        check(valid_name("名字") && !valid_name("a\nb") && !valid_name(std::string(25, 'a')), "name bounds");
        check(alert_intensity(0, 5, "danger") == 0 && alert_intensity(5, 5, "safe") == 0, "edge fades and ends");
        std::cout << "PASS: protocol, headless profile, membership, bidirectional signals, TTL, dedupe, offline, hotkeys\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
