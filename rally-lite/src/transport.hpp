#pragma once
#include <atomic>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

namespace rally {
struct NetworkError : std::runtime_error {
    bool fatal;
    explicit NetworkError(const std::string& message, bool stop = false) : std::runtime_error(message), fatal(stop) {}
};
class Transport {
public:
    explicit Transport(std::atomic<bool>& cancelled);
    ~Transport();
    std::string http(const std::string& url, bool post = false);
    void connect(const std::string& url, const std::string& token);
    void send(const std::string& message);
    std::optional<std::string> receive();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
