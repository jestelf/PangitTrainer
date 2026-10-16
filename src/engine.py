import ctypes as C
from ctypes import wintypes as W
import struct

from lang import T

K = C.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype = W.BOOL
K.WriteProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
K.WriteProcessMemory.restype = W.BOOL
K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
K.CreateToolhelp32Snapshot.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
K.GetExitCodeProcess.restype = W.BOOL
K.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
K.CreateMutexW.restype = W.HANDLE

class PROCESSENTRY(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD), ('heap', C.c_size_t), ('module', W.DWORD),
                ('threads', W.DWORD), ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]

class MODULEENTRY(C.Structure):
    _fields_ = [('size', W.DWORD), ('module', W.DWORD), ('pid', W.DWORD), ('usage', W.DWORD), ('procusage', W.DWORD),
                ('base', C.c_void_p), ('length', W.DWORD), ('handle', W.HMODULE), ('name', W.WCHAR * 256), ('path', W.WCHAR * 260)]

for _name, _type in (('Process32FirstW', PROCESSENTRY), ('Process32NextW', PROCESSENTRY),
                     ('Module32FirstW', MODULEENTRY), ('Module32NextW', MODULEENTRY)):
    _f = getattr(K, _name)
    _f.argtypes = [W.HANDLE, C.POINTER(_type)]
    _f.restype = W.BOOL

EXE = 'PANGIT_Demo-Win64-Shipping.exe'
KNOWN_BUILD = (0x00D297B3, 0xAD42000)
INVALID_HANDLE = C.c_void_p(-1).value
OBJECT_CDO = 0x10
CLASS_TYPES = ('Class', 'BlueprintGeneratedClass', 'AnimBlueprintGeneratedClass', 'WidgetBlueprintGeneratedClass')
ITEM_GARBAGE = 0x30000000

def find_process(name=EXE):
    snap = K.CreateToolhelp32Snapshot(0x2, 0)
    if snap == INVALID_HANDLE:
        return 0
    try:
        e = PROCESSENTRY()
        e.size = C.sizeof(e)
        ok = K.Process32FirstW(snap, C.byref(e))
        while ok:
            if e.exe.lower() == name.lower():
                return e.pid
            ok = K.Process32NextW(snap, C.byref(e))
    finally:
        K.CloseHandle(snap)
    return 0

def pointer(value):
    return 0x10000 < value < 0x7fffffffffff and value % 8 == 0

def pe_layout(m):
    header = m.read(m.base, 0x1000)
    if len(header) < 64 or header[:2] != b'MZ':
        raise RuntimeError(T('Не удалось прочитать заголовок игры.'))
    pe = struct.unpack_from('<I', header, 0x3c)[0]
    if not 0x40 <= pe <= 0x800 or header[pe:pe + 4] != b'PE\0\0':
        raise RuntimeError(T('Не распознан формат игрового EXE.'))
    machine, count = struct.unpack_from('<HH', header, pe + 4)
    optional = struct.unpack_from('<H', header, pe + 20)[0]
    if machine != 0x8664 or struct.unpack_from('<H', header, pe + 24)[0] != 0x20b:
        raise RuntimeError(T('Поддерживается только Windows x64.'))
    size = struct.unpack_from('<I', header, pe + 0x50)[0]
    if not 0x100000 <= size <= 0x40000000 or not 1 <= count <= 96:
        raise RuntimeError(T('Неверные границы игрового модуля.'))
    end = pe + 24 + optional + count * 40
    if end > len(header):
        header = m.read(m.base, end)
    sections = []
    for i in range(count):
        off = pe + 24 + optional + i * 40
        name = header[off:off + 8].rstrip(b'\0').decode('ascii', errors='replace')
        length, rva = struct.unpack_from('<II', header, off + 8)
        flags = struct.unpack_from('<I', header, off + 36)[0]
        if length and rva >= 0x1000 and rva + length <= size:
            sections.append((name, rva, length, flags))
    if not any(s[3] & 0x20000000 for s in sections):
        raise RuntimeError(T('Не найдены секции кода игры.'))
    return struct.unpack_from('<I', header, pe + 8)[0], size, sections

def executable(m, address):
    rva = address - m.base
    return any(start <= rva < start + length and flags & 0x20000000 for _, start, length, flags in m.sections)

