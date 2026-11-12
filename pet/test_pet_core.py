# pet_core 的测试：python3 -m unittest discover -s pet
import json
import os
import random
import tempfile
import time
import unittest

import pet_core as core
from pet_core import Brain, Stats


def at(hour, minute=0):
    """今天本地时间 hour:minute 的时间戳。"""
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1))


NOON = at(14)


def brain(now=NOON, **first):
    """一个已经读过第一份状态文件的大脑（之后的提示音和反应才会生效）。"""
    b = Brain(rng=random.Random(1), now=now)
    b.update({'state': 'waiting', 'text': '轮到你啦！', **first}, now)
    return b


class StateFile(unittest.TestCase):
    def test_unknown_state_falls_back_to_waiting(self):
        b = brain()
        b.update({'state': 'moonwalk', 'text': '？'}, NOON + 1)
        self.assertEqual(b.look(NOON + 1).sprite, 'knock')

    def test_every_state_has_a_sprite_file_name(self):
        for state in core.MOD_STATES:
            b = brain()
            b.update({'state': state, 'text': 'x'}, NOON + 1)
            self.assertIn(b.look(NOON + 1).sprite, core.sprite_names())

    def test_sound_is_muted_on_the_first_file_only(self):
        b = Brain(now=NOON)
        self.assertIsNone(b.update({'state': 'waiting', 'text': 'a', 'sound': 'done'}, NOON))
        self.assertEqual(b.update({'state': 'waiting', 'text': 'b', 'sound': 'done'}, NOON + 1), 'done')
        self.assertEqual(b.update({'state': 'asking', 'text': 'c', 'sound': 'ask'}, NOON + 2), 'ask')
        self.assertIsNone(b.update({'state': 'waiting', 'text': 'd', 'sound': 'boom'}, NOON + 3))

    def test_dots_only_while_busy(self):
        b = brain()
        b.update({'state': 'thinking', 'text': '思考中…'}, NOON + 1)
        self.assertTrue(b.look(NOON + 1).dots)
        b.update({'state': 'waiting', 'text': '等等…'}, NOON + 2)
        self.assertFalse(b.look(NOON + 2).dots)

    def test_hello_greets_by_the_hour(self):
        b = brain()
        b.update({'state': 'hello', 'text': '我来啦～', 'greet': True}, at(8))
        self.assertEqual(b.look(at(8)).text, '早上好！开工啦～')
        b.update({'state': 'hello', 'text': '清空啦，重新开始～'}, at(8, 1))
        self.assertEqual(b.look(at(8, 1)).text, '清空啦，重新开始～')


class Derived(unittest.TestCase):
    def test_waiting_goes_idle_then_asleep(self):
        b = brain()
        self.assertEqual(b.look(NOON + 60).state, 'waiting')
        self.assertEqual(b.look(NOON + core.IDLE_AFTER + 1).state, 'idle')
        asleep = b.look(NOON + core.SLEEP_AFTER + 1)
        self.assertEqual((asleep.state, asleep.sprite, asleep.text), ('sleep', 'sleep', 'Zzz…'))

    def test_night_owl_gets_sleepy_sooner(self):
        night = at(2)
        b = brain(night)
        self.assertEqual(b.look(night + core.NIGHT_SLEEP_AFTER + 1).state, 'sleep')

    def test_errors_do_not_fall_asleep(self):
        b = brain()
        b.update({'state': 'error', 'text': '出错了，来看看？'}, NOON + 1)
        self.assertEqual(b.look(NOON + 3 * core.SLEEP_AFTER).state, 'error')

    def test_asking_never_falls_asleep(self):
        b = brain()
        b.update({'state': 'asking', 'text': '可以吗？'}, NOON + 1)
        self.assertEqual(b.look(NOON + 3 * core.SLEEP_AFTER).state, 'asking')

    def test_long_command_means_tea(self):
        b = brain()
        b.update({'state': 'tool', 'text': '在跑命令…', 'kind': 'bash'}, NOON)
        self.assertEqual(b.look(NOON + 5).state, 'tool')
        # 同一个状态原样再写一遍（带来一个新反应）不重新计时
        b.update({'state': 'tool', 'text': '在跑命令…', 'kind': 'bash'}, NOON + 20)
        tea = b.look(NOON + 65)
        self.assertEqual((tea.state, tea.sprite, tea.text), ('tea', 'tea', '跑了 1:05，喝口茶～'))

    def test_long_thinking_reports_the_time(self):
        b = brain()
        b.update({'state': 'thinking', 'text': '思考中…'}, NOON)
        look = b.look(NOON + 61)
        self.assertEqual(look.text, '想了 1:01…')
        self.assertTrue(look.dots)

    def test_bye_then_sleep(self):
        b = brain()
        b.update({'state': 'bye', 'text': '下次见～'}, NOON)
        self.assertEqual(b.look(NOON + 2).sprite, 'wave')
        self.assertEqual(b.look(NOON + core.BYE_SLEEP_AFTER + 1).state, 'sleep')

    def test_new_state_wakes_her_up(self):
        b = brain()
        later = NOON + core.SLEEP_AFTER + 5
        self.assertEqual(b.look(later).state, 'sleep')
        b.update({'state': 'thinking', 'text': '思考中…'}, later)
        woke = b.look(later + 0.1)
        self.assertEqual((woke.state, woke.sprite), ('wake', 'panic'))
        self.assertEqual(b.look(later + 2).state, 'thinking')


