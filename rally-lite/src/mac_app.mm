#import <Cocoa/Cocoa.h>
#import <Carbon/Carbon.h>
#include "client.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <exception>
#include <optional>
#include <utility>

#if !__has_feature(objc_arc)
#error RallyLite macOS sources require Objective-C ARC (-fobjc-arc).
#endif

namespace {
using RLKeys = std::array<rally::Hotkey, 3>;
constexpr UInt32 RLHotkeySignature = 0x524C594C;
constexpr CGFloat RLEdgeThickness = 18.0;
constexpr double RLFlashDuration = 5.0;

NSString* RLText(const std::string& value) {
    return [[NSString alloc] initWithBytes:value.data() length:value.size()
                                 encoding:NSUTF8StringEncoding] ?: @"";
}

std::string RLUTF8(NSString* value) {
    NSData* bytes = [value dataUsingEncoding:NSUTF8StringEncoding];
    if (!bytes.length) return {};
    return std::string(static_cast<const char*>(bytes.bytes), bytes.length);
}

NSString* RLTrim(NSString* value) {
    return [value stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
}

UInt32 RLModifiers(unsigned modifiers) {
    UInt32 result = 0;
    if (modifiers & 1u) result |= controlKey;
    if (modifiers & 2u) result |= optionKey;
    if (modifiers & 4u) result |= cmdKey;
    if (modifiers & 8u) result |= shiftKey;
    return result;
}

// The shared contract is ASCII uppercase/digits, or Windows-style F1..F12 values.
// Carbon needs hardware virtual key codes, NOT ASCII or NSEvent modifier bits.
std::optional<UInt32> RLVirtualKey(unsigned key) {
    switch (key) {
        case 'A': return kVK_ANSI_A;
        case 'B': return kVK_ANSI_B;
        case 'C': return kVK_ANSI_C;
        case 'D': return kVK_ANSI_D;
        case 'E': return kVK_ANSI_E;
        case 'F': return kVK_ANSI_F;
        case 'G': return kVK_ANSI_G;
        case 'H': return kVK_ANSI_H;
        case 'I': return kVK_ANSI_I;
        case 'J': return kVK_ANSI_J;
        case 'K': return kVK_ANSI_K;
        case 'L': return kVK_ANSI_L;
        case 'M': return kVK_ANSI_M;
        case 'N': return kVK_ANSI_N;
        case 'O': return kVK_ANSI_O;
        case 'P': return kVK_ANSI_P;
        case 'Q': return kVK_ANSI_Q;
        case 'R': return kVK_ANSI_R;
        case 'S': return kVK_ANSI_S;
        case 'T': return kVK_ANSI_T;
        case 'U': return kVK_ANSI_U;
        case 'V': return kVK_ANSI_V;
        case 'W': return kVK_ANSI_W;
        case 'X': return kVK_ANSI_X;
        case 'Y': return kVK_ANSI_Y;
        case 'Z': return kVK_ANSI_Z;
        case '0': return kVK_ANSI_0;
        case '1': return kVK_ANSI_1;
        case '2': return kVK_ANSI_2;
        case '3': return kVK_ANSI_3;
        case '4': return kVK_ANSI_4;
        case '5': return kVK_ANSI_5;
        case '6': return kVK_ANSI_6;
        case '7': return kVK_ANSI_7;
        case '8': return kVK_ANSI_8;
        case '9': return kVK_ANSI_9;
        case 0x70: return kVK_F1;
        case 0x71: return kVK_F2;
        case 0x72: return kVK_F3;
        case 0x73: return kVK_F4;
        case 0x74: return kVK_F5;
        case 0x75: return kVK_F6;
        case 0x76: return kVK_F7;
        case 0x77: return kVK_F8;
        case 0x78: return kVK_F9;
        case 0x79: return kVK_F10;
        case 0x7A: return kVK_F11;
        case 0x7B: return kVK_F12;
        default: return std::nullopt;
    }
}

NSColor* RLColor(const rally::Flash& flash) {
    NSString* hex = RLText(flash.color);
    unsigned int rgb = 0;
    NSScanner* scanner = [NSScanner scannerWithString:hex.length == 7 ? [hex substringFromIndex:1] : @""];
    scanner.charactersToBeSkipped = nil;
    if (hex.length == 7 && [hex hasPrefix:@"#"] && [scanner scanHexInt:&rgb] && scanner.isAtEnd)
        return [NSColor colorWithSRGBRed:((rgb >> 16) & 255) / 255.0
                                  green:((rgb >> 8) & 255) / 255.0
                                   blue:(rgb & 255) / 255.0 alpha:1.0];
    if (flash.level == "danger") return [NSColor colorWithSRGBRed:1.0 green:0.44 blue:0.47 alpha:1.0];
    if (flash.level == "warning") return [NSColor colorWithSRGBRed:1.0 green:0.8 blue:0.35 alpha:1.0];
    return [NSColor colorWithSRGBRed:0.4 green:0.85 blue:0.65 alpha:1.0];
}

NSTextField* RLLabel(NSView* parent, NSString* text, NSRect frame) {
    NSTextField* label = [NSTextField labelWithString:text];
    label.frame = frame;
    label.font = [NSFont systemFontOfSize:13];
    [parent addSubview:label];
    return label;
}

NSTextField* RLField(NSView* parent, NSString* value, NSString* placeholder, NSRect frame) {
    NSTextField* field = [[NSTextField alloc] initWithFrame:frame];
    field.stringValue = value;
    field.placeholderString = placeholder;
    field.font = [NSFont systemFontOfSize:13];
    field.usesSingleLineMode = YES;
    field.lineBreakMode = NSLineBreakByTruncatingTail;
    [parent addSubview:field];
    return field;
}

NSButton* RLButton(NSView* parent, NSString* title, NSRect frame, id target, SEL action) {
    NSButton* button = [NSButton buttonWithTitle:title target:target action:action];
    button.frame = frame;
    button.bezelStyle = NSBezelStyleRounded;
    [parent addSubview:button];
    return button;
}
} // namespace

@interface RLPassivePanel : NSPanel
@end
@implementation RLPassivePanel
- (BOOL)canBecomeKeyWindow { return NO; }
- (BOOL)canBecomeMainWindow { return NO; }
- (NSRect)constrainFrameRect:(NSRect)frameRect toScreen:(NSScreen*)screen {
    (void)screen;
    // Edge strips belong to the physical display bounds, not the menu/Dock work area.
    return frameRect;
}
@end

@interface RLEdgeView : NSView
@property(nonatomic, strong) NSColor* color;
@property(nonatomic) NSUInteger edge;
@end
@implementation RLEdgeView
- (BOOL)isOpaque { return NO; }
- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    NSRect bounds = self.bounds;
    NSPoint start, end;
    NSRect line = bounds;
    switch (self.edge) {
        case 0: // top, outside to inside
            start = NSMakePoint(0, NSMaxY(bounds)); end = NSMakePoint(0, NSMinY(bounds));
            line.origin.y = NSMaxY(bounds) - 1.5; line.size.height = 1.5; break;
        case 1: // bottom
            start = NSMakePoint(0, NSMinY(bounds)); end = NSMakePoint(0, NSMaxY(bounds));
            line.size.height = 1.5; break;
        case 2: // left
            start = NSMakePoint(NSMinX(bounds), 0); end = NSMakePoint(NSMaxX(bounds), 0);
            line.size.width = 1.5; break;
        default: // right
            start = NSMakePoint(NSMaxX(bounds), 0); end = NSMakePoint(NSMinX(bounds), 0);
            line.origin.x = NSMaxX(bounds) - 1.5; line.size.width = 1.5; break;
    }
    NSGradient* gradient = [[NSGradient alloc] initWithStartingColor:[self.color colorWithAlphaComponent:0.28]
                                                       endingColor:[self.color colorWithAlphaComponent:0.0]];
    [gradient drawFromPoint:start toPoint:end options:0];
    [[self.color colorWithAlphaComponent:0.42] setFill];
    NSRectFillUsingOperation(line, NSCompositingOperationSourceOver);
}
@end