class Names:
    def __init__(self, m, pool, shift):
        self.m = m
        self.pool = pool
        self.shift = shift
        self.cache = {}

    def name(self, index):
        if index in self.cache:
            return self.cache[index]
        block = index >> 16
        if block > 8191:
            return '?'
        ptr = self.m.u64(self.pool + 16 + block * 8)
        if not pointer(ptr):
            return '?'
        address = ptr + (index & 65535) * 2
        raw = self.m.read(address, 2)
        if len(raw) != 2:
            return '?'
        header = struct.unpack('<H', raw)[0]
        length = header >> self.shift
        if not 0 < length < 1024:
            return '?'
        wide = bool(header & 1)
        raw = self.m.read(address + 2, length * (2 if wide else 1))
        if len(raw) != length * (2 if wide else 1):
            return '?'
        try:
            value = raw.decode('utf-16le' if wide else 'utf-8')
        except UnicodeError:
            return '?'
        if any(ord(c) < 32 for c in value):
            return '?'
        self.cache[index] = value
        return value

def name_pool(m, rva):
    raw = m.read(m.base + rva, 24)
    if len(raw) != 24:
        return None
    block, cursor, first = struct.unpack_from('<IIQ', raw, 8)
    if not 0 <= block < 8192 or not 8 <= cursor <= 131072 or not pointer(first):
        return None
    if m.base <= first < m.base + m.image_size:
        return None
    for shift in (6, 1, 5):
        names = Names(m, m.base + rva, shift)
        if names.name(0) != 'None':
            continue
        sample = m.read(first, 1024)
        if sum(value in sample for value in (b'ByteProperty', b'IntProperty', b'BoolProperty', b'ObjectProperty')) < 3:
            continue
        return names
    return None

def writable_blocks(m):
    for _, rva, size, flags in m.sections:
        if not flags & 0x80000000 or flags & 0x20000000:
            continue
        for offset in range(0, size, 0x30000):
            data = m.read(m.base + rva + offset, min(0x30000 + 32, size - offset))
            if data:
                yield rva + offset, data

