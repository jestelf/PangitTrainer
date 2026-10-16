import os
import struct
import time
from collections import deque

from engine import pointer
from lang import T

OK, WAIT, FAIL, INFO = 'ok', 'wait', 'fail', 'info'

GROUPS = {
    'player': ('ALS_Base_CharacterBP_C', 'live'),
    'zombie': ('AGLS_ZombieCharacterCore', 'live'),
    'human': ('AGLS_HumanAI_CharacterBase', 'live'),
    'zombie_attack': ('Zombie_AttackSequenceNotify_C', 'live'),
    'zombie_ctl': ('AGLS_Zombie_ControllerAI', 'live'),
    'zombie_ctl_cdo': ('AGLS_Zombie_ControllerAI', 'cdo'),
    'pistol': ('BP_I_Pistol_Actor_C', 'live'),
    'rifle': ('BP_I_Rifle_Actor_For_Player_C', 'live'),
    'picklock': ('MiniGame_PickLock_C', 'live'),
    'radial': ('BP_MiniGamesRadial_C', 'live'),
    'drone': ('BP_CombatDroneComponent_C', 'live'),
    'drone_tpl': ('BP_CombatDroneComponent_C', 'arch'),
    'datatable': ('DataTable', 'live'),
    'inventory': ('BP_InventoryComponent_C', 'live'),
    'world': ('WorldSettings', 'live'),
    'preset': ('CMC_MovementPreset', 'live'),
    'cfg_player': ('ALS_PlayerDamageConfig_C', 'cdo'),
    'cfg_zombie': ('ALS_ZombieAIs_Damage_C', 'cdo'),
    'items': ('PDA_Item_C', 'live'),
    'shake': ('LegacyCameraShake', 'any'),
    'cls_player': ('ALS_Base_CharacterBP_C', 'class'),
    'cls_anim': ('AGLS_PlayerAnimBP_C', 'class'),
    'cls_zombie_ctl': ('AGLS_ZombieAI_Controller_C', 'class'),
    'cls_pickup': ('ALS_Ability_PickupLootItem_C', 'class'),
    'cls_character': ('Character', 'class'),
    'cls_texture': ('Texture2D', 'class'),
    'key_door': ('BP_InventoryDoor_C', 'live'),
    'minibase': ('BPC_MiniGameBase_C', 'live'),
    'cls_minibase': ('BPC_MiniGameBase_C', 'class'),
    'craft_widget': ('WBP_Craft_Item_C', 'live'),
    'cls_code': ('BP_Code_C', 'class'),
    'cls_picklock': ('MiniGame_PickLock_C', 'class'),
    'cls_inventory': ('BP_InventoryComponent_C', 'class'),
    'cls_radial': ('BP_MiniGamesRadial_C', 'class'),
}

SPEED_FACTORS = (1.5, 2.0, 3.0)
TEMPO_FACTORS = (1.25, 1.5, 2.0)
PICKUP_RATE = 4.0
DRONE_ITEMS = ('DA_Injector_Drone', 'DA_Grenade_Drone', 'DA_Grenade_Smoke_Drone', 'DA_Battary_Drone')
LOCKPICK_ITEMS = ('DA_Item_Skeleton_kEY',)
SHAKES = ('CamShake_Shooting_Shake_C', 'CamShake_Sniper_Rifle_CameraShake_C')
SLOT_FIELDS = ('GUID', 'Item', 'Amount', 'InventoryPosition', 'SlotSize', 'bIsRotated', 'ShortcutIndex', 'bIsEquipped', 'GenericCounter')
SLOMO_FACTORS = (0.5, 0.25, 0.1)
HEALTH_FALLBACK = 100.0
STAMINA_MAX = 100.0
MONEY_TARGET = 999999
AMMO_RESERVE = 999
MAGAZINES = 99

