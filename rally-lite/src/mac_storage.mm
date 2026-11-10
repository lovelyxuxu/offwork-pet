#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include "client.hpp"
#include <nlohmann/json.hpp>
#include <cstdlib>
#include <stdexcept>

#if !__has_feature(objc_arc)
#error RallyLite macOS sources require Objective-C ARC (-fobjc-arc).
#endif

namespace {
NSString* nativeString(const std::string& value) {
    NSString* result = [[NSString alloc] initWithBytes:value.data()
                                              length:value.size()
                                            encoding:NSUTF8StringEncoding];
    if (!result) throw std::runtime_error("Invalid UTF-8 in local settings");
    return result;
}

NSURL* settingsDirectory() {
    if (const char* home = std::getenv("RALLY_LITE_HOME"); home && *home) {
        NSString* path = [nativeString(home) stringByExpandingTildeInPath];
        return [NSURL fileURLWithPath:path isDirectory:YES];
    }
    NSURL* support = [[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory
                                                         inDomains:NSUserDomainMask].firstObject;
    if (!support) throw std::runtime_error("Cannot locate Application Support directory");
    return [support URLByAppendingPathComponent:@"RallyLite" isDirectory:YES];
}

void validateSettings(const rally::Settings& settings) {
    if (settings.endpoint.empty() || settings.endpoint.find('\0') != std::string::npos)
        throw std::runtime_error("Invalid endpoint in settings");
    nativeString(settings.endpoint);
    if (!settings.nickname.empty() && !rally::valid_name(settings.nickname))
        throw std::runtime_error("Nickname must contain at most 24 Unicode characters");
    std::array<rally::Hotkey, 3> parsed{};
    for (std::size_t i = 0; i < parsed.size(); ++i) {
        auto value = rally::parse_hotkey(settings.hotkeys[i]);
        if (!value) throw std::runtime_error("Invalid hotkey in settings");
        parsed[i] = *value;
        for (std::size_t j = 0; j < i; ++j)
            if (parsed[j].key == value->key && parsed[j].modifiers == value->modifiers)
                throw std::runtime_error("Duplicate hotkeys in settings");
    }
}

NSMutableDictionary* keychainQuery(const std::string& endpoint) {
    if (endpoint.empty()) throw std::runtime_error("Cannot use empty Keychain account");
    return [@{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
              (__bridge id)kSecAttrService: @"offwork.rally-lite",
              (__bridge id)kSecAttrAccount: nativeString(endpoint),
              (__bridge id)kSecAttrSynchronizable: @NO} mutableCopy];
}

std::runtime_error keychainError(const char* action, OSStatus status) {
    // Only expose the operation and OSStatus, never endpoint, secret data or a query.
    return std::runtime_error(std::string("Keychain ") + action +
                              " failed (OSStatus " + std::to_string(status) + ")");
}
} // namespace

namespace rally {
Settings load_settings() {
    @autoreleasepool {
        Settings settings;
        NSURL* url = [settingsDirectory() URLByAppendingPathComponent:@"settings.json"];
        NSError* error = nil;
        NSData* data = [NSData dataWithContentsOfURL:url options:0 error:&error];
        if (!data) {
            // Missing file is first launch. Permission/corruption errors are not first launch.
            if (![error.domain isEqualToString:NSCocoaErrorDomain] ||
                error.code != NSFileReadNoSuchFileError)
                throw std::runtime_error("Cannot read RallyLite settings.json");
        } else {
            try {
                if (data.length == 0 || data.length > 32768)
                    throw std::runtime_error("Invalid settings file size");
                const auto* begin = static_cast<const char*>(data.bytes);
                auto json = nlohmann::json::parse(begin, begin + data.length);
                if (!json.is_object()) throw std::runtime_error("Expected settings object");
                if (json.contains("nickname")) settings.nickname = json.at("nickname").get<std::string>();
                if (json.contains("hotkeys")) {
                    const auto& keys = json.at("hotkeys");
                    if (!keys.is_array() || keys.size() != 3)
                        throw std::runtime_error("Expected three hotkeys");
                    for (std::size_t i = 0; i < 3; ++i) settings.hotkeys[i] = keys.at(i).get<std::string>();
                }
            } catch (const std::exception&) {
                // JSON errors can quote user input; do not propagate their payloads.
                throw std::runtime_error("Invalid RallyLite settings.json; repair it or save settings again");
            }
        }
        if (const char* endpoint = std::getenv("RALLY_LITE_ENDPOINT"); endpoint && *endpoint)
            settings.endpoint = endpoint;
        validateSettings(settings);
        return settings;
    }
}

void save_settings(const Settings& settings) {
    @autoreleasepool {
        validateSettings(settings);
        NSURL* directory = settingsDirectory();
        NSError* error = nil;
        if (![[NSFileManager defaultManager] createDirectoryAtURL:directory
                                     withIntermediateDirectories:YES
                                                      attributes:@{NSFilePosixPermissions: @0700}
                                                           error:&error])
            throw std::runtime_error("Cannot create RallyLite settings directory");
        // Endpoint selection is process-local: removing a test override restores the default.
        nlohmann::json json = {{"nickname", settings.nickname},
                               {"hotkeys", settings.hotkeys}};
        const std::string encoded = json.dump(2) + "\n";
        NSData* data = [NSData dataWithBytes:encoded.data() length:encoded.size()];
        NSURL* url = [directory URLByAppendingPathComponent:@"settings.json"];
        // Foundation writes a sibling temporary file and atomically replaces the destination.
        // No truncation of the original occurs when the write/replacement fails.
        if (![data writeToURL:url options:NSDataWritingAtomic error:&error])
            throw std::runtime_error("Cannot atomically save RallyLite settings.json");
    }
}

std::string load_token(const std::string& endpoint) {
    @autoreleasepool {
        NSMutableDictionary* query = keychainQuery(endpoint);
        query[(__bridge id)kSecReturnData] = @YES;
        query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
        CFTypeRef result = nullptr;
        const OSStatus status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &result);
        id object = CFBridgingRelease(result);
        if (status == errSecItemNotFound) return {};
        if (status != errSecSuccess) throw keychainError("read", status);
        if (![object isKindOfClass:[NSData class]] || [(NSData*)object length] == 0)
            throw std::runtime_error("Invalid RallyLite Keychain item");
        NSData* data = (NSData*)object;
        return std::string(static_cast<const char*>(data.bytes), data.length);
    }
}

void save_token(const std::string& endpoint, const std::string& token) {
    @autoreleasepool {
        if (token.empty()) throw std::runtime_error("Cannot save an empty RallyLite credential");
        NSMutableDictionary* query = keychainQuery(endpoint);
        NSData* data = [NSData dataWithBytes:token.data() length:token.size()];
        NSDictionary* update = @{(__bridge id)kSecValueData: data,
            (__bridge id)kSecAttrAccessible: (__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly};
        OSStatus status = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)update);
        if (status == errSecItemNotFound) {
            NSMutableDictionary* item = [query mutableCopy];
            [item addEntriesFromDictionary:update];
            status = SecItemAdd((__bridge CFDictionaryRef)item, nullptr);
            // Another local process may have created the same endpoint account in between.
            if (status == errSecDuplicateItem)
                status = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)update);
        }
        if (status != errSecSuccess) throw keychainError("write", status);
    }
}
} // namespace rally
