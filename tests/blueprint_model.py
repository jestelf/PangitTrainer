import struct
from types import SimpleNamespace
import cheats

P = lambda x: struct.pack('<Q', x)
I = lambda x: struct.pack('<I', x)
RET = b'\x04\x0b\x53'

class Blueprint:

    def __init__(self, memory, group):
        self.m = memory
        self.cls, self.fn, self.code = 0xD0000, 0xE0000, 0xF0000
        self.props, self.types, self.names, self.fnames = {}, {}, {}, {}
        self.raw = b''
        self.u = SimpleNamespace(m=memory, valid=lambda obj: True,
            function=lambda cls, name: self.fn, script=lambda fn: (self.code, memory.read(self.code, len(self.raw))),
            own_props=lambda owner: {n:(0,t,1,0) for n,t in self.types.items()},
            property=lambda owner, name: self.props.get(name,0), name=lambda index:self.names.get(index,'?'),
            class_props=lambda owner:{n:(0,t,1,0) for n,t in self.types.items()})
        self.c=cheats.Context(self.u)
        self.c.found[group]=[self.cls]

    def prop(self, name, kind='BoolProperty', instance=False):
        if name not in self.props:
            self.props[name]=0x100000+len(self.props)*0x1000
            self.types[name]=kind
        return (b'\x01' if instance else b'\x00')+P(self.props[name])

    def name(self, name):
        if name not in self.fnames:
            index=len(self.fnames)+1
            self.fnames[name]=index
            self.names[index]=name
        return I(self.fnames[name])*2+I(0)

    def remove(self, suffix='', inv_suffix=None, literal=False):
        inv=self.prop('CallFunc_GetComponentByClass_ReturnValue'+(suffix if inv_suffix is None else inv_suffix),'ObjectProperty')
        out=self.prop('CallFunc_RemoveItemByDataAsset_bSuccess'+suffix)
        args=(b'\x20'+P(0x800000)+b'\x1d'+I(1)) if literal else self.prop('ingredient'+suffix,'ObjectProperty')+self.prop('quantity'+suffix,'IntProperty')
        body=b'\x45'+self.name('RemoveItemByDataAsset')+args+out+b'\x16'
        return b'\x19'+inv+I(len(body))+bytes(8)+body, out

    def load(self, raw):
        self.raw=raw
        self.m.load(self.code,raw)
        return self

    def program(self): return self.m.read(self.code,len(self.raw))

    def run(self, entry=0, values=None, stock=0, fail_add=False):
        code=self.program();values=dict(values or {});events=[];writes=[];ip=entry
        def expr():
            nonlocal ip
            op=code[ip];ip+=1
            if op in (0,1,0x48):
                key=struct.unpack_from('<Q',code,ip)[0];ip+=8;return values.get(key,0)
            if op==0x20:
                key=struct.unpack_from('<Q',code,ip)[0];ip+=8;return key
            if op==0x1d:
                value=struct.unpack_from('<i',code,ip)[0];ip+=4;return value
            if op==0x24:
                value=code[ip];ip+=1;return value
            if op==0x68:
                fn=struct.unpack_from('<Q',code,ip)[0];ip+=8
                left,right=expr(),expr();assert code[ip]==0x16;ip+=1
                return {'Subtract_IntInt':lambda:left-right,'EqualEqual_IntInt':lambda:left==right,'BooleanAND':lambda:bool(left and right)}[self.native_names[fn]]()
            if op in (0x27,0x28):return op==0x27
            raise AssertionError(('expression',hex(op),ip-1))
        for _ in range(1000):
            op=code[ip]
            if op==0x04:return {'values':values,'stock':stock,'events':events,'writes':writes}
            ip+=1
            if op==0x0b:continue
            if op==0:
                ip-=1;expr();continue
            if op==0x4f:
                if not expr():return {'values':values,'stock':stock,'events':events,'writes':writes}
            elif op in (6,7):
                target=struct.unpack_from('<I',code,ip)[0];ip+=4
                if op==6 or not expr():ip=target
            elif op==0x19:
                expr();ip+=12
            elif op==0x45:
                name=self.names[struct.unpack_from('<I',code,ip)[0]];ip+=12
                if name=='RemoveItemByDataAsset':
                    expr();amount=expr();ip+=1;result=struct.unpack_from('<Q',code,ip)[0];ip+=8
                    success=stock>=max(1,amount)
                    if success:stock-=amount
                    values[result]=success;events.append(('remove',amount,success))
                elif name=='SetGameSpawned':values['spawned']=expr();events.append(('spawn',values['spawned']))
                else:raise AssertionError(name)
                assert code[ip]==0x16;ip+=1
            elif op==0x46:
                fn=struct.unpack_from('<Q',code,ip)[0];ip+=8
                item=expr();amount=expr();assert code[ip]==0x16;ip+=1
                events.append(('add',fn,item,amount))
                if not fail_add:stock+=amount
            elif op==0x14:
                ip+=1;key=struct.unpack_from('<Q',code,ip)[0];ip+=8;values[key]=expr();writes.append((key,values[key]))
            elif op==0x0f:
                ip+=8;ip+=1;key=struct.unpack_from('<Q',code,ip)[0];ip+=8;values[key]=expr();writes.append((key,values[key]))
            else:raise AssertionError(('statement',hex(op),ip-1))
        raise AssertionError('VM did not terminate')

    def craft(self):
        calls=[self.remove(s,inv_suffix=v) for s,v in (('_2','_5'),('_1','_4'),('','_3'))]
        success=self.prop('crafted',instance=True)
        failure=sum(len(call)+14 for call,_ in calls)+11
        raw=b''
        for call,out in calls:raw+=call+b'\x07'+I(failure)+out
        raw+=b'\x14'+success+b'\x27'+RET
        return self.load(raw)

    def cards(self):
        raw=RET;self.entries=[]
        for suffix in ('','_1'):
            count=self.prop('CallFunc_CheckTotal_KEY_TotalAmmo'+suffix,'IntProperty')
            condition=self.prop('CallFunc_GreaterEqual_IntInt_ReturnValue'+suffix)
            call,out=self.remove(suffix,literal=True)
            success=self.prop('opened'+suffix,instance=True)
            self.entries.append(len(raw))
            raw+=count+b'\x07'+I(0)+condition+call+b'\x07'+I(0)+out+b'\x14'+success+b'\x27'+RET
        return self.load(raw)

    def lock_entry(self):
        count=self.prop('CallFunc_CheckTotal_KEY_TotalAmmo','IntProperty')
        condition=self.prop('CallFunc_GreaterEqual_IntInt_ReturnValue')
        call,out=self.remove(literal=True)

        self.entrypoint=len(RET)
        self.load(RET+count+b'\x07'+I(0)+condition+call+RET)
        oldfn=self.u.function;oldprops=self.u.own_props
        self.c.found['cls_inventory']=[0x42000]
        self.u.function=lambda cls,name:0x43000 if cls==0x42000 else oldfn(cls,name)
        self.u.own_props=lambda owner:{'ItemToAdd':(0,'ObjectProperty',0,0),'AddAmount':(0,'IntProperty',0,0)} if owner==0x43000 else oldprops(owner)
        return self

    def spawn(self):
        state=self.prop('PickLockState','ByteProperty',instance=True)
        return self.load(b'\x0f'+state[1:]+state+b'\x24\x06'+b'\x45'+self.name('SetGameSpawned')+b'\x27\x16'+RET)

    def shot_ammo(self):
        self.native_names={0x900000:'Subtract_IntInt'}
        self.u.objname=lambda address:self.native_names.get(address,'?')
        raw=b''
        for suffix in ('','_1'):
            result=self.prop('CallFunc_Subtract_IntInt_ReturnValue'+suffix,'IntProperty')
            count=self.prop('CallFunc_BPIPW_GetCurrentAmmoInfo_AmmoCount'+suffix,'IntProperty')
            raw+=b'\x0f'+result[1:]+result+b'\x68'+P(0x900000)+count+b'\x1d'+I(1)+b'\x16'
        return self.load(raw+RET)

    def radial(self):
        self.native_names={0x900000:'EqualEqual_IntInt',0x910000:'BooleanAND'}
        self.u.objname=lambda address:self.native_names.get(address,'?')
        enabled=self.prop('InputEnable',instance=True)
        active=self.prop('IsMiniGameActivated',instance=True)
        solver=self.prop('SolverPos','IntProperty',instance=True)
        rings=[self.prop('Ring%d_Index'%i,'IntProperty',instance=True) for i in (1,2,3)]
        equals=[self.prop('CallFunc_EqualEqual_IntInt_ReturnValue'+suffix) for suffix in ('','_1','_2')]
        ands=[self.prop('CallFunc_BooleanAND_ReturnValue'+suffix) for suffix in ('','_1')]
        def math(result,fn,left,right):return b'\x14'+result+b'\x68'+P(fn)+left+right+b'\x16'
        guard=b'\x4f'+enabled+b'\x4f'+active+b'\x06'+I(25)
        raw=guard+RET
        self.comparison_start=len(raw)
        raw+=math(equals[0],0x900000,rings[0],solver)+math(equals[1],0x900000,rings[1],solver)
        raw+=math(ands[0],0x910000,equals[0],equals[1])+math(equals[2],0x900000,rings[2],solver)
        raw+=math(ands[1],0x910000,ands[0],equals[2])
        raw+=b'\x07'+I(25)+ands[1]

        raw+=b'\x14'+self.prop('success_animation',instance=True)+b'\x27'+RET
        return self.load(raw)
