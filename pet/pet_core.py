# Claude 娘桌宠的“大脑”：和平台无关的那部分，Windows 版（pet.pyw）和 Linux 版（pet_linux.py）共用。
# 进来的是 mod 写的状态文件、鼠标互动和时间；出去的是现在该显示哪张立绘、气泡里写什么、身体怎么动。
# 这里不碰任何窗口和画图的接口，所以可以单独测试：python3 -m unittest discover -s pet
import json
import math
import os
import random
import time
from collections import namedtuple

# 状态 → 立绘。前面是 mod 写进状态文件的，最后三个是桌宠自己按时间推出来的
SPRITES = {
    'thinking': 'smile-closed',
    'writing': 'write',
    'tool': 'typing',
    'reading': 'notes',
    'searching': 'search',
    'delegating': 'helpers',
    'asking': 'ask',
    'compacting': 'tidy',
    'waiting': 'knock',
    'celebrate': 'confetti',
    'full': 'dizzy',
    'hello': 'wave',
    'bye': 'wave',
    'error': 'panic',
    'aborted': 'guilty',
    'stone': 'stone',
    'tea': 'tea',
    'idle': 'smile',
    'sleep': 'sleep',
}
MOD_STATES = frozenset(SPRITES) - {'tea', 'idle', 'sleep'}
# 互动和一闪而过的反应还会用到的立绘
EXTRA_SPRITES = ('blush', 'lifted', 'cookie', 'confident')
# 缺图时的替补：换了自己的立绘包、还没画新表情的时候也能照常跑
FALLBACK = {
    'ask': 'knock', 'write': 'typing', 'search': 'notes', 'helpers': 'typing', 'tidy': 'notes',
    'tea': 'smile', 'sleep': 'smile-closed', 'wave': 'knock', 'dizzy': 'panic',
    'blush': 'smile-closed', 'lifted': 'panic', 'cookie': 'smile', 'confident': 'smile',
}
SOUNDS = ('done', 'error', 'ask')

RESTING = frozenset({'waiting', 'celebrate', 'full', 'hello'})  # “轮到你了”这一类：等久了会发呆、睡着
BUSY = frozenset({'thinking', 'writing', 'tool', 'reading', 'searching', 'delegating', 'compacting'})
ANIMATED = BUSY  # 这些状态下，台词末尾的“…”会变成跳动的点点点

IDLE_AFTER = 10 * 60        # 等你回复超过 10 分钟：发呆
SLEEP_AFTER = 30 * 60       # 超过 30 分钟：睡着
NIGHT_IDLE_AFTER = 3 * 60   # 深夜（23 点到早上 6 点）困得快
NIGHT_SLEEP_AFTER = 8 * 60
BYE_SLEEP_AFTER = 8         # 会话结束，挥完手就去睡
TEA_AFTER = 25              # 一个命令跑了 25 秒还没完：喝口茶等
LONG_THINK = 45             # 想了这么久，气泡里开始报时间

HOP = 14  # 换状态时跳起的高度
BOB = 3   # 平时上下晃动的最大幅度

DOUBLE_CLICK = 0.3    # 单击要等这么久，确认不是双击的前一半，才算“戳一下”
POKE_WINDOW = 2.5     # 这么多秒里……
DIZZY_POKES = 5       # ……被戳这么多下就晕了
RUB_TURNS = 3         # 在头上来回这么多趟算摸头
RUB_STEP = 10         # 每一趟至少挪这么远（逻辑像素）
RUB_PAUSE = 0.6       # 停手这么久，之前的不算了
PET_LINGER = 1.8      # 停手之后还开心这么久
MAX_SWING = 14        # 被拎起来时最多晃这么大角度

GREETINGS = ((5, '夜深了，我陪你～'), (11, '早上好！开工啦～'), (14, '中午好，吃饭了吗？'),
             (18, '下午好！我来啦～'), (23, '晚上好！我来啦～'), (24, '夜深了，我陪你～'))
