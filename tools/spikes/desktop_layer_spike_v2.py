"""Isolated Windows desktop experiment. No production imports.

Run --messages first (no Qt windows), then --native A/B/C separately.
Screen captures are desktop composition, never QWidget.grab/PrintWindow.
Manual icon/input/coverage evidence is required before any renderer experiment.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import time

OUT = Path(__file__).resolve().parents[2] / 'docs/spikes/v2-evidence'
U = c.WinDLL('user32', use_last_error=True)
K = c.WinDLL('kernel32', use_last_error=True)
H = w.HWND
P = c.c_ssize_t


def bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


CALLBACK = c.WINFUNCTYPE(w.BOOL, H, P)
bind(U, 'EnumWindows', w.BOOL, CALLBACK, P)
bind(U, 'EnumChildWindows', w.BOOL, H, CALLBACK, P)
bind(U, 'GetParent', H, H)
bind(U, 'GetWindow', H, H, w.UINT)
bind(U, 'GetWindowLongPtrW', P, H, c.c_int)
bind(U, 'SetWindowLongPtrW', P, H, c.c_int, P)
bind(U, 'GetClassNameW', c.c_int, H, w.LPWSTR, c.c_int)
bind(U, 'GetWindowRect', w.BOOL, H, c.POINTER(w.RECT))
bind(U, 'GetWindowThreadProcessId', w.DWORD, H, c.POINTER(w.DWORD))
bind(U, 'FindWindowW', H, w.LPCWSTR, w.LPCWSTR)
bind(U, 'IsWindowVisible', w.BOOL, H)
bind(U, 'IsWindow', w.BOOL, H)
bind(U, 'SetParent', H, H, H)
bind(U, 'SetWindowPos', w.BOOL, H, H, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
bind(U, 'MapWindowPoints', c.c_int, H, H, c.POINTER(w.POINT), w.UINT)
bind(U, 'SetLayeredWindowAttributes', w.BOOL, H, w.DWORD, w.BYTE, w.DWORD)
bind(U, 'SendMessageTimeoutW', P, H, w.UINT, c.c_size_t, P, w.UINT, w.UINT, c.POINTER(c.c_size_t))
bind(U, 'GetForegroundWindow', H)
bind(U, 'ShowWindow', w.BOOL, H, c.c_int)
bind(U, 'UpdateWindow', w.BOOL, H)
bind(U, 'RedrawWindow', w.BOOL, H, c.c_void_p, w.HANDLE, w.UINT)
bind(U, 'DestroyWindow', w.BOOL, H)
bind(U, 'DefWindowProcW', P, H, w.UINT, c.c_size_t, P)
bind(U, 'CreateWindowExW', H, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
     c.c_int, c.c_int, c.c_int, c.c_int, H, w.HMENU, w.HINSTANCE, c.c_void_p)
bind(K, 'OpenProcess', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
bind(K, 'QueryFullProcessImageNameW', w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
bind(K, 'CloseHandle', w.BOOL, w.HANDLE)


def emit(kind, data):
    OUT.mkdir(parents=True, exist_ok=True)
    line = '[DESKTOP SPIKE V2][' + kind + '] ' + json.dumps(data, ensure_ascii=True)
    print(line, flush=True)
    with (OUT / 'events.log').open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def enumerate_windows(parent=None):
    result = []
    cb = CALLBACK(lambda hwnd, _: result.append(int(hwnd)) or True)
    if parent is None:
        U.EnumWindows(cb, 0)
    else:
        U.EnumChildWindows(parent, cb, 0)
    return result


def info(hwnd):
    name, rect, pid = c.create_unicode_buffer(256), w.RECT(), w.DWORD()
    U.GetClassNameW(hwnd, name, 256)
    U.GetWindowRect(hwnd, c.byref(rect))
    U.GetWindowThreadProcessId(hwnd, c.byref(pid))
    process = K.OpenProcess(0x1000, False, pid.value)
    path, size = c.create_unicode_buffer(32768), w.DWORD(32768)
    if process:
        K.QueryFullProcessImageNameW(process, 0, path, c.byref(size))
        K.CloseHandle(process)
    style = U.GetWindowLongPtrW(hwnd, -16) & 0xffffffff
    parent_or_owner = int(U.GetParent(hwnd) or 0)
    return dict(hwnd=hwnd, cls=name.value, parent=parent_or_owner if style & 0x40000000 else 0,
                owner=0 if style & 0x40000000 else parent_or_owner,
                pid=pid.value, process=path.value, rect=[rect.left, rect.top, rect.right, rect.bottom],
                visible=bool(U.IsWindowVisible(hwnd)), style=hex(U.GetWindowLongPtrW(hwnd, -16) & 0xffffffff),
                exstyle=hex(U.GetWindowLongPtrW(hwnd, -20) & 0xffffffff),
                previous=int(U.GetWindow(hwnd, 3) or 0), next=int(U.GetWindow(hwnd, 2) or 0))


def hierarchy(label):
    tops = enumerate_windows()
    all_hwnds = list(tops)
    for hwnd in tops:
        all_hwnds.extend(enumerate_windows(hwnd))
    rows = {h: info(h) for h in dict.fromkeys(all_hwnds)}
    progman = int(U.FindWindowW('Progman', None) or 0)
    shell_pid = rows.get(progman, {}).get('pid')
    selected = {h: r for h, r in rows.items() if r['cls'] in ('Progman', 'WorkerW', 'SHELLDLL_DefView')}
    parents = {r['parent'] for r in selected.values() if r['cls'] == 'SHELLDLL_DefView'} | {progman}
    selected.update({h: r for h, r in rows.items() if r['parent'] in parents and r['parent']})
    for row in list(selected.values()):
        ancestor = row['parent']
        while ancestor in rows:
            selected[ancestor] = rows[ancestor]
            ancestor = rows[ancestor]['parent']
    for h, row in selected.items():
        if row['cls'] == 'WorkerW':
            row['classification'] = ('other-process' if row['pid'] != shell_pid else
                                     'Progman-child' if row['parent'] == progman else
                                     'shell-top-level' if h in tops else 'shell-other-child')
        row['top_level'] = h in tops
    data = dict(label=label, time=time.time(), progman=progman, windows=list(selected.values()))
    emit('HIERARCHY', data)
    (OUT / (label + '.json')).write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def targets(data):
    rows, p = data['windows'], data['progman']
    pr = next(r for r in rows if r['hwnd'] == p)
    defs = [r for r in rows if r['cls'] == 'SHELLDLL_DefView' and r['pid'] == pr['pid']]
    result = {'A': [], 'B': [], 'C': [(p, next((r['hwnd'] for r in defs if r['parent'] == p), 0))]}
    for r in rows:
        if r['cls'] != 'WorkerW' or r['pid'] != pr['pid'] or not r['visible']:
            continue
        if any(d['parent'] == r['hwnd'] for d in defs):
            continue
        if r['parent'] == p:
            result['B'].append((r['hwnd'], 0))
        elif r['top_level'] and any(d['parent'] in {x['hwnd'] for x in rows if x['next'] == r['hwnd']} for d in defs):
            result['A'].append((r['hwnd'], 0))
    # B tests direct Progman parenting without explicit sibling z-order when
    # there is no shell-owned child WorkerW; C adds that one variable.
    if not result['B']:
        result['B'] = [(p, 0)]
    return result


def self_test():
    def row(hwnd, cls, parent=0, pid=10, visible=True, next=0):
        return dict(hwnd=hwnd, cls=cls, parent=parent, pid=pid,
                    visible=visible, next=next, top_level=parent == 0)
    data = dict(progman=1, windows=[row(1, 'Progman'), row(2, 'SHELLDLL_DefView', 1),
                                  row(3, 'WorkerW', 1), row(4, 'WorkerW', pid=99),
                                  row(5, 'WorkerW', visible=False)])
    assert targets(data) == {'A': [], 'B': [(3, 0)], 'C': [(1, 2)]}
    data['windows'] = [row(1, 'Progman', next=3), row(2, 'SHELLDLL_DefView', 1),
                       row(3, 'WorkerW')]
    assert targets(data) == {'A': [(3, 0)], 'B': [(1, 0)], 'C': [(1, 2)]}
    assert c.sizeof(H) == c.sizeof(P)
    print('PASS: hierarchy selection excludes unrelated/hidden WorkerW; pointer-sized ABI')


def capture(app, label):
    for index, screen in enumerate(app.screens()):
        pix = screen.grabWindow(0)
        path = OUT / f'{label}-screen-{index}.png'
        ok = pix.save(str(path))
        emit('SCREENSHOT', dict(path=str(path), saved=ok, null=pix.isNull(),
                               geometry=screen.geometry().getRect(), dpr=pix.devicePixelRatio()))


WNDPROC = c.WINFUNCTYPE(P, H, w.UINT, c.c_size_t, P)


class WNDCLASS(c.Structure):
    _fields_ = [('style', w.UINT), ('proc', WNDPROC), ('cls_extra', c.c_int),
                ('wnd_extra', c.c_int), ('instance', w.HINSTANCE), ('icon', w.HICON),
                ('cursor', w.HANDLE), ('brush', w.HBRUSH), ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]


def win32_block(x, y):
    bind(U, 'RegisterClassW', w.ATOM, c.POINTER(WNDCLASS))
    gdi = c.WinDLL('gdi32', use_last_error=True)
    bind(gdi, 'CreateSolidBrush', w.HBRUSH, w.DWORD)
    bind(K, 'GetModuleHandleW', w.HMODULE, w.LPCWSTR)
    proc = WNDPROC(lambda hwnd, msg, wp, lp: U.DefWindowProcW(hwnd, msg, wp, lp))
    wc = WNDCLASS()
    wc.proc, wc.instance = proc, K.GetModuleHandleW(None)
    wc.brush, wc.name = gdi.CreateSolidBrush(0x00a800ff), 'DesktopSpikeV2Native'
    if not U.RegisterClassW(c.byref(wc)):
        raise c.WinError(c.get_last_error())
    hwnd = U.CreateWindowExW(0x080800a0, wc.name, 'DESKTOP SPIKE V2', 0x80000000,
                            x, y, 500, 300, None, None, wc.instance, None)
    if not hwnd:
        raise c.WinError(c.get_last_error())
    U.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
    U.ShowWindow(hwnd, 4)
    U.UpdateWindow(hwnd)
    return int(hwnd), (wc, proc, gdi)


def native(args):
    label = args.native + ('-win32' if args.win32 else '-qt-translucent' if args.qt_translucent else '-qt')
    data = hierarchy('native-' + args.native + '-before')
    candidates = targets(data)[args.native]
    emit('CANDIDATES', dict(test=args.native, targets=candidates))
    if not candidates:
        emit('NATIVE', dict(test=args.native, status='UNAVAILABLE', reason='No hierarchy-validated candidate'))
        return
    if len(candidates) != 1:
        raise RuntimeError('Ambiguous candidates; inspect hierarchy before selecting')
    parent, after = candidates[0]
    from PyQt5.QtCore import Qt, QTimer, QEventLoop
    from PyQt5.QtGui import QColor, QPainter
    from PyQt5.QtWidgets import QApplication, QWidget
    app = QApplication([])

    class Block(QWidget):
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#ff00a8'))
            painter.setPen(Qt.white)
            painter.drawText(24, 48, 'DESKTOP SPIKE V2 / ' + args.native)

    foreground = int(U.GetForegroundWindow() or 0)
    block = None
    if args.win32:
        hwnd, native_resources = win32_block(args.x, args.y)
    else:
        block = Block(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        block.setAttribute(Qt.WA_ShowWithoutActivating)
        block.setAttribute(Qt.WA_TransparentForMouseEvents)
        if args.qt_translucent:
            block.setAttribute(Qt.WA_TranslucentBackground)
        block.setGeometry(args.x, args.y, 500, 300)
        hwnd = int(block.winId())
        # Show before native parenting so Qt cannot undo native styles on show().
        block.show()
    app.processEvents()
    U.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x10 | 0x1 | 0x2 | 0x40)
    U.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x100 | 0x200)
    control_loop = QEventLoop()
    QTimer.singleShot(750, control_loop.quit)
    control_loop.exec_()
    capture(app, 'native-' + label + '-control')
    style = U.GetWindowLongPtrW(hwnd, -16)
    exstyle = U.GetWindowLongPtrW(hwnd, -20)
    U.SetWindowLongPtrW(hwnd, -16, (style | 0x40000000) & ~0x80000000)
    U.SetWindowLongPtrW(hwnd, -20, (exstyle | 0x80000 | 0x08000000 | 0x20) & ~0x8)
    c.set_last_error(0)
    alpha_ok = bool(U.SetLayeredWindowAttributes(hwnd, 0, 255, 2))
    alpha_error = c.get_last_error()
    c.set_last_error(0)
    old = U.SetParent(hwnd, parent)
    error = c.get_last_error()
    pt = w.POINT(args.x, args.y)
    U.MapWindowPoints(None, parent, c.byref(pt), 1)
    flags = 0x10 | 0x20 | 0x40 | (0 if after else 0x4)
    pos_ok = bool(U.SetWindowPos(hwnd, after, pt.x, pt.y, 500, 300, flags))
    if block:
        block.update()
    else:
        U.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x100 | 0x200)

    def enforce_layered():
        # Qt's first backing-store flush removes WS_EX_LAYERED for an opaque
        # QWidget. Apply the experimental style after that flush, and verify
        # it survives until the actual desktop capture.
        U.SetWindowLongPtrW(hwnd, -20, (exstyle | 0x80000 | 0x08000000 | 0x20) & ~0x8)
        c.set_last_error(0)
        ok = U.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
        emit('LAYERED_REAPPLY', dict(ok=bool(ok), error=c.get_last_error(), state=info(hwnd)))

    def record():
        if args.show_desktop:
            subprocess.run([str(Path.home() / '.headroom/bin/rtk.exe'), 'proxy', 'powershell',
                            '-NoProfile', '-Command', '(New-Object -ComObject Shell.Application).MinimizeAll()'],
                           check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.4)
        state = info(hwnd)
        emit('NATIVE', dict(test=label, hwnd=hwnd, target=parent, insert_after=after,
                           alpha=255, alpha_ok=alpha_ok, alpha_error=alpha_error,
                           set_parent_ok=bool(old) or error == 0, set_parent_error=error,
                           get_parent_verified=int(U.GetParent(hwnd) or 0) == parent,
                           layered_at_capture=bool(int(state['exstyle'], 16) & 0x80000),
                           pos_ok=pos_ok, state=state,
                           foreground_before=foreground, foreground_now=int(U.GetForegroundWindow() or 0),
                           visual_composition='REQUIRES_SCREENSHOT_AND_HUMAN',
                           icon_interaction='NOT_TESTED', normal_app_coverage='NOT_TESTED'))
        hierarchy('native-' + label + '-attached')
        capture(app, 'native-' + label + '-attached')

    QTimer.singleShot(1000, enforce_layered)
    QTimer.singleShot(2500, record)
    QTimer.singleShot(int(args.duration * 1000), app.quit)
    try:
        app.exec_()
    finally:
        U.ShowWindow(hwnd, 0)
        U.SetParent(hwnd, None)
        U.SetWindowLongPtrW(hwnd, -16, style)
        U.SetWindowLongPtrW(hwnd, -20, exstyle)
        if block:
            block.close()
        else:
            U.DestroyWindow(hwnd)
        emit('CLEANUP', dict(hwnd=hwnd, detached=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--messages', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--native', choices=['A', 'B', 'C'])
    parser.add_argument('--win32', action='store_true', help='Use native brush-backed HWND instead of QWidget')
    parser.add_argument('--qt-translucent', action='store_true', help='Qt-managed layered backing store, painted fully opaque')
    parser.add_argument('--show-desktop', action='store_true', help='Minimize apps immediately before evidence capture')
    parser.add_argument('--duration', type=float, default=20)
    parser.add_argument('--x', type=int, default=40)
    parser.add_argument('--y', type=int, default=40)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    emit('ENVIRONMENT', dict(os=platform.platform(), packages={n: importlib.metadata.version(n) for n in ('PyQt5', 'PyQtWebEngine')}))
    if args.native:
        native(args)
        return
    hierarchy('test0')
    if args.messages:
        for label, wp, lp in [('legacy-0-0', 0, 0), ('candidate-d-0', 13, 0), ('candidate-d-1', 13, 1)]:
            before = hierarchy(label + '-before')
            result = c.c_size_t()
            c.set_last_error(0)
            ok = U.SendMessageTimeoutW(before['progman'], 0x052C, wp, lp, 2, 1000, c.byref(result))
            emit('MESSAGE', dict(label=label, wparam=wp, lparam=lp, sent=bool(ok), result=result.value, error=c.get_last_error()))
            time.sleep(1)
            after = hierarchy(label + '-after')
            old = {r['hwnd']: r for r in before['windows']}
            emit('DELTA', dict(label=label, added=[r for r in after['windows'] if r['hwnd'] not in old],
                               changed=[r for r in after['windows'] if r['hwnd'] in old and r != old[r['hwnd']]],
                               removed=[h for h in old if h not in {r['hwnd'] for r in after['windows']}]))


if __name__ == '__main__':
    main()
