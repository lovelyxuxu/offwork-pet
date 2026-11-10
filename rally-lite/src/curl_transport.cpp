#include "transport.hpp"
#include <winsock2.h>
#include <curl/curl.h>
#include <chrono>
#include <mutex>
#include <vector>

namespace rally {
namespace {
using Handle = std::unique_ptr<CURL, decltype(&curl_easy_cleanup)>;
using Headers = std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)>;
size_t collect(char* data, size_t size, size_t count, void* target) {
    auto& result = *static_cast<std::string*>(target);
    const size_t bytes = size * count;
    if (result.size() + bytes > 32768) return 0;
    result.append(data, bytes);
    return bytes;
}
int progress(void* ctx, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
    return static_cast<std::atomic<bool>*>(ctx)->load() ? 1 : 0;
}
}
struct Transport::Impl {
    std::atomic<bool>& cancelled;
    Handle ws{nullptr, curl_easy_cleanup};
    Headers headers{nullptr, curl_slist_free_all};
    std::string incoming;
    explicit Impl(std::atomic<bool>& flag) : cancelled(flag) {
        static std::once_flag once;
        std::call_once(once, [] {
            if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) throw NetworkError("网络组件初始化失败", true);
        });
    }
    Handle handle(const std::string& url) {
        Handle result(curl_easy_init(), curl_easy_cleanup);
        if (!result) throw NetworkError("网络组件初始化失败", true);
        curl_easy_setopt(result.get(), CURLOPT_URL, url.c_str());
        curl_easy_setopt(result.get(), CURLOPT_PROTOCOLS_STR, "http,https,ws,wss");
        curl_easy_setopt(result.get(), CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(result.get(), CURLOPT_CONNECTTIMEOUT_MS, 8000L);
        curl_easy_setopt(result.get(), CURLOPT_TIMEOUT_MS, 10000L);
        curl_easy_setopt(result.get(), CURLOPT_NOPROGRESS, 0L);
        curl_easy_setopt(result.get(), CURLOPT_XFERINFOFUNCTION, progress);
        curl_easy_setopt(result.get(), CURLOPT_XFERINFODATA, &cancelled);
        curl_easy_setopt(result.get(), CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(result.get(), CURLOPT_USERAGENT, "RallyLite/1.0");
        return result;
    }
    void wait_write() {
        curl_socket_t socket = CURL_SOCKET_BAD;
        curl_easy_getinfo(ws.get(), CURLINFO_ACTIVESOCKET, &socket);
        if (socket == CURL_SOCKET_BAD) throw NetworkError("连接已断开");
        fd_set writes; FD_ZERO(&writes); FD_SET(socket, &writes);
        timeval interval{0, 100000};
        if (select(0, nullptr, &writes, nullptr, &interval) == SOCKET_ERROR) throw NetworkError("网络写入失败");
    }
};
Transport::Transport(std::atomic<bool>& cancelled) : impl_(std::make_unique<Impl>(cancelled)) {}
Transport::~Transport() = default;
std::string Transport::http(const std::string& url, bool post) {
    auto curl = impl_->handle(url);
    std::string body;
    curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, collect);
    curl_easy_setopt(curl.get(), CURLOPT_WRITEDATA, &body);
    Headers headers(nullptr, curl_slist_free_all);
    if (post) {
        headers.reset(curl_slist_append(nullptr, "Content-Type: application/json"));
        curl_easy_setopt(curl.get(), CURLOPT_HTTPHEADER, headers.get());
        curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDS, "{}");
    }
    auto result = curl_easy_perform(curl.get());
    long status = 0; curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status);
    if (result != CURLE_OK) throw NetworkError("无法连接集会服务");
    if (status == 429) throw NetworkError("服务限流，请稍后重启再试", true);
    if (status != 200) throw NetworkError("服务请求失败（HTTP " + std::to_string(status) + "）", status >= 400 && status < 500);
    return body;
}
void Transport::connect(const std::string& url, const std::string& token) {
    if (token.empty() || token.size() > 4096 || token.find_first_of("\r\n") != std::string::npos) throw NetworkError("设备身份格式不正确", true);
    impl_->ws = impl_->handle(url);
    impl_->headers.reset(curl_slist_append(nullptr, ("Authorization: Bearer " + token).c_str()));
    curl_easy_setopt(impl_->ws.get(), CURLOPT_HTTPHEADER, impl_->headers.get());
    curl_easy_setopt(impl_->ws.get(), CURLOPT_CONNECT_ONLY, 2L);
    if (curl_easy_perform(impl_->ws.get()) != CURLE_OK) throw NetworkError("无法建立集会实时连接");
}
void Transport::send(const std::string& message) {
    size_t position = 0;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    bool first = true;
    while (position < message.size()) {
        if (impl_->cancelled || std::chrono::steady_clock::now() >= deadline) throw NetworkError("提醒发送超时，未确认送达");
        size_t sent = 0;
        auto result = curl_ws_send(impl_->ws.get(), message.data() + position, message.size() - position, &sent,
                                   first ? static_cast<curl_off_t>(message.size()) : 0, CURLWS_TEXT | CURLWS_OFFSET);
        if (result == CURLE_AGAIN) { impl_->wait_write(); continue; }
        if (result != CURLE_OK) throw NetworkError("实时连接发送失败");
        first = false;
        position += sent;
        if (!sent) impl_->wait_write();
    }
}
std::optional<std::string> Transport::receive() {
    char buffer[4096];
    for (int i = 0; i < 16; ++i) {
        size_t count = 0;
        const curl_ws_frame* frame = nullptr;
        auto result = curl_ws_recv(impl_->ws.get(), buffer, sizeof(buffer), &count, &frame);
        if (result == CURLE_AGAIN) return {};
        if (result != CURLE_OK) throw NetworkError("实时连接已断开");
        if (frame->flags & CURLWS_CLOSE) {
            unsigned code = count >= 2 ? (static_cast<unsigned char>(buffer[0]) << 8) | static_cast<unsigned char>(buffer[1]) : 0;
            if (code == 4001) throw NetworkError("此身份已在另一处连接，请不要复制身份文件", true);
            if (code == 4401) throw NetworkError("设备身份已失效，请重新配置身份", true);
            throw NetworkError("实时连接已关闭");
        }
        if (frame->flags & (CURLWS_PING | CURLWS_PONG)) continue;
        if (frame->flags & CURLWS_BINARY) throw NetworkError("服务发送了不支持的数据");
        if (impl_->incoming.size() + count > 32768) throw NetworkError("服务消息超过大小限制");
        impl_->incoming.append(buffer, count);
        if (frame->bytesleft == 0 && !(frame->flags & CURLWS_CONT)) {
            auto message = std::move(impl_->incoming);
            impl_->incoming.clear();
            return message;
        }
    }
    return {};
}
}
