#include "transport.hpp"
#import <Foundation/Foundation.h>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>

namespace {
struct SocketState {
    std::mutex mutex;
    std::condition_variable condition;
    std::deque<std::string> messages;
    bool opened = false, closed = false;
    NSInteger code = 0;
};
struct Completion {
    std::mutex mutex;
    std::condition_variable condition;
    bool done = false, failed = false;
    NSInteger status = 0;
    std::string data;
};
NSString* string(const std::string& value) {
    return [[NSString alloc] initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];
}
void await(const std::shared_ptr<Completion>& state, std::atomic<bool>& cancelled, int seconds) {
    std::unique_lock<std::mutex> lock(state->mutex);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
    while (!state->done && !cancelled && std::chrono::steady_clock::now() < deadline)
        state->condition.wait_for(lock, std::chrono::milliseconds(100));
    if (!state->done || cancelled) throw rally::NetworkError("连接已取消或超时");
    if (state->failed) throw rally::NetworkError("无法连接集会服务");
}
void receive_next(NSURLSessionWebSocketTask* task, std::shared_ptr<SocketState> state) {
    [task receiveMessageWithCompletionHandler:^(NSURLSessionWebSocketMessage* message, NSError* error) {
        bool again = false;
        {
            std::lock_guard<std::mutex> lock(state->mutex);
            if (!state->closed && !error && message.type == NSURLSessionWebSocketMessageTypeString && state->messages.size() < 128) {
                NSData* data = [message.string dataUsingEncoding:NSUTF8StringEncoding];
                if (data.length <= 32768) {
                    state->messages.emplace_back(static_cast<const char*>(data.bytes), data.length);
                    again = true;
                }
            }
            if (!again) state->closed = true;
        }
        state->condition.notify_all();
        if (again) receive_next(task, state);
    }];
}
}
@interface RLNetworkDelegate : NSObject <NSURLSessionWebSocketDelegate> {
@public
    std::shared_ptr<SocketState> state;
}
@end
@implementation RLNetworkDelegate
- (void)URLSession:(NSURLSession*)session webSocketTask:(NSURLSessionWebSocketTask*)task didOpenWithProtocol:(NSString*)protocol {
    { std::lock_guard<std::mutex> lock(state->mutex); state->opened = true; }
    state->condition.notify_all();
}
- (void)URLSession:(NSURLSession*)session webSocketTask:(NSURLSessionWebSocketTask*)task didCloseWithCode:(NSURLSessionWebSocketCloseCode)code reason:(NSData*)reason {
    { std::lock_guard<std::mutex> lock(state->mutex); state->closed = true; state->code = code; }
    state->condition.notify_all();
}
- (void)URLSession:(NSURLSession*)session task:(NSURLSessionTask*)task didCompleteWithError:(NSError*)error {
    if ([task isKindOfClass:[NSURLSessionWebSocketTask class]] && error) {
        { std::lock_guard<std::mutex> lock(state->mutex); state->closed = true; }
        state->condition.notify_all();
    }
}
- (void)URLSession:(NSURLSession*)session task:(NSURLSessionTask*)task willPerformHTTPRedirection:(NSHTTPURLResponse*)response newRequest:(NSURLRequest*)request completionHandler:(void (^)(NSURLRequest*))completionHandler {
    completionHandler(nil);
}
@end

