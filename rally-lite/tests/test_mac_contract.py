"""Offline source contracts only; this does not compile or run AppKit/Keychain."""
import pathlib
import plistlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(name):
    path = ROOT / name
    if not path.is_file():
        raise AssertionError(f"Missing implementation: {path}")
    return path.read_text(encoding="utf-8")


def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


class MacContract(unittest.TestCase):
    def test_plist(self):
        value = plistlib.loads(source("Info.plist.in").encode())
        self.assertEqual(value["CFBundleIdentifier"], "app.offwork.rallylite")
        self.assertTrue(value["LSUIElement"])
        self.assertEqual(value["LSMinimumSystemVersion"], "12.0")
        self.assertEqual(value["CFBundlePackageType"], "APPL")
        self.assertTrue(value["CFBundleExecutable"])
        self.assertEqual(value["CFBundleName"], "RallyLite")

    def test_current_http_relay_is_allowed_by_ats(self):
        value = plistlib.loads(source("Info.plist.in").encode())
        self.assertTrue(value.get("NSAppTransportSecurity", {}).get("NSAllowsArbitraryLoads"),
                        'The existing relay uses HTTP, so the native macOS client must allow it')

    def test_native_arc_and_shared_contract(self):
        text = source("src/mac_app.mm")
        for required in ('#import <Cocoa/Cocoa.h>', '#import <Carbon/Carbon.h>',
                         '#include "client.hpp"', '__has_feature(objc_arc)',
                         'std::unique_ptr<rally::Client>', 'NSStatusItem',
                         'rally::parse_hotkey', 'rally::valid_name',
                         'EventKind::State', 'EventKind::Flash', 'EventKind::Fatal',
                         '_client->start()', '_client->join(', '_client->leave()',
                         '_client->alert('):
            self.assertIn(required, text)
        self.assertNotRegex(text, r'Qt|QWidget|WKWebView|UNUserNotification|NSUserNotification|NSSound|NSBeep')

    def test_hotkey_platform_mapping(self):
        text = source("src/mac_app.mm")
        for bit, carbon in ((1, 'controlKey'), (2, 'optionKey'), (4, 'cmdKey'), (8, 'shiftKey')):
            self.assertRegex(text, rf'modifiers\s*&\s*{bit}u?\)\s*result\s*\|=\s*{carbon}')
        for key in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
            self.assertRegex(text, rf"case '{key}':\s*return kVK_ANSI_{key};")
        for number in range(1, 13):
            self.assertRegex(text, rf'case 0x{0x6f + number:02X}:\s*return kVK_F{number};')
        self.assertIn('kEventHotKeyPressed', text)
        self.assertIn('kEventHotKeyReleased', text)
        self.assertIn('GetEventParameter', text)
        self.assertIn('RegisterEventHotKey', text)
        self.assertIn('UnregisterEventHotKey', text)
        self.assertIn('RemoveEventHandler', text)

    def test_hotkey_transaction_and_errors(self):
        text = source("src/mac_app.mm")
        transaction = section(text, '- (BOOL)applySettings:', '- (void)saveSettings:')
        self.assertLess(transaction.index('validateHotkeys:'), transaction.index('unregisterHotkeys'))
        self.assertLess(transaction.index('registerHotkeys:'), transaction.index('rally::save_settings'))
        self.assertLess(transaction.index('rally::save_settings'), transaction.index('_settings = candidate'))
        self.assertGreaterEqual(transaction.count('restoreHotkeys:'), 2)
        self.assertIn('catch (const std::exception&', transaction)
        self.assertIn('快捷键注册失败', text)
        self.assertIn('重复', text)
        self.assertIn('isKeyWindow', text)
        self.assertIn('_hotkeyDown', text)

    def test_main_queue_and_lifecycle(self):
        text = source("src/mac_app.mm")
        callback = section(text, 'auto sink =', '_client->start()')
        self.assertIn('rally::Event copy = event', callback)
        self.assertIn('dispatch_async(dispatch_get_main_queue()', callback)
        self.assertIn('__weak', text)
        close = section(text, '- (BOOL)windowShouldClose:', '- (void)joinRally:')
        self.assertIn('orderOut:', close)
        self.assertIn('return NO', close)
        terminate = section(text, '- (void)applicationWillTerminate:', '\n@end')
        for required in ('_stopping = YES', 'unregisterHotkeys', 'RemoveEventHandler', '_client->stop()', '_client.reset()', '[_overlay stop]'):
            self.assertIn(required, terminate)
        self.assertNotIn('activateIgnoringOtherApps:YES', text)

    def test_overlay_is_small_passive_and_timed(self):
        text = source("src/mac_app.mm")
        for required in ('NSWindowStyleMaskNonactivatingPanel', 'NSWindowStyleMaskBorderless',
                         'ignoresMouseEvents = YES', 'NSWindowCollectionBehaviorCanJoinAllSpaces',
                         'NSWindowCollectionBehaviorFullScreenAuxiliary',
                         'NSApplicationDidChangeScreenParametersNotification',
                         '[NSScreen screens]', 'visibleFrame', 'rally::alert_intensity',
                         'std::chrono::steady_clock', '5.0', '1.0 / 30.0',
                         'alphaValue', 'NSGradient', 'CGMainDisplayID()', 'RLEdgeThickness',
                         'constrainFrameRect:', 'return frameRect;'):
            self.assertIn(required, text)
        overlay = section(text, '@implementation RLOverlay', '@interface RLAppDelegate')
        self.assertNotIn('makeKeyAndOrderFront', overlay)
        self.assertNotIn('setNeedsDisplay', overlay)
        self.assertIn('invalidate', overlay)
        self.assertIn('return NO', section(text, '@implementation RLPassivePanel', '@end'))

    def test_storage_symbols_environment_and_validation(self):
        text = source("src/mac_storage.mm")
        for required in ('#include "client.hpp"', '#include <nlohmann/json.hpp>',
                         'namespace rally', 'Settings load_settings()',
                         'void save_settings(const Settings& settings)',
                         'std::string load_token(const std::string& endpoint)',
                         'void save_token(const std::string& endpoint, const std::string& token)',
                         'RALLY_LITE_HOME', 'RALLY_LITE_ENDPOINT', 'NSApplicationSupportDirectory',
                         '@"RallyLite"', '@"settings.json"', 'parse_hotkey', 'valid_name'):
            self.assertIn(required, text)

    def test_settings_atomic_and_secret_free(self):
        text = source("src/mac_storage.mm")
        save = section(text, 'void save_settings(', 'std::string load_token(')
        self.assertIn('NSDataWritingAtomic', save)
        self.assertIn('writeToURL:', save)
        self.assertIn('throw', save)
        self.assertNotIn('token', save)
        self.assertNotIn('NSLog', text)
        self.assertNotIn('std::cout', text)
        self.assertNotRegex(text, r'offwork_pet|identity\.json|pet_identity')

    def test_arc_delegate_lifetime_and_callback_generation(self):
        text = source("src/mac_app.mm")
        self.assertIn('objc_precise_lifetime', text)
        self.assertIn('generation != _clientGeneration', text)
        self.assertIn('receiveEvent:copy generation:generation', text)
        persist = section(text, 'auto persist =', 'auto sink =')
        self.assertIn('rally::save_token(endpoint, value)', persist)
        self.assertNotIn('catch', persist, 'Persistence failure must reach Client, not silently permit an ephemeral identity')
        self.assertIn('if (NSApp.isActive)', text)

    def test_storage_rejects_empty_data_and_does_not_persist_test_endpoint(self):
        text = source("src/mac_storage.mm")
        self.assertIn('data.length == 0', text)
        self.assertIn('32768', text)
        save = section(text, 'void save_settings(', 'std::string load_token(')
        self.assertNotIn('settings.endpoint', save, 'A temporary endpoint override must not become the next launch default')

    def test_keychain_service_endpoint_and_error_handling(self):
        text = source("src/mac_storage.mm")
        for required in ('#import <Security/Security.h>', '@"offwork.rally-lite"',
                         'kSecClassGenericPassword', 'kSecAttrService',
                         'kSecAttrAccount', 'kSecReturnData', 'kSecMatchLimitOne',
                         'SecItemCopyMatching', 'SecItemUpdate', 'SecItemAdd',
                         'errSecItemNotFound', 'errSecDuplicateItem',
                         'kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly',
                         'CFBridgingRelease', 'keychainQuery(endpoint)'):
            self.assertIn(required, text)
        read = section(text, 'std::string load_token(', 'void save_token(')
        self.assertRegex(read, r'errSecItemNotFound\)\s*return \{\}')
        self.assertIn('throw', read)


if __name__ == '__main__':
    unittest.main(verbosity=2)