@interface RLCaptionView : NSView
@property(nonatomic, strong) NSColor* color;
@property(nonatomic, copy) NSString* text;
@end
@implementation RLCaptionView
- (BOOL)isOpaque { return NO; }
- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    [[NSColor colorWithSRGBRed:0.06 green:0.07 blue:0.09 alpha:0.55] setFill];
    [[NSBezierPath bezierPathWithRoundedRect:self.bounds xRadius:8 yRadius:8] fill];
    [[self.color colorWithAlphaComponent:0.85] setFill];
    [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(9, 9, 5, 5)] fill];
    NSMutableParagraphStyle* paragraph = [[NSMutableParagraphStyle alloc] init];
    paragraph.lineBreakMode = NSLineBreakByTruncatingTail;
    NSDictionary* attributes = @{NSFontAttributeName: [NSFont systemFontOfSize:10],
        NSForegroundColorAttributeName: [[NSColor whiteColor] colorWithAlphaComponent:0.75],
        NSParagraphStyleAttributeName: paragraph};
    [self.text drawInRect:NSMakeRect(20, 5, self.bounds.size.width - 28, 15) withAttributes:attributes];
}
@end

@interface RLOverlay : NSObject {
    NSMutableArray<RLPassivePanel*>* _panels;
    NSTimer* _timer;
    std::chrono::steady_clock::time_point _started;
    rally::Flash _flash;
}
- (void)showFlash:(const rally::Flash&)flash;
- (void)stop;
@end