namespace rally {
struct Transport::Impl {
    std::atomic<bool>& cancelled;
    std::shared_ptr<SocketState> state = std::make_shared<SocketState>();
    NSURLSession* session;
    NSURLSessionWebSocketTask* task = nil;
    explicit Impl(std::atomic<bool>& flag) : cancelled(flag) {
        @autoreleasepool {
            auto delegate = [RLNetworkDelegate new];
            delegate->state = state;
            auto config = [NSURLSessionConfiguration ephemeralSessionConfiguration];
            config.timeoutIntervalForRequest = 10;
            config.timeoutIntervalForResource = 12;
            config.HTTPCookieStorage = nil;
            config.URLCache = nil;
            session = [NSURLSession sessionWithConfiguration:config delegate:delegate delegateQueue:nil];
        }
    }
    ~Impl() {
        @autoreleasepool {
            { std::lock_guard<std::mutex> lock(state->mutex); state->closed = true; }
            [task cancelWithCloseCode:NSURLSessionWebSocketCloseCodeNormalClosure reason:nil];
            [session invalidateAndCancel];
        }
    }
};
Transport::Transport(std::atomic<bool>& cancelled) : impl_(std::make_unique<Impl>(cancelled)) {}
Transport::~Transport() = default;
std::string Transport::http(const std::string& url, bool post) {
    @autoreleasepool {
        auto request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:string(url)]];
        if (!request.URL) throw NetworkError("服务地址无效", true);
        request.timeoutInterval = 10;
        if (post) {
            request.HTTPMethod = @"POST";
            request.HTTPBody = [@"{}" dataUsingEncoding:NSUTF8StringEncoding];
            [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
        }
        auto result = std::make_shared<Completion>();
        auto task = [impl_->session dataTaskWithRequest:request completionHandler:^(NSData* data, NSURLResponse* response, NSError* error) {
            std::lock_guard<std::mutex> lock(result->mutex);
            result->failed = error != nil || data.length > 32768 || ![response isKindOfClass:[NSHTTPURLResponse class]];
            result->status = [response isKindOfClass:[NSHTTPURLResponse class]] ? [(NSHTTPURLResponse*)response statusCode] : 0;
            if (!result->failed && data.length) result->data.assign(static_cast<const char*>(data.bytes), data.length);
            result->done = true; result->condition.notify_all();
        }];
        [task resume];
        try { await(result, impl_->cancelled, 12); }
        catch (...) { [task cancel]; throw; }
        if (result->status == 429) throw NetworkError("服务限流，请稍后重启再试", true);
        if (result->status != 200) throw NetworkError("服务请求失败（HTTP " + std::to_string(result->status) + "）", result->status >= 400 && result->status < 500);
        return result->data;
    }
}
void Transport::connect(const std::string& url, const std::string& token) {
    @autoreleasepool {
        if (token.empty() || token.size() > 4096 || token.find_first_of("\r\n") != std::string::npos) throw NetworkError("设备身份格式不正确", true);
        auto request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:string(url)]];
        if (!request.URL) throw NetworkError("服务地址无效", true);
        [request setValue:string("Bearer " + token) forHTTPHeaderField:@"Authorization"];
        impl_->task = [impl_->session webSocketTaskWithRequest:request];
        impl_->task.maximumMessageSize = 32768;
        [impl_->task resume];
        auto state = impl_->state;
        std::unique_lock<std::mutex> lock(state->mutex);
        auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        while (!state->opened && !state->closed && !impl_->cancelled && std::chrono::steady_clock::now() < deadline)
            state->condition.wait_for(lock, std::chrono::milliseconds(100));
        if (!state->opened || state->closed || impl_->cancelled) throw NetworkError("无法建立集会实时连接");
        lock.unlock();
        receive_next(impl_->task, state);
    }
}
void Transport::send(const std::string& message) {
    @autoreleasepool {
        auto state = std::make_shared<Completion>();
        auto value = [[NSURLSessionWebSocketMessage alloc] initWithString:string(message)];
        [impl_->task sendMessage:value completionHandler:^(NSError* error) {
            std::lock_guard<std::mutex> lock(state->mutex);
            state->done = true; state->failed = error != nil; state->condition.notify_all();
        }];
        await(state, impl_->cancelled, 2);
    }
}
std::optional<std::string> Transport::receive() {
    auto state = impl_->state;
    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->closed) {
        if (state->code == 4001) throw NetworkError("此身份已在另一处连接，请不要复制身份文件", true);
        if (state->code == 4401) throw NetworkError("设备身份已失效，请重新配置身份", true);
        throw NetworkError("实时连接已断开");
    }
    if (state->messages.empty()) return {};
    auto message = std::move(state->messages.front()); state->messages.pop_front(); return message;
}
}