class Reactions(unittest.TestCase):
    def test_flash_plays_once_per_id(self):
        b = brain()
        flash = {'id': 7, 'sprite': 'blush', 'text': '嘿嘿，不客气～', 'ms': 2000}
        b.update({'state': 'thinking', 'text': '思考中…', 'flash': flash}, NOON + 1)
        look = b.look(NOON + 1.5)
        self.assertEqual((look.state, look.sprite, look.text), ('flash', 'blush', '嘿嘿，不客气～'))
        self.assertEqual(b.look(NOON + 3.5).state, 'thinking')
        b.update({'state': 'tool', 'text': '在跑命令…', 'flash': flash}, NOON + 4)  # 同一个 id 再带一遍
        self.assertEqual(b.look(NOON + 4.1).state, 'tool')

    def test_flash_is_not_replayed_at_startup(self):
        b = Brain(now=NOON)
        b.update({'state': 'thinking', 'text': '思考中…', 'flash': {'id': 1, 'sprite': 'blush', 'text': 'x', 'ms': 9000}},
                 NOON)
        self.assertEqual(b.look(NOON + 0.1).state, 'thinking')

    def test_flash_without_sprite_keeps_the_current_one(self):
        b = brain()
        b.update({'state': 'tool', 'text': '在跑命令…', 'flash': {'id': 2, 'text': '收到～等我忙完', 'ms': 2000}}, NOON + 1)
        look = b.look(NOON + 1.2)
        self.assertEqual((look.sprite, look.text), ('typing', '收到～等我忙完'))

    def test_turn_done_counts_and_nudges_late_at_night(self):
        late = at(1, 30)
        b = brain(late)
        b.update({'state': 'waiting', 'text': '轮到你啦！', 'sound': 'done'}, late + 10)
        self.assertEqual(b.look(late + 10).text, '搞定～夜深了早点睡')
        b.update({'state': 'thinking', 'text': '思考中…'}, late + 20)
        b.update({'state': 'waiting', 'text': '轮到你啦！', 'sound': 'done'}, late + 30)
        self.assertEqual(b.look(late + 30).text, '轮到你啦！')  # 一晚上只唠叨一次
        self.assertEqual(b.stats.today['turns'], 2)


