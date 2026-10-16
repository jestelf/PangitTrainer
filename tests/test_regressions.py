import ast
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='pangit-tests-')
import cheats
import engine
import lang
import trainer

class Memory:
    def __init__(self):
        self.bytes = {}
        self.writes = []
        self.fail = False

    def load(self, address, data):
        self.bytes.update({address + i: b for i, b in enumerate(data)})

    def read(self, address, count):
        if any(address + i not in self.bytes for i in range(count)):
            return b''
        return bytes(self.bytes[address + i] for i in range(count))

    def write(self, address, data):
        if self.fail:
            return False
        self.writes.append((address, data))
        self.load(address, data)
        return True

    def u64(self, address):
        raw = self.read(address, 8)
        return struct.unpack('<Q', raw)[0] if len(raw) == 8 else 0

    def u32(self, address):
        raw = self.read(address, 4)
        return struct.unpack('<I', raw)[0] if len(raw) == 4 else 0

from blueprint_model import Blueprint

class CraftFixture:
    table, row_struct, entries, code, function, output = (
        0x20000, 0x30000, 0x40000, 0x50000, 0x60000, 0x70000)

    def __init__(self, opcode=b'\x48'):
        self.m = Memory()
        self.fields = {}
        for i in range(1, 4):
            self.fields['Ammo_Item_%d_guid' % i] = (0x10 * i, 'IntProperty', 0, 0)
            self.fields['Item_%d_Text_guid' % i] = (0x10 * i + 4, 'IntProperty', 0, 0)
        self.m.load(self.table + 0x28, struct.pack('<QQi', self.row_struct, self.entries, 3))
        self.m.load(self.row_struct + 0x58, struct.pack('<I', 0x84))
        allocation = bytearray(40)
        allocation[0] = 0b101
        struct.pack_into('<Qiiii', allocation, 16, 0, 3, 128, 1, 1)
        self.m.load(self.table + 0x40, allocation)
        self.rows = [0x80000, 0x90000]
        for index, row in ((0, self.rows[0]), (2, self.rows[1])):
            self.m.load(self.entries + index * 24, struct.pack('<QQQ', 123 + index, row, 0))
            self.m.load(row, bytes(0x84))
            for offset, *_ in self.fields.values():
                self.m.load(row + offset, struct.pack('<i', 3))
            self.m.load(row + 0x80, struct.pack('<i', 10))

        self.m.load(self.entries + 24, struct.pack('<QQQ', 456, 0xA0000, 0))
        self.lhs = opcode + struct.pack('<Q', self.output)
        self.original = b'\x14' + self.lhs + b'\x28' + b'\x14' + self.lhs + b'\x27\x04\x0b\x53'
        self.m.load(self.code, self.original)
        self.m.load(self.output + 0x38, struct.pack('<Q', 0x180))
        self.u = SimpleNamespace(
            m=self.m, valid=lambda o: True,
            objname=lambda o: 'DT_CraftList',
            own_props=lambda o: self.fields if o == self.row_struct else {'Sussuise': (8, 'BoolProperty', 1, 0)},
            property=lambda o, n: self.output,
            function=lambda o, n: self.function,
            script=lambda o: (self.code, self.m.read(self.code, len(self.original))))
        self.debit = Blueprint(self.m, 'cls_player').craft()
        old_function, old_script = self.u.function, self.u.script
        old_props, old_property = self.u.own_props, self.u.property
        self.u.function = lambda owner, name: self.debit.fn if name == 'ExecuteUbergraph_ALS_Base_CharacterBP' else old_function(owner, name)
        self.u.script = lambda fn: self.debit.u.script(fn) if fn == self.debit.fn else old_script(fn)
        self.u.own_props = lambda owner: self.debit.u.own_props(owner) if owner == self.debit.fn else old_props(owner)
        self.u.property = lambda owner, name: self.debit.u.property(owner, name) if owner == self.debit.fn else old_property(owner, name)
        self.u.name = self.debit.u.name
        self.c = cheats.Context(self.u)
        self.c.found['datatable'] = [self.table]
        self.c.found['cls_player'] = [0xB0000]