PET_LINES = ('嘿嘿～', '好舒服～', '呼噜呼噜～', '喜欢被摸头！', '再摸要秃啦…', '今天也会加油的！')
LIFT_LINES = ('哇啊——放我下来！', '要去哪儿呀？', '好高好高！', '轻、轻一点～')
LAND_LINES = ('落地～', '稳稳的！', '就待在这儿啦')
DIZZY_LINES = ('晕、晕了…@_@', '别戳啦，要坏掉了…', '天旋地转…')
COOKIE_LINES = ('好吃！谢谢～', '是 token 味的！', '嚼嚼嚼…', '再来一块？', '补充能量！')
IDLE_LINES = ('在呢～', '嗯？怎么啦', '要开始干活吗？', '戳我干嘛～', '今天也请多指教', '有什么想做的吗？',
              '我随时都在哦')
NIGHT_LINES = ('这么晚还不睡呀？', '夜深了，注意休息～')
MORNING_LINES = ('早呀～', '新的一天，加油！')

# 每张立绘配的动作
MOTION = {
    'typing': 'type', 'write': 'scribble', 'panic': 'shiver', 'stone': 'still', 'ask': 'beckon',
    'sleep': 'snooze', 'tea': 'calm', 'confetti': 'bounce', 'dizzy': 'wobble', 'blush': 'wiggle',
    'cookie': 'munch', 'lifted': 'dangle',
}
# 动作 → 身体绕哪儿转（0 是头顶，1 是脚底）
PIVOT = {'dangle': 0.12, 'wobble': 1.0, 'wiggle': 1.0}

Look = namedtuple('Look', 'state sprite text dots')
Pose = namedtuple('Pose', 'dy angle pivot')  # 往下挪多少像素、顺时针转多少度、绕哪儿转
Act = namedtuple('Act', 'name sprite text until rank')
RANK = {'dizzy': 90, 'petted': 50, 'fed': 50, 'flash': 40, 'poke': 30, 'land': 30, 'wake': 20}


def sprite_names():
    return sorted(set(SPRITES.values()) | set(EXTRA_SPRITES))


def resolve_sprite(name, exists):
    """name 这张图不在的话，顺着 FALLBACK 找一张在的；都不在就返回 None。exists(name) 说某张图在不在。"""
    seen = set()
    while name and name not in seen:
        if exists(name):
            return name
        seen.add(name)
        name = FALLBACK.get(name)
    return None


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def hour_of(now):
    return time.localtime(now).tm_hour


def is_night(now):
    return hour_of(now) >= 23 or hour_of(now) < 6


def greeting(now):
    hour = hour_of(now)
    return next(text for before, text in GREETINGS if hour < before)


class Stats:
    """今天和累计的次数（完成几轮 turns、摸头 pats、饼干 cookies、被戳 pokes），存在运行目录的 stats.json 里。
    path 给 None 就只记在内存里。"""

    def __init__(self, path=None):
        self.path = path
        self.day, self.today, self.total, self.flags = '', {}, {}, {}
        if path:
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                self.day = str(data.get('day', ''))
                self.today = {k: int(v) for k, v in dict(data.get('today', {})).items()}
                self.total = {k: int(v) for k, v in dict(data.get('total', {})).items()}
                self.flags = {str(k): str(v) for k, v in dict(data.get('flags', {})).items()}
            except Exception:
                pass

    def roll(self, now):
        day = time.strftime('%Y-%m-%d', time.localtime(now))
        if day != self.day:
            self.day, self.today = day, {}

    def add(self, key, now):
        self.roll(now)
        self.today[key] = self.today.get(key, 0) + 1
        self.total[key] = self.total.get(key, 0) + 1
        self.save()

    def once_today(self, flag, now):
        """今天第一次问返回 True，之后都是 False。"""
        self.roll(now)
        if self.flags.get(flag) == self.day:
            return False
        self.flags[flag] = self.day
        self.save()
        return True

    def bond(self):
        t = self.total
        return t.get('turns', 0) + t.get('pats', 0) + 2 * t.get('cookies', 0)

    def summary(self, now):
        self.roll(now)
        t = self.today
        return f"今天：完成 {t.get('turns', 0)} 轮 · 摸头 {t.get('pats', 0)} 次 · 饼干 {t.get('cookies', 0)} 块"

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump({'day': self.day, 'today': self.today, 'total': self.total, 'flags': self.flags}, f,
                          ensure_ascii=False)
        except Exception:
            pass