@implementation RLOverlay
- (instancetype)init {
    if ((self = [super init])) {
        _panels = [NSMutableArray array];
        [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(screensChanged:)
            name:NSApplicationDidChangeScreenParametersNotification object:nil];
    }
    return self;
}
- (void)dealloc {
    [_timer invalidate];
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}
- (void)clearPanels {
    for (RLPassivePanel* panel in _panels) { [panel orderOut:nil]; [panel close]; }
    [_panels removeAllObjects];
}
- (void)stop {
    [_timer invalidate];
    _timer = nil;
    [self clearPanels];
}
- (void)addPanel:(NSRect)frame view:(NSView*)view {
    RLPassivePanel* panel = [[RLPassivePanel alloc] initWithContentRect:frame
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        backing:NSBackingStoreBuffered defer:NO];
    panel.opaque = NO;
    panel.backgroundColor = [NSColor clearColor];
    panel.hasShadow = NO;
    panel.ignoresMouseEvents = YES;
    panel.hidesOnDeactivate = NO;
    panel.releasedWhenClosed = NO;
    panel.becomesKeyOnlyIfNeeded = YES;
    panel.level = NSStatusWindowLevel + 1;
    panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
        NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorStationary |
        NSWindowCollectionBehaviorIgnoresCycle;
    panel.animationBehavior = NSWindowAnimationBehaviorNone;
    panel.contentView = view;
    panel.alphaValue = 0;
    [_panels addObject:panel];
    [panel orderFrontRegardless]; // Does not activate the app or make a key window.
}
- (void)buildPanels {
    [self clearPanels];
    NSColor* color = RLColor(_flash);
    NSScreen* primary = nil;
    for (NSScreen* screen in [NSScreen screens]) {
        if ([screen.deviceDescription[@"NSScreenNumber"] unsignedIntValue] == CGMainDisplayID()) primary = screen;
        const NSRect r = screen.frame;
        const CGFloat t = std::min(RLEdgeThickness, std::min(r.size.width, r.size.height) / 4.0);
        const std::array<NSRect, 4> edges{{
            NSMakeRect(NSMinX(r), NSMaxY(r) - t, r.size.width, t),
            NSMakeRect(NSMinX(r), NSMinY(r), r.size.width, t),
            NSMakeRect(NSMinX(r), NSMinY(r) + t, t, r.size.height - 2 * t),
            NSMakeRect(NSMaxX(r) - t, NSMinY(r) + t, t, r.size.height - 2 * t)}};
        for (NSUInteger edge = 0; edge < edges.size(); ++edge) {
            RLEdgeView* view = [[RLEdgeView alloc] initWithFrame:NSMakeRect(0, 0, edges[edge].size.width, edges[edge].size.height)];
            view.edge = edge;
            view.color = color;
            [self addPanel:edges[edge] view:view];
        }
    }
    primary = primary ?: [NSScreen screens].firstObject;
    if (!primary) return;
    NSString* label = RLText(_flash.label);
    if (!label.length) label = _flash.level == "danger" ? @"危险" : (_flash.level == "warning" ? @"警戒" : @"安全");
    NSString* caption = _flash.source.empty() ? label : [NSString stringWithFormat:@"%@ · 来自 %@", label, RLText(_flash.source)];
    caption = [[caption componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]] componentsJoinedByString:@" "];
    NSRect available = primary.visibleFrame;
    CGFloat width = std::min<CGFloat>(320, [caption sizeWithAttributes:@{NSFontAttributeName: [NSFont systemFontOfSize:10]}].width + 32);
    width = std::max<CGFloat>(28, std::min(width, available.size.width - 24));
    RLCaptionView* view = [[RLCaptionView alloc] initWithFrame:NSMakeRect(0, 0, width, 24)];
    view.color = color;
    view.text = caption;
    [self addPanel:NSMakeRect(NSMaxX(available) - width - 12, NSMinY(available) + 12, width, 24) view:view];
}
- (void)tick {
    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - _started).count();
    if (elapsed >= RLFlashDuration) { [self stop]; return; }
    const double alpha = std::clamp(rally::alert_intensity(elapsed, RLFlashDuration, _flash.level), 0.0, 1.0);
    // Static, buffered narrow views: only WindowServer opacity changes at 30 Hz.
    for (RLPassivePanel* panel in _panels) panel.alphaValue = alpha;
}
- (void)showFlash:(const rally::Flash&)flash {
    [self stop];
    _flash = flash;
    _started = std::chrono::steady_clock::now();
    [self buildPanels];
    __weak RLOverlay* weakSelf = self;
    _timer = [NSTimer timerWithTimeInterval:1.0 / 30.0 repeats:YES block:^(NSTimer* timer) {
        (void)timer;
        [weakSelf tick];
    }];
    _timer.tolerance = 0.005;
    [[NSRunLoop mainRunLoop] addTimer:_timer forMode:NSRunLoopCommonModes];
}
- (void)screensChanged:(NSNotification*)notification {
    (void)notification;
    if (_timer) { [self buildPanels]; [self tick]; }
}
@end

