import ctypes as C
import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import winsound

import cheats
import engine
import lang
import trainer as core
from lang import T

VERSION = '1.0.9'
BG = '#0f1412'
PANEL = '#18201d'
CARD = '#202a26'
TEXT = '#e6efe9'
MUTED = '#8da096'
ACCENT = '#7bd88f'
RED = '#ec6f6b'
AMBER = '#e5b45e'
HISTORY = core.DATA / 'history.sqlite3'
DATA_LABEL = '%LOCALAPPDATA%\\PangitTrainer' if os.environ.get('LOCALAPPDATA') else str(core.DATA)
ASSETS = pathlib.Path(getattr(sys, '_MEIPASS', pathlib.Path(__file__).resolve().parent.parent)) / 'assets'
LAUNCH_KINDS = {'trainer': 'Трейнер', 'game': 'Игра'}
LAUNCH_STATES = {'running': 'Работает', 'finished': 'Завершён', 'ended': 'Завершена', 'detached': 'Отсоединено',
                 'interrupted': 'Сеанс прерван', 'restore_error': 'Ошибка восстановления'}
EVENT_KINDS = {'app': 'Приложение', 'game': 'Игра', 'mode': 'Функция', 'diagnostic': 'Диагностика'}
STATE_COLORS = {cheats.OK: ACCENT, cheats.WAIT: AMBER, cheats.FAIL: RED, cheats.INFO: AMBER}
OPTION_VALUES = {
    'speed_factor': cheats.SPEED_FACTORS,
    'slomo_factor': cheats.SLOMO_FACTORS,
    'tempo_factor': cheats.TEMPO_FACTORS,
}

def stamp(t=None):
    return dt.datetime.fromtimestamp(t or time.time()).strftime('%d.%m.%Y  %H:%M:%S')

def duration(seconds):
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f'{h:02}:{m:02}:{s:02}'

def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)

def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}

def parse_hotkey(text):
    if not text:
        return 0, False, False
    parts = text.split('+')
    key = parts[-1][-1]
    vk = {'.': 0x6E, '/': 0x6F, '*': 0x6A}.get(key) or 0x60 + int(key)
    return vk, 'Ctrl' in parts, 'Alt' in parts