class Context:
    def __init__(self, u):
        self.u = u
        self.found = {key: [] for key in GROUPS}
        self.cache = {}
        self.options = {}

    def begin(self):
        self.cache = {}

    def objs(self, key):
        if key not in self.cache:
            self.cache[key] = [o for o in self.found.get(key, ()) if self.u.valid(o)]
        return self.cache[key]

    def players(self):
        return [p for p in self.objs('player') if self.u.field(p, 'Current Health') is not None]

    def raise_to(self, obj, name, target):
        f = self.u.field(obj, name)
        value = self.u.read(f)
        if value is None:
            return None
        if value < target:
            return self.u.write(f, target)
        return False

    def health_cap(self):
        for cfg in self.objs('cfg_player'):
            value = self.u.get(cfg, 'MaxHealth')
            if value and 1 <= value <= 100000:
                return max(float(value), HEALTH_FALLBACK)
        return HEALTH_FALLBACK

    def pointer_field(self, obj, name):
        value = self.u.get(obj, name)
        return value if value and self.u.valid(value) else 0

    def heal(self, player):
        u = self.u
        if u.get(player, 'IsDeath'):
            return False
        self.raise_to(player, 'Current Health', self.health_cap())
        attributes = self.pointer_field(player, 'Attributes')
        if attributes:
            offset = u.struct_offset(attributes, 'Health')
            if offset is not None:
                raw = u.m.read(attributes + offset + 8, 8)
                if len(raw) == 8:
                    base, current = struct.unpack('<ff', raw)
                    if 0 < base < 1e7 and current < base:
                        u.m.write(attributes + offset + 12, struct.pack('<f', base))
        survival = self.pointer_field(player, 'BP_InventorySurvivalComponent')
        if survival:
            top = u.get(survival, 'PlayerMaxHealth')
            if top:
                self.raise_to(survival, 'PlayerHealth', top)
        return True

    def owned_weapons(self):
        out = []
        seen = set()
        for p in self.players():
            for name in ('Pistol_Equip', 'PistolActor', 'Rifle_Equip', 'RifleActor'):
                w = self.pointer_field(p, name)
                if w and w not in seen:
                    seen.add(w)
                    out.append(('pistol' if self.u.field(w, 'PistolAsset') is not None else 'rifle', w))
        return out

    def magazine_size(self, weapon):
        u = self.u
        offset = u.struct_offset(weapon, 'PistolAsset')
        if offset is not None:
            for name, (off, kind, mask, sub) in u.struct_fields(weapon, 'PistolAsset').items():
                if name.startswith('MaxAmmoCount') and kind == 'IntProperty':
                    raw = u.m.read(weapon + offset + off, 4)
                    if len(raw) == 4:
                        value = struct.unpack('<i', raw)[0]
                        if 0 < value <= 1000:
                            return value
        table = self.pointer_field(weapon, 'RifleAssetsDataTable')
        model = u.get(weapon, 'RifleModelIndex')
        if table and isinstance(model, int) and model >= 0:
            rows, fields = u.table_rows(table)
            if model < len(rows):
                entry = next((v for k, v in fields.items() if k.startswith('MaxAmmoCoutPerMagazine_') and v[1] == 'IntProperty'), None)
                if entry:
                    raw = u.m.read(rows[model] + entry[0], 4)
                    if len(raw) == 4:
                        value = struct.unpack('<i', raw)[0]
                        if 0 < value <= 1000:
                            return value
        return 0

    def player_inventories(self):
        out = []
        for inv in self.objs('inventory'):
            if self.u.clsname(inv) == 'BP_InventoryCharacterComponent_C' or self.u.get(inv, 'bIsPlayerOwned') or self.u.get(inv, 'PlayerController'):
                out.append(inv)
        return out

    def first(self, key):
        found = self.objs(key)
        return found[0] if found else 0

    def item(self, name):
        for item in self.objs('items'):
            if self.u.objname(item) == name:
                return item
        return 0

    def slot_layout(self, inv):
        info = self.u.array(inv, 'ItemSlots')
        if not info:
            return None
        data, count, size, fields = info
        off = {}
        for k, v in fields.items():
            head = k.split('_')[0]
            if head in SLOT_FIELDS:
                off[head] = v[0]
        if not size or any(k not in off for k in SLOT_FIELDS[:8]) or not 0 <= count <= 512:
            return None
        return data, count, size, off

    def stock(self, inv, item, amount):
        u = self.u
        m = u.m
        layout = self.slot_layout(inv)
        if not layout:
            return False
        data, count, size, off = layout
        for i in range(count):
            base = data + i * size
            if m.u64(base + off['Item']) == item:
                raw = m.read(base + off['Amount'], 4)
                if len(raw) == 4 and struct.unpack('<i', raw)[0] < amount:
                    m.write(base + off['Amount'], struct.pack('<i', amount))
                return True
        header = inv + u.struct_offset(inv, 'ItemSlots')
        raw = m.read(header + 12, 4)
        if len(raw) != 4 or not pointer(data) or count >= struct.unpack('<i', raw)[0]:
            return False
        cell = self.free_cell(inv, data, count, size, off)
        if cell is None:
            return False
        x, y, width, cache = cell
        slot = bytearray(size)
        slot[off['GUID']:off['GUID'] + 16] = os.urandom(16)
        struct.pack_into('<Q', slot, off['Item'], item)
        struct.pack_into('<i', slot, off['Amount'], amount)
        struct.pack_into('<2d', slot, off['InventoryPosition'], float(x), float(y))
        struct.pack_into('<2d', slot, off['SlotSize'], 1.0, 1.0)
        slot[off['bIsRotated']] = 0
        slot[off['bIsEquipped']] = 0
        struct.pack_into('<i', slot, off['ShortcutIndex'], -1)
        if 'GenericCounter' in off:
            struct.pack_into('<i', slot, off['GenericCounter'], 0)
        if not m.write(data + count * size, bytes(slot)):
            return False
        if cache:
            m.write(cache + y * width + x, b'\x01')
        return m.write(header + 8, struct.pack('<i', count + 1))

    def free_cell(self, inv, data, count, size, off):
        u = self.u
        m = u.m
        width, height = 7, 6
        config = self.pointer_field(inv, 'InventoryConfig')
        f = u.field(config, 'InventorySize') if config else None
        raw = m.read(f.address, 16) if f else b''
        if len(raw) == 16:
            w, h = struct.unpack('<2d', raw)
            if 1 <= w <= 64 and 1 <= h <= 64:
                width, height = int(w), int(h)
        used = set()
        for i in range(count):
            base = data + i * size
            raw = m.read(base + off['InventoryPosition'], 16) + m.read(base + off['SlotSize'], 16)
            if len(raw) != 32:
                continue
            x, y, w, h = struct.unpack('<4d', raw)
            if m.read(base + off['bIsRotated'], 1) == b'\x01':
                w, h = h, w
            for dx in range(max(1, int(w))):
                for dy in range(max(1, int(h))):
                    used.add((int(x) + dx, int(y) + dy))
        cache = 0
        f = u.field(inv, 'AllocationsCache')
        raw = m.read(f.address, 16) if f else b''
        if len(raw) == 16:
            ptr, n, cap = struct.unpack('<Qii', raw)
            if n == width * height and pointer(ptr):
                cache = ptr
                for k, b in enumerate(m.read(ptr, n)):
                    if b:
                        used.add((k % width, k // width))
        for y in range(height):
            for x in range(width):
                if (x, y) not in used:
                    return x, y, width, cache
        return None

    def keep_items(self, names, amount=None):
        inventories = self.player_inventories()
        if not inventories:
            return None
        kept = 0
        for name in names:
            item = self.item(name)
            if not item:
                continue
            top = amount or self.u.get(item, 'MaxStackAmount') or 10
            if self.stock(inventories[0], item, max(1, min(int(top), 999))):
                kept += 1
        return kept

    def inventory_slots(self, inv):
        info = self.u.array(inv, 'ItemSlots')
        if not info:
            return []
        data, count, size, fields = info
        item_off = next((v[0] for k, v in fields.items() if k.startswith('Item_')), None)
        amount_off = next((v[0] for k, v in fields.items() if k.startswith('Amount_')), None)
        if item_off is None or amount_off is None or not size or not 0 <= count <= 512:
            return []
        out = []
        for i in range(count):
            base = data + i * size
            item = self.u.m.u64(base + item_off)
            if item:
                out.append((item, base + amount_off))
        return out

class Feature:
    key = ''
    hotkey = ''
    title = ''
    description = ''
    experimental = False
    option = None
    editor = False

    def __init__(self):
        self.saved = {}

    def tick(self, c):
        return WAIT, T('Ожидание игры')

    def release(self, c):
        self.saved.clear()

    def has_changes(self):
        return bool(self.saved) or bool(getattr(self, 'ui_saved', {})) or any(
            isinstance(value, CodePatch) and bool(value.sites) for value in self.__dict__.values())

def restore_floats(c, saved, name, expect=None):
    for obj, original in list(saved.items()):
        if c.u.valid(obj):
            f = c.u.field(obj, name)
            current = c.u.read(f)
            if current is not None and (expect is None or abs(current - expect(obj)) < 1e-3):
                c.u.write(f, original)
    saved.clear()

class CodePatch:
    def __init__(self):
        self.sites = {}
        self.key = None

    def build(self, c, key, sites):
        if key != self.key:
            self.restore(c)
            addresses = set()
            for address, old, new in sites:
                if len(old) != len(new) or not old:
                    raise ValueError('Patch sizes differ')
                span = set(range(address, address + len(old)))
                if addresses & span:
                    raise ValueError('Patch sites overlap')
                addresses.update(span)
            self.key = key
            self.sites = {address: (old, new) for address, old, new in sites}

    def apply(self, c):
        done = 0
        for address, (old, new) in self.sites.items():
            current = c.u.m.read(address, len(old))
            if current == old:
                if c.u.m.write(address, new) and c.u.m.read(address, len(new)) == new:
                    done += 1
            elif current == new:
                done += 1
        return done

    def restore(self, c):
        failed = {}
        for address, (old, new) in self.sites.items():
            if c.u.m.read(address, len(new)) == new:
                if not c.u.m.write(address, old) or c.u.m.read(address, len(old)) != old:
                    failed[address] = (old, new)
        self.sites = failed
        if failed:
            raise RuntimeError(T('Не удалось восстановить изменения'))
        self.key = None

def occurrences(raw, pattern):
    out = []
    i = raw.find(pattern)
    while i >= 0:
        out.append(i)
        i = raw.find(pattern, i + 1)
    return out

def script_of(c, cls_key, function):
    cls = c.first(cls_key)
    if not cls:
        return 0, 0, 0, b''
    f = c.u.function(cls, function)
    if not f:
        return cls, 0, 0, b''
    data, raw = c.u.script(f)
    return cls, f, data, raw

def constant_output(c, patch, cls, function_name, output_name, kind, value):
    function = c.u.function(cls, function_name) if cls else 0
    data, raw = c.u.script(function) if function else (0, b'')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    entry = c.u.own_props(function).get(output_name)
    prop = c.u.property(function, output_name) if entry else 0
    if not prop or entry[1] != kind:
        return 0
    flags_raw = c.u.m.read(prop + 0x38, 8)
    if len(flags_raw) != 8:
        return 0
    flags = struct.unpack('<Q', flags_raw)[0]
    if flags & 0x180 != 0x180:
        return 0
    operand = None
    for opcode in (b'\x48', b'\x00'):
        candidate = opcode + struct.pack('<Q', prop)
        prefix = b'\x14' if kind == 'BoolProperty' else b'\x0f' + struct.pack('<Q', prop)
        if prefix + candidate in raw:
            operand = candidate
            break
    if operand is None:
        return 0
    if kind == 'BoolProperty':
        body = b'\x14' + operand + (b'\x27' if value else b'\x28')
    elif kind == 'IntProperty':
        body = b'\x0f' + struct.pack('<Q', prop) + operand + b'\x1d' + struct.pack('<i', value)
    else:
        return 0
    body += b'\x04' + (operand if flags & 0x400 else b'\x0b') + b'\x53'
    if len(body) > len(raw):
        return 0
    patch.build(c, key, [(data, raw, body + b'\x0b' * (len(raw) - len(body)))])
    return patch.apply(c)

def success_assignment(c, patch, cls, function_name, field_name):
    function = c.u.function(cls, function_name) if cls else 0
    data, raw = c.u.script(function) if function else (0, b'')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    field = c.u.class_props(cls).get(field_name)
    prop = c.u.property(cls, field_name) if field else 0
    if not prop or field[1] != 'BoolProperty':
        return 0
    pattern = b'\x14\x01' + struct.pack('<Q', prop) + b'\x28'
    matches = occurrences(raw, pattern)
    if len(matches) != 1:
        return 0
    patch.build(c, key, [(data + matches[0] + 10, b'\x28', b'\x27')])
    return patch.apply(c)

def local_operand(c, owner, name, kind=None):
    entry = c.u.own_props(owner).get(name)
    prop = c.u.property(owner, name) if entry else 0
    if not prop or (kind and entry[1] != kind):
        return b''
    return b'\x00' + struct.pack('<Q', prop)

def true_branch(raw, operand):

    matches = [i - 5 for i in occurrences(raw, operand)
               if i >= 5 and raw[i - 5] == 0x07
               and 0 <= struct.unpack_from('<I', raw, i - 4)[0] < len(raw)] if operand else []
    if len(matches) != 1:
        return None
    start = matches[0] + 5
    return start, operand, b'\x27' + b'\x0b' * 8

def removal_call(c, function, raw, suffix, literal=False, inventory_suffix=None):

    inv = local_operand(c, function, 'CallFunc_GetComponentByClass_ReturnValue' + (suffix if inventory_suffix is None else inventory_suffix), 'ObjectProperty')
    result = local_operand(c, function, 'CallFunc_RemoveItemByDataAsset_bSuccess' + suffix, 'BoolProperty')
    if not inv or not result:
        return None
    payload_size = 37 if literal else 41
    prefix = b'\x19' + inv + struct.pack('<I', payload_size) + bytes(8) + b'\x45'
    matches = occurrences(raw, prefix)
    found = []
    for start in matches:
        call = start + 22
        if c.u.name(struct.unpack_from('<I', raw, call + 1)[0]) != 'RemoveItemByDataAsset':
            continue
        end = start + 22 + payload_size
        if raw[end - 10:end] != result + b'\x16':
            continue
        if literal and (raw[call + 13] != 0x20 or raw[call + 22:call + 27] != b'\x1d\x01\x00\x00\x00'):
            continue
        if not literal and (raw[call + 13] != 0 or raw[call + 22] != 0):
            continue
        found.append((start, end, result))
    return found[0] if len(found) == 1 else None

def craft_consumption(c, patch):
    cls, function, data, raw = script_of(c, 'cls_player', 'ExecuteUbergraph_ALS_Base_CharacterBP')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    sites = []
    for suffix in ('', '_1', '_2'):
        call = removal_call(c, function, raw, suffix, inventory_suffix={'': '_3', '_1': '_4', '_2': '_5'}[suffix])
        if not call:
            return 0
        start, end, result = call
        if raw[end:end + 1] != b'\x07' or raw[end + 5:end + 14] != result:
            return 0

        target = end + 14
        sites.append((data + start, raw[start:start + 5], b'\x06' + struct.pack('<I', target)))
    patch.build(c, key, sites)
    return patch.apply(c)

def card_access(c, patch):
    cls, function, data, raw = script_of(c, 'cls_code', 'ExecuteUbergraph_BP_Code')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    sites = []
    for suffix in ('', '_1'):
        count = local_operand(c, function, 'CallFunc_CheckTotal_KEY_TotalAmmo' + suffix, 'IntProperty')
        condition = local_operand(c, function, 'CallFunc_GreaterEqual_IntInt_ReturnValue' + suffix, 'BoolProperty')
        gate = true_branch(raw, condition)
        call = removal_call(c, function, raw, suffix, literal=True)
        if not count or count not in raw or not gate or not call:
            return 0
        start, end, result = call
        if raw[end:end + 1] != b'\x07' or raw[end + 5:end + 14] != result:
            return 0
        sites.append((data + gate[0], gate[1], gate[2]))
        sites.append((data + start, raw[start:start + 5], b'\x06' + struct.pack('<I', end + 14)))
    patch.build(c, key, sites)
    return patch.apply(c)

def lockpick_entry(c, patch):
    cls, function, data, raw = script_of(c, 'cls_minibase', 'ExecuteUbergraph_BPC_MiniGameBase')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    count = local_operand(c, function, 'CallFunc_CheckTotal_KEY_TotalAmmo', 'IntProperty')
    condition = local_operand(c, function, 'CallFunc_GreaterEqual_IntInt_ReturnValue', 'BoolProperty')
    gate = true_branch(raw, condition)
    call = removal_call(c, function, raw, '', literal=True)
    inventory = c.first('cls_inventory')
    add = c.u.function(inventory, 'AddItemSimple') if inventory else 0
    args = c.u.own_props(add) if add else {}
    if (not count or count not in raw or not gate or not call
            or args.get('ItemToAdd', (0, ''))[1] != 'ObjectProperty'
            or args.get('AddAmount', (0, ''))[1] != 'IntProperty'):
        return 0
    start, end, result = call

    item = raw[start + 35:start + 44]
    body = b'\x46' + struct.pack('<Q', add) + item + b'\x1d\x01\x00\x00\x00\x16'
    old = raw[start + 22:end]
    if len(body) > len(old):
        return 0
    patch.build(c, key, [(data + gate[0], gate[1], gate[2]),
                        (data + start + 22, old, body + b'\x0b' * (len(old) - len(body)))])
    return patch.apply(c)

def lockpick_spawn(c, patch):
    cls, function, data, raw = script_of(c, 'cls_picklock', 'ExecuteUbergraph_MiniGame_PickLock')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    entry = c.u.class_props(cls).get('PickLockState')
    prop = c.u.property(cls, 'PickLockState') if entry else 0
    if not prop or entry[1] != 'ByteProperty':
        return 0
    assign = b'\x0f' + struct.pack('<Q', prop) + b'\x01' + struct.pack('<Q', prop) + b'\x24\x06'
    sites = []
    for start in occurrences(raw, assign):
        end = start + len(assign)
        if (raw[end:end + 1] == b'\x45' and raw[end + 13:end + 15] == b'\x27\x16'
                and c.u.name(struct.unpack_from('<I', raw, end + 1)[0]) == 'SetGameSpawned'):

            sites.append((data + end - 1, b'\x06', b'\x05'))
    if len(sites) != 1:
        return 0
    patch.build(c, key, sites)
    return patch.apply(c)

def preserve_shot_ammo(c, patch):
    cls, function, data, raw = script_of(c, 'cls_player', 'S-UpdateAmmoCount')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    sites = []
    for suffix in ('', '_1'):
        result = local_operand(c, function, 'CallFunc_Subtract_IntInt_ReturnValue' + suffix, 'IntProperty')
        count = local_operand(c, function, 'CallFunc_BPIPW_GetCurrentAmmoInfo_AmmoCount' + suffix, 'IntProperty')
        if not result or not count:
            return 0
        prefix = b'\x0f' + result[1:] + result + b'\x68'
        matches = []
        for start in occurrences(raw, prefix):
            math = start + len(prefix)
            if (raw[math + 8:math + 23] == count + b'\x1d\x01\x00\x00\x00\x16'
                    and c.u.objname(struct.unpack_from('<Q', raw, math)[0]) == 'Subtract_IntInt'):
                matches.append(math + 18)
        if len(matches) != 1:
            return 0
        sites.append((data + matches[0], b'\x01\x00\x00\x00', bytes(4)))

    patch.build(c, key, sites)
    return patch.apply(c)

def radial_auto(c, patch):
    cls, function, data, raw = script_of(c, 'cls_radial', 'ExecuteUbergraph_BP_MiniGamesRadial')
    if not raw:
        return 0
    key = (function, data)
    if patch.key == key:
        return patch.apply(c)
    def instance(name, kind):
        entry = c.u.class_props(cls).get(name)
        prop = c.u.property(cls, name) if entry else 0
        return b'\x01' + struct.pack('<Q', prop) if prop and entry[1] == kind else b''
    enabled, active = instance('InputEnable', 'BoolProperty'), instance('IsMiniGameActivated', 'BoolProperty')
    solver = instance('SolverPos', 'IntProperty')
    rings = [instance('Ring%d_Index' % n, 'IntProperty') for n in (1, 2, 3)]
    if not enabled or not active or not solver or not all(rings):
        return 0
    guard = b'\x4f' + enabled + b'\x4f' + active + b'\x06'
    guards = occurrences(raw, guard)
    condition = local_operand(c, function, 'CallFunc_BooleanAND_ReturnValue_1', 'BoolProperty')
    gate = true_branch(raw, condition)
    if len(guards) != 1 or not gate:
        return 0

    compare_start = gate[0] - 5 - 190
    if compare_start < 0:
        return 0
    for offset, suffix, ring in ((0, '', rings[0]), (38, '_1', rings[1]), (114, '_2', rings[2])):
        result = local_operand(c, function, 'CallFunc_EqualEqual_IntInt_ReturnValue' + suffix, 'BoolProperty')
        part = raw[compare_start + offset:compare_start + offset + 38]
        if (not result or part[:11] != b'\x14' + result + b'\x68'
                or part[19:] != ring + solver + b'\x16'
                or c.u.objname(struct.unpack_from('<Q', part, 11)[0]) != 'EqualEqual_IntInt'):
            return 0
    for offset, result_name, left_name, right_name in (
            (76, 'CallFunc_BooleanAND_ReturnValue', 'CallFunc_EqualEqual_IntInt_ReturnValue', 'CallFunc_EqualEqual_IntInt_ReturnValue_1'),
            (152, 'CallFunc_BooleanAND_ReturnValue_1', 'CallFunc_BooleanAND_ReturnValue', 'CallFunc_EqualEqual_IntInt_ReturnValue_2')):
        part = raw[compare_start + offset:compare_start + offset + 38]
        result, left, right = [local_operand(c, function, name, 'BoolProperty') for name in (result_name, left_name, right_name)]
        if (not all((result, left, right)) or part[:11] != b'\x14' + result + b'\x68'
                or part[19:] != left + right + b'\x16'
                or c.u.objname(struct.unpack_from('<Q', part, 11)[0]) != 'BooleanAND'):
            return 0
    success = gate[0] + 9
    jump = guards[0] + len(guard) - 1

    body = b'\x14' + enabled + b'\x28\x06' + struct.pack('<I', success)
    patch.build(c, key, [(data + jump + 1, raw[jump + 1:jump + 5], struct.pack('<I', compare_start)),
                        (data + compare_start, raw[compare_start:compare_start + len(body)], body)])
    return patch.apply(c)

class God(Feature):
    key = 'god'
    hotkey = 'Ctrl+Num1'
    title = 'Бессмертие'
    description = 'Здоровье персонажа удерживается на максимуме'

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        alive = sum(1 for p in players if c.heal(p))
        if not alive:
            return WAIT, T('Персонаж мёртв — ожидание перезапуска уровня')
        return OK, T('Здоровье удерживается на максимуме')

class Untouchable(Feature):
    key = 'untouchable'
    hotkey = 'Ctrl+Num2'
    title = 'Неуязвимость (no hit)'
    description = 'Удары зомби проходят насквозь, стрелки не стреляют, урон ×0; захват пока возможен'
    experimental = True

    def __init__(self):
        super().__init__()
        self.notifies = {}
        self.shooters = {}

    def silence_notify(self, c, notify):
        f = c.u.field(notify, 'Object Types')
        if f is None:
            return False
        raw = c.u.m.read(f.address, 16)
        if len(raw) != 16:
            return False
        data, count, capacity = struct.unpack('<Qii', raw)
        if notify not in self.notifies:
            damage = c.u.get(notify, 'DamageValue')
            if count <= 0 and damage == 0.0:
                return True
            self.notifies[notify] = (count, damage)
        if count != 0:
            c.u.m.write(f.address + 8, struct.pack('<i', 0))
        if c.u.get(notify, 'DamageValue'):
            c.u.set(notify, 'DamageValue', 0.0)
        return True

    def disarm_shooter(self, c, human):
        if c.u.get(human, 'IsDeathC'):
            return False
        if human not in self.shooters:
            self.shooters[human] = (c.u.get(human, 'CanShoot'), c.u.get(human, 'DealingDamageScalar'))
        c.u.set(human, 'CanShoot', False)
        c.u.set(human, 'DealingDamageScalar', 0.0)
        return True

    def tick(self, c):
        configs = c.objs('cfg_player')
        for cfg in configs:
            f = c.u.field(cfg, 'ReceiveDamageScale')
            value = c.u.read(f)
            if value is None:
                continue
            if cfg not in self.saved and value != 0.0:
                self.saved[cfg] = value
            if value != 0.0:
                c.u.write(f, 0.0)
        for p in c.players():
            c.heal(p)
        attacks = sum(1 for n in c.objs('zombie_attack') if self.silence_notify(c, n))
        shooters = sum(1 for h in c.objs('human') if self.disarm_shooter(c, h))
        if not configs and not attacks and not shooters:
            return WAIT, T('Ожидание настроек урона игры')
        return OK, T('Удары зомби не попадают: ') + str(attacks) + T(' · стрелков обезврежено: ') + str(shooters)

    def release(self, c):
        restore_floats(c, self.saved, 'ReceiveDamageScale', lambda obj: 0.0)
        for notify, (count, damage) in list(self.notifies.items()):
            if c.u.valid(notify):
                f = c.u.field(notify, 'Object Types')
                raw = c.u.m.read(f.address, 16) if f else b''
                if len(raw) == 16 and struct.unpack('<Qii', raw)[1] == 0 and count > 0:
                    c.u.m.write(f.address + 8, struct.pack('<i', count))
                if damage is not None and c.u.get(notify, 'DamageValue') == 0.0:
                    c.u.set(notify, 'DamageValue', damage)
        self.notifies.clear()
        for human, (can_shoot, scalar) in list(self.shooters.items()):
            if c.u.valid(human):
                if can_shoot is not None:
                    c.u.set(human, 'CanShoot', can_shoot)
                if scalar is not None and c.u.get(human, 'DealingDamageScalar') == 0.0:
                    c.u.set(human, 'DealingDamageScalar', scalar)
        self.shooters.clear()

FALL_LIMITS = {
    'VelocityTresholds': (-1e9, -1e9, -1e9),
    'AccelerationThresholds': (-1e9, 1e9, 1e9),
}

class Fall(Feature):
    key = 'fall'
    hotkey = 'Ctrl+Num3'
    title = 'Без урона от падения'
    description = 'Пороги урона от приземления отключены'
    experimental = True

    def __init__(self):
        super().__init__()
        self.history = {}

    def disable_thresholds(self, c, component):
        done = 0
        for name, limits in FALL_LIMITS.items():
            f = c.u.field(component, name)
            if f is None:
                continue
            raw = c.u.m.read(f.address, 24)
            if len(raw) != 24:
                continue
            current = struct.unpack('<3d', raw)
            key = (component, name)
            if key not in self.saved and current != limits:
                self.saved[key] = current
            if current != limits:
                c.u.m.write(f.address, struct.pack('<3d', *limits))
            done += 1
        return done == len(FALL_LIMITS)

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        now = time.monotonic()
        found = 0
        for p in players:
            component = c.pointer_field(p, 'BP_FallDamageAndSliding')
            if not component:
                continue
            if self.disable_thresholds(c, component):
                found += 1
            anim = c.u.field(component, 'StartFallDamageAnim')
            event = c.u.field(component, 'StartFallDamageEvent')
            health = c.u.field(p, 'Current Health')
            hp = c.u.read(health)
            active = bool(c.u.read(anim) or c.u.read(event))
            log = self.history.setdefault(p, deque())
            if active:
                stable = max((v for t, v in log), default=None)
                if stable is not None and hp is not None and hp < stable:
                    c.u.write(health, stable)
                c.u.write(anim, False)
                c.u.write(event, False)
            elif hp is not None:
                log.append((now, hp))
            while log and now - log[0][0] > 2.0:
                log.popleft()
        if not found:
            return WAIT, T('Ожидание компонента падения')
        return OK, T('Пороги урона от падения отключены')

    def release(self, c):
        for (component, name), original in list(self.saved.items()):
            if c.u.valid(component):
                f = c.u.field(component, name)
                raw = c.u.m.read(f.address, 24) if f else b''
                if len(raw) == 24 and struct.unpack('<3d', raw) == FALL_LIMITS[name]:
                    c.u.m.write(f.address, struct.pack('<3d', *original))
        self.saved.clear()
        self.history.clear()

AMMO_ITEMS = ('9MM_Box', 'shotgun_Box', 'Ammo_', '556')

class Ammo(Feature):
    key = 'ammo'
    hotkey = 'Ctrl+Num4'
    title = 'Бесконечные патроны'
    description = 'Патроны в инвентаре, запас и магазины оружия не кончаются'
    experimental = True

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        weapons = c.owned_weapons()
        for kind, w in weapons:
            size = c.magazine_size(w)
            if kind == 'pistol':
                c.raise_to(w, 'MagazineCount', 9)
                if size and (c.u.get(w, 'AmmoCount') or 0) <= 0:
                    c.u.set(w, 'AmmoCount', size)
            c.raise_to(w, 'TotalAmmo', AMMO_RESERVE)
        if weapons:
            for p in players:
                c.raise_to(p, 'TotalAmmo', AMMO_RESERVE)
        boxes = 0
        for inv in c.player_inventories():
            for item, address in c.inventory_slots(inv):
                name = c.u.objname(item)
                if any(tag in name for tag in AMMO_ITEMS):
                    top = c.u.get(item, 'MaxStackAmount') or 1
                    raw = c.u.m.read(address, 4)
                    if len(raw) == 4 and struct.unpack('<i', raw)[0] < max(1, top):
                        c.u.m.write(address, struct.pack('<i', max(1, top)))
                    boxes += 1
        if not weapons and not boxes:
            return WAIT, T('Ожидание оружия')
        return OK, T('Оружие: ') + str(len(weapons)) + T(' · патроны в инвентаре: ') + str(boxes)

class NoReload(Feature):
    key = 'noreload'
    hotkey = 'Ctrl+Num5'
    title = 'Без перезарядки'
    description = 'Патроны при выстреле не расходуются; магазин пополняется по данным оружия'
    experimental = True

    def __init__(self):
        super().__init__()
        self.caps = {}
        self.debit = CodePatch()

    def keep(self, c, obj, name, floor=0):
        field = c.u.field(obj, name)
        value = c.u.read(field)
        if field is None or field.type != 'IntProperty' or value is None:
            return False
        key = (obj, c.u.cls(obj), name)
        cap = floor if floor > 0 else max(self.caps.get(key, 0), value)
        self.caps[key] = cap
        if not 0 < cap <= 1000:
            return False
        return value == cap or c.u.write(field, cap)

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        patched = preserve_shot_ammo(c, self.debit)
        weapons = c.owned_weapons()
        if not weapons:
            return WAIT, T('Ожидание оружия')
        done = 0
        for kind, weapon in weapons:
            field = 'AmmoCount' if kind == 'pistol' else 'DefaultAmmoCount'
            if self.keep(c, weapon, field, c.magazine_size(weapon)):
                done += 1
        if patched != 2 or done != len(weapons):
            return FAIL, T('Не все магазины распознаны или запись не удалась')
        return OK, T('Без расхода патронов · оружия: ') + str(done)

    def release(self, c):
        self.debit.restore(c)
        self.caps.clear()
        super().release(c)

class Stamina(Feature):
    key = 'stamina'
    hotkey = 'Ctrl+Num6'
    title = 'Бесконечная выносливость'
    description = 'Выносливость не расходуется'

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        for p in players:
            c.raise_to(p, 'Stamina', STAMINA_MAX)
        return OK, T('Выносливость удерживается на максимуме')

class Invisible(Feature):
    key = 'invisible'
    hotkey = 'Ctrl+Num7'
    title = 'Невидимость'
    description = 'Зрение зомби не замечает персонажей, слух приглушён'
    experimental = True

    def __init__(self):
        super().__init__()
        self.patch = CodePatch()

    def default_state(self, c):
        for cdo in c.objs('zombie_ctl_cdo'):
            value = c.u.get(cdo, 'SightPerceptionStatus')
            if value is not None:
                return value
        return 0

    def sites(self, c):
        cls, f, data, raw = script_of(c, 'cls_zombie_ctl', 'UpdateSightPerceptionValues')
        character = c.first('cls_character')
        blind = c.first('cls_texture')
        if not raw or not character or not blind:
            return None
        old = struct.pack('<Q', character)
        out = [(data + i + 1, old, struct.pack('<Q', blind)) for i in occurrences(raw, b'\x2e' + old)]
        return (f, data), out

    def tick(self, c):
        found = self.sites(c)
        patched = 0
        if found:
            self.patch.build(c, *found)
            patched = self.patch.apply(c)
        controllers = c.objs('zombie_ctl')
        if not controllers:
            return WAIT, T('Ожидание зомби')
        players = set(c.players())
        idle = self.default_state(c)
        for ctl in controllers:
            c.u.set(ctl, 'DesiredDetectionValue', 0.0)
            c.u.set(ctl, 'AcumulatedSoundsStrength', 0.0)
            c.u.set(ctl, 'SightPerceptionStatus', idle)
            c.u.set(ctl, 'bDetectedEnemy', False)
            f = c.u.field(ctl, 'ChoosedEnemyActor')
            if f is not None and c.u.m.u64(f.address) in players:
                c.u.m.write(f.address, bytes(8))
        return OK, T('Зрение зомби: ') + (T('отключено') if patched else T('не найдено')) + T(' · зомби: ') + str(len(controllers))

    def release(self, c):
        self.patch.restore(c)

class OneHit(Feature):
    key = 'onehit'
    hotkey = 'Ctrl+Num8'
    title = 'Убийство с одного удара'
    description = 'Любое попадание убивает зомби и людей'
    experimental = True

    def tick(self, c):
        configs = c.objs('cfg_zombie')
        for cfg in configs:
            f = c.u.field(cfg, 'ReceiveDamageScale')
            value = c.u.read(f)
            if value is None:
                continue
            if cfg not in self.saved and value != 10000.0:
                self.saved[cfg] = value
            if value != 10000.0:
                c.u.write(f, 10000.0)
        enemies = 0
        for key in ('zombie', 'human'):
            for z in c.objs(key):
                if c.u.get(z, 'IsDeathC'):
                    continue
                f = c.u.field(z, 'CurrentHealthPoints')
                hp = c.u.read(f)
                if hp is not None and hp > 1.0:
                    c.u.write(f, 1.0)
                if hp is not None:
                    enemies += 1
        if not configs and not enemies:
            return WAIT, T('Ожидание врагов')
        return OK, T('Урон усилен · врагов с 1 HP: ') + str(enemies)

    def release(self, c):
        restore_floats(c, self.saved, 'ReceiveDamageScale', lambda obj: 10000.0)

class PickLock(Feature):
    key = 'picklock'
    hotkey = 'Ctrl+Num9'
    title = 'Автовзлом отмычкой'
    description = 'Добавляет отмычку при взаимодействии и запускает штатное открытие замка'
    experimental = True

    def __init__(self):
        super().__init__()
        self.solve = CodePatch()
        self.entry = CodePatch()

    def tick(self, c):
        entered = lockpick_entry(c, self.entry)
        solved = lockpick_spawn(c, self.solve)
        if entered != 2 or solved != 1:
            return (FAIL if c.first('cls_minibase') or c.first('cls_picklock') else WAIT), T('Цепочка автовзлома не распознана')
        for game in c.objs('picklock'):
            if (c.u.get(game, 'GameSpawned') and c.u.get(game, 'MiniGameState') == 1
                    and not c.u.get(game, 'WantExit') and not c.u.get(game, 'ProcessToExit')
                    and not c.u.get(game, 'IsGameComplete')
                    and c.u.get(game, 'PickLockState') in (1, 2, 3, 4, 6)):
                if not c.u.set(game, 'PickLockState', 5):
                    return FAIL, T('Не удалось запустить открытие замка')
        return OK, T('Автовзлом готов · отмычка добавляется при взаимодействии')

    def release(self, c):
        self.solve.restore(c)
        self.entry.restore(c)

class Radial(Feature):
    key = 'radial'
    hotkey = 'Ctrl+Num0'
    title = 'Автовзлом радиального замка'
    description = 'При входе запускает штатную анимацию успеха, возврат камеры и завершение'
    experimental = True

    def __init__(self):
        super().__init__()
        self.solve = CodePatch()

    def tick(self, c):
        if radial_auto(c, self.solve) != 2:
            return (FAIL if c.first('cls_radial') else WAIT), T('Цепочка радиального замка не распознана')
        return OK, T('Автовзлом колец готов · начните взаимодействие')

    def release(self, c):
        self.solve.restore(c)

class Speed(Feature):
    key = 'speed'
    hotkey = 'Ctrl+Alt+Num1'
    title = 'Скорость передвижения'
    description = 'Пресеты скорости игрока умножаются'
    experimental = True
    option = 'speed_factor'

    def presets(self, c):
        out = []
        for preset in c.objs('preset'):
            if 'PlayerPresets' not in c.u.path(preset):
                continue
            for name, (offset, kind, mask, sub) in c.u.class_props(c.u.cls(preset)).items():
                if kind == 'StructProperty' and sub and c.u.objname(sub).startswith('CALSMovementSettingsStrafe'):
                    out.append((preset, preset + offset))
        return out

    def tick(self, c):
        factor = float(c.options.get(self.option, SPEED_FACTORS[0]))
        targets = self.presets(c)
        if not targets:
            return WAIT, T('Ожидание пресетов движения')
        applied = 0
        for preset, address in targets:
            raw = c.u.m.read(address, 72)
            if len(raw) != 72:
                continue
            current = list(struct.unpack('<9d', raw))
            if address not in self.saved:
                if any(not 0.0 <= v <= 2000.0 for v in current):
                    continue
                self.saved[address] = current
            original = self.saved[address]
            wanted = [min(v * factor, 4000.0) for v in original]
            if any(abs(a - b) > 1e-6 for a, b in zip(current, wanted)):
                c.u.m.write(address, struct.pack('<9d', *wanted))
            applied += 1
        return OK, T('Скорость ×') + ('%g' % factor) + T(' · пресетов: ') + str(applied)

    def release(self, c):
        for address, original in list(self.saved.items()):
            raw = c.u.m.read(address, 72)
            if len(raw) == 72:
                c.u.m.write(address, struct.pack('<9d', *original))
        self.saved.clear()

class Slomo(Feature):
    key = 'slomo'
    hotkey = 'Ctrl+Alt+Num2'
    title = 'Замедление времени'
    description = 'Игровое время идёт медленнее'
    option = 'slomo_factor'

    def tick(self, c):
        factor = float(c.options.get(self.option, SLOMO_FACTORS[0]))
        worlds = c.objs('world')
        if not worlds:
            return WAIT, T('Ожидание мира игры')
        for w in worlds:
            f = c.u.field(w, 'TimeDilation')
            value = c.u.read(f)
            if value is None:
                continue
            if w not in self.saved:
                self.saved[w] = value if abs(value - factor) > 1e-4 else 1.0
            if abs(value - factor) > 1e-4:
                c.u.write(f, factor)
        return OK, T('Время ×') + ('%g' % factor)

    def release(self, c):
        for w, original in list(self.saved.items()):
            if c.u.valid(w):
                c.u.set(w, 'TimeDilation', original)
        self.saved.clear()

class Drone(Feature):
    key = 'drone'
    hotkey = 'Ctrl+Alt+Num3'
    title = 'Дрон: неуязвимость'
    description = 'Здоровье дрона не убывает'
    experimental = True

    def tick(self, c):
        drones = c.objs('drone')
        if not drones:
            return WAIT, T('Дрон не развёрнут')
        for d in drones:
            top = c.u.get(d, 'SkillBaseHealth') or 100.0
            c.raise_to(d, 'DroneCurrentHealth', float(top))
            f = c.u.field(d, 'IgnoreOnDamage')
            if d not in self.saved:
                self.saved[d] = bool(c.u.read(f))
            c.u.write(f, True)
        return OK, T('Дрон неуязвим · дронов: ') + str(len(drones))

    def release(self, c):
        for d, original in list(self.saved.items()):
            if c.u.valid(d):
                c.u.set(d, 'IgnoreOnDamage', original)
        self.saved.clear()

class Money(Feature):
    key = 'money'
    hotkey = 'Ctrl+Alt+Num4'
    title = 'Деньги'
    description = 'Валюта игрока удерживается на 999 999'
    experimental = True

    def tick(self, c):
        mine = c.player_inventories()
        if not mine:
            return WAIT, T('Ожидание инвентаря игрока')
        for inv in mine:
            c.raise_to(inv, 'CurrencyAmount', MONEY_TARGET)
        value = c.u.get(mine[0], 'CurrencyAmount')
        return OK, T('Валюта: ') + (f'{value:,}'.replace(',', ' ') if value is not None else '—')

class Hunger(Feature):
    key = 'hunger'
    hotkey = 'Ctrl+Alt+Num5'
    title = 'Без голода и жажды'
    description = 'Сытость и вода удерживаются на максимуме'

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        enabled = False
        seen = False
        for p in players:
            survival = c.pointer_field(p, 'BP_InventorySurvivalComponent')
            if not survival:
                continue
            seen = True
            if c.u.get(survival, 'bEnableHunger') or c.u.get(survival, 'bEnableThirst'):
                enabled = True
            for stat, top in (('PlayerNutrition', 'PlayerMaxNutrition'), ('PlayerHydration', 'PlayerMaxHydration')):
                limit = c.u.get(survival, top)
                if limit:
                    c.raise_to(survival, stat, limit)
        if not seen:
            return WAIT, T('Ожидание компонента выживания')
        if not enabled:
            return INFO, T('В демо голод и жажда отключены игрой — заработает в полной версии')
        return OK, T('Сытость и вода удерживаются на максимуме')

class Accuracy(Feature):
    key = 'accuracy'
    hotkey = 'Ctrl+Alt+Num6'
    title = 'Максимальная точность'
    description = 'Обнуляет масштаб разброса без изменения кода выстрела'
    experimental = True

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        done = 0
        for player in players:
            field = c.u.field(player, 'SS-DispersionScale')
            if field is None or field.type != 'StructProperty':
                continue
            members = c.u.struct_fields(player, 'SS-DispersionScale')
            if not all(name in members and members[name][1] == 'DoubleProperty' and members[name][0] == offset
                       for name, offset in (('X', 0), ('Y', 8))):
                continue
            raw = c.u.m.read(field.address, 16)
            if len(raw) != 16:
                continue
            self.saved.setdefault(player, (c.u.cls(player), raw))
            if raw == bytes(16) or c.u.m.write(field.address, bytes(16)):
                done += 1
        return (OK if done else FAIL), T('Масштаб разброса обнулён: ') + str(done)

    def release(self, c):
        for player, (cls, original) in list(self.saved.items()):
            if not c.u.valid(player) or c.u.cls(player) != cls:
                continue
            field = c.u.field(player, 'SS-DispersionScale')
            if field and c.u.m.read(field.address, 16) == bytes(16):
                if not c.u.m.write(field.address, original):
                    raise RuntimeError(T('Не удалось восстановить изменения'))
        self.saved.clear()

class NoRecoil(Feature):
    key = 'norecoil'
    hotkey = 'Ctrl+Alt+Num7'
    title = 'Без отдачи'
    description = 'Уменьшает отдачу и тряску камеры; таймлайн стрельбы не изменяется'
    experimental = True

    def calm_shakes(self, c):
        calmed = 0
        for shake in c.objs('shake'):
            if c.u.clsname(shake) not in SHAKES:
                continue
            addresses = []
            for name, count in (('RotOscillation', 3), ('LocOscillation', 3), ('FOVOscillation', 1)):
                off = c.u.struct_offset(shake, name)
                if off is not None:
                    addresses += [shake + off + 12 * k for k in range(count)]
            for address in addresses:
                raw = c.u.m.read(address, 4)
                if len(raw) != 4:
                    continue
                value = struct.unpack('<f', raw)[0]
                if value != 0.0:
                    self.saved.setdefault(address, value)
                    c.u.m.write(address, struct.pack('<f', 0.0))
            calmed += 1
        return calmed

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        done = 0
        for player in players:
            field = c.u.field(player, 'SS-UpOffsetScale')
            value = c.u.read(field)
            if field is None or field.type not in ('FloatProperty', 'DoubleProperty') or value is None:
                continue
            self.saved.setdefault(('scale', player), (c.u.cls(player), value))
            if c.u.write(field, 0.0):
                done += 1
        shakes = self.calm_shakes(c)
        return (OK if done or shakes else FAIL), T('Масштаб отдачи обнулён: ') + str(done) + T(' · тряска камеры: ') + str(shakes)

    def release(self, c):
        for key, value in list(self.saved.items()):
            if isinstance(key, tuple):
                player = key[1]
                cls, original = value
                if c.u.valid(player) and c.u.cls(player) == cls and c.u.get(player, 'SS-UpOffsetScale') == 0.0:
                    if not c.u.set(player, 'SS-UpOffsetScale', original):
                        raise RuntimeError(T('Не удалось восстановить изменения'))
            else:
                raw = c.u.m.read(key, 4)
                if len(raw) == 4 and struct.unpack('<f', raw)[0] == 0.0:
                    if not c.u.m.write(key, struct.pack('<f', value)):
                        raise RuntimeError(T('Не удалось восстановить изменения'))
        self.saved.clear()

class DroneCooldown(Feature):
    key = 'drone_cd'
    hotkey = 'Ctrl+Alt+Num8'
    title = 'Дрон без отката'
    description = 'Нет задержки вызова дрона и перезарядки его навыков'
    experimental = True

    def tick(self, c):
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        for p in players:
            f = c.u.field(p, 'ColdDownTime')
            value = c.u.read(f)
            if value is not None:
                if p not in self.saved and value > 0.05:
                    self.saved[p] = value
                if value > 0.05:
                    c.u.write(f, 0.05)
            c.u.set(p, 'ColdDownFinished?', True)
        drones = c.objs('drone')
        for d in drones:
            if (c.u.get(d, 'ColdDownTime') or 0) > 0:
                c.u.set(d, 'ColdDownTime', 0.0)
        return OK, T('Откат дрона снят') + (T(' · дронов: ') + str(len(drones)) if drones else '')

    def release(self, c):
        for p, original in list(self.saved.items()):
            if c.u.valid(p) and abs((c.u.get(p, 'ColdDownTime') or 0) - 0.05) < 1e-6:
                c.u.set(p, 'ColdDownTime', original)
        self.saved.clear()

DRONE_STATS = (
    ('BaseDamage', 'Базовый урон'),
    ('DamageMultiplier', 'Множитель урона'),
    ('Max Damage', 'Максимальный урон'),
    ('SkillBaseHealth', 'Здоровье'),
    ('HealthMultiplier', 'Множитель здоровья'),
    ('SkillBaseArmor', 'Броня'),
    ('DroneMaxSpeed', 'Скорость полёта'),
    ('SensingRadius', 'Радиус обнаружения'),
    ('SkillDistance', 'Дальность атаки'),
    ('SearchInterval', 'Интервал поиска целей, с'),
    ('MinSkillBurstDelay', 'Мин. пауза между очередями, с'),
    ('MaxSkillBurstDelay', 'Макс. пауза между очередями, с'),
)

class DroneStats(Feature):
    key = 'drone_edit'
    hotkey = ''
    title = 'Дрон: характеристики'
    description = 'Урон, здоровье, броня, скорость и дальность дрона'
    experimental = True
    option = 'drone_stats'
    editor = True

    def targets(self, c):
        return c.objs('drone') + c.objs('drone_tpl')

    def defaults(self, c):
        for d in self.targets(c):
            values = {}
            for name, _ in DRONE_STATS:
                v = c.u.get(d, name)
                if v is not None:
                    values[name] = self.saved.get((d, name), v)
            if values:
                return values
        return {}

    def tick(self, c):
        wanted = c.options.get(self.option) or {}
        targets = self.targets(c)
        if not targets:
            return WAIT, T('Ожидание дрона')
        if not wanted:
            return INFO, T('Откройте редактор и задайте значения')
        for d in targets:
            for name, value in wanted.items():
                f = c.u.field(d, name)
                current = c.u.read(f)
                if current is None:
                    continue
                key = (d, name)
                if key not in self.saved:
                    self.saved[key] = current
                target = int(value) if f.type == 'IntProperty' else float(value)
                if current != target:
                    c.u.write(f, target)
        live = len(c.objs('drone'))
        return OK, T('Характеристики применены · дронов: ') + str(live)

    def release(self, c):
        for (d, name), original in list(self.saved.items()):
            if c.u.valid(d):
                c.u.set(d, name, original)
        self.saved.clear()

class AutoKey(Feature):
    key = 'autokey'
    hotkey = 'Ctrl+Alt+Num/'
    title = 'Автоключ'
    description = 'Двери с ключом или картой открываются без предмета в инвентаре'
    experimental = True

    def __init__(self):
        super().__init__()
        self.key_check = CodePatch()

    def tick(self, c):
        bypass = card_access(c, self.key_check) == 4
        doors = c.objs('key_door')
        if not doors and not bypass:
            if c.first('cls_code'):
                return FAIL, T('Проверка ключа не распознана или запись не удалась')
            return WAIT, T('Ожидание дверей с ключом или картой')
        done = 0
        for door in doors:
            item = c.pointer_field(door, 'KeyItem')
            locked = c.u.field(door, 'bIsLocked')
            if not item or locked is None or locked.type != 'BoolProperty':
                continue
            current = c.u.read(locked)
            if current is None:
                continue
            key = (door, 'bIsLocked')
            if key not in self.saved:
                self.saved[key] = (c.u.cls(door), item, current)
            if c.u.write(locked, False):
                done += 1
        if not done and not bypass:
            return FAIL, T('Проверка ключа не распознана или запись не удалась')
        return OK, T('Дверей без требования ключа: ') + str(done) + T(' · карты терминалов T1/T2: ') + (T('отключено') if bypass else T('не найдено'))

    def release(self, c):
        self.key_check.restore(c)
        for (door, name), (cls, item, original) in list(self.saved.items()):
            if (c.u.valid(door) and c.u.cls(door) == cls
                    and c.pointer_field(door, 'KeyItem') == item
                    and c.u.get(door, name) is False):
                c.u.set(door, name, original)
        self.saved.clear()

class FreeCraft(Feature):
    key = 'craft'
    hotkey = 'Ctrl+Alt+Num9'
    title = 'Бесплатный крафт'
    description = 'Рецепты не требуют ингредиентов'
    experimental = True

    def __init__(self):
        super().__init__()
        self.check = CodePatch()
        self.debit = CodePatch()
        self.ui_saved = {}

    def ingredient_check(self, c):
        return constant_output(c, self.check, c.first('cls_player'), 'FindInventoryItem', 'Sussuise', 'BoolProperty', True)

    def update_ui(self, c):
        for player in c.players():
            for name in ('IsItem_1?', 'IsItem_2?', 'IsItem_3?'):
                field = c.u.field(player, name)
                original = c.u.read(field)
                if field and field.type == 'BoolProperty' and original is not None:
                    self.ui_saved.setdefault((player, name), (original, True))
                    c.u.write(field, True)
        for widget in c.objs('craft_widget'):
            if not (c.u.get(widget, 'Add Item') or 0) > 0:
                continue
            for name, target in (('Ammo Item 1', 0), ('Ammo Item 2', 0), ('Ammo Item 3', 0), ('Restart_Buttom', False)):
                field = c.u.field(widget, name)
                original = c.u.read(field)
                if original is not None:
                    self.ui_saved.setdefault((widget, name), (original, target))
                    c.u.write(field, target)

    def table(self, c):
        for t in c.objs('datatable'):
            if c.u.objname(t) == 'DT_CraftList':
                return t
        return 0

    def rows(self, c, table):
        m = c.u.m
        row_struct = m.u64(table + 0x28)
        data = m.u64(table + 0x30)
        raw = m.read(table + 0x38, 4)
        if not pointer(row_struct) or not pointer(data) or len(raw) != 4:
            return [], {}
        count = struct.unpack('<i', raw)[0]
        if not 0 < count < 1000:
            return [], {}

        allocation = m.read(table + 0x40, 40)
        if len(allocation) != 40:
            return [], {}
        secondary, bit_count, bit_capacity, first_free, num_free = struct.unpack_from('<Qiiii', allocation, 16)
        if bit_count != count or not count <= bit_capacity <= 100000 or not 0 <= num_free <= count:
            return [], {}
        bits = m.read(secondary, (count + 7) // 8) if secondary else allocation[:16]
        if len(bits) * 8 < count:
            return [], {}
        if sum(bool(bits[i // 8] & (1 << (i % 8))) for i in range(count)) != count - num_free:
            return [], {}
        fields = {}
        for k, v in c.u.own_props(row_struct).items():
            if v[1] == 'IntProperty' and (k.startswith('Ammo_Item_') or k.startswith('Item_1_Text') or k.startswith('Item_2_Text') or k.startswith('Item_3_Text')):
                fields[k] = v
        row_size = m.u32(row_struct + 0x58)
        if len(fields) != 6 or not 0 < row_size <= 4096 or any(v[0] + 4 > row_size for v in fields.values()):
            return [], {}
        rows = []
        for i in range(count):
            if not bits[i // 8] & (1 << (i % 8)):
                continue
            entry = m.read(data + i * 24, 24)
            if len(entry) == 24:
                row = struct.unpack_from('<Q', entry, 8)[0]
                if pointer(row):
                    rows.append(row)
        if len(rows) != count - num_free:
            return [], {}
        return rows, fields

    def tick(self, c):
        table = self.table(c)
        if not table:
            return WAIT, T('Ожидание таблицы рецептов')
        rows, fields = self.rows(c, table)
        if not rows or not fields:
            return FAIL, T('Структура рецептов не распознана')
        if not self.ingredient_check(c) or craft_consumption(c, self.debit) != 3:
            return FAIL, T('Проверка ингредиентов не распознана или запись не удалась')
        done = 0
        failed = 0
        for row in rows:
            for name, (offset, kind, mask, sub) in fields.items():
                address = row + offset
                raw = c.u.m.read(address, 4)
                if len(raw) != 4:
                    failed += 1
                    continue
                value = struct.unpack('<i', raw)[0]
                if address not in self.saved and value != 0:
                    self.saved[address] = value
                if value != 0 and not c.u.m.write(address, struct.pack('<i', 0)):
                    failed += 1
                else:
                    done += 1
        if failed or not done:
            return FAIL, T('Не удалось изменить все требования рецептов')
        self.update_ui(c)
        return OK, T('Рецептов без ингредиентов: ') + str(len(rows))

    def release(self, c):
        self.check.restore(c)
        self.debit.restore(c)
        for address, original in list(self.saved.items()):
            raw = c.u.m.read(address, 4)
            if len(raw) == 4 and struct.unpack('<i', raw)[0] == 0:
                c.u.m.write(address, struct.pack('<i', original))
        self.saved.clear()
        for (obj, name), (original, target) in self.ui_saved.items():
            if c.u.valid(obj) and c.u.get(obj, name) == target:
                c.u.set(obj, name, original)
        self.ui_saved.clear()

class HackTimer(Feature):
    key = 'hack_timer'
    hotkey = 'Ctrl+Alt+Num*'
    title = 'Заморозить таймер взлома'
    description = 'Отдельная пауза таймера мини-игр с отмычкой'
    experimental = True

    def __init__(self):
        super().__init__()
        self.clock = CodePatch()

    def tick(self, c):
        cls, function, data, raw = script_of(c, 'cls_minibase', 'CalculateTimer')
        if not raw:
            return WAIT, T('Ожидание таймера мини-игры')
        key = (function, data)
        if self.clock.key != key:
            prop = c.u.property(cls, 'CurrentTime')
            if not prop or b'\x01' + struct.pack('<Q', prop) not in raw or len(raw) < 3:
                return FAIL, T('Таймер мини-игры не распознан')
            self.clock.build(c, key, [(data, raw, b'\x04\x0b\x53' + b'\x0b' * (len(raw) - 3))])
        if not self.clock.apply(c):
            return FAIL, T('Таймер мини-игры не распознан')
        for obj in c.objs('minibase'):
            original = c.u.get(obj, 'UseTimer')
            if original is not None:
                self.saved.setdefault(obj, original)
                c.u.set(obj, 'UseTimer', False)
            if c.u.get(obj, 'GameSpawned') and c.u.get(obj, 'IsFirstTick'):
                duration = c.u.get(obj, 'Timer')
                if duration and duration > 0:
                    c.u.set(obj, 'CurrentTime', duration)
        return OK, T('Таймер взлома остановлен')

    def release(self, c):
        self.clock.restore(c)
        for obj, original in self.saved.items():
            if c.u.valid(obj):
                c.u.set(obj, 'UseTimer', original)
                if c.u.get(obj, 'GameSpawned'):
                    c.u.set(obj, 'IsFirstTick', True)
        self.saved.clear()

class DroneTime(Feature):
    key = 'drone_time'
    hotkey = 'Ctrl+Alt+Num.'
    title = 'Дрон без разрядки'
    description = 'Время работы дрона (батарея) не убывает'
    experimental = True

    def tick(self, c):
        drones = c.objs('drone')
        if not drones:
            return WAIT, T('Дрон не развёрнут')
        for d in drones:
            if (c.u.get(d, 'CurrentTime') or 0) > 0:
                c.u.set(d, 'CurrentTime', 0.0)
        return OK, T('Таймер дрона заморожен · дронов: ') + str(len(drones))

class DroneItems(Feature):
    key = 'drone_items'
    hotkey = 'Ctrl+Num.'
    title = 'Ресурсы дрона'
    description = 'Инъекторы, гранаты, дымовые гранаты и батареи дрона добавляются и не кончаются'
    experimental = True

    def tick(self, c):
        kept = c.keep_items(DRONE_ITEMS)
        if kept is None:
            return WAIT, T('Ожидание инвентаря игрока')
        if not kept:
            return WAIT, T('Нет свободного места в инвентаре')
        return OK, T('Ресурсов дрона в запасе: ') + '%d/%d' % (kept, len(DRONE_ITEMS))

class Tempo(Feature):
    key = 'tempo'
    hotkey = 'Ctrl+Num/'
    title = 'Темп персонажа'
    description = 'Персонаж и его анимации работают быстрее, мир — с обычной скоростью'
    experimental = True
    option = 'tempo_factor'

    def tick(self, c):
        factor = float(c.options.get(self.option, TEMPO_FACTORS[0]))
        players = c.players()
        if not players:
            return WAIT, T('Ожидание персонажа')
        for p in players:
            f = c.u.field(p, 'CustomTimeDilation')
            value = c.u.read(f)
            if value is None:
                continue
            if p not in self.saved:
                self.saved[p] = value if abs(value - factor) > 1e-4 else 1.0
            if abs(value - factor) > 1e-4:
                c.u.write(f, factor)
        return OK, T('Темп ×') + ('%g' % factor)

    def release(self, c):
        for p, original in list(self.saved.items()):
            if c.u.valid(p):
                c.u.set(p, 'CustomTimeDilation', original)
        self.saved.clear()

class FastPickup(Feature):
    key = 'fastpick'
    hotkey = 'Ctrl+Num*'
    title = 'Быстрый подбор'
    description = 'Анимация подбора предметов проигрывается в 4 раза быстрее'
    experimental = True

    def __init__(self):
        super().__init__()
        self.rate = CodePatch()
        self.delay = CodePatch()

    def tick(self, c):
        cls, f, data, raw = script_of(c, 'cls_pickup', 'SetAnimPlayRateAndPosition')
        if not raw:
            return WAIT, T('Ожидание способности подбора')
        old = b'\x37' + struct.pack('<d', 1.0) + b'\x37' + struct.pack('<d', 1.08)
        new = b'\x37' + struct.pack('<d', PICKUP_RATE) + b'\x37' + struct.pack('<d', PICKUP_RATE)
        self.rate.build(c, (f, data), [(data + i, old, new) for i in occurrences(raw, old)])
        cls, f, data, raw = script_of(c, 'cls_pickup', 'ExecuteUbergraph_ALS_Ability_PickupLootItem')
        sites = []
        for value in (0.6, 0.8):
            old = b'\x1e' + struct.pack('<f', value)
            found = occurrences(raw, old)
            if len(found) == 1:
                sites.append((data + found[0], old, b'\x1e' + struct.pack('<f', value / PICKUP_RATE)))
        self.delay.build(c, (f, data), sites)
        done = self.rate.apply(c) + self.delay.apply(c)
        if not done:
            return FAIL, T('Код подбора не распознан')
        return OK, T('Подбор ×') + ('%g' % PICKUP_RATE)

    def release(self, c):
        self.rate.restore(c)
        self.delay.restore(c)

AGILE_SCALE = ((4.0, 4.0, 2.0), 2.0)

class Agile(Feature):
    key = 'agile'
    hotkey = 'Ctrl+Alt+Num0'
    title = 'Лёгкое движение'
    description = 'Без инерции: быстрые старт, остановка и повороты'
    experimental = True

    def curves(self, c):
        out = {}
        for preset in c.objs('preset'):
            if 'PlayerPresets' not in c.u.path(preset):
                continue
            for name, (offset, kind, mask, sub) in c.u.class_props(c.u.cls(preset)).items():
                if kind == 'StructProperty' and sub and c.u.objname(sub).startswith('CALSMovementSettingsStrafe'):
                    movement = c.u.m.u64(preset + offset + 0x48)
                    rotation = c.u.m.u64(preset + offset + 0x50)
                    if movement:
                        out[movement] = AGILE_SCALE[0]
                    if rotation:
                        out[rotation] = (AGILE_SCALE[1],)
        return out

    def scale_curve(self, c, curve, factors):
        m = c.u.m
        props = c.u.class_props(c.u.cls(curve))
        first = min((v[0] for v in props.values() if v[1] == 'StructProperty'), default=None)
        if first is None:
            return 0
        done = 0
        for index, factor in enumerate(factors):
            base = curve + first + index * 0x80
            raw = m.read(base + 0x70, 16)
            if len(raw) != 16:
                continue
            data, count, capacity = struct.unpack('<Qii', raw)
            if not data or not 0 < count < 64:
                continue
            for k in range(count):
                key = data + k * 0x1C
                values = m.read(key + 8, 16)
                if len(values) != 16:
                    continue
                value, arrive, weight, leave = struct.unpack('<ffff', values)
                if key not in self.saved:
                    if not 0 <= value <= 100000:
                        continue
                    self.saved[key] = (value, arrive, leave)
                original = self.saved[key]
                wanted = (original[0] * factor, original[1] * factor, original[2] * factor)
                if abs(value - wanted[0]) > 1e-3:
                    m.write(key + 8, struct.pack('<ff', wanted[0], wanted[1]))
                    m.write(key + 0x14, struct.pack('<f', wanted[2]))
                done += 1
        return done

    def tick(self, c):
        curves = self.curves(c)
        if not curves:
            return WAIT, T('Ожидание пресетов движения')
        for curve, factors in curves.items():
            self.scale_curve(c, curve, factors)
        return OK, T('Инерция снижена · кривых: ') + str(len(curves))

    def release(self, c):
        for key, (value, arrive, leave) in list(self.saved.items()):
            raw = c.u.m.read(key + 8, 4)
            if len(raw) == 4:
                c.u.m.write(key + 8, struct.pack('<ff', value, arrive))
                c.u.m.write(key + 0x14, struct.pack('<f', leave))
        self.saved.clear()

FEATURES = [God, Untouchable, Fall, Ammo, NoReload, Accuracy, NoRecoil, Stamina, Invisible, OneHit, Speed, Tempo, Agile, FastPickup, Slomo, PickLock, Radial, HackTimer,
            AutoKey, FreeCraft, Money, Drone, DroneCooldown, DroneTime, DroneItems, DroneStats, Hunger]
CORE = ('god', 'untouchable', 'fall', 'ammo', 'noreload', 'stamina', 'invisible', 'onehit')

def diagnostics(c, trainer):
    u = c.u
    lines = []
    add = lines.append
    add('PangitTrainer diagnostics')
    add('time: ' + time.strftime('%Y-%m-%d %H:%M:%S'))
    add('build: timestamp 0x%08X image 0x%X known=%s' % (u.m.timestamp, u.m.image_size, u.m.known_build))
    add('objects: %d' % u.count)
    add('')
    for key in GROUPS:
        add('group %-15s %d objects' % (key, len(c.objs(key))))
    add('')
    for p in c.players():
        add('player ' + u.path(p).split('PersistentLevel.')[-1])
        for name in ('Current Health', 'IsDeath', 'Stamina', 'Ammo_Pistol', 'Ammo_Rifle', 'TotalAmmo', 'LockShootingInput', 'isAim', 'isRun', 'Slomo', 'IsHaveBackpack'):
            add('   %-20s %s' % (name, u.get(p, name)))
        attributes = c.pointer_field(p, 'Attributes')
        if attributes:
            offset = u.struct_offset(attributes, 'Health')
            if offset is not None:
                raw = u.m.read(attributes + offset + 8, 8)
                if len(raw) == 8:
                    add('   GAS Health base/current %s' % (struct.unpack('<ff', raw),))
        survival = c.pointer_field(p, 'BP_InventorySurvivalComponent')
        if survival:
            for name in ('PlayerHealth', 'PlayerMaxHealth', 'bEnableHunger', 'bEnableThirst', 'PlayerNutrition', 'PlayerHydration'):
                add('   survival.%-16s %s' % (name, u.get(survival, name)))
        component = c.pointer_field(p, 'BP_FallDamageAndSliding')
        if component:
            for name in ('StartFallDamageAnim', 'StartFallDamageEvent'):
                add('   fall.%-20s %s' % (name, u.get(component, name)))
            for name in FALL_LIMITS:
                f = u.field(component, name)
                raw = u.m.read(f.address, 24) if f else b''
                add('   fall.%-20s %s' % (name, struct.unpack('<3d', raw) if len(raw) == 24 else None))
    add('')
    for key, w in c.owned_weapons():
        add('weapon %s %s' % (key, u.path(w).split('PersistentLevel.')[-1]))
        for name in ('AmmoCount', 'MagazineCount', 'TotalAmmo', 'DefaultAmmoCount', 'DefaultMagazineCount', 'RifleModelIndex'):
            add('   %-20s %s' % (name, u.get(w, name)))
    for key in ('pistol', 'rifle'):
        for w in c.objs(key):
            add('weapon-in-world %s owner=%s' % (key, u.get(w, 'Owner')))
    add('')
    for key in ('cfg_player', 'cfg_zombie'):
        for cfg in c.objs(key):
            add('%s ReceiveDamageScale=%s MaxHealth=%s ArmorValue=%s' % (key, u.get(cfg, 'ReceiveDamageScale'), u.get(cfg, 'MaxHealth'), u.get(cfg, 'ArmorValue')))
    for key in ('zombie', 'human'):
        for z in c.objs(key)[:20]:
            add('%s %s hp=%s dead=%s' % (key, u.objname(z), u.get(z, 'CurrentHealthPoints'), u.get(z, 'IsDeathC')))
    for n in c.objs('zombie_attack')[:30]:
        f = u.field(n, 'Object Types')
        raw = u.m.read(f.address, 16) if f else b''
        add('zombie_attack %s types=%s damage=%s' % (u.objname(n), struct.unpack('<Qii', raw)[1] if len(raw) == 16 else None, u.get(n, 'DamageValue')))
    for h in c.objs('human')[:20]:
        add('human %s CanShoot=%s DealingDamageScalar=%s' % (u.objname(h), u.get(h, 'CanShoot'), u.get(h, 'DealingDamageScalar')))
    for ctl in c.objs('zombie_ctl')[:20]:
        add('zombie_ctl %s sight=%s detect=%s sound=%s' % (u.objname(ctl), u.get(ctl, 'SightPerceptionStatus'), u.get(ctl, 'DesiredDetectionValue'), u.get(ctl, 'AcumulatedSoundsStrength')))
    add('')
    for key in ('picklock', 'radial'):
        for g in c.objs(key):
            names = ('GameSpawned', 'ActivatePressed', 'MiniGameState', 'GameStatus', 'IsLockSolved', 'PinAlreadySolved', 'NeedToComplete', 'CurrentCompleted', 'PickLockState', 'WantExit', 'ProcessToExit', 'IsGameComplete') if key == 'picklock' \
                else ('IsMiniGameActivated', 'MiniGameEnabled', 'InputEnable', 'IsRotation', 'Success', 'Quest Complite', 'SolverPos', 'Ring1_Index', 'Ring2_Index', 'Ring3_Index')
            add('%s %s %s' % (key, u.path(g).split('PersistentLevel.')[-1][:60], {n: u.get(g, n) for n in names}))
    for d in c.objs('drone'):
        add('drone %s' % ({n: u.get(d, n) for n in ('DroneCurrentHealth', 'SkillBaseHealth', 'IgnoreOnDamage')},))
    for i in c.objs('inventory'):
        add('inventory player=%s currency=%s %s' % (u.get(i, 'bIsPlayerOwned'), u.get(i, 'CurrencyAmount'), u.path(i).split('.')[-2][:40]))
    for key, names in (
            ('cls_player', ('S-ShotTraceFunction', 'FindInventoryItem')),
            ('cls_minibase', ('CheckTotal_KEY', 'CalculateTimer', 'MiniGame_SetComplete')),
            ('cls_code', ('CheckTotal_KEY', 'ExecuteUbergraph_BP_Code'))):
        cls = c.first(key)
        add('class %s=%s' % (key, hex(cls)))
        for name in names:
            function = u.function(cls, name) if cls else 0
            data, raw = u.script(function) if function else (0, b'')
            add('  function %s address=%s script=%s size=%d' % (name, hex(function), hex(data), len(raw)))
            if function:
                for prop_name, entry in u.own_props(function).items():
                    prop = u.property(function, prop_name)
                    add('    %s %s flags=%X' % (prop_name, entry[1], u.m.u64(prop + 0x38)))
    for widget in c.objs('craft_widget')[:40]:
        add('craft-widget %s %s' % (u.objname(widget), {name: u.get(widget, name) for name in (
            'Add Item', 'Ammo Item 1', 'Ammo Item 2', 'Ammo Item 3', 'Restart_Buttom')}))
    for door in c.objs('key_door'):
        item = c.pointer_field(door, 'KeyItem')
        add('key-door %s key=%s locked=%s closed=%s' % (
            u.objname(door), u.objname(item) if item else None,
            u.get(door, 'bIsLocked'), u.get(door, 'bIsClosed')))
    craft = trainer.by_key.get('craft')
    if craft:
        add('craft ingredient_check_sites=%s modified_counts=%d' % (len(craft.check.sites), len(craft.saved)))
    for feature_key, attributes in (('craft', ('debit',)), ('autokey', ('key_check',)), ('picklock', ('entry', 'solve')), ('radial', ('solve',)), ('noreload', ('debit',))):
        feature = trainer.by_key.get(feature_key)
        if feature:
            add('%s patches=%s' % (feature_key, {name: len(getattr(feature, name).sites) for name in attributes}))
    for w in c.objs('world'):
        add('world TimeDilation=%s' % u.get(w, 'TimeDilation'))
    for preset in c.objs('preset'):
        raw = u.m.read(preset + 0x30, 72)
        if len(raw) == 72:
            add('preset %s %s' % (u.objname(preset), [round(v, 1) for v in struct.unpack('<9d', raw)]))
    add('')
    for key, (state, text) in trainer.status.items():
        add('feature %-12s %-5s %s' % (key, state, text))
    return '\n'.join(lines) + '\n'
