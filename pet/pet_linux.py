# Claude 娘桌宠悬浮窗（Linux 版，GTK3）
# Wayland 下用 gtk-layer-shell 做置顶悬浮层；X11 下退回成普通的无边框置顶窗口。
# mod 把每个会话的状态写进 <运行目录>/sessions/<会话id>.json，这里读最新的那个来换表情和气泡。
# 用法：python3 pet_linux.py <运行目录>
import glob
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time

# 有 Wayland 就走 Wayland（layer-shell 只在 Wayland 下能用），必须在导入 Gtk 之前设
if os.environ.get('WAYLAND_DISPLAY'):
    os.environ.setdefault('GDK_BACKEND', 'wayland')

import cairo
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
except (ValueError, ImportError):
    GtkLayerShell = None

HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS = os.path.join(os.path.dirname(HERE), 'sounds')
RUN_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser('~'), '.claude', 'desk-pet')
SESSIONS = os.path.join(RUN_DIR, 'sessions')
SETTINGS = os.path.join(RUN_DIR, 'settings.json')

# 只允许开一个桌宠：占住一个本地端口，占不到说明已经有一个在跑
try:
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lock.bind(('127.0.0.1', 47321))
except OSError:
    sys.exit(0)

# 状态 → 立绘
SPRITES = {
    'thinking': 'smile-closed',
    'tool': 'typing',
    'reading': 'notes',
    'waiting': 'knock',
    'celebrate': 'confetti',
    'idle': 'smile',
    'error': 'panic',
    'aborted': 'guilty',
    'stone': 'stone',
}
SIZES = {'小': 140, '中': 190, '大': 250}  # 角色高度（逻辑像素）
IDLE_AFTER = 10 * 60  # 等你回复超过 10 分钟就开始发呆
FONT = 'Sans 10'
ORANGE = (0xe8 / 255, 0x74 / 255, 0x3b / 255)
BUBBLE_BG = (0xff / 255, 0xfa / 255, 0xf3 / 255)
TEXT = (0x3a / 255, 0x2a / 255, 0x1a / 255)
ANIMATED = {'thinking', 'tool', 'reading'}  # 这些状态下，台词末尾的“…”会变成跳动的点点点
DOTS = '...'
HOP = 14  # 换状态时跳起的高度
BOB = 3   # 平时上下晃动的最大幅度
GAP = 4   # 跳到最高时，头顶和气泡尖之间至少留这么多
CLAUDE_APP_IDS = ('com.anthropic.claude', 'claude')
CLAUDE_PROCS = ('claude', 'claude-desktop')


# ---------- niri（Wayland 合成器）IPC：查焦点窗口、把 Claude 叫到前台 ----------
def niri(request):
    path = os.environ.get('NIRI_SOCKET')
    if not path:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(path)
            s.sendall((json.dumps(request) + '\n').encode())
            buf = b''
            while not buf.endswith(b'\n'):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf).get('Ok')
    except Exception:
        return None


def is_claude_window(w):
    return bool(w) and (w.get('app_id') or '').lower() in CLAUDE_APP_IDS


def claude_is_foreground():
    reply = niri('FocusedWindow')
    return bool(reply) and is_claude_window(reply.get('FocusedWindow'))


def focus_claude_window():
    reply = niri('Windows')
    for w in (reply or {}).get('Windows', []):
        if is_claude_window(w):
            niri({'Action': {'FocusWindow': {'id': w['id']}}})
            return


def claude_running():
    try:
        for comm in glob.glob('/proc/[0-9]*/comm'):
            try:
                with open(comm) as f:
                    if f.read().strip() in CLAUDE_PROCS:
                        return True
            except OSError:
                continue
        return False
    except Exception:
        return True


PLAYER = next((p for p in ('pw-play', 'paplay', 'aplay') if shutil.which(p)), None)


