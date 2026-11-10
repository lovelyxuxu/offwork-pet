#include "client.hpp"
#include "win_common.hpp"
#include <shellapi.h>
#include <objidl.h>
#include <gdiplus.h>
#include <algorithm>
#include <chrono>
#include <set>
#include <vector>

using namespace rally;
namespace {
constexpr UINT incoming_message = WM_APP + 1, tray_message = WM_APP + 2;
constexpr int join_id = 100, leave_id = 101, save_id = 102, quit_id = 103, show_id = 104;
const wchar_t* level_names[] = {L"安全", L"警戒", L"危险"};
struct Layer {
    HWND window = nullptr;
    HDC dc = nullptr;
    HBITMAP bitmap = nullptr;
    HGDIOBJ previous = nullptr;
    POINT origin{};
    SIZE size{};
    ~Layer() {
        if (window) DestroyWindow(window);
        if (previous) SelectObject(dc, previous);
        if (bitmap) DeleteObject(bitmap);
        if (dc) DeleteDC(dc);
    }
    void show(BYTE alpha) {
        POINT source{};
        BLENDFUNCTION blend{AC_SRC_OVER, 0, alpha, AC_SRC_ALPHA};
        UpdateLayeredWindow(window, nullptr, &origin, &size, dc, &source, 0, &blend, ULW_ALPHA);
        SetWindowPos(window, HWND_TOPMOST, origin.x, origin.y, size.cx, size.cy, SWP_NOACTIVATE | SWP_SHOWWINDOW);
    }
};
struct App {
    HWND window{}, nickname{}, code{}, status{}, group{}, hotkey_status{}, join_button{}, leave_button{};
    HWND shortcuts[3]{}, send_buttons[3]{};
    Settings settings;
    Snapshot snapshot;
    std::unique_ptr<Client> client;
    std::vector<std::unique_ptr<Layer>> layers;
    Flash active;
    std::chrono::steady_clock::time_point started;
    HFONT font{}, title_font{};
    NOTIFYICONDATAW tray{};
    UINT taskbar_created = RegisterWindowMessageW(L"TaskbarCreated");
    int dpi = 96;
    bool closing = false;
    int px(int value) const { return MulDiv(value, dpi, 96); }
    std::wstring get(HWND control) {
        std::wstring value(GetWindowTextLengthW(control) + 1, L'\0');
        value.resize(GetWindowTextW(control, value.data(), static_cast<int>(value.size())));
        return value;
    }
    void notice(const std::wstring& value) { SetWindowTextW(status, value.c_str()); }
    HWND control(const wchar_t* kind, const wchar_t* caption, int x, int y, int w, int h, int id = 0, DWORD style = 0) {
        auto handle = CreateWindowExW(kind == std::wstring(L"EDIT") ? WS_EX_CLIENTEDGE : 0, kind, caption,
                                       WS_CHILD | WS_VISIBLE | style, px(x), px(y), px(w), px(h), window,
                                       reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), GetModuleHandleW(nullptr), nullptr);
        SendMessageW(handle, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
        return handle;
    }
    void unregister_keys() { for (int i = 0; i < 3; ++i) UnregisterHotKey(window, 200 + i); }
    void register_keys() {
        unregister_keys();
        std::wstring failed;
        for (int i = 0; i < 3; ++i) {
            auto key = parse_hotkey(settings.hotkeys[i]);
            unsigned modifiers = key ? ((key->modifiers & 1 ? MOD_CONTROL : 0) | (key->modifiers & 2 ? MOD_ALT : 0) |
                                       (key->modifiers & 4 ? MOD_WIN : 0) | (key->modifiers & 8 ? MOD_SHIFT : 0) | MOD_NOREPEAT) : 0;
            if (!key || !RegisterHotKey(window, 200 + i, modifiers, key->key)) failed += wide(settings.hotkeys[i]) + L" ";
        }
        SetWindowTextW(hotkey_status, failed.empty() ? L"全局快捷键已启用，切到其他软件也能发送" : (L"快捷键被占用或无效：" + failed + L"请修改").c_str());
    }
    void save() {
        Settings candidate = settings;
        candidate.nickname = utf8(get(nickname));
        std::set<std::pair<unsigned, unsigned>> combinations;
        for (int i = 0; i < 3; ++i) {
            candidate.hotkeys[i] = utf8(get(shortcuts[i]));
            auto key = parse_hotkey(candidate.hotkeys[i]);
            if (!key || !combinations.emplace(key->modifiers, key->key).second)
                throw std::runtime_error("快捷键需要修饰键和字母、数字或 F1–F12，且不能重复");
        }
        save_settings(candidate);
        settings = std::move(candidate);
        register_keys();
        notice(L"设置已保存");
    }
    void build_ui() {
        auto heading = control(L"STATIC", L"集会提醒", 22, 16, 420, 30);
        SendMessageW(heading, WM_SETFONT, reinterpret_cast<WPARAM>(title_font), TRUE);
        control(L"STATIC", L"输入下班搭子的集会码，与现有成员互通", 22, 53, 460, 22);
        control(L"STATIC", L"昵称", 22, 91, 50, 24);
        nickname = control(L"EDIT", wide(settings.nickname).c_str(), 78, 87, 145, 28, 10, WS_TABSTOP | ES_AUTOHSCROLL);
        SendMessageW(nickname, EM_SETLIMITTEXT, 48, 0);
        control(L"STATIC", L"集会码", 240, 91, 55, 24);
        code = control(L"EDIT", L"", 296, 87, 175, 28, 11, WS_TABSTOP | ES_AUTOHSCROLL | ES_UPPERCASE);
        SendMessageW(code, EM_SETLIMITTEXT, 32, 0);
        join_button = control(L"BUTTON", L"加入集会", 22, 126, 105, 30, join_id, WS_TABSTOP);
        leave_button = control(L"BUTTON", L"退出集会", 137, 126, 105, 30, leave_id, WS_TABSTOP);
        group = control(L"STATIC", L"尚未加入集会", 22, 168, 449, 24);
        for (int i = 0; i < 3; ++i) {
            int x = 22 + i * 154;
            send_buttons[i] = control(L"BUTTON", level_names[i], x, 204, 141, 36, 200 + i, WS_TABSTOP);
            shortcuts[i] = control(L"EDIT", wide(settings.hotkeys[i]).c_str(), x, 251, 141, 27, 300 + i, WS_TABSTOP | ES_AUTOHSCROLL);
            SendMessageW(shortcuts[i], EM_SETLIMITTEXT, 48, 0);
            EnableWindow(send_buttons[i], FALSE);
        }
        control(L"BUTTON", L"保存快捷键与昵称", 22, 294, 160, 30, save_id, WS_TABSTOP);
        control(L"STATIC", L"仅边框提示 · 无声音 · 关闭窗口后仍接收", 195, 300, 290, 24);
        hotkey_status = control(L"STATIC", L"", 22, 337, 449, 37);
        status = control(L"STATIC", L"正在连接…", 22, 379, 449, 42);
        EnableWindow(join_button, FALSE); EnableWindow(leave_button, FALSE);
        register_keys();
    }
    void add_tray() {
        tray.cbSize = sizeof(tray); tray.hWnd = window; tray.uID = 1;
        tray.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
        tray.uCallbackMessage = tray_message;
        tray.hIcon = LoadIconW(nullptr, IDI_INFORMATION);
        lstrcpynW(tray.szTip, L"集会提醒 · 双击打开", 128);
        Shell_NotifyIconW(NIM_ADD, &tray);
    }
    void show() { ShowWindow(window, SW_SHOWNORMAL); SetForegroundWindow(window); }
    void menu() {
        auto menu = CreatePopupMenu();
        AppendMenuW(menu, MF_STRING, show_id, L"打开集会提醒");
        for (int i = 0; i < 3; ++i) AppendMenuW(menu, MF_STRING | (snapshot.ready && !snapshot.rally_id.empty() ? 0 : MF_GRAYED), 200 + i, level_names[i]);
        AppendMenuW(menu, MF_SEPARATOR, 0, nullptr); AppendMenuW(menu, MF_STRING, quit_id, L"退出程序");
        POINT point; GetCursorPos(&point); SetForegroundWindow(window);
        TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, window, nullptr);
        PostMessageW(window, WM_NULL, 0, 0); DestroyMenu(menu);
    }
    void update(const Snapshot& value) {
        snapshot = value;
        notice(wide(value.status));
        bool joined = !value.rally_id.empty();
        SetWindowTextW(group, joined ? wide(value.rally_name + " · 集会码 " + value.code).c_str() : L"尚未加入集会");
        if (joined) SetWindowTextW(code, wide(value.code).c_str());
        EnableWindow(join_button, value.ready && !joined); EnableWindow(leave_button, value.ready && joined);
        EnableWindow(code, !joined);
        for (auto button : send_buttons) EnableWindow(button, value.ready && joined);
    }
    std::unique_ptr<Layer> layer(int x, int y, int w, int h, int edge, bool caption) {
        auto result = std::make_unique<Layer>();
        result->origin = {x, y}; result->size = {w, h};
        result->window = CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
                                         L"RallyLiteEdge", L"", WS_POPUP, x, y, w, h, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        result->dc = CreateCompatibleDC(nullptr);
        BITMAPINFO info{}; info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER); info.bmiHeader.biWidth = w; info.bmiHeader.biHeight = -h;
        info.bmiHeader.biPlanes = 1; info.bmiHeader.biBitCount = 32; info.bmiHeader.biCompression = BI_RGB;
        void* pixels = nullptr;
        result->bitmap = CreateDIBSection(result->dc, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
        if (!result->window || !result->dc || !result->bitmap) throw std::runtime_error("无法创建屏幕边框");
        result->previous = SelectObject(result->dc, result->bitmap);
        unsigned color = std::stoul(active.color.substr(1), nullptr, 16);
        BYTE red = (color >> 16) & 255, green = (color >> 8) & 255, blue = color & 255;
        Gdiplus::Bitmap surface(w, h, w * 4, PixelFormat32bppPARGB, static_cast<BYTE*>(pixels));
        Gdiplus::Graphics painter(&surface);
        painter.Clear(Gdiplus::Color(0, 0, 0, 0));
        if (!caption) {
            int thickness = edge < 2 ? h : w;
            for (int i = 0; i < thickness; ++i) {
                double fraction = 1.0 - static_cast<double>(i) / thickness;
                Gdiplus::SolidBrush brush(Gdiplus::Color(static_cast<BYTE>(100 * fraction * fraction), red, green, blue));
                if (edge < 2) painter.FillRectangle(&brush, 0, edge == 0 ? i : h - 1 - i, w, 1);
                else painter.FillRectangle(&brush, edge == 2 ? i : w - 1 - i, 0, 1, h);
            }
        } else {
            Gdiplus::SolidBrush background(Gdiplus::Color(160, 15, 17, 23)), dot(Gdiplus::Color(230, red, green, blue)), ink(Gdiplus::Color(230, 255, 255, 255));
            painter.FillRectangle(&background, 0, 0, w, h);
            painter.FillEllipse(&dot, px(8), h / 2 - px(3), px(5), px(5));
            Gdiplus::Font caption_font(L"Microsoft YaHei UI", static_cast<Gdiplus::REAL>(px(11)), Gdiplus::FontStyleRegular, Gdiplus::UnitPixel);
            Gdiplus::StringFormat format;
            format.SetLineAlignment(Gdiplus::StringAlignmentCenter); format.SetFormatFlags(Gdiplus::StringFormatFlagsNoWrap);
            format.SetTrimming(Gdiplus::StringTrimmingEllipsisCharacter);
            auto label = wide(active.label + (active.source.empty() ? "" : " · 来自 " + active.source));
            painter.SetTextRenderingHint(Gdiplus::TextRenderingHintAntiAliasGridFit);
            painter.DrawString(label.c_str(), -1, &caption_font, Gdiplus::RectF(static_cast<float>(px(20)), 0, static_cast<float>(w - px(28)), static_cast<float>(h)), &format, &ink);
        }
        return result;
    }
    void flash(const Flash& value) {
        layers.clear(); active = value; started = std::chrono::steady_clock::now();
        EnumDisplayMonitors(nullptr, nullptr, [](HMONITOR monitor, HDC, LPRECT, LPARAM data) -> BOOL {
            auto& app = *reinterpret_cast<App*>(data);
            MONITORINFO info{sizeof(info)}; GetMonitorInfoW(monitor, &info);
            auto r = info.rcMonitor; int w = r.right - r.left, h = r.bottom - r.top, t = app.px(18);
            app.layers.push_back(app.layer(r.left, r.top, w, t, 0, false));
            app.layers.push_back(app.layer(r.left, r.bottom - t, w, t, 1, false));
            app.layers.push_back(app.layer(r.left, r.top + t, t, h - t * 2, 2, false));
            app.layers.push_back(app.layer(r.right - t, r.top + t, t, h - t * 2, 3, false));
            if (info.dwFlags & MONITORINFOF_PRIMARY) {
                int width = std::min(app.px(320), static_cast<int>(info.rcWork.right - info.rcWork.left) - app.px(24));
                app.layers.push_back(app.layer(info.rcWork.right - width - app.px(12), info.rcWork.bottom - app.px(34), width, app.px(22), 0, true));
            }
            return TRUE;
        }, reinterpret_cast<LPARAM>(this));
        SetTimer(window, 1, 33, nullptr);
    }
    void animate() {
        double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        if (elapsed >= 5) { KillTimer(window, 1); layers.clear(); return; }
        BYTE alpha = static_cast<BYTE>(255 * alert_intensity(elapsed, 5, active.level));
        for (const auto& layer : layers) layer->show(alpha);
    }
    void command(int id) {
        if (id == join_id) {
            auto candidate = settings; candidate.nickname = utf8(get(nickname));
            save_settings(candidate); settings = candidate;
            client->join(utf8(get(code)), settings.nickname);
        } else if (id == leave_id) {
            if (MessageBoxW(window, snapshot.owner ? L"你是创建者，退出会解散整个集会。确定继续？" : L"确定退出当前集会？", L"退出集会", MB_YESNO | MB_ICONQUESTION) == IDYES) client->leave();
        } else if (id == save_id) save();
        else if (id >= 200 && id < 203) client->alert(id - 200);
        else if (id == show_id) show();
        else if (id == quit_id) DestroyWindow(window);
    }
};
App* current = nullptr;
LRESULT CALLBACK edge_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_NCHITTEST) return HTTRANSPARENT;
    if (msg == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
    return DefWindowProcW(hwnd, msg, wp, lp);
}
LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (!current) return DefWindowProcW(hwnd, msg, wp, lp);
    auto& app = *current;
    try {
        if (msg == app.taskbar_created) { app.add_tray(); return 0; }
        if (msg == WM_COMMAND) { if (HIWORD(wp) == BN_CLICKED) app.command(LOWORD(wp)); return 0; }
        if (msg == WM_HOTKEY) {
            auto focus = GetFocus();
            if (focus == app.nickname || focus == app.code || std::find(std::begin(app.shortcuts), std::end(app.shortcuts), focus) != std::end(app.shortcuts)) return 0;
            app.command(static_cast<int>(wp)); return 0;
        }
        if (msg == incoming_message) {
            std::unique_ptr<Event> event(reinterpret_cast<Event*>(lp));
            if (event->kind == EventKind::State) app.update(event->state);
            else if (event->kind == EventKind::Flash) app.flash(event->flash);
            return 0;
        }
        if (msg == WM_TIMER) { app.animate(); return 0; }
        if (msg == WM_DISPLAYCHANGE) { app.layers.clear(); KillTimer(hwnd, 1); return 0; }
        if (msg == tray_message) {
            if (lp == WM_LBUTTONDBLCLK) app.show();
            else if (lp == WM_RBUTTONUP) app.menu();
            return 0;
        }
        if (msg == WM_CLOSE) { ShowWindow(hwnd, SW_HIDE); return 0; }
        if (msg == WM_DESTROY) {
            app.closing = true;
            app.unregister_keys(); KillTimer(hwnd, 1); app.layers.clear(); Shell_NotifyIconW(NIM_DELETE, &app.tray);
            if (app.client) app.client->stop();
            MSG queued;
            while (PeekMessageW(&queued, hwnd, incoming_message, incoming_message, PM_REMOVE)) delete reinterpret_cast<Event*>(queued.lParam);
            PostQuitMessage(0); return 0;
        }
    } catch (const std::exception& error) { app.notice(wide(error.what())); return 0; }
    return DefWindowProcW(hwnd, msg, wp, lp);
}
}
int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int) {
    HANDLE singleton = CreateMutexW(nullptr, FALSE, L"Local\\OffworkRallyLite");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        if (auto existing = FindWindowW(L"RallyLiteMain", nullptr)) { ShowWindow(existing, SW_SHOWNORMAL); SetForegroundWindow(existing); }
        if (singleton) CloseHandle(singleton); return 0;
    }
    SetProcessDPIAware();
    ULONG_PTR graphics_token{}; Gdiplus::GdiplusStartupInput startup;
    Gdiplus::GdiplusStartup(&graphics_token, &startup, nullptr);
    int exit_code = 0;
    try {
        App app; current = &app;
        auto dc = GetDC(nullptr); app.dpi = GetDeviceCaps(dc, LOGPIXELSX); ReleaseDC(nullptr, dc);
        app.settings = load_settings();
        app.font = CreateFontW(-app.px(13), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
        app.title_font = CreateFontW(-app.px(23), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
        WNDCLASSW klass{}; klass.lpfnWndProc = window_proc; klass.hInstance = instance; klass.lpszClassName = L"RallyLiteMain";
        klass.hCursor = LoadCursorW(nullptr, IDC_ARROW); klass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
        klass.hIcon = LoadIconW(nullptr, IDI_INFORMATION); RegisterClassW(&klass);
        klass.lpfnWndProc = edge_proc; klass.lpszClassName = L"RallyLiteEdge"; klass.hbrBackground = nullptr; RegisterClassW(&klass);
        RECT rect{0, 0, app.px(496), app.px(438)}; AdjustWindowRect(&rect, WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, FALSE);
        app.window = CreateWindowW(L"RallyLiteMain", L"集会提醒", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                   CW_USEDEFAULT, CW_USEDEFAULT, rect.right - rect.left, rect.bottom - rect.top, nullptr, nullptr, instance, nullptr);
        if (!app.window) throw std::runtime_error("无法创建程序窗口");
        app.build_ui(); app.add_tray();
        app.client = std::make_unique<Client>(app.settings.endpoint, load_token(app.settings.endpoint), [&](const std::string& token) {
            save_token(app.settings.endpoint, token);
        }, [&](const Event& event) {
            auto copy = std::make_unique<Event>(event);
            if (PostMessageW(app.window, incoming_message, 0, reinterpret_cast<LPARAM>(copy.get()))) copy.release();
        });
        app.client->start(); app.show();
        MSG message;
        while (GetMessageW(&message, nullptr, 0, 0) > 0) {
            if (!IsDialogMessageW(app.window, &message)) { TranslateMessage(&message); DispatchMessageW(&message); }
        }
        app.client.reset();
        DeleteObject(app.font); DeleteObject(app.title_font); current = nullptr;
    } catch (const std::exception& error) {
        current = nullptr;
        MessageBoxW(nullptr, wide(error.what()).c_str(), L"集会提醒启动失败", MB_OK | MB_ICONERROR); exit_code = 1;
    }
    Gdiplus::GdiplusShutdown(graphics_token);
    if (singleton) CloseHandle(singleton);
    return exit_code;
}