class ToggleAudio:
    def __init__(self, options, enabled=True):
        self.previous = dict(options)
        self.enabled = enabled

    def update(self, options):
        changed = [bool(v) for k, v in options.items() if self.previous.get(k) != v]
        self.previous = dict(options)
        if not changed or not self.enabled:
            return
        name = 'enabled' if any(changed) else 'disabled'
        try:
            winsound.PlaySound(str(ASSETS / (name + '.wav')), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (OSError, RuntimeError):
            self.enabled = False

class History:
    def __init__(self, path=HISTORY):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS launches (id INTEGER PRIMARY KEY, kind TEXT, started REAL, ended REAL, pid INTEGER, status TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time REAL, kind TEXT, message TEXT, extra TEXT)')
        self.run_id = None
        self.game_id = None
        self.revision = 0

    def begin(self):
        with self.lock, self.db:
            self.db.execute("UPDATE launches SET status='interrupted' WHERE kind='trainer' AND ended IS NULL")
            self.run_id = self.db.execute('INSERT INTO launches(kind,started,pid,status) VALUES (?,?,?,?)',
                                          ('trainer', time.time(), os.getpid(), 'running')).lastrowid
        self.event('app', T('Приложение запущено'), {})

    def event(self, kind, message, extra=None):
        extra = extra or {}
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(time,kind,message,extra) VALUES (?,?,?,?)', (time.time(), kind, message, json.dumps(extra, ensure_ascii=False)))
            if kind == 'game' and 'created' in extra:
                pid = extra.get('pid')
                started = extra.get('created') or time.time()
                self.game_id = self.db.execute('INSERT INTO launches(kind,started,pid,status) VALUES (?,?,?,?)', ('game', started, pid, 'running')).lastrowid
            elif kind == 'game' and self.game_id:
                self.db.execute("UPDATE launches SET ended=?,status='ended' WHERE id=?", (time.time(), self.game_id))
                self.game_id = None
            self.revision += 1

    def finish(self, restored=True):
        self.event('app', T('Приложение закрыто; функции отключены') if restored else T('Приложение закрыто с ошибкой восстановления'), {})
        with self.lock, self.db:
            if self.run_id:
                self.db.execute('UPDATE launches SET ended=?,status=? WHERE id=?', (time.time(), 'finished' if restored else 'restore_error', self.run_id))
            if self.game_id:
                self.db.execute("UPDATE launches SET ended=?,status='detached' WHERE id=? AND ended IS NULL", (time.time(), self.game_id))

    def rows(self):
        with self.lock:
            launches = self.db.execute('SELECT id,kind,started,ended,pid,status FROM launches ORDER BY started DESC LIMIT 100').fetchall()
            events = self.db.execute('SELECT id,time,kind,message FROM events ORDER BY id DESC LIMIT 150').fetchall()
            count = self.db.execute("SELECT COUNT(*) FROM launches WHERE kind='trainer'").fetchone()[0]
            return launches, events, count

class PreviewTrainer(core.Trainer):
    def __init__(self, options):
        super().__init__(options)
        self.state.update({'pid': 4242, 'text': T('Подключено · таблицы движка проверены'), 'known': True, 'created': time.time() - 754})
        self.metrics = {'health': 100.0, 'stamina': 98.0, 'enemies': 6}
        demo = {
            'god': (cheats.OK, T('Здоровье удерживается на максимуме')),
            'untouchable': (cheats.OK, T('Удары зомби не попадают: ') + '9' + T(' · стрелков обезврежено: ') + '0'),
            'fall': (cheats.OK, T('Пороги урона от падения отключены')),
            'ammo': (cheats.WAIT, T('Ожидание оружия')),
            'stamina': (cheats.OK, T('Выносливость удерживается на максимуме')),
            'invisible': (cheats.OK, T('Зрение зомби: ') + T('отключено') + T(' · зомби: ') + '6'),
            'onehit': (cheats.OK, T('Урон усилен · врагов с 1 HP: ') + '6'),
            'picklock': (cheats.OK, T('Замков: ') + '3' + T(' · идёт взлом: ') + '0' + T(' · отмычки: ') + T('есть')),
            'norecoil': (cheats.OK, T('Импульс отдачи: ') + T('снят') + T(' · тряска камеры: ') + '2'),
            'accuracy': (cheats.OK, T('Разброс снят в расчёте выстрела: ') + '4/4'),
            'drone_items': (cheats.OK, T('Ресурсов дрона в запасе: ') + '4/4'),
            'drone_time': (cheats.OK, T('Таймер дрона заморожен · дронов: ') + '1'),
            'fastpick': (cheats.OK, T('Подбор ×') + '4'),
            'speed': (cheats.OK, T('Скорость ×') + '1.5' + T(' · пресетов: ') + '16'),
            'money': (cheats.WAIT, T('Ожидание инвентаря игрока (в демо создаётся позже)')),
            'hunger': (cheats.INFO, T('В демо голод и жажда отключены игрой — заработает в полной версии')),
        }
        for key in demo:
            self.options[key] = True
        self.status = demo

    def loop(self):
        while self.running:
            time.sleep(0.1)

class Dashboard:
    def __init__(self, trainer, history, ui, hidden=False, persist=True):
        self.persist = persist
        self.t = trainer
        self.history = history
        self.t.on_event = history.event
        self.started = time.time()
        self.closing = False
        self.last_show = None
        self.last_save = None
        self.ui = ui
        self.audio = ToggleAudio(self.t.options, bool(ui.get('sound_enabled', True)))
        self.hotkeys = [(f.key, *parse_hotkey(f.hotkey)) for f in self.t.features]
        self.root = tk.Tk()
        height = min(1040, max(540, self.root.winfo_screenheight() - 90))
        width = min(1420, max(900, self.root.winfo_screenwidth() - 60))
        self.root.geometry('%dx%d' % (width, height))
        self.root.minsize(900, 540)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        try:
            self.root.iconbitmap(str(ASSETS / 'Pangit.ico'))
        except tk.TclError:
            pass
        if hidden:
            self.root.withdraw()
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure('TNotebook', background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        self.style.configure('TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(18, 8), font=('Segoe UI', 10), bordercolor=BG, lightcolor=BG, darkcolor=BG)
        self.style.map('TNotebook.Tab', background=[('selected', CARD)], foreground=[('selected', TEXT)])
        self.style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=27, font=('Segoe UI', 9), borderwidth=0, bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        self.style.configure('Treeview.Heading', background=CARD, foreground=MUTED, font=('Segoe UI', 9), padding=7, bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        self.style.map('Treeview', background=[('selected', '#2c4034')], foreground=[('selected', TEXT)])
        self.style.configure('Vertical.TScrollbar', background='#33423b', troughcolor=PANEL, borderwidth=0, arrowsize=12)
        self.style.configure('TCombobox', fieldbackground=CARD, background=CARD, foreground=TEXT, arrowcolor=TEXT, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        self.style.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)], selectbackground=[('readonly', CARD)], selectforeground=[('readonly', TEXT)])
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)
        self.build()
        self.root.bind('<MouseWheel>', self.scroll_features)
        self.thread = threading.Thread(target=self.t.loop, daemon=True)
        self.thread.start()
        self.down = {}
        self.root.after(80, self.poll)

    def label(self, parent, text, size=10, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=parent['bg'], fg=color, font=('Segoe UI', size, 'bold' if bold else 'normal'), anchor='w')

    def button(self, parent, text, command, color):
        return tk.Button(parent, text=text, command=command, bg=color, fg=TEXT, activebackground=color, activeforeground=TEXT,
                         font=('Segoe UI', 10, 'bold'), relief='flat', padx=14, pady=8, cursor='hand2', bd=0, highlightthickness=0)

    def build(self):
        self.last_revision = -1
        self.root.title(T('PANGIT Trainer'))
        head = tk.Frame(self.root, bg=BG)
        head.grid(row=0, column=0, sticky='ew', padx=28, pady=(12, 8))
        head.grid_columnconfigure(1, weight=1)
        self.label(head, 'PANGIT', 25, ACCENT, True).grid(row=0, column=0, sticky='w')
        self.label(head, 'TRAINER  /  v' + VERSION + '  /  PLAYTEST', 10, MUTED).grid(row=1, column=0, sticky='w')
        self.session_label = self.label(head, '', 10, MUTED)
        self.session_label.grid(row=0, column=1, sticky='e')
        self.label(head, T('Ctrl+F12 — отключить всё  ·  F10 — окно'), 10, MUTED).grid(row=1, column=1, sticky='e')
        switch = tk.Frame(head, bg=BG)
        switch.grid(row=0, column=2, rowspan=2, sticky='ne', padx=(18, 0))
        for code, text in (('ru', 'RU'), ('en', 'EN')):
            active = lang.current() == code
            tk.Button(switch, text=text, command=lambda code=code: self.set_language(code), bg=ACCENT if active else PANEL, fg=BG if active else MUTED,
                      activebackground=ACCENT, activeforeground=BG, font=('Segoe UI', 10, 'bold'), relief='flat', width=4, pady=4,
                      cursor='hand2', bd=0, highlightthickness=0).pack(side='left', padx=(0, 4))
        hero = tk.Frame(self.root, bg=PANEL)
        hero.grid(row=1, column=0, sticky='ew', padx=28)
        hero.grid_columnconfigure(0, weight=1)
        self.label(hero, 'PANGIT Playtest', 16, TEXT, True).grid(row=0, column=0, sticky='w', padx=18, pady=(14, 3))
        self.connection = self.label(hero, T('Поиск игры…'), 10, MUTED)
        self.connection.config(wraplength=980, justify='left')
        self.connection.grid(row=1, column=0, sticky='w', padx=18, pady=(0, 12))
        self.pid_label = self.label(hero, 'PID  —', 11, MUTED)
        self.pid_label.grid(row=0, column=1, padx=20)
        self.build_label = self.label(hero, T('Сборка UE 5.7'), 9, MUTED)
        self.build_label.grid(row=1, column=1, padx=20, sticky='e')
        metrics = tk.Frame(hero, bg=PANEL)
        metrics.grid(row=2, column=0, columnspan=2, sticky='ew', padx=18, pady=(0, 10))
        self.metric_labels = {}
        for j, (key, title) in enumerate((('health', 'ЗДОРОВЬЕ'), ('stamina', 'ВЫНОСЛИВОСТЬ'), ('enemies', 'ВРАГОВ'), ('active', 'ФУНКЦИИ'))):
            metrics.grid_columnconfigure(j, weight=1)
            f = tk.Frame(metrics, bg=CARD)
            f.grid(row=0, column=j, sticky='ew', padx=(0, 8) if j < 3 else 0)
            self.label(f, T(title), 8, MUTED).pack(anchor='w', padx=12, pady=(6, 0))
            v = self.label(f, '—', 16, TEXT, True)
            v.pack(anchor='w', padx=12, pady=(0, 5))
            self.metric_labels[key] = v
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=2, column=0, sticky='nsew', padx=28, pady=(10, 6))
        controls_page = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(controls_page, text=T('Функции'))
        controls_page.grid_columnconfigure(0, weight=1)
        controls_page.grid_rowconfigure(0, weight=1)
        self.controls_canvas = tk.Canvas(controls_page, bg=BG, highlightthickness=0, yscrollincrement=24)
        self.controls_canvas.grid(row=0, column=0, sticky='nsew')
        self.controls_scrollbar = ttk.Scrollbar(controls_page, orient='vertical', command=self.controls_canvas.yview)
        self.controls_scrollbar.grid(row=0, column=1, sticky='ns')
        self.controls_canvas.configure(yscrollcommand=self.controls_scrollbar.set)
        controls = tk.Frame(self.controls_canvas, bg=BG)
        self.controls_frame = controls
        self.controls_window = self.controls_canvas.create_window((0, 0), window=controls, anchor='nw')
        controls.bind('<Configure>', lambda event: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox('all')))
        self.controls_canvas.bind('<Configure>', self.layout_features)
        self.feature_cards = []
        columns = 5
        for c in range(columns):
            controls.grid_columnconfigure(c, weight=1, uniform='card')
        self.vars = {}
        self.state_labels = {}
        self.option_boxes = {}
        for i, f in enumerate(self.t.features):
            row, col = divmod(i, columns)
            card = tk.Frame(controls, bg=PANEL)
            self.feature_cards.append(card)
            card.grid(row=row, column=col, sticky='nsew', padx=3, pady=3)
            card.grid_columnconfigure(0, weight=1)
            top = tk.Frame(card, bg=PANEL)
            top.grid(row=0, column=0, sticky='ew', padx=12, pady=(7, 0))
            top.grid_columnconfigure(0, weight=1)
            self.label(top, f.hotkey or '—', 9, ACCENT, True).grid(row=0, column=0, sticky='w')
            v = tk.BooleanVar(value=self.t.options[f.key])
            self.vars[f.key] = v
            tk.Checkbutton(top, variable=v, command=lambda key=f.key: self.toggle(key), bg=PANEL, activebackground=PANEL, fg=ACCENT, activeforeground=ACCENT,
                           selectcolor=CARD, highlightthickness=0, cursor='hand2', bd=0, padx=0, pady=0).grid(row=0, column=1, sticky='e')
            title = self.label(card, T(f.title), 10, TEXT, True)
            title.config(wraplength=235, justify='left')
            title.grid(row=1, column=0, sticky='w', padx=12, pady=(0, 1))
            text = T(f.description) + ('  ·  ' + T('экспериментально') if f.experimental else '')
            description = self.label(card, text, 8, MUTED)
            description.config(wraplength=235, justify='left', anchor='nw')
            description.grid(row=2, column=0, sticky='w', padx=12)
            status = self.label(card, T('Выключено'), 9, MUTED)
            status.config(wraplength=235, justify='left', anchor='nw')
            status.grid(row=3, column=0, padx=12, pady=(2, 2), sticky='w')
            self.state_labels[f.key] = status
            if f.editor:
                b = tk.Button(card, text=T('Редактор…'), command=self.open_drone_editor, bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
                              font=('Segoe UI', 9, 'bold'), relief='flat', padx=10, pady=2, cursor='hand2', bd=0, highlightthickness=0)
                b.grid(row=4, column=0, padx=12, pady=(0, 6), sticky='w')
            elif f.option:
                values = ['×' + ('%g' % x) for x in OPTION_VALUES[f.option]]
                box = ttk.Combobox(card, values=values, state='readonly', width=7)
                current = float(self.t.values.get(f.option, OPTION_VALUES[f.option][0]))
                box.set('×' + ('%g' % current))
                box.bind('<<ComboboxSelected>>', lambda e, option=f.option, box=box: self.choose(option, box))
                box.grid(row=4, column=0, padx=12, pady=(0, 6), sticky='w')
                self.option_boxes[f.key] = box
            else:
                tk.Frame(card, bg=PANEL, height=4).grid(row=4, column=0)
        actions = tk.Frame(controls_page, bg=BG)
        actions.grid(row=1, column=0, columnspan=2, sticky='ew', padx=3, pady=6)
        self.button(actions, T('Включить основные'), self.enable_core, ACCENT).pack(side='left', padx=(0, 8))
        actions.winfo_children()[-1].config(fg=BG, activeforeground=BG)
        self.button(actions, T('Отключить всё'), self.disable_all, '#33423b').pack(side='left')
        self.button(actions, T('Отчёт диагностики'), self.report, '#33423b').pack(side='left', padx=8)
        self.sound_var = tk.BooleanVar(value=self.audio.enabled)
        tk.Checkbutton(actions, text=T('Звук переключений'), variable=self.sound_var, command=self.toggle_sound, bg=BG, activebackground=BG, fg=TEXT,
                       activeforeground=TEXT, selectcolor=PANEL, highlightthickness=0, font=('Segoe UI', 10), cursor='hand2').pack(side='right', padx=8)
        self.events = self.table(T('Журнал'), [('time', T('Время'), 170), ('kind', T('Тип'), 105), ('message', T('Событие'), 600)])
        self.launches = self.table(T('Запуски'), [('kind', T('Что запущено'), 100), ('start', T('Начало'), 170), ('duration', T('Время'), 90), ('pid', 'PID', 70), ('status', T('Состояние'), 160)])
        self.footer = self.label(self.root, '', 9, MUTED)
        self.footer.grid(row=3, column=0, sticky='w', padx=28, pady=(0, 12))

    def layout_features(self, event):
        self.controls_canvas.itemconfigure(self.controls_window, width=event.width)
        columns = max(2, min(5, event.width // 250))
        for index in range(5):
            self.controls_frame.grid_columnconfigure(index, weight=1 if index < columns else 0, minsize=0, uniform='card' if index < columns else '')
        for index, card in enumerate(self.feature_cards):
            row, column = divmod(index, columns)
            card.grid_configure(row=row, column=column)
        self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox('all'))

    def scroll_features(self, event):
        widget = event.widget
        if isinstance(widget, ttk.Combobox):
            return
        if widget is self.controls_canvas or str(widget).startswith(str(self.controls_frame) + '.'):
            units = -max(1, abs(event.delta) // 120) if event.delta > 0 else max(1, abs(event.delta) // 120)
            if event.delta:
                self.controls_canvas.yview_scroll(units, 'units')
                return 'break'

    def open_drone_editor(self):
        if getattr(self, 'editor', None) is not None and self.editor.winfo_exists():
            self.editor.lift()
            return
        current = dict(self.t.values.get('drone_stats') or {})
        defaults = self.t.drone_defaults()
        win = tk.Toplevel(self.root)
        self.editor = win
        win.title(T('Дрон: характеристики'))
        win.configure(bg=BG)
        win.resizable(False, False)
        try:
            win.iconbitmap(str(ASSETS / 'Pangit.ico'))
        except tk.TclError:
            pass
        self.label(win, T('Дрон: характеристики'), 14, ACCENT, True).grid(row=0, column=0, columnspan=3, sticky='w', padx=18, pady=(14, 2))
        hint = self.label(win, T('Пусто — значение игры. Применяется к текущему и новым дронам.'), 9, MUTED)
        hint.grid(row=1, column=0, columnspan=3, sticky='w', padx=18, pady=(0, 10))
        self.label(win, T('Игра'), 9, MUTED).grid(row=2, column=1, sticky='w', padx=6)
        self.label(win, T('Новое значение'), 9, MUTED).grid(row=2, column=2, sticky='w', padx=6)
        entries = {}
        for i, (name, label) in enumerate(cheats.DRONE_STATS):
            self.label(win, T(label), 10, TEXT).grid(row=3 + i, column=0, sticky='w', padx=(18, 10), pady=2)
            base = defaults.get(name)
            self.label(win, ('%g' % round(base, 3)) if isinstance(base, (int, float)) else '—', 10, MUTED).grid(row=3 + i, column=1, sticky='w', padx=6)
            e = tk.Entry(win, width=12, bg=CARD, fg=TEXT, insertbackground=TEXT, relief='flat', font=('Segoe UI', 10))
            if name in current:
                e.insert(0, '%g' % current[name])
            e.grid(row=3 + i, column=2, sticky='w', padx=(6, 18), pady=2)
            entries[name] = e
        buttons = tk.Frame(win, bg=BG)
        buttons.grid(row=4 + len(cheats.DRONE_STATS), column=0, columnspan=3, sticky='ew', padx=18, pady=(12, 16))

        def apply():
            values = {}
            for name, e in entries.items():
                text = e.get().strip().replace(',', '.')
                if not text:
                    continue
                try:
                    values[name] = float(text)
                except ValueError:
                    e.config(bg='#5a2a2a')
                    return
            self.t.values['drone_stats'] = values
            self.t.options['drone_edit'] = bool(values)
            win.destroy()

        def reset():
            self.t.values['drone_stats'] = {}
            self.t.options['drone_edit'] = False
            win.destroy()

        self.button(buttons, T('Применить'), apply, ACCENT).pack(side='left')
        buttons.winfo_children()[-1].config(fg=BG, activeforeground=BG)
        self.button(buttons, T('Сбросить к игре'), reset, '#33423b').pack(side='left', padx=8)
        self.button(buttons, T('Закрыть'), win.destroy, '#33423b').pack(side='right')

    def table(self, title, columns):
        f = tk.Frame(self.notebook, bg=PANEL)
        self.notebook.add(f, text=title)
        tree = ttk.Treeview(f, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        bar = ttk.Scrollbar(f, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        for key, label, width in columns:
            tree.heading(key, text=label, anchor='w')
            tree.column(key, width=width, minwidth=60, anchor='w', stretch=key in ('status', 'message'))
        return tree

    def set_language(self, code):
        if lang.current() == code:
            return
        lang.set_language(code)
        self.ui['language'] = code
        self.save_ui()
        for child in self.root.winfo_children():
            child.destroy()
        self.build()
        self.refresh_history()

    def choose(self, option, box):
        try:
            self.t.values[option] = float(box.get().lstrip('×'))
        except ValueError:
            pass

    def toggle(self, key):
        self.t.options[key] = self.vars[key].get()

    def save_ui(self):
        if not self.persist:
            return
        try:
            atomic_json(core.UI_SETTINGS, self.ui)
        except OSError:
            pass

    def toggle_sound(self):
        self.audio.enabled = self.sound_var.get()
        self.ui['sound_enabled'] = self.audio.enabled
        self.save_ui()

    def enable_core(self):
        for key in cheats.CORE:
            self.t.options[key] = True

    def disable_all(self):
        for key in self.t.options:
            self.t.options[key] = False

    def report(self):
        self.t.report_request = True

    def snapshot_settings(self):
        data = dict(self.t.options)
        data.update(self.t.values)
        return data

    def save(self):
        data = self.snapshot_settings()
        if self.persist and data != self.last_save:
            try:
                atomic_json(core.SETTINGS, data)
                self.last_save = data
            except OSError:
                pass

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.save()
        self.t.running = False
        self.connection.config(text=T('Отключение функций…'))
        self.root.after(50, self.await_close)

    def await_close(self):
        if self.thread.is_alive():
            self.root.after(100, self.await_close)
            return
        restored = not any(f.has_changes() for f in self.t.features)
        self.history.finish(restored)
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()

    def refresh_history(self):
        launches, events, count = self.history.rows()
        rows = {str(row[0]) for row in launches}
        for child in self.launches.get_children():
            if child not in rows:
                self.launches.delete(child)
        for id, kind, start, end, pid, status in launches:
            values = (T(LAUNCH_KINDS.get(kind, kind)), stamp(start), duration((end or time.time()) - start) if status != 'interrupted' else '—', pid or '—', T(LAUNCH_STATES.get(status, status)))
            if self.launches.exists(str(id)):
                self.launches.item(str(id), values=values)
            else:
                self.launches.insert('', 0, iid=str(id), values=values)
        self.launches.set_children('', *[str(row[0]) for row in launches])
        if self.last_revision != self.history.revision:
            self.events.delete(*self.events.get_children())
            for id, when, kind, message in events:
                self.events.insert('', 'end', iid=str(id), values=(stamp(when), T(EVENT_KINDS.get(kind, kind)), message))
            self.last_revision = self.history.revision
        self.session_label.config(text=T('Запусков трейнера: ') + str(count) + T('  ·  Сеанс ') + duration(time.time() - self.started))

    def poll(self):
        if self.closing:
            return
        foreground = engine.W.DWORD()
        engine.C.windll.user32.GetWindowThreadProcessId(engine.C.windll.user32.GetForegroundWindow(), C.byref(foreground))
        snap = self.t.snapshot()
        pid = snap['state']['pid']
        allowed = foreground.value in (os.getpid(), pid)
        key_state = engine.C.windll.user32.GetAsyncKeyState
        ctrl = bool(key_state(0x11) & 0x8000)
        alt = bool(key_state(0x12) & 0x8000)
        for key, vk, need_ctrl, need_alt in self.hotkeys:
            if not vk:
                continue
            pressed = bool(key_state(vk) & 0x8000) and ctrl == need_ctrl and alt == need_alt
            if pressed and not self.down.get(key) and allowed:
                self.t.options[key] = not self.t.options[key]
            self.down[key] = pressed
        f12 = bool(key_state(0x7b) & 0x8000) and ctrl
        if f12 and not self.down.get('f12') and allowed:
            self.disable_all()
        self.down['f12'] = f12
        f10 = bool(key_state(0x79) & 0x8000)
        if f10 and not self.down.get('f10') and allowed:
            if self.root.state() == 'withdrawn':
                self.show()
            else:
                self.root.withdraw()
        self.down['f10'] = f10
        control = read_json(core.CONTROL)
        show = control.get('show')
        if show and show != self.last_show:
            self.last_show = show
            self.show()
        state = snap['state']
        self.audio.update(self.t.options)
        self.connection.config(text=state['text'], fg=ACCENT if pid and not state['errors'] else AMBER)
        self.pid_label.config(text=f'PID  {pid or "—"}')
        self.build_label.config(text=(T('UE 5.7 · известная сборка') if state['known'] else T('UE 5.7 · автоадаптация')) if pid else T('Поиск игры'))
        for key, var in self.vars.items():
            if var.get() != self.t.options[key]:
                var.set(self.t.options[key])
            if not self.t.options[key]:
                self.state_labels[key].config(text=T('Выключено'), fg=MUTED)
            else:
                status = snap['status'].get(key)
                if not pid:
                    self.state_labels[key].config(text=T('Ожидание игры'), fg=AMBER)
                elif status:
                    self.state_labels[key].config(text=status[1], fg=STATE_COLORS.get(status[0], AMBER))
                else:
                    self.state_labels[key].config(text=T('Включается…'), fg=AMBER)
        metrics = snap['metrics']
        health, stamina = metrics.get('health'), metrics.get('stamina')
        self.metric_labels['health'].config(text=('%g' % round(health, 1)) if health is not None else '—')
        self.metric_labels['stamina'].config(text=('%g' % round(stamina, 1)) if stamina is not None else '—')
        self.metric_labels['enemies'].config(text=str(metrics.get('enemies', '—')) if pid else '—')
        self.metric_labels['active'].config(text=f'{sum(self.t.options.values())} / {len(self.t.features)}')
        self.footer.config(text=T('Настройки и история: ') + DATA_LABEL + T('  ·  Ошибок: ') + str(state['errors']) + T('  ·  Закрытие приложения отключает функции'))
        self.save()
        if not hasattr(self, 'last_history') or time.monotonic() - self.last_history >= 1:
            self.refresh_history()
            self.last_history = time.monotonic()
        self.root.after(100, self.poll)

    def run(self):
        self.root.mainloop()

def main():
    shell = C.WinDLL('shell32')
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [C.c_wchar_p]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = C.c_long
    shell.SetCurrentProcessExplicitAppUserModelID('jestelf.PangitTrainer')
    preview = '--preview' in sys.argv
    mutex = None
    if not preview:
        mutex = engine.K.CreateMutexW(None, False, 'Local\\PangitTrainer.jestelf')
        if C.get_last_error() == 183:
            atomic_json(core.CONTROL, {'show': time.time()})
            return
        atomic_json(core.CONTROL, {})
    ui = read_json(core.UI_SETTINGS)
    forced = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--lang=')), None)
    lang.set_language(forced or ui.get('language') or lang.system_language())
    options = {k: v for k, v in read_json(core.SETTINGS).items()}
    if '--read-only' in sys.argv:
        options = {}
    if '--all' in sys.argv:
        options.update({k: True for k in cheats.CORE})
    history = History() if not preview else History(pathlib.Path(os.environ.get('TEMP', '.')) / 'pangit-preview.sqlite3')
    history.begin()
    trainer = PreviewTrainer(options) if preview else core.Trainer(options)
    try:
        Dashboard(trainer, history, ui, '--background' in sys.argv, not preview).run()
    except Exception as e:
        history.event('diagnostic', T('Ошибка приложения: ') + str(e))
        history.finish(False)
        raise
    finally:
        if mutex:
            engine.K.CloseHandle(mutex)

if __name__ == '__main__':
    main()