class Memory:
    def __init__(self, pid):
        self.pid = pid
        self.h = K.OpenProcess(0x438, False, pid)
        if not self.h:
            raise C.WinError(C.get_last_error())
        try:
            snap = K.CreateToolhelp32Snapshot(0x18, pid)
            e = MODULEENTRY()
            e.size = C.sizeof(e)
            if snap == INVALID_HANDLE or not K.Module32FirstW(snap, C.byref(e)):
                raise C.WinError(C.get_last_error())
            K.CloseHandle(snap)
            self.base = e.base
            self.path = e.path
            self.timestamp, self.image_size, self.sections = pe_layout(self)
            self.known_build = (self.timestamp, self.image_size) == KNOWN_BUILD
            self.names, self.array = self.locate()
        except Exception:
            self.close()
            raise

    def locate(self):
        pools, candidates = [], []
        for rva, raw in writable_blocks(self):
            for off in range(0, len(raw) - 31, 8):
                block, cursor, first = struct.unpack_from('<IIQ', raw, off + 8)
                if block < 8192 and 8 <= cursor <= 131072 and pointer(first) and not self.base <= first < self.base + self.image_size:
                    found = name_pool(self, rva + off)
                    if found and all(found.pool != n.pool for n in pools):
                        pools.append(found)
                chunks, _, maximum, count, maxchunks, used = struct.unpack_from('<QQIIII', raw, off)
                if pointer(chunks) and 5000 <= count <= maximum <= 16000000 and 1 <= used <= maxchunks <= 1024 \
                        and maximum % maxchunks == 0 and maximum // maxchunks in (16384, 32768, 65536, 131072, 262144):
                    candidates.append((rva + off, chunks, maximum, count, maxchunks))
        if len(pools) != 1:
            raise RuntimeError(T('Эта сборка: таблица имён UE не найдена. Функции недоступны.'))
        names = pools[0]
        found = []
        for rva, chunks, maximum, count, maxchunks in candidates:
            size = maximum // maxchunks
            for stride, offset in ((24, 0), (24, 8), (32, 0), (32, 8)):
                good = 0
                for index in (1, 2, 3, 7, count // 2, count - 1):
                    chunk = self.u64(chunks + 8 * (index // size))
                    obj = self.u64(chunk + stride * (index % size) + offset) if pointer(chunk) else 0
                    raw = self.read(obj, 40) if pointer(obj) else b''
                    if len(raw) != 40:
                        continue
                    vtable, flags, actual, cls = struct.unpack_from('<QIIQ', raw)
                    if actual == index and pointer(cls) and self.base < vtable < self.base + self.image_size \
                            and names.name(self.u32(cls + 24)) in ('Class', 'Package', 'ScriptStruct', 'Function', 'BlueprintGeneratedClass'):
                        good += 1
                if good >= 4:
                    found.append({'rva': rva, 'stride': stride, 'offset': offset, 'chunk_size': size})
        if len(found) != 1:
            raise RuntimeError(T('Эта сборка: таблица объектов UE не найдена. Запись отключена.'))
        return names, found[0]

    def read(self, a, n):
        if not 0x10000 < a < 0x7fffffffffff or n <= 0:
            return b''
        if n > 262144:
            out = b''
            while n > 0:
                part = self.read(a + len(out), min(n, 262144))
                if not part:
                    break
                out += part
                n -= len(part)
            return out
        buffer = C.create_string_buffer(n)
        got = C.c_size_t()
        K.ReadProcessMemory(self.h, C.c_void_p(a), buffer, n, C.byref(got))
        return buffer.raw[:got.value]

    def write(self, a, data):
        if not 0x10000 < a < 0x7fffffffffff:
            return False
        got = C.c_size_t()
        return bool(K.WriteProcessMemory(self.h, C.c_void_p(a), data, len(data), C.byref(got))) and got.value == len(data)

    def u64(self, a):
        b = self.read(a, 8)
        return struct.unpack('<Q', b)[0] if len(b) == 8 else 0

    def u32(self, a):
        b = self.read(a, 4)
        return struct.unpack('<I', b)[0] if len(b) == 4 else 0

    def alive(self):
        code = W.DWORD()
        return bool(self.h and K.GetExitCodeProcess(self.h, C.byref(code)) and code.value == 259)

    def close(self):
        if getattr(self, 'h', None):
            K.CloseHandle(self.h)
            self.h = None

READERS = {
    'FloatProperty': ('<f', 4),
    'DoubleProperty': ('<d', 8),
    'IntProperty': ('<i', 4),
    'UInt32Property': ('<I', 4),
    'Int64Property': ('<q', 8),
    'ByteProperty': ('<B', 1),
    'EnumProperty': ('<B', 1),
    'BoolProperty': ('<B', 1),
    'ObjectProperty': ('<Q', 8),
    'ObjectPtrProperty': ('<Q', 8),
}

class Field:
    __slots__ = ('address', 'type', 'mask')

    def __init__(self, address, type, mask):
        self.address = address
        self.type = type
        self.mask = mask

class Unreal:
    def __init__(self, m):
        self.m = m
        self.names = m.names
        self.chunks = m.u64(m.base + m.array['rva'])
        self.count = m.u32(m.base + m.array['rva'] + 20)
        self.stride = m.array['stride']
        self.offset = m.array['offset']
        self.chunk_size = m.array['chunk_size']
        self.flag_offset = 8 if self.offset == 0 else 4
        self.props = {}

    def name(self, index):
        return self.names.name(index)

    def fname(self, a):
        raw = self.m.read(a, 8)
        if len(raw) != 8:
            return '?'
        index, number = struct.unpack('<II', raw)
        text = self.name(index)
        return text if number == 0 else '%s_%d' % (text, number - 1)

    def refresh_count(self):
        self.count = self.m.u32(self.m.base + self.m.array['rva'] + 20)
        return self.count

    def items(self):
        m = self.m
        step = 262144 // self.stride
        size = self.chunk_size
        count = self.refresh_count()
        for ch in range((count + size - 1) // size):
            chunk = m.u64(self.chunks + 8 * ch)
            if not pointer(chunk):
                continue
            total = min(size, count - ch * size)
            for start in range(0, total, step):
                part = min(step, total - start)
                raw = m.read(chunk + self.stride * start, part * self.stride)
                for j in range(0, len(raw) - self.stride + 1, self.stride):
                    ptr = struct.unpack_from('<Q', raw, j + self.offset)[0]
                    if ptr and not struct.unpack_from('<I', raw, j + self.flag_offset)[0] & ITEM_GARBAGE:
                        yield ptr

    def cls(self, obj):
        return self.m.u64(obj + 16)

    def objname(self, obj):
        return self.fname(obj + 24)

    def clsname(self, obj):
        c = self.cls(obj)
        return self.fname(c + 24) if pointer(c) else '?'

    def outer(self, obj):
        return self.m.u64(obj + 32)

    def path(self, obj):
        parts = []
        guard = 0
        while pointer(obj) and guard < 12:
            parts.append(self.objname(obj))
            obj = self.outer(obj)
            guard += 1
        return '.'.join(reversed(parts))

    def valid(self, obj):
        raw = self.m.read(obj, 40)
        if len(raw) != 40:
            return False
        vtable, flags, index, cls = struct.unpack_from('<QIIQ', raw)
        if flags & 0x18000 or not self.m.base < vtable < self.m.base + self.m.image_size:
            return False
        return index < self.count and pointer(cls)

    def super_of(self, cls):
        return self.m.u64(cls + 0x40)

    def chain(self, cls):
        out = []
        while pointer(cls) and cls not in out and len(out) < 40:
            out.append(cls)
            cls = self.super_of(cls)
        return out

    def own_props(self, cls):
        m = self.m
        out = {}
        f = m.u64(cls + 0x50)
        seen = set()
        while pointer(f) and f not in seen and len(seen) < 4000:
            seen.add(f)
            raw = m.read(f, 0x88)
            if len(raw) < 0x88:
                break

            name = self.fname(f + 0x20)
            kind = self.fname(m.u64(f + 8))
            offset = struct.unpack_from('<I', raw, 0x44)[0]
            if name != '?' and kind.endswith('Property') and offset < 0x100000:
                sub = struct.unpack_from('<Q', raw, 0x70)[0]
                if kind in ('ArrayProperty', 'SetProperty') and not pointer(sub):
                    sub = struct.unpack_from('<Q', raw, 0x78)[0]
                out[name] = (offset, kind, raw[0x72] if kind == 'BoolProperty' else 0, sub)
            f = struct.unpack_from('<Q', raw, 0x18)[0]
        return out

    def class_props(self, cls):
        if cls in self.props:
            return self.props[cls]
        merged = {}
        for c in reversed(self.chain(cls)):
            merged.update(self.own_props(c))
        self.props[cls] = merged
        return merged

    def field(self, obj, name):
        props = self.class_props(self.cls(obj))
        entry = props.get(name)
        if not entry:
            return None
        return Field(obj + entry[0], entry[1], entry[2])

    def struct_offset(self, obj, name):
        entry = self.class_props(self.cls(obj)).get(name)
        return entry[0] if entry else None

    def array(self, obj, name):
        entry = self.class_props(self.cls(obj)).get(name)
        if not entry or entry[1] != 'ArrayProperty':
            return None
        raw = self.m.read(obj + entry[0], 16)
        if len(raw) != 16:
            return None
        data, count, capacity = struct.unpack('<Qii', raw)
        inner = entry[3]
        size, fields = 0, {}
        if pointer(inner) and self.fname(self.m.u64(inner + 8)) == 'StructProperty':
            st = self.m.u64(inner + 0x70)
            if pointer(st):
                size = self.m.u32(st + 0x58)
                fields = {k: (v[0], v[1], v[2]) for k, v in self.own_props(st).items()}
        return data, count, size, fields

    def struct_fields(self, obj, name):
        entry = self.class_props(self.cls(obj)).get(name)
        if not entry or entry[1] != 'StructProperty' or not pointer(entry[3]):
            return {}
        return self.own_props(entry[3])

    def read(self, f):
        if f is None:
            return None
        if f.type == 'BoolProperty':
            raw = self.m.read(f.address, 1)
            return bool(raw[0] & f.mask) if raw and f.mask else None
        spec = READERS.get(f.type)
        if not spec:
            return None
        raw = self.m.read(f.address, spec[1])
        return struct.unpack(spec[0], raw)[0] if len(raw) == spec[1] else None

    def write(self, f, value):
        if f is None:
            return False
        if f.type == 'BoolProperty':
            raw = self.m.read(f.address, 1)
            if not raw or not f.mask:
                return False
            byte = (raw[0] | f.mask) if value else (raw[0] & ~f.mask & 0xff)
            return True if byte == raw[0] else self.m.write(f.address, bytes([byte]))
        spec = READERS.get(f.type)
        if not spec or f.type in ('ObjectProperty', 'ObjectPtrProperty'):
            return False
        data = struct.pack(spec[0], value)
        raw = self.m.read(f.address, spec[1])
        return True if raw == data else self.m.write(f.address, data)

    def function(self, cls, name):
        for owner in self.chain(cls):
            f = self.m.u64(owner + 0x48)
            seen = set()
            while pointer(f) and f not in seen and len(seen) < 5000:
                seen.add(f)
                if self.objname(f) == name:
                    return f
                f = self.m.u64(f + 0x28)
        return 0

    def property(self, owner, name):
        for s in self.chain(owner):
            f = self.m.u64(s + 0x50)
            seen = set()
            while pointer(f) and f not in seen and len(seen) < 4000:
                seen.add(f)
                if self.fname(f + 0x20) == name:
                    return f
                f = self.m.u64(f + 0x18)
        return 0

    def script(self, function):
        raw = self.m.read(function + 0x60, 12)
        if len(raw) != 12:
            return 0, b''
        data, count = struct.unpack('<Qi', raw)
        if not pointer(data) or not 0 < count < 1000000:
            return 0, b''
        return data, self.m.read(data, count)

    def table_rows(self, table):

        m = self.m
        row_struct, data = m.u64(table + 0x28), m.u64(table + 0x30)
        raw, allocation = m.read(table + 0x38, 4), m.read(table + 0x40, 40)
        if not pointer(row_struct) or not pointer(data) or len(raw) != 4 or len(allocation) != 40:
            return [], {}
        count = struct.unpack('<i', raw)[0]
        secondary, bits_count, capacity, first_free, num_free = struct.unpack_from('<Qiiii', allocation, 16)
        if not 0 < count <= 10000 or bits_count != count or not count <= capacity <= 100000 or not 0 <= num_free <= count:
            return [], {}
        bits = m.read(secondary, (count + 7) // 8) if secondary else allocation[:16]
        if len(bits) * 8 < count:
            return [], {}
        occupied = [i for i in range(count) if bits[i // 8] & (1 << (i % 8))]
        if len(occupied) != count - num_free:
            return [], {}
        size = m.u32(row_struct + 0x58)
        if not 0 < size <= 65536:
            return [], {}
        rows = []
        for index in occupied:
            row = m.u64(data + index * 24 + 8)
            if not pointer(row):
                return [], {}
            rows.append(row)
        return rows, self.own_props(row_struct)

    def get(self, obj, name):
        return self.read(self.field(obj, name))

    def set(self, obj, name, value):
        return self.write(self.field(obj, name), value)

class Scanner:
    def __init__(self, u, groups):
        self.u = u
        self.groups = groups
        self.targets = {key: set() for key in groups}
        self.wanted = {}
        for key, (name, _) in groups.items():
            self.wanted.setdefault(name, []).append(key)
        self.class_names = {}
        self.metaclasses = set()
        self.known_classes = set()
        self.processed = set()
        self.member = {}
        self.membership_version = 0

    def class_name(self, cls):
        if cls not in self.class_names:
            self.class_names[cls] = self.u.fname(cls + 24)
        return self.class_names[cls]

    def groups_of(self, cls):
        cached = self.member.get(cls)
        if cached is not None and cached[0] == self.membership_version:
            return cached[1]
        hit = set()
        for c in self.u.chain(cls):
            for key, targets in self.targets.items():
                if c in targets:
                    hit.add(key)
        self.member[cls] = (self.membership_version, tuple(hit))
        return self.member[cls][1]

    def scan(self):
        u = self.u
        m = u.m
        found = {key: [] for key in self.groups}
        entries = []
        for ptr in u.items():
            raw = m.read(ptr + 8, 16)
            if len(raw) != 16:
                continue
            flags, _, cls = struct.unpack('<IIQ', raw)
            entries.append((ptr, flags, cls))
        for _, _, cls in entries:
            if cls not in self.known_classes:
                self.known_classes.add(cls)
                if self.class_name(cls) in CLASS_TYPES:
                    self.metaclasses.add(cls)
        changed = False
        for ptr, flags, cls in entries:
            if cls in self.metaclasses and ptr not in self.processed:
                self.processed.add(ptr)
                name = self.class_name(ptr)
                for key in self.wanted.get(name, ()):
                    if ptr not in self.targets[key]:
                        self.targets[key].add(ptr)
                        changed = True
        if changed:
            self.membership_version += 1
        for ptr, flags, cls in entries:
            if flags & 0x18000:
                continue
            for key in self.groups_of(cls):
                mode = self.groups[key][1]
                if mode == 'cdo':
                    if flags & OBJECT_CDO:
                        found[key].append(ptr)
                elif mode == 'arch':
                    if flags & 0x20 and not flags & OBJECT_CDO:
                        found[key].append(ptr)
                elif mode == 'any':
                    found[key].append(ptr)
                elif mode == 'live' and not flags & (OBJECT_CDO | 0x20):
                    found[key].append(ptr)
        for key, (_, mode) in self.groups.items():
            if mode == 'class':
                found[key] = sorted(self.targets[key])
        return found