def play(name):
    if not PLAYER:
        return
    try:
        subprocess.Popen([PLAYER, os.path.join(SOUNDS, name + '.wav')],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def load_settings():
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception:
        pass


def read_latest():
    """读所有会话里最新的状态；顺手清掉两天前的旧文件。"""
    best, now = None, time.time()
    for p in glob.glob(os.path.join(SESSIONS, '*.json')):
        try:
            mtime = os.path.getmtime(p)
            if now - mtime > 2 * 86400:
                os.remove(p)
                continue
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
            if best is None or mtime > best[0]:
                best = (mtime, data)
        except Exception:
            continue
    return best


def fit_text(text, max_w, measure):
    """文字太长就从中间省略，保留开头（“在改”）和结尾（扩展名）。"""
    if measure(text) <= max_w:
        return text
    head, tail = len(text) // 2, len(text) - len(text) // 2
    while head + tail > 1:
        if head >= tail:
            head -= 1
        else:
            tail -= 1
        cut = text[:head] + '…' + text[len(text) - tail:]
        if measure(cut) <= max_w:
            return cut
    return '…'


def bubble_path(cr, x0, y0, x1, y1, r, cx, half, tip):
    """圆角矩形加底部居中的倒三角尾巴，连成一条轮廓，描边时接缝处不会断开。"""
    cr.new_path()
    cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
    cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
    cr.line_to(cx + half, y1)
    cr.line_to(cx, tip)
    cr.line_to(cx - half, y1)
    cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
    cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Pet:
    def __init__(self):
        self.settings = load_settings()
        self.win = Gtk.Window(title='Claude 桌宠')
        self.win.set_app_paintable(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)

        self.layer = bool(GtkLayerShell and GtkLayerShell.is_supported())
        if self.layer:
            GtkLayerShell.init_for_window(self.win)
            GtkLayerShell.set_namespace(self.win, 'claude-desk-pet')
            GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.NONE)
            # 铺满整个屏幕，桌宠画在里面，透明的地方靠输入区域让点击穿透。
            # Wayland 下程序拿不到鼠标的屏幕坐标，挪动窗口拖拽会因为位置更新的延迟越拖越飘；
            # 窗口本身不动的话，鼠标在窗口里的坐标就是屏幕坐标，拖拽只是换个地方画
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self.win, edge, True)
        else:
            self.win.set_decorated(False)
            self.win.set_keep_above(True)
            self.win.set_skip_taskbar_hint(True)
            self.win.set_skip_pager_hint(True)
            self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)

        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect('draw', self.on_draw)
        self.area.connect('button-press-event', self.on_press)
        self.area.connect('motion-notify-event', self.on_drag)
        self.area.connect('button-release-event', self.on_release)
        self.area.connect('size-allocate', self.on_allocate)
        self.win.add(self.area)
        self.win.connect('destroy', Gtk.main_quit)

        self.state, self.text, self.project = 'waiting', '我来啦～', ''
        self.state_since = time.time()
        self.last_mtime = 0
        self.last_mtime_seen = 0
        self.hop_at = time.time()
        self.hidden = False
        self.drag = None
        self.last_alive = time.time()
        # 量文字宽度用的排版对象，和绘制时用同一个字体
        self.pango = self.area.create_pango_layout('')
        self.pango.set_font_description(Pango.FontDescription(FONT))

        self.make_menu()
        self.build()
        self.place_window()
        self.win.show_all()
        self.update_input_region()

        self.poll()
        GLib.timeout_add(33, self.animate)
        GLib.timeout_add(500, self.watch_focus)
        GLib.timeout_add(30000, self.watch_claude)

    # ---------- 布局 ----------
    def build(self):
        h = SIZES.get(self.settings.get('size', '中'), 190)
        # GTK3 只有整数缩放，按 2 倍分辨率加载，分数缩放的屏幕上也不会糊
        self.px = max(2, self.win.get_scale_factor())
        self.images = {}
        for state, name in SPRITES.items():
            self.images[state] = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                os.path.join(HERE, 'sprites', name + '.png'), -1, h * self.px, True)
        self.char_h = h
        self.char_w = max(img.get_width() for img in self.images.values()) // self.px
        self.W = max(self.char_w, 190)
        # 气泡在最上面，下面给跳跃和晃动留够空间，角色怎么动都碰不到气泡，也不会出窗口底边
        self.bubble_bottom = 38
        self.tail_tip = self.bubble_bottom + 10
        self.rest_y = self.tail_tip + GAP + HOP + BOB  # 静止时立绘顶边的位置
        self.H = self.rest_y + h + BOB
        if not self.layer:
            self.area.set_size_request(self.W, self.H)
            self.win.resize(self.W, self.H)
        self.layout_bubble()

    def measure(self, text):
        self.pango.set_text(text, -1)
        return self.pango.get_pixel_extents()[1].width

    def layout_bubble(self):
        """按当前台词排好气泡：算出要显示的文字、文字和气泡的位置。"""
        pad = 10
        max_w = self.W - 2 * pad - 4
        state = self.effective_state()
        text, self.dots_base = self.display_text(), None
        if state in ANIMATED and (text.endswith('…') or text == '思考中'):
            # 末尾的省略号换成一跳一跳的点点点；文字左对齐，按三个点的宽度留好位置，点数变化时文字不会左右晃
            self.dots_base = fit_text(text.rstrip('…'), max_w - self.measure(DOTS), self.measure)
            text = self.dots_base + DOTS
        else:
            text = fit_text(text, max_w, self.measure)
        text_w = self.measure(text)
        self.bubble_key = (state, self.display_text(), self.W)
        self.bubble_text = text
        self.text_x = self.W / 2 - text_w / 2
        x0 = max(1, math.floor(self.text_x) - pad)
        x1 = min(self.W - 2, math.ceil(self.text_x + text_w) + pad)
        # 文字特别短时也要放得下圆角和尾巴
        min_half = 12 + 6 + 2
        x0, x1 = min(x0, self.W // 2 - min_half), max(x1, self.W // 2 + min_half)
        self.bubble = (x0, 2, x1, self.bubble_bottom)

    def display_text(self):
        if self.effective_state() == 'idle':
            return '在发呆～ zZ'
        return self.text

    def effective_state(self):
        if self.state == 'waiting' and time.time() - self.state_since > IDLE_AFTER:
            return 'idle'
        return self.state

    def screen_size(self):
        # layer 模式下窗口就是整块屏幕（去掉面板占的地方），按窗口实际大小算
        if self.layer and self.area.get_allocated_width() > 1:
            return self.area.get_allocated_width(), self.area.get_allocated_height()
        display = Gdk.Display.get_default()
        mon = display.get_monitor_at_window(self.win.get_window()) if self.win.get_window() else None
        mon = mon or display.get_primary_monitor() or display.get_monitor(0)
        g = mon.get_geometry()
        return g.width, g.height

    def clamp(self, right, bottom):
        sw, sh = self.screen_size()
        return min(max(0, right), max(0, sw - self.W)), min(max(0, bottom), max(0, sh - self.H))

    def origin(self):
        """桌宠左上角在窗口里的位置。"""
        if not self.layer:
            return 0, 0
        # 窗口刚建出来时会先拿到一个临时的小尺寸，这里只在画的时候截一下，不改记住的位置
        sw, sh = self.screen_size()
        right, bottom = self.clamp(self.right, self.bottom)
        return sw - self.W - right, sh - self.H - bottom

    def place_window(self):
        # 位置记成到屏幕右下角的距离，换了分辨率也大致在原来的角落
        self.right, self.bottom = self.settings.get('right', 40), self.settings.get('bottom', 70)
        self.move_to(self.right, self.bottom)

    def move_to(self, right, bottom):
        sw, sh = self.screen_size()
        old = self.pet_rect()
        self.right, self.bottom = self.clamp(right, bottom)
        if self.layer:
            self.area.queue_draw_area(*old)
            self.area.queue_draw_area(*self.pet_rect())
        else:
            self.win.move(sw - self.W - self.right, sh - self.H - self.bottom)

    def pet_rect(self):
        ox, oy = self.origin()
        return ox, oy, self.W, self.H

    def make_menu(self):
        self.menu = Gtk.Menu()
        size_item = Gtk.MenuItem(label='大小')
        size_menu = Gtk.Menu()
        group = None
        current = self.settings.get('size', '中')
        for name in SIZES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
            group = group or item
            item.set_active(name == current)
            item.connect('toggled', self.resize, name)
            size_menu.append(item)
        size_item.set_submenu(size_menu)
        self.menu.append(size_item)
        self.hide_item = Gtk.CheckMenuItem(label='切到 Claude 时自动隐藏')
        self.hide_item.set_active(self.settings.get('hideOnFocus', False))
        self.hide_item.connect('toggled', self.toggle_hide)
        if os.environ.get('NIRI_SOCKET'):  # 只有 niri 下才知道焦点在哪个窗口
            self.menu.append(self.hide_item)
        self.sound_item = Gtk.CheckMenuItem(label='提示音')
        self.sound_item.set_active(self.settings.get('sound', True))
        self.sound_item.connect('toggled', self.toggle_sound)
        self.menu.append(self.sound_item)
        # 状态栏语言：插件那边读 settings.json 的 lang，下一次刷新状态栏时生效
        lang_item = Gtk.MenuItem(label='状态栏语言 / Language')
        lang_menu = Gtk.Menu()
        group = None
        current = self.settings.get('lang', 'auto')
        for value, label in (('auto', '跟随系统 / Auto'), ('zh', '中文'), ('en', 'English')):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            item.set_active(value == current)
            item.connect('toggled', self.set_lang, value)
            lang_menu.append(item)
        lang_item.set_submenu(lang_menu)
        self.menu.append(lang_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label='退出桌宠')
        quit_item.connect('activate', lambda *_: self.win.destroy())
        self.menu.append(quit_item)
        self.menu.show_all()

    def resize(self, item, name):
        if not item.get_active():
            return
        self.settings['size'] = name
        save_settings(self.settings)
        self.build()
        self.move_to(self.right, self.bottom)
        self.update_input_region()
        self.area.queue_draw()

    def toggle_hide(self, item):
        self.settings['hideOnFocus'] = item.get_active()
        save_settings(self.settings)

    def toggle_sound(self, item):
        self.settings['sound'] = item.get_active()
        save_settings(self.settings)

    def set_lang(self, item, value):
        if not item.get_active():
            return
        self.settings['lang'] = value
        save_settings(self.settings)

    # ---------- 绘制 ----------
    def draw_bubble(self, cr):
        x0, y0, x1, y1 = self.bubble
        lw = 2
        # 描边是沿轮廓线居中画的，轮廓往里收半个线宽，整个气泡正好落在 bubble 框里
        h = lw / 2
        bubble_path(cr, x0 + h, y0 + h, x1 - h, y1 - h, 12 - h, self.W / 2, 6, self.tail_tip - h)
        cr.set_source_rgb(*BUBBLE_BG)
        cr.fill_preserve()
        cr.set_source_rgb(*ORANGE)
        cr.set_line_width(lw)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

        shown = self.bubble_text
        if self.dots_base is not None:
            # “思考中…” 这类进行中的台词，后面的点点点一跳一跳
            shown = self.dots_base + '.' * (int(time.time() * 2.5) % 3 + 1)
        # 用量宽度的同一个排版对象来画，量出来多宽画出来就多宽
        self.pango.set_text(shown, -1)
        _, logical = self.pango.get_pixel_extents()
        cr.set_source_rgb(*TEXT)
        cr.move_to(self.text_x, 20 - logical.height / 2)
        PangoCairo.show_layout(cr, self.pango)

    def sprite_offset(self):
        t = time.time()
        st = self.effective_state()
        if st == 'stone':
            bob = 0
        elif st == 'tool':
            bob = 2.5 * math.sin(t * 9)   # 敲键盘：快速小幅抖动
        elif st == 'error':
            bob = 2 * math.sin(t * 25)    # 慌张：发抖
        else:
            bob = 3 * math.sin(t * 2.2)   # 平时：慢慢呼吸
        since = t - self.hop_at
        hop = -HOP * math.sin(since / 0.28 * math.pi) if since < 0.28 else 0  # 换状态时小跳一下
        return round(bob + hop)

    def draw_sprite(self, cr, img, still=False):
        w = img.get_width() / self.px
        cr.save()
        cr.translate(round(self.W / 2 - w / 2), self.rest_y + (0 if still else self.sprite_offset()))
        cr.scale(1 / self.px, 1 / self.px)
        Gdk.cairo_set_source_pixbuf(cr, img, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

    def on_draw(self, widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.translate(*self.origin())
        self.draw_sprite(cr, self.images[self.effective_state()])
        self.draw_bubble(cr)
        return True

    def update_input_region(self):
        # 只有画出来的部分接收鼠标，透明的地方点击会穿透到下面的窗口
        ox, oy = self.origin()
        try:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.W, self.H)
            cr = cairo.Context(surface)
            self.draw_sprite(cr, self.images[self.effective_state()], still=True)
            x0, y0, x1, y1 = self.bubble
            cr.set_source_rgb(0, 0, 0)
            cr.rectangle(x0, y0, x1 - x0, self.tail_tip - y0)
            cr.fill()
            region = Gdk.cairo_region_create_from_surface(surface)
        except Exception:
            # 算不出轮廓就退回整个桌宠的矩形，总之不能让整块屏幕都挡住鼠标
            region = cairo.Region(cairo.RectangleInt(0, 0, self.W, self.H))
        region.translate(ox, oy)
        self.win.input_shape_combine_region(region)

    # ---------- 鼠标 ----------
    def on_press(self, widget, e):
        if e.button == 3:
            self.menu.popup_at_pointer(e)
            return True
        if e.button != 1:
            return False
        if e.type == Gdk.EventType._2BUTTON_PRESS:
            # 双击：把 Claude 窗口叫到前台
            self.drag = None
            focus_claude_window()
            return True
        if e.type == Gdk.EventType.BUTTON_PRESS:
            self.drag = (*self.pointer(e), self.right, self.bottom)
        return True

    def pointer(self, e):
        # layer 模式下窗口铺满屏幕不会动，窗口坐标就是屏幕坐标；X11 下直接有屏幕坐标
        return (e.x, e.y) if self.layer else (e.x_root, e.y_root)

    def on_drag(self, widget, e):
        if not self.drag:
            return False
        x, y = self.pointer(e)
        x0, y0, right, bottom = self.drag
        self.move_to(right - round(x - x0), bottom - round(y - y0))
        return True

    def on_release(self, widget, e):
        if e.button != 1 or not self.drag:
            return False
        self.drag = None
        self.settings['right'], self.settings['bottom'] = self.right, self.bottom
        save_settings(self.settings)
        self.update_input_region()
        return True

    def on_allocate(self, widget, alloc):
        # 屏幕大小定下来（或者变了）之后，桌宠的位置跟着变，输入区域也要更新
        self.area.queue_draw()
        self.update_input_region()

    # ---------- 循环 ----------
    def poll(self):
        latest = read_latest()
        if latest and latest[0] != self.last_mtime:
            self.last_mtime_seen, self.last_mtime = self.last_mtime, latest[0]
            data = latest[1]
            state = data.get('state', 'waiting')
            if state not in SPRITES:
                state = 'waiting'
            changed = state != self.state
            self.state, self.text = state, data.get('text', '')
            self.project = data.get('project', '')
            self.state_since = time.time()
            sound = data.get('sound')
            if sound in ('done', 'error') and self.sound_item.get_active() and self.last_mtime_seen:  # 刚启动时不重播旧提示音
                play(sound)
            if changed:
                self.hop_at = time.time()
        if (self.effective_state(), self.display_text(), self.W) != self.bubble_key:  # 台词变了，或等太久进入发呆
            self.layout_bubble()
            self.update_input_region()
        GLib.timeout_add(250, self.poll)
        return False

    def animate(self):
        # 只重画桌宠那一块，整屏重画太浪费
        self.area.queue_draw_area(*self.pet_rect())
        return True

    def watch_focus(self):
        should_hide = self.hide_item.get_active() and self.drag is None and claude_is_foreground()
        if should_hide and not self.hidden:
            self.win.hide()
            self.hidden = True
        elif not should_hide and self.hidden:
            self.win.show_all()
            self.hidden = False
        return True

    def watch_claude(self):
        # Claude 关掉两分钟后，桌宠也自己退出
        if claude_running():
            self.last_alive = time.time()
        elif time.time() - self.last_alive > 120:
            self.win.destroy()
            return False
        return True


if __name__ == '__main__':
    try:
        Pet()
        Gtk.main()
    except Exception:
        import traceback
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(os.path.join(RUN_DIR, 'pet-error.log'), 'w', encoding='utf-8') as f:
            traceback.print_exc(file=f)