class RegressionTests(unittest.TestCase):
    def test_free_craft_absent_ingredients_and_output_unchanged(self):
        for opcode in (b'\x48', b'\x00'):
            f = CraftFixture(opcode)
            feature = cheats.FreeCraft()
            self.assertEqual(feature.tick(f.c)[0], cheats.OK)
            code = f.m.read(f.code, len(f.original))

            self.assertEqual(code[:2], b'\x14' + opcode)
            self.assertEqual(struct.unpack('<Q', code[2:10])[0], f.output)
            self.assertEqual(code[10:14], b'\x27\x04\x0b\x53')
            for row in f.rows:
                self.assertEqual(f.m.u32(row + 0x80), 10)
                for offset, *_ in f.fields.values():
                    self.assertEqual(f.m.u32(row + offset), 0)
            self.assertEqual(feature.tick(f.c)[0], cheats.OK)
            feature.release(f.c)
            self.assertEqual(f.m.read(f.code, len(f.original)), f.original)
            for row in f.rows:
                for offset, *_ in f.fields.values():
                    self.assertEqual(f.m.u32(row + offset), 3)

    def test_free_sparse_entry_is_never_written(self):
        f = CraftFixture()
        self.assertEqual(cheats.FreeCraft().tick(f.c)[0], cheats.OK)
        self.assertFalse(any(0xA0000 <= address < 0xA0084 for address, _ in f.m.writes))

    def test_unknown_script_does_not_change_recipes(self):
        f = CraftFixture()
        f.m.load(f.code, b'\x0b' * len(f.original))
        self.assertEqual(cheats.FreeCraft().tick(f.c)[0], cheats.FAIL)
        self.assertEqual(f.m.writes, [])

    def test_invalid_allocation_bitmap_is_rejected(self):
        f = CraftFixture()
        f.m.load(f.table + 0x40, b'\x07')
        self.assertEqual(cheats.FreeCraft().tick(f.c)[0], cheats.FAIL)
        self.assertEqual(f.m.writes, [])

    def test_failed_writes_do_not_report_success(self):
        f = CraftFixture()
        f.m.fail = True
        self.assertEqual(cheats.FreeCraft().tick(f.c)[0], cheats.FAIL)

    def door_context(self, locked=True):
        state = {'locked': locked, 'key': 0x40000, 'class': 0x50000, 'valid': True}
        field = SimpleNamespace(type='BoolProperty')
        def write(f, value):
            state['locked'] = value
            return True
        u = SimpleNamespace(valid=lambda o: state['valid'], cls=lambda o: state['class'],
            field=lambda o, n: field if n == 'bIsLocked' else None,
            read=lambda f: state['locked'], write=write,
            get=lambda o, n: state['key'] if n == 'KeyItem' else state['locked'],
            set=lambda o, n, v: write(field, v))
        c = cheats.Context(u)
        c.found['key_door'] = [0x30000]
        return c, state

    def test_key_and_card_doors_open_without_inventory_and_restore(self):
        for locked in (True, False):
            c, state = self.door_context(locked)
            feature = cheats.AutoKey()
            self.assertEqual(feature.tick(c)[0], cheats.OK)
            self.assertFalse(state['locked'])
            self.assertEqual(feature.tick(c)[0], cheats.OK)
            feature.release(c)
            self.assertEqual(state['locked'], locked)

    def test_recycled_door_is_not_restored(self):
        c, state = self.door_context()
        feature = cheats.AutoKey()
        feature.tick(c)
        state['key'] += 8
        feature.release(c)
        self.assertFalse(state['locked'])

    def test_unkeyed_door_ignored(self):
        c, state = self.door_context()
        state['key'] = 0
        self.assertEqual(cheats.AutoKey().tick(c)[0], cheats.FAIL)
        self.assertTrue(state['locked'])

    def test_reconnect_forgets_old_process_patches(self):
        t = trainer.Trainer({})
        t.by_key['craft'].check.key = ('old-process',)
        t.by_key['craft'].check.sites[123] = (b'a', b'b')
        t.by_key['autokey'].saved[(123, 'bIsLocked')] = (456, 789, True)
        t.disconnect()
        self.assertIsNone(t.by_key['craft'].check.key)
        self.assertEqual(t.by_key['craft'].check.sites, {})
        self.assertEqual(t.by_key['autokey'].saved, {})

    def test_all_features_wait_without_game_objects(self):
        c = cheats.Context(SimpleNamespace(valid=lambda o: False))
        for cls in cheats.FEATURES:
            with self.subTest(feature=cls.key):
                self.assertIn(cls().tick(c)[0], (cheats.WAIT, cheats.INFO, cheats.FAIL))

    def test_translations_and_hotkeys(self):
        from app import parse_hotkey
        keys = [parse_hotkey(f.hotkey) for f in cheats.FEATURES if f.hotkey]
        self.assertEqual(len(keys), len(set(keys)))
        for path in (ROOT / 'src').glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'T' and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and any('\u0400' <= ch <= '\u04ff' for ch in arg.value):
                        self.assertIn(arg.value, lang.EN, (path.name, node.lineno))
        for cls in cheats.FEATURES:
            self.assertIn(cls.title, lang.EN)
            self.assertIn(cls.description, lang.EN)

    def test_reflection_preserves_numbered_fname_property_names(self):
        m = Memory()
        owner, first, second, field_class = 0x20000, 0x30000, 0x40000, 0x50000
        m.load(owner + 0x50, struct.pack('<Q', first))
        m.load(field_class, struct.pack('<II', 2, 0))
        for prop, number, next_prop in ((first, 0, second), (second, 2, 0)):
            raw = bytearray(0x88)
            struct.pack_into('<Q', raw, 8, field_class)
            struct.pack_into('<Q', raw, 0x18, next_prop)
            struct.pack_into('<II', raw, 0x20, 1, number)
            struct.pack_into('<I', raw, 0x44, number * 4)
            m.load(prop, raw)
        u = engine.Unreal.__new__(engine.Unreal)
        u.m = m
        u.names = SimpleNamespace(name=lambda index: {1: 'CallFunc_Result', 2: 'IntProperty'}[index])
        props = u.own_props(owner)
        self.assertEqual(set(props), {'CallFunc_Result', 'CallFunc_Result_1'})
        self.assertEqual(props['CallFunc_Result'][0], 0)
        self.assertEqual(props['CallFunc_Result_1'][0], 8)

    def test_inherited_function_lookup(self):
        m = Memory()
        child, parent, function = 0x20000, 0x30000, 0x40000
        m.load(child + 0x48, struct.pack('<Q', 0))
        m.load(parent + 0x48, struct.pack('<Q', function))
        u = engine.Unreal.__new__(engine.Unreal)
        u.m = m
        u.chain = lambda cls: [child, parent]
        u.objname = lambda obj: 'InheritedFunction'
        self.assertEqual(u.function(child, 'InheritedFunction'), function)

    def test_return_parameter_is_returned_instead_of_discarded(self):
        f = CraftFixture()
        f.m.load(f.output + 0x38, struct.pack('<Q', 0x580))
        feature = cheats.FreeCraft()
        self.assertEqual(feature.ingredient_check(f.c), 1)
        code = f.m.read(f.code, len(f.original))
        self.assertEqual(code[11:21], b'\x04' + f.lhs)

    def test_craft_widget_cached_counts_and_button_are_updated_and_restored(self):
        f = CraftFixture()
        values = {'Add Item': 10, 'Ammo Item 1': 3, 'Ammo Item 2': 1, 'Ammo Item 3': 5, 'Restart_Buttom': True}
        initial = dict(values)
        fields = {name: SimpleNamespace(name=name, type='BoolProperty' if isinstance(value, bool) else 'IntProperty') for name, value in values.items()}
        f.u.get = lambda obj, name: values[name]
        f.u.field = lambda obj, name: fields.get(name)
        f.u.read = lambda field: values.get(field.name) if field else None
        def write(field, value):
            values[field.name] = value
            return True
        f.u.write = write
        f.u.set = lambda obj, name, value: write(fields[name], value)
        f.c.found['craft_widget'] = [0xC0000]
        feature = cheats.FreeCraft()
        feature.update_ui(f.c)
        self.assertEqual(values['Add Item'], 10)
        self.assertEqual(values['Ammo Item 1'], 0)
        self.assertFalse(values['Restart_Buttom'])
        feature.release(f.c)
        self.assertEqual(values, initial)

    def test_failed_patch_restore_keeps_backup_and_can_retry(self):
        f = CraftFixture()
        patcher = cheats.CodePatch()
        patcher.build(f.c, 1, [(f.code, f.original[:1], b'\x27')])
        self.assertEqual(patcher.apply(f.c), 1)
        f.m.fail = True
        with self.assertRaises(RuntimeError):
            patcher.restore(f.c)
        self.assertTrue(patcher.sites)
        self.assertEqual(patcher.key, 1)
        f.m.fail = False
        patcher.restore(f.c)
        self.assertEqual(f.m.read(f.code, len(f.original)), f.original)
        self.assertFalse(patcher.sites)

    def test_accuracy_and_recoil_do_not_change_input_or_shot_timeline(self):
        m = Memory()
        cls, player = 0x20000, 0x30000
        fields = {
            'SS-DispersionScale': SimpleNamespace(address=0x40000, type='StructProperty'),
            'SS-UpOffsetScale': SimpleNamespace(address=0x50000, type='DoubleProperty'),
            'SS-UpOffsetTimeline': SimpleNamespace(address=0x60000, type='DoubleProperty')}
        m.load(0x40000, struct.pack('<dd', 1.5, 2.5))
        m.load(0x50000, struct.pack('<d', 3.5))
        m.load(0x60000, struct.pack('<d', .75))
        input_state = {'SS-IsShooting?': False, 'LockShootingInput': False, 'isAim': True}
        before = dict(input_state)
        def read(field):
            return struct.unpack('<d', m.read(field.address, 8))[0] if field else None
        def write(field, value):
            self.assertEqual(field.address, 0x50000)
            return m.write(field.address, struct.pack('<d', value))
        def get(obj, name):
            return read(fields[name]) if name in fields else input_state.get(name)
        def function(*args):
            raise AssertionError('Weapon functions must not be inspected or patched')
        u = SimpleNamespace(m=m, valid=lambda obj: True, cls=lambda obj: cls,
            field=lambda obj, name: fields.get(name), read=read, write=write,
            get=get, set=lambda obj, name, value: write(fields[name], value), function=function,
            struct_fields=lambda obj, name: {'X': (0, 'DoubleProperty', 0, 0), 'Y': (8, 'DoubleProperty', 0, 0)})
        c = cheats.Context(u)
        c.players = lambda: [player]
        accuracy, recoil = cheats.Accuracy(), cheats.NoRecoil()
        self.assertEqual(accuracy.tick(c)[0], cheats.OK)
        self.assertEqual(recoil.tick(c)[0], cheats.OK)
        self.assertEqual(accuracy.tick(c)[0], cheats.OK)
        self.assertEqual(recoil.tick(c)[0], cheats.OK)
        self.assertEqual(m.read(0x40000, 16), bytes(16))
        self.assertEqual(read(fields['SS-UpOffsetScale']), 0.)
        self.assertEqual(read(fields['SS-UpOffsetTimeline']), .75)
        self.assertEqual(input_state, before)
        accuracy.release(c)
        recoil.release(c)
        self.assertEqual(m.read(0x40000, 16), struct.pack('<dd', 1.5, 2.5))
        self.assertEqual(read(fields['SS-UpOffsetScale']), 3.5)
        self.assertEqual(input_state, before)

if __name__ == '__main__':
    unittest.main()