@interface RLAppDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate, NSTextFieldDelegate> {
    NSStatusItem* _statusItem;
    NSMenuItem* _menuState;
    NSWindow* _window;
    NSTextField* _nickname;
    NSTextField* _code;
    NSTextField* _connectionLabel;
    NSTextField* _rallyLabel;
    NSTextField* _hotkeyStatus;
    NSTextField* _messageLabel;
    NSArray<NSTextField*>* _hotkeyFields;
    NSArray<NSButton*>* _sendButtons;
    NSButton* _joinButton;
    NSButton* _leaveButton;
    RLOverlay* _overlay;
    rally::Settings _settings;
    rally::Snapshot _state;
    std::unique_ptr<rally::Client> _client;
    std::array<EventHotKeyRef, 3> _hotkeyRefs;
    std::array<UInt32, 3> _hotkeyIDs;
    std::array<bool, 3> _hotkeyDown;
    RLKeys _activeKeys;
    EventHandlerRef _eventHandler;
    UInt32 _nextHotkeyID;
    UInt64 _clientGeneration;
    BOOL _hotkeysRegistered;
    BOOL _stopping;
}
- (void)receiveHotkey:(UInt32)identifier pressed:(BOOL)pressed;
- (void)receiveEvent:(const rally::Event&)event generation:(UInt64)generation;
- (void)showSettings:(id)sender;
- (void)quit:(id)sender;
@end

static OSStatus RLHotkeyHandler(EventHandlerCallRef next, EventRef event, void* context) {
    (void)next;
    EventHotKeyID identifier{};
    const OSStatus status = GetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID,
        nullptr, sizeof(identifier), nullptr, &identifier);
    if (status != noErr || identifier.signature != RLHotkeySignature) return eventNotHandledErr;
    const BOOL pressed = GetEventKind(event) == kEventHotKeyPressed;
    __weak RLAppDelegate* delegate = (__bridge RLAppDelegate*)context;
    dispatch_async(dispatch_get_main_queue(), ^{ [delegate receiveHotkey:identifier.id pressed:pressed]; });
    return noErr;
}

