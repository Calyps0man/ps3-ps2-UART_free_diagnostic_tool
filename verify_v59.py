"""Instruction-level checks, not a PS3 hardware emulator.
Models emitted EE instructions, SPR, ROM, CPU GIF transfers and a shortened
test span. Injects channel-specific read errors. Never claims hardware timing.
"""
from pathlib import Path
import json, struct, sys
from PIL import Image, ImageDraw

ROOT=Path(__file__).parent
layout=json.loads((ROOT/'layout.json').read_text());L=layout['labels']
rom=(ROOT/'Calyps0_V59_Stable_EE_Tests.elf').read_bytes()[0x1b06e8:0x3b06e8]
MASK=(1<<64)-1
def sx(x,n):x&=(1<<n)-1;return x-(1<<n) if x>>(n-1) else x

class CPU:
    def __init__(self,code,error,bad=0,spr_bad=False,fpu_bad=False,ee_bad=False,cache_bad=False,dma_bad=False):
        self.r=[0]*32;self.r[2]=error&MASK;self.pc=L[code];self.pending=None
        self.mem={};self.mmio={};self.lo=0;self.hi=0;self.f=[0]*32;self.status=0;self.bad=bad;self.spr_bad=spr_bad;self.fpu_bad=fpu_bad;self.ee_bad=ee_bad;self.cache_bad=cache_bad;self.dma_bad=dma_bad;self.accesses=0
        self.ram_log=[];self.gif=[];self.text=[];self.steps=0;self.shorten=True
    def read(self,address,n):
        address&=0xffffffff
        if 0xb2000000<=address<=0xb2001100:return int(self.mmio.get(address,0)).to_bytes(n,'little')
        if address==0xb2001000:return (3).to_bytes(n,'little')
        if address==0xb2001080:return (0x13579bdf).to_bytes(n,'little')
        if address==0xb2000000:return (0x11223344).to_bytes(n,'little')
        if 0x10000000<=address<0x13000000:return int(self.mmio.get(address,0)).to_bytes(n,'little')
        if 0x9fc00000<=address<0x9fe00000:return rom[address-0x9fc00000:address-0x9fc00000+n]
        if 0x70000000<=address<0x70004000:
            value=bytearray(self.mem.get(address+j,0) for j in range(n))
            if self.spr_bad and address==0x70000400:value[0]^=1
            return bytes(value)
        if 0x80000000<=address<0x82000000 or 0xa0000000<=address<0xa2000000:
            self.accesses+=1;self.ram_log.append(('read',address));v=bytearray(self.mem.get(address+j,0) for j in range(n))
            base=address&0x1fffffff;v=bytearray(self.mem.get(base+j,0) for j in range(n))
            if self.bad & (2 if address&16 else 1):v[0]^=1
            if self.cache_bad and address==0x81ffff00:v[0]^=1
            return bytes(v)
        raise AssertionError(('unexpected read',hex(address),n))
    def write(self,address,value,n):
        address&=0xffffffff
        if address==0xb0006000:
            assert n==16;self.gif.append(value);reg=value>>64;val=value&MASK
            if reg==0x60:self.mmio[0xb2001000]=self.mmio.get(0xb2001000,0)|1;self.mmio[0xb2001080]=val
            if reg==0x61:self.mmio[0xb2001000]=self.mmio.get(0xb2001000,0)|2
            if (value&0xffffffff)==0x11223344:self.mmio[0xb2000000]=0x11223344
            return
        if 0xb2000000<=address<=0xb2001100:
            self.mmio[address]=value
            return
        if 0x10000000<=address<0x13000000:
            self.mmio[address]=value&0xffffffff
            if address==0x10004000:self.mmio[0x11004000]=0x1234
            if address==0x10005000:self.mmio[0x1100c000]=0x1234
            if address==0x1000d000 and value&0x100:
                if not self.dma_bad:
                    madr=self.mmio[0x1000d010]&0x1fffffff;sadr=self.mmio[0x1000d080]&0x3fff;qwc=self.mmio[0x1000d020]
                    for j in range(qwc*16):self.mem[madr+j]=self.mem.get(0x70000000+sadr+j,0)
                self.mmio[address]=0
            if address==0x1000d400 and value&0x100:
                if not self.dma_bad:
                    madr=self.mmio[0x1000d410]&0x1fffffff;sadr=self.mmio[0x1000d480]&0x3fff;qwc=self.mmio[0x1000d420]
                    for j in range(qwc*16):self.mem[0x70000000+sadr+j]=self.mem.get(madr+j,0)
                    self.mmio[address]=0
            return
        assert 0x70000000<=address<0x70004000 or 0x80000000<=address<0x82000000 or 0xa0000000<=address<0xa2000000,hex(address)
        if 0x80000000<=address<0x82000000 or address>=0xa0000000:self.accesses+=1;self.ram_log.append(('write',address))
        base=(address&0x1fffffff) if address>=0x80000000 else address
        for j in range(n):self.mem[base+j]=(value>>(8*j))&255
    def string(self,ptr):
        data=bytearray()
        while True:
            b=self.read(ptr,1)[0]
            if not b:return data.decode()
            data.append(b);ptr+=1
    def step(self):
        if self.pc==L['text']:self.text.append((self.string(self.r[4]),self.r[5],self.r[6]))
        if self.pc in (L['write'],L['read']) and self.shorten:
            self.r[12]=0xffffffffa0000100+(self.r[30]<<4)
            self.r[14]=self.r[12]
        w=int.from_bytes(self.read(self.pc,4),'little');op=w>>26;rs=(w>>21)&31;rt=(w>>16)&31;rd=(w>>11)&31;sh=(w>>6)&31;fn=w&63;imm=sx(w&65535,16)
        old_pending=self.pending;self.pending=None;nextpc=self.pc+4
        R=self.r
        def put(reg,v):
            if reg:R[reg]=v&MASK
        if op==0:
            if w==0 or fn==15 or fn==12:pass
            elif fn==0:put(rd,sx((R[rt]&0xffffffff)<<sh,32))
            elif fn==4:put(rd,sx((R[rt]&0xffffffff)<<(R[rs]&31),32))
            elif fn==0x26:
                value=R[rs]^R[rt]
                if self.ee_bad and L['ee_test']<=self.pc<L['ee_fail']:value^=1
                put(rd,value)
            elif fn==2:put(rd,sx((R[rt]&0xffffffff)>>sh,32))
            elif fn==0x24:put(rd,R[rs]&R[rt])
            elif fn==6:put(rd,sx((R[rt]&0xffffffff)>>(R[rs]&31),32))
            elif fn==8:self.pending=R[rs]&0xffffffff
            elif fn==16:put(rd,self.hi)
            elif fn==18:put(rd,self.lo)
            elif fn==31:raise AssertionError('DDIVU is not an EE instruction')
            elif fn==25:
                value=(R[rs]&0xffffffff)*(R[rt]&0xffffffff)
                self.lo=sx(value&0xffffffff,32)&MASK;self.hi=sx(value>>32,32)&MASK
            elif fn==27:self.lo=sx((R[rs]&0xffffffff)//(R[rt]&0xffffffff),32)&MASK;self.hi=sx((R[rs]&0xffffffff)%(R[rt]&0xffffffff),32)&MASK
            elif fn==0x21:put(rd,sx(R[rs]+R[rt],32))
            elif fn==0x25:put(rd,R[rs]|R[rt])
            elif fn==0x2b:put(rd,int(R[rs]<R[rt]))
            elif fn==0x3a:put(rd,R[rt]>>sh)
            elif fn==0x2a:put(rd,int(sx(R[rs],64)<sx(R[rt],64)))
            elif fn==0x2d:put(rd,R[rs]+R[rt])
            elif fn==0x2f:put(rd,R[rs]-R[rt])
            elif fn==0x38:put(rd,R[rt]<<sh)
            else:raise AssertionError(('fn',hex(self.pc),hex(w)))
        elif op in (2,3):
            if op==3:put(31,self.pc+8)
            self.pending=((self.pc+4)&0xf0000000)|((w&0x3ffffff)<<2)
        elif op==0x10:
            coprs=(w>>21)&31
            if coprs==0:put(rt,self.status)
            elif coprs==4:self.status=R[rt]
            else:raise AssertionError(('cop0',hex(self.pc),hex(w)))
        elif op==0x11:
            coprs=(w>>21)&31;fs=(w>>11)&31;fd=(w>>6)&31
            if coprs==0:put(rt,sx(self.f[fs],32))
            elif coprs==4:self.f[fs]=R[rt]&0xffffffff
            elif coprs==16 and fn==0:
                import struct
                x=struct.unpack('<f',struct.pack('<I',self.f[fs]))[0]
                y=struct.unpack('<f',struct.pack('<I',self.f[rt]))[0]
                self.f[fd]=struct.unpack('<I',struct.pack('<f',x+y))[0]
                if self.fpu_bad:self.f[fd]^=1
            else:raise AssertionError(('cop1',hex(self.pc),hex(w)))
        elif op in (1,4,5):
            yes=((sx(R[rs],64)>=0) if rt==1 else (sx(R[rs],64)<0)) if op==1 else (R[rs]==R[rt] if op==4 else R[rs]!=R[rt])
            if yes:self.pending=self.pc+4+imm*4
        elif op==9:put(rt,sx(R[rs]+imm,32))
        elif op==10:put(rt,int(sx(R[rs],64)<imm))
        elif op==12:put(rt,R[rs]&(w&65535))
        elif op==13:put(rt,R[rs]|(w&65535))
        elif op==15:put(rt,sx((w&65535)<<16,32))
        elif op in (0x23,0x24,0x37,0x1e):
            n={0x23:4,0x24:1,0x37:8,0x1e:16}[op];v=int.from_bytes(self.read(R[rs]+imm,n),'little')
            if op==0x1e:R[rt]=v
            else:put(rt,sx(v,32) if op==0x23 else v)
        elif op in (0x28,0x2b,0x3f,0x1f):self.write(R[rs]+imm,R[rt],{0x28:1,0x2b:4,0x3f:8,0x1f:16}[op])
        elif op==0x2f:pass # CACHE operation; alias model above represents visibility.
        else:raise AssertionError(('op',hex(self.pc),hex(w)))
        self.pc=old_pending if old_pending is not None else nextpc;self.steps+=1
    def run(self):
        while self.pc!=L['hold']:
            self.step();assert self.steps<16000000,(hex(self.pc),self.r[10],self.mmio.get(0xb2001000))
        return self
    def image(self,path):
        im=Image.new('RGB',(640,448),(16,16,16));d=ImageDraw.Draw(im);colour=(255,255,255);start=None;i=0
        while i<len(self.gif):
            tag=self.gif[i];loops=tag&0x7fff;flg=(tag>>58)&3;nreg=(tag>>60)&15;i+=1
            if flg==2:
                i+=loops
                continue
            assert nreg==1
            for _ in range(loops):
                value=self.gif[i]&MASK;reg=self.gif[i]>>64;i+=1
                if reg==1:colour=(value&255,(value>>8)&255,(value>>16)&255)
                if reg==5:
                    point=((value&65535)//16,((value>>16)&65535)//16)
                    if start is None:start=point
                    else:d.rectangle((start[0],start[1],max(start[0],point[0]-1),point[1]-1),fill=colour);start=None
        im.save(path)

# Direct self-test sanity and injected failure checks.
for label,flags,expected in [('ee_test',{},1),('spr_test',{},1),('spr_test',{'spr_bad':True},2),('cache_test',{},1),('cache_test',{'cache_bad':True},2),('dma_test',{},1)]:
    probe=CPU('real',0,**flags);probe.pc=L[label];probe.r[31]=L['hold'];probe.run()
    assert probe.r[2]==expected,(label,flags,probe.r[2])

results=[]
for mode,error,bad in [('preflight',33554432,0),*[('real', -n, 0) for n in range(1,15)],('real',-9,1),('real',-9,2),('real',-9,3),('real',-123456789,0),('real',-2147483648,0),('real',33554432,0)]:
    c=CPU(mode,error,bad).run();statuses=[s for s,x,y in c.text if x==248][-2:]
    expected=['NOT TESTED']*2 if mode=='preflight' or error>=0 else [('FAIL' if bad&1 else 'OK'),('FAIL' if bad&2 else 'OK')]
    assert statuses==expected,(statuses,expected)
    assert c.r[17]==(128 if bad&1 and mode!='preflight' and error<0 else 0)
    assert c.r[18]==(128 if bad&2 and mode!='preflight' and error<0 else 0)
    rates=[s for s,x,y in c.text if x==320][-2:]
    assert rates==(['--','--'] if mode=='preflight' or error>=0 else ['0.00%','0.00%'])
    if mode!='preflight':
        a_status=[s for s,x,y in c.text if x==248 and y==168]
        b_status=[s for s,x,y in c.text if x==248 and y==216]
        if error>=0:
            expected_a=['NOT TESTED','NOT TESTED'];expected_b=['NOT TESTED','NOT TESTED']
        else:
            expected_a=['NOT TESTED','TESTING',expected[0],expected[0],expected[0],expected[0]]
            expected_b=['NOT TESTED','NOT TESTED','NOT TESTED','TESTING',expected[1],expected[1]]
        assert a_status==expected_a,(mode,error,bad,a_status,expected_a)
        assert b_status==expected_b,(mode,error,bad,b_status,expected_b)
    assert str(error) in [s for s,x,y in c.text]
    if mode=='preflight':assert c.accesses==0
    elif error<0:
        assert c.accesses>=512
        assert all(not address&16 for kind,address in c.ram_log[:256])
        assert all(address&16 for kind,address in c.ram_log[256:512])
        assert {address for kind,address in c.ram_log if kind=='write' and 0xa0000000<=address<0xa0000100}==set(range(0xa0000000,0xa0000100,4))
        assert {address for kind,address in c.ram_log if kind=='read' and 0xa0000000<=address<0xa0000100}==set(range(0xa0000000,0xa0000100,4))
    right=[s for s,x,y in c.text if x==450]
    if mode=='preflight':
        assert all(name in right for name in ['EE:N/T','SPR:N/T','FPU:N/T','CACHE:N/T'])
    elif error<0:
        assert all(name in right for name in ['EE:N/T','SPR:N/T','FPU:N/T','CACHE:N/T'])
    else:
        assert all(name in right for name in ['EE:OK','SPR:OK','FPU:OK','CACHE:OK','GS:VIS'])
    if error==-9:c.image(ROOT/'init_minus9_test_complete.png')
    if error==33554432 and mode=='real' and bad==0:c.image(ROOT/'diagnostic_pass.png')
    results.append({'mode':mode,'return':error,'injected_channel_mask':bad,'display':statuses,'ram_accesses_short_span':c.accesses,'steps':c.steps})
# Stop mid-test to check that an incomplete sweep never becomes OK.
partial=CPU('real',-9,3)
while partial.accesses<100:partial.step()
assert partial.r[19]==0
assert [s for s,x,y in partial.text if x==248][-2:]==['TESTING','NOT TESTED']
results.append({'case':'interrupted mid-test','return':-9,'display':['TESTING','NOT TESTED'],'completed':False})
(ROOT/'verification.json').write_text(json.dumps({'scope':'Emitted instruction semantics and GIF packets; NOT PS3 hardware/timing validation. Test span shortened to 256 bytes in model only; shipped binary remains 32 MiB.','cases':results},indent=2))
print(json.dumps(results,indent=2))
