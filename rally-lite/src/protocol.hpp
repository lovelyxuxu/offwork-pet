#pragma once
#include "client.hpp"
#include <nlohmann/json.hpp>
#include <map>
#include <vector>

namespace rally {
class Protocol {
public:
    explicit Protocol(Client::Sink sink) : sink_(std::move(sink)) {}
    void connected(const std::vector<std::string>& features, double mono);
    void disconnected(const std::string& message);
    void receive(const std::string& wire, double mono);
    void join(const std::string& code, const std::string& name, double mono);
    void leave(double mono);
    void alert(int index, double mono);
    void tick(double mono);
    const Snapshot& snapshot() const { return state_; }
private:
    struct Pending { std::string kind; double deadline; };
    void status(const std::string& text);
    void request(nlohmann::json message, double mono);
    void sync_rally(const nlohmann::json& value);
    void flash(const Flash& value);
    Client::Sink sink_;
    Snapshot state_;
    std::map<std::string, Pending> pending_;
    std::map<std::string, double> seen_;
    double server_epoch_ = 0, sync_mono_ = 0;
    bool clock_synced_ = false;
    bool signals_ = false;
};
}