class Brain:
    def __init__(self, stats=None, rng=None, now=None):
        now = time.time() if now is None else now
        self.stats = stats or Stats()
        self.rng = rng or random.Random()
        self.state, self.text, self.kind, self.project = 'waiting', '我来啦～', '', ''
        self.since = now       # 现在这个状态是什么时候开始的
        self.awake_at = now    # 最近一次有动静（新状态或互动）的时间，发呆和睡觉从这儿算起
        self.hop_at = now
        self.turn_at = None    # 这一轮是什么时候开始的（秒），不在干活就是 None
        self.tools = 0         # 这一轮用了几次工具
        self.loaded = False    # 读过第一份状态文件了没：刚启动时不重播旧的提示音和反应
        self.flash_id = None
        self.act = None        # 正在进行的互动或反应，盖在平时的状态上面
        self.lifted = False
        self.lift_text = ''
        self.swing = self.swing_v = 0.0
        self.swing_t = now
        self.poke_due = None
        self.pokes = []
        self.cookies = []
        self.rub = None

    # ---------- mod 那边来的状态 ----------
    def update(self, data, now):
        """收到一份新的状态文件。返回要播放的提示音名字，没有就是 None。"""
        state = data.get('state', 'waiting')
        if state not in MOD_STATES:
            state = 'waiting'
        text, kind = str(data.get('text', '')), str(data.get('kind') or '')
        first, self.loaded = not self.loaded, True
        was = self.base_state(now)
        if state != self.state:
            self.hop_at = now
        if (state, text, kind) != (self.state, self.text, self.kind):
            self.since = now  # 同一个状态原样再写一遍（比如只是带来一个新反应）不重新计时
        self.state, self.text, self.kind = state, text, kind
        self.project = str(data.get('project', ''))
        self.awake_at = now
        turn = data.get('turn')
        if isinstance(turn, dict) and isinstance(turn.get('at'), (int, float)):
            self.turn_at, self.tools = turn['at'] / 1000, int(turn.get('tools') or 0)
        else:
            self.turn_at, self.tools = None, 0
        if state == 'hello' and data.get('greet'):
            self.text = greeting(now)

        sound = data.get('sound')
        if first or sound not in SOUNDS:
            sound = None
        if sound == 'done':
            self.stats.add('turns', now)
            if state == 'waiting' and hour_of(now) < 5 and self.stats.once_today('nightNudge', now):
                self.text = '搞定～夜深了早点睡'
        if was == 'sleep' and state != 'bye' and not first:
            self.start('wake', 'panic', '啊！醒了醒了！', 1.2, now)

        flash = data.get('flash')
        if isinstance(flash, dict) and flash.get('id') != self.flash_id:
            self.flash_id = flash.get('id')
            sprite = flash.get('sprite')
            if not first and self.start('flash', sprite if sprite in sprite_names() else None,
                                        str(flash.get('text', '')), float(flash.get('ms') or 2500) / 1000, now):
                self.hop_at = now
        return sound

    def base_state(self, now):
        """不算互动的话，现在是什么状态。等太久会发呆、睡着；命令跑太久会去喝茶。"""
        s, quiet = self.state, now - self.awake_at
        if s == 'bye':
            return 'sleep' if quiet > BYE_SLEEP_AFTER else s
        if s in RESTING:
            idle_after, sleep_after = (NIGHT_IDLE_AFTER, NIGHT_SLEEP_AFTER) if is_night(now) else (IDLE_AFTER,
                                                                                                    SLEEP_AFTER)
            if quiet > sleep_after:
                return 'sleep'
            if quiet > idle_after:
                return 'idle'
        if s == 'tool' and now - self.since > TEA_AFTER:
            return 'tea'
        return s

    def base_text(self, base, now):
        if base == 'idle':
            return '在发呆～ zZ'
        if base == 'sleep':
            return 'Zzz…'
        if base == 'tea':
            return f'跑了 {clock(now - self.since)}，喝口茶～'
        if base == 'thinking' and now - self.since > LONG_THINK:
            return f'想了 {clock(now - self.since)}…'
        return self.text

    # ---------- 互动 ----------
    def start(self, name, sprite, text, seconds, now):
        """开始一个互动或反应。正在进行的那个更要紧的话就不打断它，返回 False。"""
        rank = RANK[name]
        if self.lifted or (self.act and now < self.act.until and self.act.rank > rank):
            return False
        self.act = Act(name, sprite, text, now + seconds, rank)
        return True

    def pick(self, lines):
        return self.rng.choice(lines)

    def click(self, now):
        """单击。等一小会儿确认不是双击，再算戳一下。"""
        self.poke_due = now + DOUBLE_CLICK

    def double_click(self):
        self.poke_due = None

    def tick(self, now):
        if self.poke_due is not None and now >= self.poke_due:
            self.poke_due = None
            self.poke(now)
        if self.act and now >= self.act.until:
            self.act = None

    def poke(self, now):
        base = self.base_state(now)
        self.awake_at = now
        self.stats.add('pokes', now)
        self.pokes = [t for t in self.pokes if now - t < POKE_WINDOW] + [now]
        if len(self.pokes) >= DIZZY_POKES and base != 'stone':
            self.pokes = []
            self.start('dizzy', 'dizzy', self.pick(DIZZY_LINES), 3.5, now)
            return
        text, sprite = self.poke_reply(base, now)
        if self.start('poke', sprite, text, 2.6, now):
            self.hop_at = now

    def poke_reply(self, base, now):
        if base == 'sleep':
            return '唔…怎么啦？', 'panic'
        if base == 'stone':
            return '（硬邦邦的）', None
        if base == 'asking':
            return '在等你点头呢～', None
        if base in ('error', 'aborted'):
            return '呜…别戳啦', None
        if base in BUSY or base == 'tea':
            if self.turn_at is None:
                return '在忙呢，等我一下～', None
            tools = f' · 工具 {self.tools} 次' if self.tools else ''
            return f'本轮 {clock(now - self.turn_at)}{tools}', None
        if base == 'celebrate':
            return '嘿嘿，厉害吧！', 'confident'
        if base == 'bye':
            return '还想再聊会儿？', 'smile'
        lines = IDLE_LINES
        if hour_of(now) < 5:
            lines += NIGHT_LINES
        elif hour_of(now) < 10:
            lines += MORNING_LINES
        return self.pick(lines), self.pick(('smile', 'confident', None))

    def hover(self, x, now, on_head):
        """鼠标没按着键，在桌宠身上挪到了横坐标 x。在头上来回蹭够几趟就是摸头。"""
        if not on_head:
            self.rub = None
            return
        r = self.rub
        if r is None or now - r['t'] > RUB_PAUSE:
            self.rub = {'x': x, 'dir': 0, 'run': 0.0, 'turns': 0, 't': now}
            return
        dx = x - r['x']
        if abs(dx) < 2:
            return
        direction = 1 if dx > 0 else -1
        if direction == r['dir']:
            r['run'] += abs(dx)
        else:
            if r['dir'] and r['run'] >= RUB_STEP:
                r['turns'] += 1
            r['dir'], r['run'] = direction, abs(dx)
        r['x'], r['t'] = x, now
        if r['turns'] >= RUB_TURNS:
            r['turns'] = RUB_TURNS - 1  # 接着摸的话，每多一趟就续上
            self.petted(now)

    def petted(self, now):
        self.awake_at = now
        if self.act and self.act.name == 'petted' and now < self.act.until:
            self.act = self.act._replace(until=now + PET_LINGER)
        elif self.start('petted', 'blush', self.pick(PET_LINES), PET_LINGER, now):
            self.stats.add('pats', now)

    def treat(self, now):
        """喂一块小饼干。一分钟里吃太多会吃不下。"""
        self.awake_at = now
        self.cookies = [t for t in self.cookies if now - t < 60] + [now]
        if len(self.cookies) > 3:
            self.start('fed', 'guilty', '吃、吃不下了…', 3.0, now)
        elif self.start('fed', 'cookie', self.pick(COOKIE_LINES), 4.0, now):
            self.stats.add('cookies', now)
            self.hop_at = now

    def lift(self, now):
        """被鼠标拎起来了。"""
        self.act, self.lifted, self.awake_at = None, True, now
        self.lift_text = '（好重…）' if self.state == 'stone' else self.pick(LIFT_LINES)
        self.poke_due = None

    def carry(self, dx, now):
        """被拎着横向挪了 dx 像素：身体跟着往后荡。"""
        self.step_swing(now)
        self.swing_v += dx * 5

    def drop(self, now):
        self.lifted, self.awake_at = False, now
        if self.start('land', None, self.pick(LAND_LINES), 1.4, now):
            self.hop_at = now

    def step_swing(self, now):
        # 一个有阻尼的摆：被拎着的时候来回荡，放下之后很快停住
        dt = min(max(now - self.swing_t, 0), 0.05)
        self.swing_t = now
        self.swing_v += (-70 * self.swing - 6 * self.swing_v) * dt
        self.swing = min(max(self.swing + self.swing_v * dt, -MAX_SWING), MAX_SWING)
        if not self.lifted and abs(self.swing) < 0.2 and abs(self.swing_v) < 2:
            self.swing = self.swing_v = 0.0

    # ---------- 现在的样子 ----------
    def look(self, now):
        """现在该显示什么：状态名、立绘、台词、台词末尾要不要跳点点点。"""
        self.tick(now)
        base = self.base_state(now)
        sprite, text = SPRITES[base], self.base_text(base, now)
        if self.lifted:
            return Look('lifted', 'stone' if self.state == 'stone' else 'lifted', self.lift_text, False)
        if self.act:
            return Look(self.act.name, self.act.sprite or sprite, self.act.text, False)
        return Look(base, sprite, text, base in ANIMATED and (text.endswith('…') or text == '思考中'))

    def pose(self, now, look=None):
        """身体现在的位置：上下晃多少、转多少度。"""
        look = look or self.look(now)
        move = 'dangle' if look.state == 'lifted' else MOTION.get(look.sprite, 'breathe')
        self.step_swing(now)
        t, angle = now, 0.0
        if move == 'still':
            bob = 0
        elif move == 'type':        # 敲键盘：快速小幅抖动
            bob = 2.5 * math.sin(t * 9)
        elif move == 'scribble':    # 写字：手上忙，身子轻轻点
            bob = 1.5 * math.sin(t * 7)
        elif move == 'shiver':      # 慌张：发抖
            bob = 2 * math.sin(t * 25)
        elif move == 'beckon':      # 举手：隔一会儿踮一下脚，提醒你看过来
            phase = (t - self.since) % 2.6
            bob = 2 * math.sin(t * 2.2) - (9 * math.sin(phase / 0.3 * math.pi) if phase < 0.3 else 0)
        elif move == 'snooze':      # 睡着：呼吸很慢
            bob = 2 * math.sin(t * 1.0)
        elif move == 'calm':
            bob = 2 * math.sin(t * 1.6)
        elif move == 'bounce':      # 撒花：一蹦一蹦
            bob = 1 - 4 * abs(math.sin(t * 4.5))
        elif move == 'wobble':      # 晕：左摇右晃
            bob, angle = 1.5 * math.sin(t * 3), 6 * math.sin(t * 3.3)
        elif move == 'wiggle':      # 被摸头：开心地扭
            bob, angle = 1.5 * math.sin(t * 7), 2.5 * math.sin(t * 7)
        elif move == 'munch':       # 吃饼干：一口一口
            bob = 1.5 * abs(math.sin(t * 8))
        elif move == 'dangle':      # 被拎着：悬在半空荡
            bob, angle = 1.5 * math.sin(t * 6) - 5, self.swing
        else:                       # 平时：慢慢呼吸
            bob = 3 * math.sin(t * 2.2)
        if look.state == 'land':    # 刚放下：把剩下的那点晃荡晃完
            angle, move = self.swing, 'dangle'
        since = t - self.hop_at
        hop = -HOP * math.sin(since / 0.28 * math.pi) if 0 <= since < 0.28 else 0  # 换状态时小跳一下
        dy = min(max(bob + hop, -(HOP + BOB)), BOB)
        return Pose(dy, angle, PIVOT.get(move, 1.0))
