import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_regressions import Memory
from blueprint_model import Blueprint
import cheats
import engine

class WeaponFixture:
    def __init__(self):
        self.m=Memory()
        self.player,self.pistol,self.rifle=0x20000,0x30000,0x40000
        self.table,self.row_struct,self.entries=0x50000,0x60000,0x70000
        self.caps=(30,20,8,5)
        self.m.load(self.table+0x28,struct.pack('<QQi',self.row_struct,self.entries,5))
        self.m.load(self.row_struct+0x58,struct.pack('<I',64))
        bitmap=bytearray(40);bitmap[0]=0b11101
        struct.pack_into('<Qiiii',bitmap,16,0,5,128,1,1)
        self.m.load(self.table+0x40,bitmap)
        self.schema={'MaxAmmoCoutPerMagazine_41_test':(12,'IntProperty',0,0)}
        for model,index in enumerate((0,2,3,4)):
            row=0x80000+model*0x1000
            self.m.load(self.entries+index*24,struct.pack('<QQQ',index+10,row,0))
            self.m.load(row,bytes(64));self.m.load(row+12,struct.pack('<i',self.caps[model]))
        self.m.load(self.entries+24,struct.pack('<QQQ',99,0xB0000,0))
        self.fields={};self.metadata={}
        def define(obj,name,kind,value,offset):
            f=engine.Field(obj+offset,kind,1);self.fields[obj,name]=f;self.metadata[obj,name]=offset
            self.m.load(f.address,struct.pack(engine.READERS[kind][0],value))
        self.define=define
        define(self.player,'PistolActor','ObjectProperty',self.pistol,0x10)
        define(self.player,'RifleActor','ObjectProperty',self.rifle,0x20)
        define(self.pistol,'AmmoCount','IntProperty',0,0x30)
        define(self.rifle,'DefaultAmmoCount','IntProperty',0,0x30)
        define(self.rifle,'RifleAssetsDataTable','ObjectProperty',self.table,0x40)
        define(self.rifle,'RifleModelIndex','ByteProperty',0,0x50)
        self.fields[self.pistol,'PistolAsset']=engine.Field(self.pistol+0x60,'StructProperty',0)
        self.metadata[self.pistol,'PistolAsset']=0x60
        self.m.load(self.pistol+0x60,struct.pack('<i',15))
        self.u=SimpleNamespace(m=self.m,valid=lambda obj:obj in (self.player,self.pistol,self.rifle,self.table),
            field=lambda obj,name:self.fields.get((obj,name)),cls=lambda obj:obj+0x1000,
            struct_offset=lambda obj,name:self.metadata.get((obj,name)),
            struct_fields=lambda obj,name:{'MaxAmmoCount_28_test':(0,'IntProperty',0,0)} if name=='PistolAsset' else {},
            own_props=lambda owner:self.schema if owner==self.row_struct else {})
        self.u.read=lambda field:engine.Unreal.read(self.u,field)
        self.u.write=lambda field,value:engine.Unreal.write(self.u,field,value)
        self.u.get=lambda obj,name:self.u.read(self.u.field(obj,name))
        self.u.table_rows=lambda table:engine.Unreal.table_rows(self.u,table)
        self.c=cheats.Context(self.u);self.c.players=lambda:[self.player]
        self.m.writes.clear()

