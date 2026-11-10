#include "client.hpp"
#include "protocol.hpp"
#include "transport.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>

namespace rally {
namespace {
double mono() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
std::string endpoint_root(std::string url) {
    while (!url.empty() && url.back() == '/') url.pop_back();
    size_t start = url.rfind("https://", 0) == 0 ? 8 : url.rfind("http://", 0) == 0 ? 7 : 0;
    if (!start || url.size() == start || url.find_first_of("/@?#\\ \r\n\t", start) != std::string::npos)
        throw NetworkError("服务地址必须是 HTTP 或 HTTPS 根地址", true);
    return url;
}
}
struct Client::Impl {
    std::string endpoint, token;
    std::function<void(const std::string&)> persist;
    Sink sink;
    std::atomic<bool> stopped{false};
    std::thread worker;
    std::mutex mutex;
    std::condition_variable condition;
    struct Command { int kind; std::string code, name; int index = 0; };
    std::deque<Command> commands;
    std::unique_ptr<Transport> transport;
    bool fatal = false;
    Protocol protocol{[this](const Event& event) {
        if (event.kind == EventKind::Send) transport->send(event.wire);
        else if (event.kind == EventKind::Fatal) fatal = true;
        else if (event.kind == EventKind::Reconnect) throw NetworkError(event.state.status);
        else sink(event);
    }};
    Impl(std::string url, std::string identity, std::function<void(const std::string&)> save, Sink callback)
        : endpoint(std::move(url)), token(std::move(identity)), persist(std::move(save)), sink(std::move(callback)) {}
    void enqueue(Command command) {
        std::lock_guard<std::mutex> lock(mutex);
        if (commands.size() < 32) commands.push_back(std::move(command));
        condition.notify_all();
    }
    void clear_commands() {
        std::lock_guard<std::mutex> lock(mutex);
        commands.clear();
    }
    void run() {
        int delay = 1;
        while (!stopped && !fatal) {
            try {
                endpoint = endpoint_root(endpoint);
                protocol.disconnected("正在连接集会服务…");
                transport = std::make_unique<Transport>(stopped);
                auto health = nlohmann::json::parse(transport->http(endpoint + "/health"));
                if (!health.is_object() || health.value("ok", nlohmann::json(false)) != true)
                    throw NetworkError("服务返回了无效状态", true);
                const auto protocols = health.value("supported_protocols", nlohmann::json::array());
                if (!protocols.is_array() || std::find(protocols.begin(), protocols.end(), 2) == protocols.end())
                    throw NetworkError("服务不支持当前通信协议", true);
                auto features = health.value("features", nlohmann::json::array());
                if (!features.is_array() || std::find(features.begin(), features.end(), "rally/v1") == features.end())
                    throw NetworkError("服务不支持集会提醒", true);
                std::vector<std::string> names;
                for (const auto& value : features) if (value.is_string()) names.push_back(value.get<std::string>());
                if (token.empty()) {
                    auto identity = nlohmann::json::parse(transport->http(endpoint + "/v2/devices", true));
                    if (!identity.is_object() || !identity.contains("token") || !identity["token"].is_string() || identity["token"].get<std::string>().empty())
                        throw NetworkError("服务返回了无效设备身份", true);
                    token = identity["token"].get<std::string>();
                    try { persist(token); }
                    catch (...) { throw NetworkError("无法安全保存设备身份，请检查本地存储权限", true); }
                }
                auto url = endpoint;
                url.replace(0, url.find("://"), endpoint.rfind("https://", 0) == 0 ? "wss" : "ws");
                transport->connect(url + "/v2/connect", token);
                protocol.connected(names, mono());
                double last_received = mono(), last_heartbeat = mono();
                clear_commands();
                while (!stopped && !fatal) {
                    if (auto message = transport->receive()) {
                        last_received = mono();
                        protocol.receive(*message, mono());
                        if (protocol.snapshot().ready) delay = 1;
                    }
                    std::deque<Command> batch;
                    { std::lock_guard<std::mutex> lock(mutex); batch.swap(commands); }
                    for (const auto& cmd : batch) {
                        if (cmd.kind == 0) protocol.join(cmd.code, cmd.name, mono());
                        else if (cmd.kind == 1) protocol.leave(mono());
                        else protocol.alert(cmd.index, mono());
                    }
                    if (mono() - last_received > 30) throw NetworkError("连接已超时");
                    if (mono() - last_heartbeat >= 10) {
                        transport->send("{\"type\":\"heartbeat\"}"); last_heartbeat = mono();
                    }
                    protocol.tick(mono());
                    std::unique_lock<std::mutex> lock(mutex);
                    condition.wait_for(lock, std::chrono::milliseconds(25), [this] { return stopped || !commands.empty(); });
                }
            } catch (const NetworkError& error) {
                fatal = error.fatal;
                if (!stopped) protocol.disconnected(std::string(error.what()) + (fatal ? "" : "，稍后自动重连"));
            } catch (const nlohmann::json::exception&) {
                if (!stopped) protocol.disconnected("服务数据格式不正确，稍后自动重连");
            } catch (const std::exception&) {
                if (!stopped) protocol.disconnected("连接失败，稍后自动重连");
            }
            transport.reset();
            clear_commands();
            if (!stopped && !fatal) {
                std::unique_lock<std::mutex> lock(mutex);
                condition.wait_for(lock, std::chrono::seconds(delay), [this] { return stopped.load(); });
                delay = std::min(delay * 2, 30);
            }
        }
    }
};
Client::Client(std::string endpoint, std::string token, std::function<void(const std::string&)> persist_token, Sink sink)
    : impl_(std::make_unique<Impl>(std::move(endpoint), std::move(token), std::move(persist_token), std::move(sink))) {}
Client::~Client() { stop(); }
void Client::start() {
    if (!impl_->worker.joinable()) { impl_->stopped = false; impl_->fatal = false; impl_->worker = std::thread([this] { impl_->run(); }); }
}
void Client::stop() {
    impl_->stopped = true; impl_->condition.notify_all();
    if (impl_->worker.joinable()) impl_->worker.join();
}
void Client::join(std::string code, std::string name) { impl_->enqueue({0, std::move(code), std::move(name)}); }
void Client::leave() { impl_->enqueue({1, {}, {}}); }
void Client::alert(int index) { impl_->enqueue({2, {}, {}, index}); }
}
