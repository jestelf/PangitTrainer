import json
import os
import pathlib
import threading
import time

import cheats
import engine
from lang import T

DATA = pathlib.Path(os.environ.get('LOCALAPPDATA') or pathlib.Path.home()) / 'PangitTrainer'
DATA.mkdir(parents=True, exist_ok=True)
SETTINGS = DATA / 'settings.json'
UI_SETTINGS = DATA / 'ui.json'
CONTROL = DATA / 'control.json'
REPORT = DATA / 'diagnostics.txt'

TICK = 0.02
SCAN_EVERY = 2.0
CONNECT_EVERY = 1.0

class Trainer:
    def __init__(self, options):
        self.features = [cls() for cls in cheats.FEATURES]
        self.by_key = {f.key: f for f in self.features}
        self.options = {f.key: False for f in self.features}
        self.options.update({k: bool(v) for k, v in options.items() if k in self.options})
        self.values = {'speed_factor': cheats.SPEED_FACTORS[0], 'slomo_factor': cheats.SLOMO_FACTORS[0], 'tempo_factor': cheats.TEMPO_FACTORS[0], 'drone_stats': {}}
        for key in ('speed_factor', 'slomo_factor', 'tempo_factor'):
            if key in options:
                try:
                    self.values[key] = float(options[key])
                except (TypeError, ValueError):
                    pass
        stats = options.get('drone_stats')
        if isinstance(stats, dict):
            names = {name for name, _ in cheats.DRONE_STATS}
            self.values['drone_stats'] = {k: float(v) for k, v in stats.items() if k in names and isinstance(v, (int, float))}
        self.running = True
        self.on_event = lambda kind, message, extra=None: None
        self.status = {}
        self.active = set()
        self.m = None
        self.u = None
        self.ctx = None
        self.scanner = None
        self.state = {'pid': 0, 'text': T('Поиск игры…'), 'known': False, 'errors': 0, 'created': None}
        self.metrics = {}
        self.lock = threading.RLock()
        self.report_request = False
        self.last_scan = 0.0
        self.scan_thread = None
        self.last_error = {}
        self.last_auto_report = 0.0

    def drone_defaults(self):
        ctx = self.ctx
        if ctx is None:
            return {}
        try:
            return self.by_key['drone_edit'].defaults(ctx)
        except Exception:
            return {}

    def snapshot(self):
        with self.lock:
            return {'state': dict(self.state), 'status': dict(self.status), 'metrics': dict(self.metrics)}

    def set_text(self, text):
        with self.lock:
            changed = self.state['text'] != text
            self.state['text'] = text
        return changed

    def disconnect(self, message=None):
        with self.lock:
            m = self.m
            self.m = None
            self.u = None
            self.ctx = None
            self.scanner = None
            self.active.clear()
            for f in self.features:
                f.__init__()
            self.status = {}
            self.metrics = {}
            self.state.update({'pid': 0, 'known': False})
        if m:
            m.close()
        if message:
            self.set_text(message)

    def connect(self):
        pid = engine.find_process()
        if not pid:
            self.set_text(T('Ожидание запущенной PANGIT'))
            return
        self.set_text(T('Подключение к игре…'))
        try:
            m = engine.Memory(pid)
            u = engine.Unreal(m)
            scanner = engine.Scanner(u, cheats.GROUPS)
            ctx = cheats.Context(u)
        except Exception as e:
            self.state['errors'] += 1
            self.set_text(T('Подключение не удалось: ') + str(e))
            time.sleep(2.0)
            return
        with self.lock:
            self.m, self.u, self.scanner, self.ctx = m, u, scanner, ctx
            self.state.update({'pid': pid, 'known': m.known_build, 'created': time.time()})
        self.on_event('game', T('Подключено: PANGIT, PID ') + str(pid), {'pid': pid, 'created': time.time()})
        self.set_text(T('Подключено · таблицы движка проверены'))
        self.last_scan = 0.0
        self.scan_thread = threading.Thread(target=self.scan_loop, args=(m, scanner, ctx), daemon=True)
        self.scan_thread.start()

    def scan_loop(self, m, scanner, ctx):
        while self.running and self.m is m:
            started = time.monotonic()
            try:
                found = scanner.scan()
                ctx.found = found
            except Exception as e:
                self.state['errors'] += 1
                self.on_event('diagnostic', T('Ошибка поиска объектов: ') + str(e))
            time.sleep(max(0.2, SCAN_EVERY - (time.monotonic() - started)))

    def release_feature(self, feature):
        if self.ctx is None:
            feature.saved.clear()
            return True
        try:
            feature.release(self.ctx)
            return True
        except Exception as e:
            self.on_event('diagnostic', T('Ошибка восстановления: ') + feature.key + ' ' + str(e))
            return False

    def release_all(self):
        for f in self.features:
            if f.key in self.active:
                if self.release_feature(f):
                    self.active.discard(f.key)

    def tick(self):
        ctx = self.ctx
        ctx.begin()
        ctx.options = {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.values.items()}
        for f in self.features:
            wanted = bool(self.options.get(f.key))
            if f.key in self.active and not wanted:
                if not self.release_feature(f):
                    self.status[f.key] = (cheats.FAIL, T('Не удалось восстановить изменения'))
                    continue
                self.active.discard(f.key)
                self.status.pop(f.key, None)
                self.on_event('mode', T(f.title) + T(' — выключено'))
            if not wanted:
                continue
            if f.key not in self.active:
                self.active.add(f.key)
                self.on_event('mode', T(f.title) + T(' — включено'))
            try:
                result = f.tick(ctx)
                self.last_error.pop(f.key, None)
            except Exception as e:
                self.state['errors'] += 1
                result = (cheats.FAIL, T('Ошибка: ') + str(e))
                if self.last_error.get(f.key) != str(e):
                    self.last_error[f.key] = str(e)
                    self.on_event('diagnostic', f.key + ': ' + str(e))
            with self.lock:
                if self.status.get(f.key) != result:
                    self.status[f.key] = result
        players = ctx.players()
        metrics = {}
        if players:
            metrics['health'] = ctx.u.get(players[0], 'Current Health')
            metrics['stamina'] = ctx.u.get(players[0], 'Stamina')
        metrics['enemies'] = len(ctx.objs('zombie')) + len(ctx.objs('human'))
        with self.lock:
            self.metrics = metrics
        if (any(state == cheats.FAIL for state, message in self.status.values())
                and time.monotonic() - self.last_auto_report > 10.0):
            self.last_auto_report = time.monotonic()
            self.report_request = True
        if self.report_request:
            self.report_request = False
            try:
                REPORT.write_text(cheats.diagnostics(ctx, self), encoding='utf-8')
                self.on_event('diagnostic', T('Диагностика сохранена: ') + str(REPORT))
            except OSError as e:
                self.on_event('diagnostic', T('Не удалось сохранить диагностику: ') + str(e))

    def loop(self):
        last_connect = 0.0
        while self.running:
            try:
                if self.m is None:
                    if time.monotonic() - last_connect >= CONNECT_EVERY:
                        last_connect = time.monotonic()
                        self.connect()
                    time.sleep(0.1)
                    continue
                if not self.m.alive():
                    self.on_event('game', T('Игра завершена'), {'pid': self.state['pid']})
                    self.disconnect(T('Игра закрыта · ожидание запуска'))
                    continue
                self.tick()
            except Exception as e:
                self.state['errors'] += 1
                self.on_event('diagnostic', T('Ошибка цикла: ') + str(e))
                if self.m is not None and not self.m.alive():
                    self.disconnect(T('Игра закрыта · ожидание запуска'))
                time.sleep(0.5)
            time.sleep(TICK)
        try:
            if self.m is not None and self.ctx is not None:
                self.release_all()
        finally:
            if self.m is not None:
                self.m.close()