class WeaponRadialTests(unittest.TestCase):
    def test_real_weapon_counters_fill_even_when_empty(self):
        f=WeaponFixture();feature=cheats.NoReload()
        with patch('cheats.preserve_shot_ammo',return_value=2):
            self.assertEqual(feature.tick(f.c)[0],cheats.OK)
        self.assertEqual(f.u.get(f.pistol,'AmmoCount'),15)
        self.assertEqual(f.u.get(f.rifle,'DefaultAmmoCount'),30)
        self.assertNotIn((f.player,'Ammo_Rifle'),feature.caps)

    def test_rifle_models_use_sparse_row_iteration_and_reset_old_capacity(self):
        f=WeaponFixture();feature=cheats.NoReload()
        with patch('cheats.preserve_shot_ammo',return_value=2):
            for model,size in enumerate(f.caps):
                f.m.load(f.rifle+0x50,bytes([model]))
                self.assertEqual(f.c.magazine_size(f.rifle),size)
                self.assertEqual(feature.tick(f.c)[0],cheats.OK)
                self.assertEqual(f.u.get(f.rifle,'DefaultAmmoCount'),size)
        self.assertFalse(any(0xB0000<=address<0xB0040 for address,_ in f.m.writes))

    def test_bad_table_and_unknown_empty_magazine_do_not_report_full(self):
        f=WeaponFixture();feature=cheats.NoReload()
        f.m.load(f.table+0x40,b'\x1f')
        self.assertEqual(f.c.magazine_size(f.rifle),0)
        with patch('cheats.preserve_shot_ammo',return_value=2):
            self.assertEqual(feature.tick(f.c)[0],cheats.FAIL)
        self.assertEqual(f.u.get(f.rifle,'DefaultAmmoCount'),0)

    def test_reload_write_failures_do_not_report_full(self):
        f=WeaponFixture();feature=cheats.NoReload();f.m.fail=True
        with patch('cheats.preserve_shot_ammo',return_value=2):
            self.assertEqual(feature.tick(f.c)[0],cheats.FAIL)

    def test_pistol_and_long_gun_shot_consumption_patch_and_restore(self):
        b=Blueprint(Memory(),'cls_player').shot_ammo();original=b.program()
        patcher=cheats.CodePatch()
        values={b.props['CallFunc_BPIPW_GetCurrentAmmoInfo_AmmoCount']:5,b.props['CallFunc_BPIPW_GetCurrentAmmoInfo_AmmoCount_1']:9}
        before=b.run(values=values)['values']
        self.assertEqual(before[b.props['CallFunc_Subtract_IntInt_ReturnValue']],4)
        self.assertEqual(before[b.props['CallFunc_Subtract_IntInt_ReturnValue_1']],8)
        self.assertEqual(cheats.preserve_shot_ammo(b.c,patcher),2)
        after=b.run(values=values)['values']
        self.assertEqual(after[b.props['CallFunc_Subtract_IntInt_ReturnValue']],5)
        self.assertEqual(after[b.props['CallFunc_Subtract_IntInt_ReturnValue_1']],9)
        self.assertEqual(sum(a!=z for a,z in zip(original,b.program())),2)
        self.assertEqual(cheats.preserve_shot_ammo(b.c,patcher),2)
        patcher.restore(b.c);self.assertEqual(b.program(),original)

    def test_radial_auto_requires_active_and_input_ready_and_runs_once(self):
        b=Blueprint(Memory(),'cls_radial').radial();original=b.program()
        patcher=cheats.CodePatch();self.assertEqual(cheats.radial_auto(b.c,patcher),2)
        for active,ready in ((False,False),(False,True),(True,False)):
            result=b.run(values={b.props['IsMiniGameActivated']:active,b.props['InputEnable']:ready})
            self.assertEqual(result['writes'],[])
        values={b.props['IsMiniGameActivated']:True,b.props['InputEnable']:True,b.props['SolverPos']:3}
        result=b.run(values=values)
        self.assertFalse(result['values'][b.props['InputEnable']])
        self.assertTrue(result['values'][b.props['success_animation']])
        self.assertEqual(len(result['writes']),2)
        self.assertEqual(b.run(values=result['values'])['writes'],[])
        self.assertEqual(cheats.radial_auto(b.c,patcher),2)
        patcher.restore(b.c);self.assertEqual(b.program(),original)
        unmatched=b.run(entry=b.comparison_start,values=values)
        self.assertFalse(unmatched['values'].get(b.props['success_animation'],False))

    def test_new_patches_reject_unknown_layout_and_failed_writes(self):
        for setup,group,method,missing in (
                ('shot_ammo','cls_player',cheats.preserve_shot_ammo,'CallFunc_Subtract_IntInt_ReturnValue_1'),
                ('radial','cls_radial',cheats.radial_auto,'Ring3_Index')):
            b=getattr(Blueprint(Memory(),group),setup)();b.types.pop(missing)
            self.assertEqual(method(b.c,cheats.CodePatch()),0)
            self.assertEqual(b.m.writes,[])
            b=getattr(Blueprint(Memory(),group),setup)();b.m.fail=True
            self.assertEqual(method(b.c,cheats.CodePatch()),0)
