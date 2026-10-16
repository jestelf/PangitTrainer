import unittest
from unittest.mock import patch
from test_regressions import Memory
import cheats
from blueprint_model import Blueprint

class InteractionTests(unittest.TestCase):
    def test_craft_skips_all_debits_and_keeps_existing_ingredients(self):
        for stock in (0, 1, 99):
            b = Blueprint(Memory(), 'cls_player').craft()
            original = b.program()
            patcher = cheats.CodePatch()
            self.assertEqual(cheats.craft_consumption(b.c, patcher), 3)
            result = b.run(stock=stock)
            self.assertTrue(result['values'][b.props['crafted']])
            self.assertEqual(result['stock'], stock)
            self.assertEqual(result['events'], [])
            self.assertEqual(cheats.craft_consumption(b.c, patcher), 3)
            patcher.restore(b.c)
            self.assertEqual(b.program(), original)
            self.assertTrue(any(event[0] == 'remove' for event in b.run(stock=stock)['events']))

    def test_cards_t1_t2_open_without_items_or_consumption(self):
        b = Blueprint(Memory(), 'cls_code').cards()
        original = b.program()
        for index, entry in enumerate(b.entries):
            suffix = '' if index == 0 else '_1'
            self.assertFalse(b.run(entry)['values'].get(b.props['opened' + suffix], False))
        patcher = cheats.CodePatch()
        self.assertEqual(cheats.card_access(b.c, patcher), 4)
        for index, entry in enumerate(b.entries):
            suffix = '' if index == 0 else '_1'
            for stock in (0, 3):
                result = b.run(entry, stock=stock)
                self.assertTrue(result['values'][b.props['opened' + suffix]])
                self.assertEqual(result['stock'], stock)
                self.assertEqual(result['events'], [])
        self.assertEqual(cheats.card_access(b.c, patcher), 4)
        patcher.restore(b.c)
        self.assertEqual(b.program(), original)

    def test_lockpick_entry_adds_item_on_game_thread_without_existing_stock(self):
        b = Blueprint(Memory(), 'cls_minibase').lock_entry()
        original = b.program()
        self.assertEqual(b.run(b.entrypoint)['events'], [])
        patcher = cheats.CodePatch()
        self.assertEqual(cheats.lockpick_entry(b.c, patcher), 2)
        for stock in (0, 10):
            result = b.run(b.entrypoint, stock=stock)
            self.assertEqual(result['stock'], stock + 1)
            self.assertEqual(result['events'], [('add', 0x43000, 0x800000, 1)])
        self.assertEqual(len(b.run(b.entrypoint, fail_add=True)['events']), 1)
        self.assertEqual(cheats.lockpick_entry(b.c, patcher), 2)
        patcher.restore(b.c)
        self.assertEqual(b.program(), original)

    def test_lockpick_spawn_selects_normal_unlock_animation(self):
        b = Blueprint(Memory(), 'cls_picklock').spawn()
        original = b.program()
        self.assertEqual(b.run()['values'][b.props['PickLockState']], 6)
        patcher = cheats.CodePatch()
        self.assertEqual(cheats.lockpick_spawn(b.c, patcher), 1)
        result = b.run()
        self.assertEqual(result['values'][b.props['PickLockState']], 5)
        self.assertTrue(result['values']['spawned'])
        self.assertEqual(sum(a != z for a, z in zip(original, b.program())), 1)
        patcher.restore(b.c)
        self.assertEqual(b.program(), original)

    def test_unrecognized_and_incomplete_sequences_are_rejected(self):
        for group, setup, method, broken in (
                ('cls_player', 'craft', cheats.craft_consumption, 'CallFunc_RemoveItemByDataAsset_bSuccess_1'),
                ('cls_code', 'cards', cheats.card_access, 'CallFunc_CheckTotal_KEY_TotalAmmo_1'),
                ('cls_minibase', 'lock_entry', cheats.lockpick_entry, 'CallFunc_CheckTotal_KEY_TotalAmmo'),
                ('cls_picklock', 'spawn', cheats.lockpick_spawn, 'PickLockState')):
            b = getattr(Blueprint(Memory(), group), setup)()
            b.types.pop(broken)
            self.assertEqual(method(b.c, cheats.CodePatch()), 0)
            self.assertEqual(b.m.writes, [])

    def test_picklock_feature_changes_only_active_attempt(self):
        b = Blueprint(Memory(), 'cls_picklock').spawn()
        b.c.found['cls_minibase'] = [0x61000]
        games = {
            1: {'GameSpawned': False, 'MiniGameState': 1, 'PickLockState': 6},
            2: {'GameSpawned': True, 'MiniGameState': 5, 'PickLockState': 6},
            3: {'GameSpawned': True, 'MiniGameState': 1, 'WantExit': True, 'PickLockState': 6},
            4: {'GameSpawned': True, 'MiniGameState': 1, 'ProcessToExit': True, 'PickLockState': 6},
            5: {'GameSpawned': True, 'MiniGameState': 1, 'IsGameComplete': True, 'PickLockState': 0},
            6: {'GameSpawned': True, 'MiniGameState': 1, 'PickLockState': 6}}
        b.c.found['picklock'] = list(games)
        b.u.get = lambda obj, name: games[obj].get(name)
        b.u.set = lambda obj, name, value: games[obj].__setitem__(name, value) is None
        before = {key:dict(value) for key,value in games.items()}
        feature = cheats.PickLock()
        with patch('cheats.lockpick_entry', return_value=2):
            self.assertEqual(feature.tick(b.c)[0], cheats.OK)
            self.assertEqual(games[6]['PickLockState'], 5)
            for key in range(1,6): self.assertEqual(games[key], before[key])
        feature.release(b.c)