@implementation RLAppDelegate
- (void)buildMenu {
    _statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    _statusItem.button.title = @"集";
    _statusItem.button.toolTip = @"RallyLite · 集会提醒";
    NSMenu* menu = [[NSMenu alloc] initWithTitle:@"RallyLite"];
    menu.autoenablesItems = NO;
    _menuState = [menu addItemWithTitle:@"尚未连接" action:nil keyEquivalent:@""];
    _menuState.enabled = NO;
    [menu addItem:[NSMenuItem separatorItem]];
    NSMenuItem* settings = [menu addItemWithTitle:@"设置…" action:@selector(showSettings:) keyEquivalent:@","];
    settings.target = self;
    [menu addItem:[NSMenuItem separatorItem]];
    NSMenuItem* quit = [menu addItemWithTitle:@"退出 RallyLite" action:@selector(quit:) keyEquivalent:@"q"];
    quit.target = self;
    _statusItem.menu = menu;

    // Native responder-chain editing commands for nickname/code/hotkey text fields.
    NSMenu* main = [[NSMenu alloc] initWithTitle:@""];
    NSMenuItem* appItem = [[NSMenuItem alloc] initWithTitle:@"RallyLite" action:nil keyEquivalent:@""];
    NSMenu* appMenu = [[NSMenu alloc] initWithTitle:@"RallyLite"];
    NSMenuItem* open = [appMenu addItemWithTitle:@"设置…" action:@selector(showSettings:) keyEquivalent:@","];
    open.target = self;
    NSMenuItem* exit = [appMenu addItemWithTitle:@"退出 RallyLite" action:@selector(quit:) keyEquivalent:@"q"];
    exit.target = self;
    appItem.submenu = appMenu;
    [main addItem:appItem];
    NSMenuItem* editItem = [[NSMenuItem alloc] initWithTitle:@"编辑" action:nil keyEquivalent:@""];
    NSMenu* edit = [[NSMenu alloc] initWithTitle:@"编辑"];
    [edit addItemWithTitle:@"撤销" action:@selector(undo:) keyEquivalent:@"z"];
    [edit addItemWithTitle:@"剪切" action:@selector(cut:) keyEquivalent:@"x"];
    [edit addItemWithTitle:@"复制" action:@selector(copy:) keyEquivalent:@"c"];
    [edit addItemWithTitle:@"粘贴" action:@selector(paste:) keyEquivalent:@"v"];
    [edit addItemWithTitle:@"全选" action:@selector(selectAll:) keyEquivalent:@"a"];
    editItem.submenu = edit;
    [main addItem:editItem];
    NSApp.mainMenu = main;
}
- (void)buildWindow {
    _window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 560, 530)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        backing:NSBackingStoreBuffered defer:NO];
    _window.title = @"RallyLite · 集会提醒";
    _window.releasedWhenClosed = NO;
    _window.delegate = self;
    [_window center];
    NSView* view = _window.contentView;
    NSTextField* heading = RLLabel(view, @"只在需要时，轻轻提醒同伴", NSMakeRect(24, 489, 512, 24));
    heading.font = [NSFont systemFontOfSize:17 weight:NSFontWeightSemibold];
    RLLabel(view, @"昵称", NSMakeRect(24, 446, 76, 20));
    _nickname = RLField(view, RLText(_settings.nickname), @"最多 24 个 Unicode 字符", NSMakeRect(110, 442, 426, 25));
    RLLabel(view, @"集会码", NSMakeRect(24, 402, 76, 20));
    _code = RLField(view, @"", @"8 位字母或数字", NSMakeRect(110, 398, 170, 25));
    _joinButton = RLButton(view, @"加入", NSMakeRect(295, 395, 110, 30), self, @selector(joinRally:));
    _leaveButton = RLButton(view, @"退出集会", NSMakeRect(415, 395, 121, 30), self, @selector(leaveRally:));
    _connectionLabel = RLLabel(view, @"连接：准备中", NSMakeRect(24, 355, 512, 30));
    _connectionLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    _rallyLabel = RLLabel(view, @"集会：尚未加入", NSMakeRect(24, 310, 512, 40));
    _rallyLabel.maximumNumberOfLines = 2;
    _rallyLabel.lineBreakMode = NSLineBreakByWordWrapping;
    NSMutableArray<NSButton*>* sends = [NSMutableArray array];
    NSArray<NSString*>* levels = @[@"安全", @"警戒", @"危险"];
    for (NSInteger i = 0; i < 3; ++i) {
        NSButton* button = RLButton(view, levels[i], NSMakeRect(24 + i * 176, 267, 160, 32), self, @selector(sendAlert:));
        button.tag = i;
        [sends addObject:button];
    }
    _sendButtons = sends;
    NSTextField* hint = RLLabel(view, @"快捷键：Ctrl / Alt / Cmd / Shift + 字母、数字或 F1–F12", NSMakeRect(24, 233, 512, 22));
    hint.font = [NSFont systemFontOfSize:11];
    NSMutableArray<NSTextField*>* fields = [NSMutableArray array];
    for (NSInteger i = 0; i < 3; ++i) {
        CGFloat y = 198 - i * 38;
        RLLabel(view, levels[i], NSMakeRect(24, y + 3, 76, 20));
        NSTextField* field = RLField(view, RLText(_settings.hotkeys[i]), @"例如 Ctrl+Alt+G", NSMakeRect(110, y, 266, 25));
        field.delegate = self;
        [fields addObject:field];
    }
    _hotkeyFields = fields;
    _nickname.delegate = self;
    _code.delegate = self;
    RLButton(view, @"保存设置", NSMakeRect(399, 120, 137, 30), self, @selector(saveSettings:));
    _hotkeyStatus = RLLabel(view, @"", NSMakeRect(24, 59, 512, 54));
    _hotkeyStatus.maximumNumberOfLines = 3;
    _hotkeyStatus.lineBreakMode = NSLineBreakByWordWrapping;
    _hotkeyStatus.font = [NSFont systemFontOfSize:11];
    _messageLabel = RLLabel(view, @"关闭此窗口后，仍在菜单栏接收提醒。", NSMakeRect(24, 10, 512, 44));
    _messageLabel.maximumNumberOfLines = 3;
    _messageLabel.lineBreakMode = NSLineBreakByWordWrapping;
    _messageLabel.font = [NSFont systemFontOfSize:11];
}
- (void)showMessage:(NSString*)message error:(BOOL)error {
    _messageLabel.stringValue = message;
    _messageLabel.textColor = error ? [NSColor systemRedColor] : [NSColor secondaryLabelColor];
    _messageLabel.toolTip = message;
}
- (BOOL)validateHotkeys:(const rally::Settings&)candidate keys:(RLKeys&)keys error:(NSString* __autoreleasing*)error {
    for (std::size_t i = 0; i < keys.size(); ++i) {
        auto parsed = rally::parse_hotkey(candidate.hotkeys[i]);
        if (!parsed || !RLVirtualKey(parsed->key) || parsed->modifiers == 0 || (parsed->modifiers & ~15u)) {
            *error = [NSString stringWithFormat:@"第 %lu 个快捷键无效，请使用 Ctrl/Alt/Cmd/Shift + 字母、数字或 F1–F12。", (unsigned long)i + 1];
            return NO;
        }
        keys[i] = *parsed;
        for (std::size_t j = 0; j < i; ++j) {
            if (keys[i].key == keys[j].key && keys[i].modifiers == keys[j].modifiers) {
                *error = @"三个快捷键不能重复，原设置未修改。";
                return NO;
            }
        }
    }
    return YES;
}
- (void)unregisterHotkeys {
    for (std::size_t i = 0; i < _hotkeyRefs.size(); ++i) {
        _hotkeyIDs[i] = 0; // Discard queued events from replaced registrations.
        _hotkeyDown[i] = false;
        if (_hotkeyRefs[i]) {
            OSStatus status = UnregisterEventHotKey(_hotkeyRefs[i]);
            if (status != noErr)
                [self showMessage:[NSString stringWithFormat:@"释放快捷键失败（OSStatus %d），请退出后重试。", (int)status] error:YES];
            _hotkeyRefs[i] = nullptr;
        }
    }
    _hotkeysRegistered = NO;
}
- (BOOL)registerHotkeys:(const RLKeys&)keys error:(NSString* __autoreleasing*)error {
    if (!_eventHandler) {
        *error = @"快捷键注册失败：全局事件处理器不可用，请重新启动。";
        return NO;
    }
    for (std::size_t i = 0; i < keys.size(); ++i) {
        EventHotKeyID identifier{RLHotkeySignature, ++_nextHotkeyID};
        const OSStatus status = RegisterEventHotKey(*RLVirtualKey(keys[i].key), RLModifiers(keys[i].modifiers),
            identifier, GetApplicationEventTarget(), 0, &_hotkeyRefs[i]);
        if (status != noErr) {
            *error = [NSString stringWithFormat:@"第 %lu 个快捷键注册失败（OSStatus %d），可能被系统或其他应用占用；请修改后保存。", (unsigned long)i + 1, (int)status];
            [self unregisterHotkeys];
            return NO;
        }
        _hotkeyIDs[i] = identifier.id;
    }
    _activeKeys = keys;
    _hotkeysRegistered = YES;
    return YES;
}
- (void)restoreHotkeys:(const RLKeys&)previous wasRegistered:(BOOL)registered message:(NSString*)message {
    [self unregisterHotkeys];
    NSString* restoreError = nil;
    if (registered && ![self registerHotkeys:previous error:&restoreError])
        message = [message stringByAppendingFormat:@"\n旧快捷键恢复失败：%@", restoreError];
    else if (registered)
        message = [message stringByAppendingString:@"\n已恢复旧快捷键，原设置未修改。"];
    else
        message = [message stringByAppendingString:@"\n快捷键当前未启用；仍可使用发送按钮。"];
    _hotkeyStatus.stringValue = message;
    _hotkeyStatus.toolTip = message;
    _hotkeyStatus.textColor = [NSColor systemRedColor];
}
- (BOOL)applySettings:(const rally::Settings&)candidate persist:(BOOL)persist {
    if (!candidate.nickname.empty() && !rally::valid_name(candidate.nickname)) {
        [self showMessage:@"昵称须为 1–24 个 Unicode 字符，不能包含控制字符。" error:YES];
        return NO;
    }
    RLKeys keys{};
    NSString* error = nil;
    if (![self validateHotkeys:candidate keys:keys error:&error]) {
        _hotkeyStatus.stringValue = error;
        _hotkeyStatus.toolTip = error;
        _hotkeyStatus.textColor = [NSColor systemRedColor];
        return NO;
    }
    const RLKeys previous = _activeKeys;
    const BOOL registered = _hotkeysRegistered;
    [self unregisterHotkeys];
    if (![self registerHotkeys:keys error:&error]) {
        [self restoreHotkeys:previous wasRegistered:registered message:error];
        return NO;
    }
    try {
        if (persist) rally::save_settings(candidate);
    } catch (const std::exception& failure) {
        [self restoreHotkeys:previous wasRegistered:registered
                    message:[@"保存失败：" stringByAppendingString:RLText(failure.what())]];
        return NO;
    }
    _settings = candidate;
    _hotkeyStatus.stringValue = @"三个全局快捷键已注册。设置窗口激活时暂停快捷键发送，避免编辑时误触发。";
    _hotkeyStatus.toolTip = _hotkeyStatus.stringValue;
    _hotkeyStatus.textColor = [NSColor secondaryLabelColor];
    return YES;
}
- (void)saveSettings:(id)sender {
    (void)sender;
    rally::Settings candidate = _settings;
    candidate.nickname = RLUTF8(RLTrim(_nickname.stringValue));
    for (std::size_t i = 0; i < 3; ++i) candidate.hotkeys[i] = RLUTF8(RLTrim(_hotkeyFields[i].stringValue));
    if ([self applySettings:candidate persist:YES]) {
        [self showMessage:@"设置已保存。昵称在下次加入集会时使用。" error:NO];
        if (!_client || !_state.ready) {
            ++_clientGeneration; // Ignore already queued callbacks from a stopped connection.
            if (_client) { _client->stop(); _client.reset(); }
            _state = {};
            [self startClient];
        }
    }
}
- (void)receiveHotkey:(UInt32)identifier pressed:(BOOL)pressed {
    if (_stopping) return;
    for (std::size_t i = 0; i < _hotkeyIDs.size(); ++i) {
        if (!_hotkeyIDs[i] || _hotkeyIDs[i] != identifier) continue;
        if (!pressed) { _hotkeyDown[i] = false; return; }
        if (_hotkeyDown[i]) return;
        _hotkeyDown[i] = true;
        // Suppress the entire focused settings window, including IME/field editor sessions.
        if (NSApp.isActive) {
            if ([_window isKeyWindow] || _nickname.currentEditor || _code.currentEditor) return;
            for (NSTextField* field in _hotkeyFields) if (field.currentEditor) return;
        }
        [self broadcast:(int)i];
        return;
    }
}
- (void)updateState {
    NSString* status = _state.status.empty() ? (_state.ready ? @"已连接" : @"正在连接…") : RLText(_state.status);
    _connectionLabel.stringValue = [@"连接：" stringByAppendingString:status];
    _connectionLabel.toolTip = _connectionLabel.stringValue;
    const BOOL joined = !_state.rally_id.empty();
    _rallyLabel.stringValue = joined ? [NSString stringWithFormat:@"集会：%@ · %d 人%@\n集会码：%@",
        RLText(_state.rally_name), _state.members, _state.owner ? @" · 创建者" : @"", RLText(_state.code)] : @"集会：尚未加入";
    _rallyLabel.toolTip = _rallyLabel.stringValue;
    _menuState.title = _state.ready ? (joined ? @"已连接 · 已加入集会" : @"已连接 · 尚未加入集会") : status;
    _statusItem.button.toolTip = [NSString stringWithFormat:@"RallyLite · %@", _menuState.title];
    _joinButton.enabled = _client && _state.ready && !joined;
    _leaveButton.enabled = _client && _state.ready && joined;
    for (NSButton* button in _sendButtons) button.enabled = _client && _state.ready && joined;
}
- (void)receiveEvent:(const rally::Event&)event generation:(UInt64)generation {
    if (_stopping || generation != _clientGeneration) return;
    switch (event.kind) {
        case rally::EventKind::State:
            _state = event.state;
            [self updateState];
            break;
        case rally::EventKind::Flash:
            [_overlay showFlash:event.flash];
            break;
        case rally::EventKind::Fatal:
            _state = event.state;
            _state.ready = false;
            [self updateState];
            [self showMessage:_state.status.empty() ? @"连接失败，请检查服务兼容性和本地设置。" : RLText(_state.status) error:YES];
            break;
        case rally::EventKind::Send:
            break; // Client owns all transport/protocol work; never expose wire payloads.
    }
}
- (void)startClient {
    if (_client || _stopping) return;
    const UInt64 generation = ++_clientGeneration;
    try {
        const std::string endpoint = _settings.endpoint;
        const std::string token = rally::load_token(endpoint);
        __weak RLAppDelegate* weakSelf = self;
        auto persist = [endpoint](const std::string& value) {
            // Client reports persistence failure and aborts connection; never silently use an unsaved identity.
            rally::save_token(endpoint, value);
        };
        auto sink = [weakSelf, generation](const rally::Event& event) {
            rally::Event copy = event; // Client's event reference expires before the main-queue block runs.
            dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf receiveEvent:copy generation:generation]; });
        };
        _client = std::make_unique<rally::Client>(endpoint, token, std::move(persist), std::move(sink));
        _client->start();
        [self updateState];
    } catch (const std::exception&) {
        if (_client) { _client->stop(); _client.reset(); }
        _state.ready = false;
        _state.status = "无法启动连接";
        [self updateState];
        [self showMessage:@"无法读取钥匙串或启动连接；不会忽略旧身份重新注册。修复后点击“保存设置”重试。" error:YES];
        [self showSettings:nil];
    }
}
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    (void)notification;
    _hotkeyRefs.fill(nullptr);
    _hotkeyIDs.fill(0);
    _hotkeyDown.fill(false);
    _activeKeys = {};
    BOOL loaded = YES;
    NSString* loadError = nil;
    try { _settings = rally::load_settings(); }
    catch (const std::exception& failure) {
        loaded = NO;
        loadError = [@"读取设置失败；修复文件或重新保存后连接。" stringByAppendingString:RLText(failure.what())];
        // Preserve local-test routing even when a corrupt settings file needs repair.
        if (const char* endpoint = std::getenv("RALLY_LITE_ENDPOINT"); endpoint && *endpoint) _settings.endpoint = endpoint;
    }
    [self buildMenu];
    [self buildWindow];
    _overlay = [[RLOverlay alloc] init];
    const EventTypeSpec events[] = {{kEventClassKeyboard, kEventHotKeyPressed},
                                    {kEventClassKeyboard, kEventHotKeyReleased}};
    OSStatus status = InstallApplicationEventHandler(&RLHotkeyHandler, 2, events, (__bridge void*)self, &_eventHandler);
    BOOL registered = NO;
    if (status != noErr) {
        _hotkeyStatus.stringValue = [NSString stringWithFormat:@"快捷键注册失败：事件处理器安装失败（OSStatus %d）。发送按钮仍可使用。", (int)status];
        _hotkeyStatus.textColor = [NSColor systemRedColor];
    } else {
        registered = [self applySettings:_settings persist:NO];
    }
    [self updateState];
    if (loaded) [self startClient];
    else {
        _state.status = "未连接：设置需要修复";
        [self updateState];
        [self showMessage:loadError error:YES];
    }
    if (!loaded || _settings.nickname.empty() || !registered) [self showSettings:nil];
}
- (void)showSettings:(id)sender {
    if (sender) [NSApp activateIgnoringOtherApps:NO];
    [_window makeKeyAndOrderFront:nil];
}
- (BOOL)applicationShouldHandleReopen:(NSApplication*)application hasVisibleWindows:(BOOL)visible {
    (void)application; (void)visible;
    [self showSettings:self];
    return YES;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)sender {
    (void)sender;
    return NO;
}
- (BOOL)windowShouldClose:(NSWindow*)sender {
    [sender makeFirstResponder:nil];
    [sender orderOut:nil];
    return NO;
}
- (void)joinRally:(id)sender {
    (void)sender;
    if (!_client || !_state.ready) return;
    NSString* codeText = [RLTrim(_code.stringValue) uppercaseString];
    const std::string code = RLUTF8(codeText);
    const std::string nickname = RLUTF8(RLTrim(_nickname.stringValue));
    if (nickname.empty() || !rally::valid_name(nickname)) {
        [self showMessage:@"请输入 1–24 个 Unicode 字符的昵称，不能包含控制字符。" error:YES]; return;
    }
    if (code.size() != 8 || !std::all_of(code.begin(), code.end(), [](unsigned char c) {
        return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
    })) {
        [self showMessage:@"集会码必须为 8 位英文字母或数字。" error:YES]; return;
    }
    rally::Settings candidate = _settings;
    candidate.nickname = nickname;
    try {
        rally::save_settings(candidate);
        _settings = candidate;
        _code.stringValue = codeText;
        _client->join(code, nickname);
        [self showMessage:@"已请求加入集会。" error:NO];
    } catch (const std::exception&) {
        [self showMessage:@"保存昵称或加入集会失败，请检查本地设置目录权限后重试。" error:YES];
    }
}
- (void)leaveRally:(id)sender {
    (void)sender;
    if (!_client || !_state.ready || _state.rally_id.empty()) return;
    try { _client->leave(); }
    catch (const std::exception&) { [self showMessage:@"退出集会失败，请重试。" error:YES]; }
}
- (void)broadcast:(int)index {
    if (_stopping || !_client || !_state.ready || _state.rally_id.empty() || index < 0 || index > 2) return;
    try { _client->alert(index); }
    catch (const std::exception&) { [self showMessage:@"发送失败，请检查连接状态。" error:YES]; }
}
- (void)sendAlert:(NSButton*)sender { [self broadcast:(int)sender.tag]; }
- (void)controlTextDidChange:(NSNotification*)notification {
    (void)notification;
    // Do not truncate UTF-16/IME marked text. The shared Unicode validator runs on save/join.
    [self updateState];
}
- (void)quit:(id)sender { (void)sender; [NSApp terminate:nil]; }
- (void)applicationWillTerminate:(NSNotification*)notification {
    (void)notification;
    _stopping = YES;
    [self unregisterHotkeys];
    if (_eventHandler) { RemoveEventHandler(_eventHandler); _eventHandler = nullptr; }
    [_overlay stop];
    if (_client) { _client->stop(); _client.reset(); }
    [[NSStatusBar systemStatusBar] removeStatusItem:_statusItem];
}
@end

int main(int argc, const char* argv[]) {
    (void)argc; (void)argv;
    @autoreleasepool {
        NSApplication* application = [NSApplication sharedApplication];
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        __attribute__((objc_precise_lifetime)) RLAppDelegate* delegate = [[RLAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
        // Keep the weak application delegate alive throughout the run loop under ARC.
        application.delegate = nil;
        (void)delegate;
    }
    return 0;
}