class Mouse(unittest.TestCase):
    def test_click_waits_for_a_possible_double_click(self):
        b = brain()
        b.click(NOON + 1)
        self.assertEqual(b.look(NOON + 1.1).state, 'waiting')
        self.assertEqual(b.look(NOON + 1 + core.DOUBLE_CLICK + 0.01).state, 'poke')

    def test_double_click_is_not_a_poke(self):
        b = brain()
        b.click(NOON + 1)
        b.double_click()
        self.assertEqual(b.look(NOON + 2).state, 'waiting')
        self.assertNotIn('pokes', b.stats.today)

    def test_poke_while_busy_reports_progress(self):
        b = brain()
        b.update({'state': 'tool', 'text': '在跑命令…', 'turn': {'at': (NOON - 151) * 1000, 'tools': 12}}, NOON)
        b.poke(NOON)
        look = b.look(NOON + 0.1)
        self.assertEqual((look.sprite, look.text), ('typing', '本轮 2:31 · 工具 12 次'))

    def test_poke_wakes_her_up(self):
        b = brain()
        later = NOON + core.SLEEP_AFTER + 5
        b.poke(later)
        self.assertEqual(b.look(later + 0.1).text, '唔…怎么啦？')
        self.assertEqual(b.look(later + 5).state, 'waiting')  # 醒了，回到“轮到你啦”

    def test_too_many_pokes_make_her_dizzy(self):
        b = brain()
        for i in range(core.DIZZY_POKES):
            b.poke(NOON + i * 0.3)
        look = b.look(NOON + 1.6)
        self.assertEqual((look.state, look.sprite), ('dizzy', 'dizzy'))
        b.poke(NOON + 2)  # 晕的时候再戳也不会打断
        self.assertEqual(b.look(NOON + 2.1).state, 'dizzy')
        self.assertEqual(b.look(NOON + 6).state, 'waiting')

    def rub(self, b, start, trips, on_head=True):
        t, x = start, 100
        for trip in range(trips):
            for _ in range(4):
                x += 6 if trip % 2 == 0 else -6
                t += 0.03
                b.hover(x, t, on_head)
        return t

    def test_rubbing_the_head_is_a_pat(self):
        b = brain()
        t = self.rub(b, NOON, 2)
        self.assertEqual(b.look(t).state, 'waiting')  # 蹭一个来回还不算
        t = self.rub(b, t, 4)
        look = b.look(t)
        self.assertEqual((look.state, look.sprite), ('petted', 'blush'))
        self.assertIn(look.text, core.PET_LINES)
        self.assertEqual(b.stats.today['pats'], 1)
        t = self.rub(b, t, 6)  # 接着摸：还是同一次，台词不变
        self.assertEqual(b.look(t).text, look.text)
        self.assertEqual(b.stats.today['pats'], 1)
        self.assertEqual(b.look(t + core.PET_LINGER + 0.1).state, 'waiting')

    def test_rubbing_elsewhere_is_not_a_pat(self):
        b = brain()
        t = self.rub(b, NOON, 8, on_head=False)
        self.assertEqual(b.look(t).state, 'waiting')

    def test_slow_drift_is_not_a_pat(self):
        b = brain()
        t, x = NOON, 100
        for i in range(8):  # 每次挪动之间都停很久
            x += 12 if i % 2 == 0 else -12
            t += 1.0
            b.hover(x, t, True)
        self.assertEqual(b.look(t).state, 'waiting')

    def test_cookies(self):
        b = brain()
        b.treat(NOON)
        look = b.look(NOON + 0.1)
        self.assertEqual((look.state, look.sprite), ('fed', 'cookie'))
        for i in range(1, 4):
            b.treat(NOON + i * 5)
        full = b.look(NOON + 15.1)
        self.assertEqual((full.sprite, full.text), ('guilty', '吃、吃不下了…'))
        self.assertEqual(b.stats.today['cookies'], 3)

    def test_lift_carry_drop(self):
        b = brain()
        b.lift(NOON)
        self.assertEqual(b.look(NOON).sprite, 'lifted')
        t = NOON
        for _ in range(5):
            t += 0.016
            b.carry(12, t)
        self.assertGreater(b.pose(t).angle, 1)  # 往右拎，身子往左荡（顺时针）
        self.assertLessEqual(abs(b.pose(t).angle), core.MAX_SWING)
        self.assertEqual(b.pose(t).pivot, core.PIVOT['dangle'])
        b.drop(t)
        self.assertEqual(b.look(t + 0.1).state, 'land')
        for i in range(200):
            t += 0.033
            b.pose(t)
        self.assertEqual(b.pose(t).angle, 0)  # 放下之后会停住
        self.assertEqual(b.look(t).state, 'waiting')

    def test_stone_is_heavy(self):
        b = brain()
        b.update({'state': 'stone', 'text': '石化了…'}, NOON)
        b.lift(NOON + 1)
        look = b.look(NOON + 1)
        self.assertEqual((look.sprite, look.text), ('stone', '（好重…）'))

    def test_body_stays_inside_the_window(self):
        b = brain()
        for sprite in core.sprite_names():
            for i in range(400):
                pose = b.pose(NOON + i * 0.033, core.Look('x', sprite, '', False))
                self.assertLessEqual(pose.dy, core.BOB)
                self.assertGreaterEqual(pose.dy, -(core.HOP + core.BOB))
                self.assertLessEqual(abs(pose.angle), core.MAX_SWING)


class Bookkeeping(unittest.TestCase):
    def test_stats_persist_and_roll_over(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'run', 'stats.json')
            s = Stats(path)
            s.add('pats', NOON)
            s.add('cookies', NOON)
            self.assertTrue(s.once_today('nightNudge', NOON))
            self.assertFalse(s.once_today('nightNudge', NOON))
            again = Stats(path)
            self.assertEqual(again.today, {'pats': 1, 'cookies': 1})
            self.assertEqual(again.bond(), 3)
            self.assertEqual(again.summary(NOON), '今天：完成 0 轮 · 摸头 1 次 · 饼干 1 块')
            again.add('pats', NOON + 86400)  # 第二天
            self.assertEqual(again.today, {'pats': 1})
            self.assertEqual(again.total['pats'], 2)
            self.assertTrue(again.once_today('nightNudge', NOON + 86400))
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f)['total'], {'pats': 2, 'cookies': 1})

    def test_broken_stats_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'stats.json')
            with open(path, 'w') as f:
                f.write('{oops')
            s = Stats(path)
            s.add('turns', NOON)
            self.assertEqual(s.today, {'turns': 1})

    def test_missing_sprites_fall_back(self):
        have = {'knock', 'smile', 'smile-closed', 'panic', 'typing', 'notes'}
        self.assertEqual(core.resolve_sprite('ask', have.__contains__), 'knock')
        self.assertEqual(core.resolve_sprite('dizzy', have.__contains__), 'panic')
        self.assertEqual(core.resolve_sprite('smile', have.__contains__), 'smile')
        self.assertIsNone(core.resolve_sprite('stone', have.__contains__))
        for name in core.FALLBACK.values():  # 替补必须是最早那批立绘里的
            self.assertNotIn(name, core.FALLBACK)

    def test_every_shipped_sprite_exists(self):
        sprites = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sprites')
        for name in core.sprite_names():
            self.assertTrue(os.path.exists(os.path.join(sprites, name + '.png')), name)


if __name__ == '__main__':
    unittest.main()
