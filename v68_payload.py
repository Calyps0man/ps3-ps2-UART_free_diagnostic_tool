"""Generate the Calyps0 V68 NoPS2 diagnostic from its verified base image.

The original InitRDRAM implementation and settings remain unchanged. Its real
signed return is preserved and displayed. A non-negative return skips the
destructive RDRAM sweep and runs the component probes; a negative return runs
the independent Channel A/B sweep and leaves component probes N/T. The final
screen is held for inspection.
"""
from pathlib import Path
import sys, struct, hashlib, json

ROM_FILE = 0x1b06e8
CAVE = 0x160000
PC = 0x9fc00000 + CAVE
BASE_SHA = '397fa65fd2e1226cda642e08ef64b16dc2f367040021c9b5db455f590bb2ead7'

# Boot ROM addresses in RESET (file "RESET", rom offset 0).
HOOK = 0x89c            # bltz v0, 9fc0094c -- replaced by j real

# EE scratchpad save area. It is outside the packet and SPR-test windows.
GPR_SAVE = 0x70003800

# Persistent result block, above everything the diagnostic scribbles on and
# above the GPR save window.
#   +0  EE     +4  SPR    +8  FPU     +12 CACHE
#   +16 DMA    +20 VU0    +24 VU1     +28 GS CORE
#   +32 reserved       +36 PS2 BRIDGE      +40 reserved
#   +44 currently running test/status
# State values: 0 N/T, 1 OK, 2 FAIL, 3 TIMEOUT, 4 BAD RESPONSE, 5 FAIL MEM,
# 7 FAIL DATA.
RESULTS = 0x70003a00

# Final two-column result panel: label, result offset, label x, value x, y.
COMPONENTS = [
    ('lbl_ee',0,32,96,248), ('lbl_vu0',20,320,402,248),
    ('lbl_spr',4,32,114,272), ('lbl_vu1',24,320,402,272),
    ('lbl_fpu',8,32,114,296), ('lbl_gscore',28,320,456,296),
    ('lbl_cache',12,32,150,320), ('lbl_bridge',36,320,456,320),
    ('lbl_dma',16,32,114,344),
]

# ---------------------------------------------------------------------------
# Component test configuration.
# ---------------------------------------------------------------------------
# Iterations before a bounded wait gives up and reports TIMEOUT.
TIMEOUT = 0x200000

# DMA test: scratchpad source and destination, and the RDRAM staging address.
# MADR is a physical address; the top of the array is used because the sweep
# has already been there and the clear before handoff wipes it again.
DMA_SRC = 0x70000800
DMA_DST = 0x70000900
DMA_MADR = 0x01fff800

# VU test: bytes of micro and data memory walked per unit. VU0 has 4 KiB of
# each and VU1 16 KiB, so 1 KiB is inside both.
VU_WINDOW = 0x400

# Bridge probes: the EE side of the SIF register block, and a word near the
# top of the IOP RAM window the EE sees at physical 1c000000.
IOP_PROBE = 0xbc1fff00

class Asm:
    def __init__(self): self.words=[]; self.labels={}; self.fix=[]
    def label(self,n): self.labels[n]=PC+len(self.words)*4
    def word(self,w): self.words.append(w & 0xffffffff)
    def i(self,op,rt,rs,imm): self.word(op<<26|rs<<21|rt<<16|(imm&65535))
    def r(self,fn,rd,rs=0,rt=0,sh=0): self.word(rs<<21|rt<<16|rd<<11|sh<<6|fn)
    def li(self,rt,value): self.i(15,rt,0,value>>16); self.i(13,rt,rt,value)
    def move(self,rd,rs): self.r(0x2d,rd,rs,0)
    def load(self,rt,rs,off=0): self.i(0x23,rt,rs,off)
    def store(self,rt,rs,off=0): self.i(0x2b,rt,rs,off)
    def branch(self,op,rs,rt,label):
        self.fix.append((len(self.words),'branch',label));self.i(op,rt,rs,0);self.word(0)
    def jump(self,label,link=False):
        self.fix.append((len(self.words),'jump',label));self.word((3 if link else 2)<<26);self.word(0)
    def jabs(self,target,link=False):
        assert (target>>28)==((PC+len(self.words)*4+4)>>28),hex(target)
        self.word(((3 if link else 2)<<26)|((target>>2)&0x3ffffff));self.word(0)
    def addr(self,rt,label):
        self.fix.append((len(self.words),'addr',label));self.li(rt,0)
    def ret(self): self.r(8,0,31);self.word(0)
    def finish(self):
        for index,kind,label in self.fix:
            dest=self.labels[label]
            if kind=='branch':
                d=(dest-(PC+index*4+4))//4;assert -32768<=d<=32767
                self.words[index]|=d&65535
            elif kind=='jump':
                assert (dest>>28)==((PC+index*4+4)>>28)
                self.words[index]|=(dest>>2)&0x3ffffff
            else:
                self.words[index]|=dest>>16;self.words[index+1]|=dest&65535
        return b''.join(struct.pack('<I',w) for w in self.words)

# Register use: s0=raw result, s1=A bad, s2=B bad, s3=completed,
# s4=sweep stage (display only); s6=scratch packet, s7=uncached GIF FIFO.
a=Asm()

def save_gprs():
    # k0 is the base. The boot ROM reloads k0 at 9fc008a4, so k0 itself is
    # dead here and is deliberately not part of the snapshot.
    a.li(26,GPR_SAVE)
    for reg in range(1,32):
        if reg!=26: a.i(0x3f,reg,26,reg*8)

def restore_gprs():
    a.li(26,GPR_SAVE)
    for reg in range(1,32):
        if reg!=26: a.i(0x37,reg,26,reg*8)

def const64(rt,v):
    a.i(13,rt,0,(v>>48)&65535)
    for shift in [32,16,0]: a.r(0x38,rt,0,rt,16);a.i(13,rt,rt,(v>>shift)&65535)

a.label('real');save_gprs();a.move(16,2);a.move(20,0);a.jump('entry')
a.label('entry')
a.li(29,0x70003f00);a.li(22,0x70000100);a.li(23,0xb0006000)
a.move(17,0);a.move(18,0);a.move(19,0);a.i(9,30,0,-1)
# Clear the whole result block, including the current-test status word.
a.li(8,RESULTS)
for off in range(0,48,4):a.store(0,8,off)
def priv(offset,value):
    a.li(8,0xb2000000+offset);const64(9,value);a.i(0x3f,9,8,0)
a.jump('gs_init',True)
# Clear 640x448 with a black sprite; then restore white drawing colour.
a.addr(4,'clear');a.i(9,5,0,5);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.addr(4,'visual_bars');a.i(9,5,0,11);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.jump('screen',True)
a.branch(5,20,0,'hold')
# A non-negative InitRDRAM result is the BIOS success condition. Skip the
# destructive sweep and proceed directly to the component tests.
a.branch(1,16,1,'eegs_tests')
a.li(8,RESULTS);a.i(9,9,0,1);a.store(9,8,44)
a.move(30,0);a.jump('screen',True)
a.label('channel');a.move(21,0)
a.label('pattern')
a.addr(8,'patterns');a.r(0,9,0,21,2);a.r(0x21,8,8,9);a.load(10,8)
a.r(0,9,0,30,4)
a.li(11,0xa0000000);a.r(0x21,11,11,9)
a.li(12,0xa2000000);a.r(0x21,12,12,9)
a.li(14,0xa0400000);a.r(0x21,14,14,9)
a.move(20,0);a.jump('progress',True)
a.label('write')
for offset in [0,4,8,12]:a.store(10,11,offset)
a.i(9,11,11,32);a.branch(5,11,14,'write')
a.word(0x0000000f);a.jump('progress',True)
a.li(8,0x00400000);a.r(0x21,14,14,8);a.branch(5,11,12,'write')
a.r(0,9,0,30,4);a.li(11,0xa0000000);a.r(0x21,11,11,9)
a.li(14,0xa0400000);a.r(0x21,14,14,9)
a.i(9,20,0,1);a.jump('progress',True)
a.label('read');a.move(15,0)
for offset in [0,4,8,12]:
    a.load(13,11,offset);a.r(0x26,13,13,10);a.r(0x2b,13,0,13);a.r(0x2d,15,15,13)
a.branch(4,15,0,'next');a.branch(5,30,0,'bad_b')
a.r(0x2d,17,17,15);a.jump('next')
a.label('bad_b');a.r(0x2d,18,18,15)
a.label('next');a.i(9,11,11,32);a.branch(5,11,14,'read')
a.jump('progress',True);a.li(8,0x00400000);a.r(0x21,14,14,8);a.branch(5,11,12,'read')
a.i(9,21,21,1);a.i(10,8,21,4);a.branch(5,8,0,'pattern')

# ------------------------------------------- pass 5: address uniqueness -----
# The four fixed patterns write the same value everywhere, so a stuck or open
# address line reads back the pattern it expects and passes. This pass writes
# each word's own address into that word, which makes every location unique and
# turns any aliasing into a data mismatch. Failures are counted into the same
# per-channel totals, so a wiring fault contributes to NG RATE like any other.
a.r(0,9,0,30,4)
a.li(11,0xa0000000);a.r(0x21,11,11,9)
a.li(12,0xa2000000);a.r(0x21,12,12,9)
a.li(14,0xa0400000);a.r(0x21,14,14,9)
a.move(20,0);a.jump('progress',True)
a.label('addr_write')
a.store(11,11,0)
for offset in [4,8,12]:
    a.i(9,3,11,offset);a.store(3,11,offset)
a.i(9,11,11,32);a.branch(5,11,14,'addr_write')
a.word(0x0000000f);a.jump('progress',True)
a.li(8,0x00400000);a.r(0x21,14,14,8);a.branch(5,11,12,'addr_write')
a.r(0,9,0,30,4);a.li(11,0xa0000000);a.r(0x21,11,11,9)
a.li(14,0xa0400000);a.r(0x21,14,14,9)
a.i(9,20,0,1);a.jump('progress',True)
a.label('addr_read');a.move(15,0)
for offset in [0,4,8,12]:
    a.i(9,3,11,offset)
    a.load(13,11,offset);a.r(0x26,13,13,3);a.r(0x2b,13,0,13);a.r(0x2d,15,15,13)
a.branch(4,15,0,'addr_next');a.branch(5,30,0,'addr_bad_b')
a.r(0x2d,17,17,15);a.jump('addr_next')
a.label('addr_bad_b');a.r(0x2d,18,18,15)
a.label('addr_next');a.i(9,11,11,32);a.branch(5,11,14,'addr_read')
a.jump('progress',True);a.li(8,0x00400000);a.r(0x21,14,14,8);a.branch(5,11,12,'addr_read')

a.i(9,8,0,1);a.r(4,8,30,8);a.r(0x25,19,19,8)
a.jump('screen',True)
# After Channel A, proceed to B. This path was entered only for a negative
# InitRDRAM return, so finish after Channel B with component tests left N/T.
a.branch(4,30,0,'start_b')
a.jump('tests_done')
a.label('eegs_tests')
# Every component test runs only on the successful initialization path. Redraw
# after each completed test so a later fatal hardware access still leaves a
# useful last-completed status on screen.
a.li(8,RESULTS);a.i(9,9,0,2);a.store(9,8,44);a.jump('screen',True);a.jump('ee_test',True);a.li(8,RESULTS);a.store(2,8,0)
a.li(8,RESULTS);a.i(9,9,0,3);a.store(9,8,44);a.jump('screen',True);a.jump('spr_test',True);a.li(8,RESULTS);a.store(2,8,4)
a.li(8,RESULTS);a.i(9,9,0,4);a.store(9,8,44);a.jump('screen',True);a.jump('fpu_test',True);a.li(8,RESULTS);a.store(2,8,8)
a.li(8,RESULTS);a.i(9,9,0,5);a.store(9,8,44);a.jump('screen',True);a.jump('cache_test',True);a.li(8,RESULTS);a.store(2,8,12)
a.li(8,RESULTS);a.i(9,9,0,6);a.store(9,8,44);a.jump('screen',True);a.jump('dma_test',True);a.li(8,RESULTS);a.store(2,8,16)
a.li(8,RESULTS);a.i(9,9,0,7);a.store(9,8,44);a.jump('screen',True);a.i(9,4,0,0);a.jump('vu_test',True);a.li(8,RESULTS);a.store(2,8,20)
a.li(8,RESULTS);a.i(9,9,0,8);a.store(9,8,44);a.jump('screen',True);a.i(9,4,0,1);a.jump('vu_test',True);a.li(8,RESULTS);a.store(2,8,24)
a.li(8,RESULTS);a.i(9,9,0,9);a.store(9,8,44);a.jump('screen',True);a.jump('gs_core_test',True);a.li(8,RESULTS);a.store(2,8,28)
a.li(8,RESULTS);a.i(9,9,0,11);a.store(9,8,44);a.jump('screen',True)
a.jump('bridge_test',True);a.li(8,RESULTS);a.store(2,8,36)
a.label('tests_done')
a.li(8,RESULTS);a.i(9,9,0,12);a.store(9,8,44)
a.jump('screen',True)
a.jump('hold')
a.label('hold');a.jump('hold')
a.label('start_b');a.i(9,30,0,1);a.jump('screen',True);a.jump('channel')

# ------------------------------------------------------------- GS bring-up --
# Fixed NTSC interlaced timing from the supplied ROMGSCRT. This takes control
# of the display without using ROMGSCRT's entry stubs or stack fields.
a.label('gs_init')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
priv(0,0)
priv(0x10,0x0000000740834504)
priv(0x40,0x0007f5b61f06f040)
priv(0x50,0x0033a4d8)
priv(0x60,0x00c7800601a01801)
priv(0x20,1);priv(0x30,8)
priv(0x10,0x0000000740814504)
priv(0x90,10<<9)
priv(0xa0,(447<<44)|(2559<<32)|(3<<23)|(50<<12)|636)
priv(0x70,10<<9)
priv(0x80,(447<<44)|(2559<<32)|(3<<23)|(50<<12)|636)
priv(0xe0,0)
# Enable read circuit 2, fixed alpha.
priv(0,0x8006)
a.addr(4,'gs_setup');a.i(9,5,0,9);a.jump('send_rom',True)
a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# ------------------------------------------------- continuation panel -------
# state(a0 = RESULTS byte offset, a1 = x, a2 = y): draw one component's state word as
# text in the value column. 0 N/T, 1 OK, 2 FAIL, 3 TIMEOUT, anything else
# BAD RESP -- the same five words the second hook will report with.
a.label('state')
a.i(9,29,29,-32);a.i(0x3f,31,29,0);a.i(0x3f,5,29,8);a.i(0x3f,6,29,16)
a.li(8,RESULTS);a.r(0x21,8,8,4);a.load(9,8)
a.addr(4,'st_nt');a.branch(4,9,0,'state_draw')
for value,blob in ((1,'st_ok'),(2,'st_fail'),(3,'st_timeout'),(4,'st_badresp'),
                   (5,'st_failmem'),(7,'st_faildata')):
    a.i(9,10,0,value);a.addr(4,blob);a.branch(4,9,10,'state_draw')
a.addr(4,'st_fail')
a.label('state_draw')
a.i(0x37,5,29,8);a.i(0x37,6,29,16);a.jump('text',True)
a.i(0x37,31,29,0);a.i(9,29,29,32);a.ret()

# panel(): all nine enabled component states in two columns, read from RESULTS.
a.label('panel')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
for blob_name,offset,label_x,value_x,y in COMPONENTS:
    a.addr(4,blob_name);a.i(9,5,0,label_x);a.i(9,6,0,y);a.jump('text',True)
    a.i(9,4,0,offset);a.i(9,5,0,value_x);a.i(9,6,0,y);a.jump('state',True)
a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# Deterministic EE integer execution test. This is not an exhaustive CPU test.
a.label('ee_test')
a.li(8,0x12345678);a.li(9,0x0fedcba9)
a.r(0x26,10,8,9);a.li(11,0x1dd99dd1);a.branch(5,10,11,'ee_fail')
a.r(0x21,10,8,9);a.li(11,0x22222221);a.branch(5,10,11,'ee_fail')
a.r(0,10,0,8,4);a.li(11,0x23456780);a.branch(5,10,11,'ee_fail')
a.r(0x19,0,8,9);a.r(0x12,10);a.li(11,0x9a363d38);a.branch(5,10,11,'ee_fail')
a.r(0x10,10);a.li(11,0x0121fa00);a.branch(5,10,11,'ee_fail')
a.li(9,0x1234);a.r(0x1b,0,8,9);a.r(0x12,10);a.li(11,0x10004);a.branch(5,10,11,'ee_fail')
a.r(0x10,10);a.li(11,0xda8);a.branch(5,10,11,'ee_fail')
a.i(9,2,0,1);a.ret()
a.label('ee_fail');a.i(9,2,0,2);a.ret()

# Test unused SPR window 70000400..70002fff with all four patterns.
a.label('spr_test');a.move(15,0)
a.label('spr_pattern');a.addr(8,'patterns');a.r(0,9,0,15,2);a.r(0x21,8,8,9);a.load(10,8)
a.li(11,0x70000400);a.li(12,0x70003000)
a.label('spr_write');a.store(10,11);a.i(9,11,11,4);a.branch(5,11,12,'spr_write')
a.li(11,0x70000400)
a.label('spr_read');a.load(13,11);a.branch(5,13,10,'spr_fail');a.i(9,11,11,4);a.branch(5,11,12,'spr_read')
a.i(9,15,15,1);a.i(10,8,15,4);a.branch(5,8,0,'spr_pattern')
a.i(9,2,0,1);a.ret()
a.label('spr_fail');a.i(9,2,0,2);a.ret()

# Enable CU1 temporarily and verify 1.5f + 2.25f = 3.75f exactly.
a.label('fpu_test')
a.word(0x400f6000) # mfc0 t7, Status
a.i(15,24,0,0x2000);a.r(0x25,24,24,15);a.word(0x40986000) # mtc0 t8, Status
a.word(0x000000c0);a.word(0);a.word(0) # EHB + settling NOPs
a.li(8,0x3fc00000);a.word(0x44880000) # mtc1 t0, f0
a.li(9,0x40100000);a.word(0x44890800) # mtc1 t1, f1
a.word(0x46010080) # add.s f2, f0, f1
a.word(0x44081000) # mfc1 t0, f2
a.word(0x408f6000);a.word(0x000000c0) # restore Status + EHB
a.li(9,0x40700000);a.branch(5,8,9,'fpu_fail')
a.i(9,2,0,1);a.ret()
a.label('fpu_fail');a.i(9,2,0,2);a.ret()

# Cached/uncached coherency, D-cache writeback and invalidation. This uses a
# reserved end-of-RDRAM test word and therefore only runs after RDRAM passes.
# The final operation is a hit invalidate, so no dirty line is left behind for
# the continuation path to write back over cleared memory.
a.label('cache_test')
a.li(8,0xa1ffff00);a.li(9,0x81ffff00)
a.li(10,0x11223344);a.store(10,8);a.word(0x0000000f)
a.i(0x2f,0x1a,9,0);a.word(0x0000000f) # EE DHIN: Hit invalidate D
a.load(11,9);a.branch(5,11,10,'cache_fail')
a.li(10,0x55667788);a.store(10,9);a.word(0x0000000f)
a.i(0x2f,0x18,9,0);a.word(0x0000000f) # EE DHWBIN: Hit writeback+invalidate D
a.load(11,8);a.branch(5,11,10,'cache_fail')
a.li(10,0xa5a55a5a);a.store(10,8);a.word(0x0000000f)
a.i(0x2f,0x1a,9,0);a.word(0x0000000f);a.load(11,9)
a.branch(5,11,10,'cache_fail')
a.i(0x2f,0x1a,9,0);a.word(0x0000000f)
a.i(9,2,0,1);a.ret()
a.label('cache_fail');a.i(0x2f,0x1a,9,0);a.word(0x0000000f);a.i(9,2,0,2);a.ret()

# ===========================================================================
# Component tests. Every one of these runs before the EE kernel exists, so
# every hardware register is reached through its unmapped uncached kseg1 alias
# (0xb000xxxx / 0xb100xxxx / 0xb200xxxx), never the 0x1000xxxx form. At this
# point in the boot the only TLB entry that exists is the scratchpad mapping
# written at 9fc0083c and Status has BEV set, so a kuseg access would take a
# TLB refill into the ROM handler at 9fc00680 and never come back. That, not
# a hardware fault, is why these routines failed when they were first tried.
#
# Each returns a state in v0: 1 OK, 2 FAIL, 3 TIMEOUT, 4 BAD RESPONSE,
# 5 FAIL MEM or 7 FAIL DATA.
# Only t0-t9, a0-a3, v0, v1 and at are touched; s0-s7 and fp carry the sweep
# results and must survive.
# ===========================================================================

# 64-byte SPR -> RDRAM -> SPR round trip through EE DMAC channels 8 and 9.
# Nothing else owns the DMAC at this point in the boot, so the controller is
# brought up here rather than shared. Every wait is bounded.
a.label('dma_test')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
a.li(8,DMA_SRC);a.li(9,DMA_DST);a.li(10,0x13579bdf);a.i(9,11,0,16)
a.label('dma_fill');a.store(10,8);a.store(0,9);a.i(9,10,10,0x1111)
a.i(9,8,8,4);a.i(9,9,9,4);a.i(9,11,11,-1);a.branch(5,11,0,'dma_fill')
# Stop channels 8/9, clear their stale completion state, release the global
# DMA hold and enable the controller without disturbing unrelated settings.
a.li(8,0xb000d000);a.store(0,8)
a.li(8,0xb000d400);a.store(0,8)
a.li(8,0xb000e010);a.li(9,0x300);a.store(9,8)
a.li(8,0xb000f520);a.load(9,8);a.li(10,0xfffeffff);a.r(0x24,9,9,10)
a.li(8,0xb000f590);a.store(9,8)
a.li(8,0xb000e000);a.load(9,8);a.i(13,9,9,1);a.store(9,8);a.word(0x0000000f)
# Channel 8 fromSPR: MADR=01fff800, QWC=4, SADR=0800, CHCR=101.
a.li(8,0xb000d000);a.li(9,DMA_MADR);a.store(9,8,0x10);a.i(9,9,0,4);a.store(9,8,0x20)
a.li(9,DMA_SRC & 0x3fff);a.store(9,8,0x80);a.li(9,0x101);a.store(9,8)
a.li(10,TIMEOUT)
a.label('dma_wait8');a.load(9,8);a.i(12,9,9,0x100);a.branch(4,9,0,'dma_start9')
a.i(9,10,10,-1);a.branch(5,10,0,'dma_wait8')
a.i(9,2,0,3);a.jump('dma_return')
# Channel 9 toSPR: MADR=01fff800, QWC=4, SADR=0900, CHCR=100.
a.label('dma_start9')
a.li(8,0xb000d400);a.li(9,DMA_MADR);a.store(9,8,0x10);a.i(9,9,0,4);a.store(9,8,0x20)
a.li(9,DMA_DST & 0x3fff);a.store(9,8,0x80);a.li(9,0x100);a.store(9,8)
a.li(10,TIMEOUT)
a.label('dma_wait9');a.load(9,8);a.i(12,9,9,0x100);a.branch(4,9,0,'dma_compare')
a.i(9,10,10,-1);a.branch(5,10,0,'dma_wait9')
a.i(9,2,0,3);a.jump('dma_return')
a.label('dma_compare');a.li(8,DMA_SRC);a.li(9,DMA_DST);a.i(9,10,0,16)
a.label('dma_cmp');a.load(11,8);a.load(12,9);a.branch(5,11,12,'dma_data_fail')
a.i(9,8,8,4);a.i(9,9,9,4);a.i(9,10,10,-1);a.branch(5,10,0,'dma_cmp')
a.i(9,2,0,1);a.jump('dma_return')
a.label('dma_data_fail');a.i(9,2,0,7)
a.label('dma_return');a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# VU test. a0 = 0 selects VU0/VIF0, a0 = 1 selects VU1/VIF1.
# Phase 1 walks two patterns through the first 1 KiB of micro memory and the
# first 1 KiB of data memory. That proves the unit is powered, clocked and
# reachable over the EE bus, which is the question a dead console actually
# poses.
a.label('vu_test')
a.i(9,29,29,-32);a.i(0x3f,31,29,0);a.i(0x3f,4,29,8)
a.branch(5,4,0,'vu1_addr')
a.li(8,0xb1000000);a.li(9,0xb1004000);a.li(10,0xb0003810);a.li(11,0xb0004000)
a.jump('vu_addr_done')
a.label('vu1_addr')
a.li(8,0xb1008000);a.li(9,0xb100c000);a.li(10,0xb0003c10);a.li(11,0xb0005000)
a.label('vu_addr_done')
for base_reg in (8, 9):
    for pattern in (0xa5a55a5a, 0x5a5aa5a5):
        a.li(12,pattern);a.move(13,0)
        a.label(f'vu_w{base_reg}_{pattern:x}')
        a.r(0x21,14,base_reg,13);a.store(12,14)
        a.i(9,13,13,4);a.i(10,15,13,VU_WINDOW);a.branch(5,15,0,f'vu_w{base_reg}_{pattern:x}')
        a.word(0x0000000f);a.move(13,0)
        a.label(f'vu_r{base_reg}_{pattern:x}')
        a.r(0x21,14,base_reg,13);a.load(15,14);a.branch(5,15,12,'vu_mem_fail')
        a.i(9,13,13,4);a.i(10,15,13,VU_WINDOW);a.branch(5,15,0,f'vu_r{base_reg}_{pattern:x}')
# Leave both windows zeroed so nothing downstream inherits a test pattern.
for base_reg in (8, 9):
    a.move(13,0)
    a.label(f'vu_z{base_reg}')
    a.r(0x21,14,base_reg,13);a.store(0,14)
    a.i(9,13,13,4);a.i(10,15,13,VU_WINDOW);a.branch(5,15,0,f'vu_z{base_reg}')
a.word(0x0000000f)
a.i(9,2,0,1);a.jump('vu_return')
a.label('vu_mem_fail');a.i(9,2,0,5)
a.label('vu_return');a.i(0x37,31,29,0);a.i(9,29,29,32);a.ret()

# GS core. Deterministic and side-effect free: CSR carries the GS ID and
# revision in bits 16..31, and SIGLBLID is one of the few GS registers that
# reads back what was written. A GS that is unpowered or unclocked answers
# with all zeros or all ones on both. The GIF-to-GS path is independently
# evidenced by the working text display.
a.label('gs_core_test')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
a.li(8,0xb2001000);a.load(9,8)
a.r(2,10,0,9,16);a.i(12,10,10,0xffff)
a.branch(4,10,0,'gs_core_noresp')
a.li(11,0xffff);a.branch(4,10,11,'gs_core_noresp')
a.li(8,0xb2001080);a.i(0x37,12,8,0)
for pattern in (0x13579bdf, 0x2468ace0):
    a.li(9,pattern);a.i(0x3f,9,8,0);a.word(0x0000000f)
    a.i(0x37,10,8,0);a.branch(5,10,9,'gs_core_restore_fail')
a.i(0x3f,12,8,0);a.word(0x0000000f)
a.i(9,2,0,1);a.jump('gs_core_return')
a.label('gs_core_restore_fail');a.i(0x3f,12,8,0);a.word(0x0000000f)
a.label('gs_core_fail');a.i(9,2,0,2);a.jump('gs_core_return')
a.label('gs_core_noresp');a.i(9,2,0,4)
a.label('gs_core_return');a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# PS2 bridge data-path probe. IOP RAM is visible to the EE at physical
# 1c000000; the boot ROM's own exception handler uses the same window. A
# save/write/read/restore transaction therefore checks that the EE-to-IOP RAM
# path responds. This is deliberately not called a complete SIF protocol test:
# the IOP has not been started yet, and MSCOM is a mailbox register rather than
# ordinary storage, so demanding RAM-like readback from it creates false FAILs.
a.label('bridge_test')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
a.li(8,IOP_PROBE);a.load(12,8);a.load(13,8,4)
for pattern in (0x13579bdf, 0xeca86420):
    a.li(9,pattern);a.store(9,8);a.store(9,8,4);a.word(0x0000000f)
    a.load(10,8);a.branch(5,10,9,'bridge_data_fail')
    a.load(10,8,4);a.branch(5,10,9,'bridge_data_fail')
a.store(12,8);a.store(13,8,4);a.word(0x0000000f)
a.i(9,2,0,1);a.jump('bridge_return')
a.label('bridge_data_fail');a.store(12,8);a.store(13,8,4);a.i(9,2,0,7)
a.jump('bridge_return')
a.label('bridge_return');a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

a.label('screen')
a.i(9,29,29,-16);a.i(0x3f,31,29,0)
def text(label,x,y):
    a.addr(4,label);a.i(9,5,0,x);a.i(9,6,0,y);a.jump('text',True)
text('title',32,32)
text('init',32,80)
# Convert actual result to signed decimal in SPR, without libc.
a.li(8,0x70000280);a.i(9,8,8,24);a.i(0x28,0,8,0)
a.move(9,16);a.r(0x2a,10,9,0);a.branch(4,10,0,'positive')
a.r(0x2f,9,0,9)
a.label('positive');a.i(9,11,0,10)
a.label('digit');a.r(0x1b,0,9,11);a.r(0x12,9);a.r(0x10,12)
a.i(9,12,12,48);a.i(9,8,8,-1);a.i(0x28,12,8,0);a.branch(5,9,0,'digit')
a.branch(4,10,0,'number');a.i(9,8,8,-1);a.i(9,12,0,45);a.i(0x28,12,8,0)
a.label('number');a.move(4,8);a.i(9,5,0,32);a.i(9,6,0,112);a.jump('text',True)
a.addr(4,'init_ok');a.branch(1,16,0,'init_failure');a.jump('init_status')
a.label('init_failure');a.addr(4,'init_fail')
a.label('init_status');a.i(9,5,0,280);a.i(9,6,0,112);a.jump('text',True)
text('a_label',32,144);text('b_label',32,192)
# Clear the complete dynamic channel area before drawing current results.
a.addr(4,'status_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
text('rate_a_label',32,168);text('rate_b_label',32,216)
# Keep only the GS/RGB visual evidence in the upper-right area. EE, SPR, FPU
# and CACHE are rendered once in the unified lower panel.
a.addr(4,'eegs_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.addr(4,'visual_bars');a.i(9,5,0,11);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
text('gs_visual',450,72)
# A successful initializer reports both channels good without a destructive
# sweep. During a failure-path sweep, keep both rows at TEST IN PROGRESS until
# both channels have completed, then replace them with measured OK/FAIL.
a.branch(1,16,1,'a_reported')
a.i(12,8,19,3);a.i(9,9,0,3);a.branch(5,8,9,'a_progress')
a.i(12,8,19,1);a.branch(5,8,0,'a_complete')
a.label('a_reported');a.addr(4,'reported_good');a.jump('a_draw')
a.label('a_progress');a.addr(4,'test_progress');a.jump('a_draw')
a.label('a_complete');a.addr(4,'ok');a.branch(4,17,0,'a_draw');a.addr(4,'fail')
a.label('a_draw');a.i(9,5,0,152);a.i(9,6,0,144);a.jump('text',True)
a.branch(1,16,1,'b_reported')
a.i(12,8,19,3);a.i(9,9,0,3);a.branch(5,8,9,'b_progress')
a.i(12,8,19,2);a.branch(5,8,0,'b_complete')
a.label('b_reported');a.addr(4,'reported_good');a.jump('b_draw')
a.label('b_progress');a.addr(4,'test_progress');a.jump('b_draw')
a.label('b_complete');a.addr(4,'ok');a.branch(4,18,0,'b_draw');a.addr(4,'fail')
a.label('b_draw');a.i(9,5,0,152);a.i(9,6,0,192);a.jump('text',True)

a.i(12,8,19,1);a.branch(4,8,0,'a_pending_rate')
a.move(4,17);a.i(9,6,0,168);a.jump('percent',True);a.jump('b_rate')
a.label('a_pending_rate');a.addr(4,'pending_rate');a.i(9,5,0,224);a.i(9,6,0,168);a.jump('text',True)
a.label('b_rate');a.i(12,8,19,2);a.branch(4,8,0,'b_pending_rate')
a.move(4,18);a.i(9,6,0,216);a.jump('percent',True);a.jump('rates_done')
a.label('b_pending_rate');a.addr(4,'pending_rate');a.i(9,5,0,224);a.i(9,6,0,216);a.jump('text',True)
a.label('rates_done')
a.addr(4,'panel_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.jump('panel',True)
# Remove the complete sweep indicator before drawing ordinary test status.
a.addr(4,'status_clear2');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
text('status_label',32,400)
a.li(8,RESULTS);a.load(9,8,44);a.addr(4,'status_starting');a.branch(4,9,0,'status_draw')
for value,label in ((1,'status_rdram'),(2,'status_ee'),(3,'status_spr'),
                    (4,'status_fpu'),(5,'status_cache'),(6,'status_dma'),
                    (7,'status_vu0'),(8,'status_vu1'),(9,'status_gscore'),
                    (11,'status_bridge'),
                    (12,'status_complete')):
    a.i(9,10,0,value);a.addr(4,label);a.branch(4,9,10,'status_draw')
a.addr(4,'status_starting')
a.label('status_draw');a.i(9,5,0,176);a.i(9,6,0,400);a.jump('text',True)
a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()


# Format round(bad * 10000 / 2**24) as 0.00%..100.00%.
# Multiply via 64-bit shifts/adds, divide by shift, then use EE DIVU32.
# Never uses unsupported DDIVU/DMULTU or overflowing 32-bit bad*10000.
a.label('percent');a.i(9,29,29,-16);a.i(0x3f,31,29,0);a.move(8,0)
for shift in [13,10,9,8,4]:a.r(0x38,9,0,4,shift);a.r(0x2d,8,8,9)
a.li(9,0x00800000);a.r(0x2d,8,8,9);a.r(0x3a,8,0,8,24);a.i(9,12,0,100);a.r(0x1b,0,8,12);a.r(0x12,9);a.r(0x10,10)
a.li(11,0x700002e0);a.i(0x28,0,11,0);a.i(9,11,11,-1);a.i(9,13,0,37);a.i(0x28,13,11,0)
a.i(9,12,0,10);a.r(0x1b,0,10,12);a.r(0x10,13);a.r(0x12,10)
a.i(9,11,11,-1);a.i(9,13,13,48);a.i(0x28,13,11,0)
a.i(9,11,11,-1);a.i(9,10,10,48);a.i(0x28,10,11,0)
a.i(9,11,11,-1);a.i(9,13,0,46);a.i(0x28,13,11,0)
a.label('percent_digit');a.r(0x1b,0,9,12);a.r(0x12,9);a.r(0x10,13)
a.i(9,11,11,-1);a.i(9,13,13,48);a.i(0x28,13,11,0);a.branch(5,9,0,'percent_digit')
a.move(4,11);a.i(9,5,0,224);a.jump('text',True)
a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# Progress routine preserves the worker's volatile registers and return PC.
a.label('progress');a.i(9,29,29,-80)
for reg in range(8,16):a.i(0x3f,reg,29,(reg-8)*8)
a.i(0x3f,31,29,64)
a.addr(4,'progress_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
# Stage label: 0 WRITE, 1 READ, anything else CLEAR. The whole sweep indicator
# occupies the bottom status row rather than the component panel.
a.addr(4,'write_label');a.branch(4,20,0,'stage_draw')
a.i(9,8,0,1);a.addr(4,'read_label');a.branch(4,20,8,'stage_draw');a.addr(4,'clear_label')
a.label('stage_draw');a.i(9,5,0,32);a.i(9,6,0,400);a.jump('text',True)
a.addr(4,'pass_label');a.i(9,5,0,150);a.i(9,6,0,400);a.jump('text',True)
a.li(4,0x700002c0);a.i(9,8,21,49);a.i(0x28,8,4,0);a.i(0x28,0,4,1)
a.i(9,5,0,258);a.i(9,6,0,400);a.jump('text',True)
# Draw completed MiB as a bar, 8 screen pixels per MiB (256 px total).
a.li(15,0x70000300);const64(8,0x1000000000008002);a.i(0x3f,8,15,0)
a.i(9,8,0,14);a.i(0x3f,8,15,8)
const64(8,(300*16)|((404*16)<<16));a.i(0x3f,8,15,16)
a.i(9,8,0,5);a.i(0x3f,8,15,24);a.i(0x3f,8,15,40)
a.i(0x37,8,29,24);a.r(2,8,0,8,20);a.i(12,8,8,63)
# a0000000 >>20 has low6 zero; a2000000 has low6 32.
a.r(0,8,0,8,3);a.i(9,8,8,300);a.r(0,8,0,8,4)
a.li(9,(420*16)<<16);a.r(0x25,8,8,9);a.i(0x3f,8,15,32)
a.move(4,15);a.i(9,5,0,3);a.jump('send_rom',True)
for reg in range(8,16):a.i(0x37,reg,29,(reg-8)*8)
a.i(0x37,31,29,64);a.i(9,29,29,80);a.ret()

# CPU GIF writes. LQ/SQ are EE-specific; no DMA, malloc or RDRAM buffers.
a.label('send_rom');a.li(15,0xb0006000)
a.label('send_loop');a.i(0x1e,24,4,0);a.i(0x1f,24,15,0)
a.i(9,4,4,16);a.i(9,5,5,-1);a.branch(5,5,0,'send_loop');a.ret()

# Render compact 8x12 glyphs at 2x scale using SPR-backed A+D sprite packets.
a.label('text');a.i(9,29,29,-128)
for reg in [16,17,18,19,20,21,22,23,31]: a.i(0x3f,reg,29,(reg-16)*8 if reg!=31 else 120)
a.move(16,4);a.move(17,5);a.move(18,6);a.li(22,0x70000100);a.li(23,0xb0006000)
# Packet header: NLOOP=2, EOP=1, NREG=1, A+D.
const64(8,0x1000000000008002);a.i(0x3f,8,22,0);a.i(9,8,0,14);a.i(0x3f,8,22,8)
a.i(9,8,0,5);a.i(0x3f,8,22,24);a.i(0x3f,8,22,40)
a.label('char');a.i(0x24,8,16,0);a.branch(4,8,0,'text_done')
a.addr(21,'font');a.r(0,8,0,8,4);a.r(0x21,21,21,8);a.move(19,0)
a.label('row');a.r(0x21,8,21,19);a.i(0x24,15,8,0);a.move(20,0)
a.label('column');a.r(6,8,20,15);a.i(12,8,8,1);a.branch(4,8,0,'skip_pixel')
a.r(0,8,0,20,1);a.r(0x21,8,17,8);a.r(0,8,0,8,4)
a.r(0,9,0,19,1);a.r(0x21,9,18,9);a.r(0,9,0,9,4);a.r(0,9,0,9,16)
a.r(0x25,8,8,9);a.i(0x3f,8,22,16)
a.li(9,0x00200020);a.r(0x21,8,8,9);a.i(0x3f,8,22,32)
for off in [0,16,32]:a.i(0x1e,24,22,off);a.i(0x1f,24,23,0)
a.label('skip_pixel');a.i(9,20,20,1);a.i(10,8,20,8);a.branch(5,8,0,'column')
a.i(9,19,19,1);a.i(10,8,19,12);a.branch(5,8,0,'row')
a.i(9,16,16,1);a.i(9,17,17,18);a.jump('char')
a.label('text_done')
for reg in [16,17,18,19,20,21,22,23,31]:a.i(0x37,reg,29,(reg-16)*8 if reg!=31 else 120)
a.i(9,29,29,128);a.ret()

while len(a.words)%4:a.word(0)
def blob(label,data):
    a.label(label)
    data+=bytes((-len(data))%4)
    for i in range(0,len(data),4):a.word(struct.unpack_from('<I',data,i)[0])
def ad_packet(items):
    return struct.pack('<QQ',len(items)|0x8000|1<<60,14)+b''.join(struct.pack('<QQ',v,r) for r,v in items)
def xy(x,y):return (x*16)|((y*16)<<16)
blob('gs_setup',ad_packet([(0x4c,10<<16),(0x18,0),(0x40,639<<16|447<<48),(0x1a,1),(0x47,0),(0,6),(1,0x80ffffff),(0x46,0)]))
blob('clear',ad_packet([(1,0x80000000),(5,xy(0,0)),(5,xy(640,448)),(1,0x80ffffff)]))
blob('progress_clear',ad_packet([(1,0x80000000),(5,xy(32,392)),(5,xy(639,432)),(1,0x80ffffff),(0x61,0)]))
blob('white',ad_packet([(1,0x80ffffff)]))
blob('visual_bars',ad_packet([(1,0x800000ff),(5,xy(500,96)),(5,xy(536,108)),(1,0x8000ff00),(5,xy(540,96)),(5,xy(576,108)),(1,0x80ff0000),(5,xy(580,96)),(5,xy(616,108)),(1,0x80ffffff)]))
blob('status_clear',ad_packet([(1,0x80000000),
    (5,xy(144,136)),(5,xy(448,168)),
    (5,xy(216,160)),(5,xy(360,192)),
    (5,xy(144,184)),(5,xy(448,216)),
    (5,xy(216,208)),(5,xy(360,240)),(0x61,0)]))
blob('status_clear2',ad_packet([(1,0x80000000),(5,xy(32,392)),(5,xy(639,432)),(1,0x80ffffff),(0x61,0)]))
# The panel replaces the sweep progress area and the RANGE line beneath it.
blob('panel_clear',ad_packet([(1,0x80000000),(5,xy(32,240)),(5,xy(639,399)),(1,0x80ffffff),(0x61,0)]))
blob('eegs_clear',ad_packet([(1,0x80000000),(5,xy(446,48)),(5,xy(639,252)),(1,0x80ffffff),(0x61,0)]))
for label,string in [
    ('title','NOPS2 TEST BY CALYPS0'), ('init','RDRAM INIT RETURN:'),
    ('a_label','CH. A:'), ('b_label','CH. B:'),
    ('rate_a_label','NG RATE A:'), ('rate_b_label','NG RATE B:'),
    ('ok','OK'), ('fail','FAIL'), ('reported_good','REPORTED GOOD'),
    ('test_progress','TEST IN PROGRESS'), ('pending_rate','--'),
    ('write_label','WRITE'), ('read_label','READ'), ('clear_label','CLEAR'),
    ('pass_label','PASS'), ('init_ok','RDRAM OK'), ('init_fail','RDRAM FAIL'),
    ('gs_visual','GS:VIS'),
    ('lbl_ee','EE:'), ('lbl_spr','SPR:'), ('lbl_fpu','FPU:'),
    ('lbl_cache','CACHE:'), ('lbl_dma','DMA:'), ('lbl_vu0','VU0:'),
    ('lbl_vu1','VU1:'), ('lbl_gscore','GSCORE:'), ('lbl_bridge','BRIDGE:'),
    ('status_label','STATUS:'), ('status_starting','STARTING'),
    ('status_rdram','RUNNING RDRAM'), ('status_ee','RUNNING EE'),
    ('status_spr','RUNNING SPR'), ('status_fpu','RUNNING FPU'),
    ('status_cache','RUNNING CACHE'), ('status_dma','RUNNING DMA'),
    ('status_vu0','RUNNING VU0'), ('status_vu1','RUNNING VU1'),
    ('status_gscore','RUNNING GSCORE'), ('status_bridge','RUNNING BRIDGE'),
    ('status_complete','TESTS COMPLETE'),
    ('st_nt','N/T'), ('st_ok','OK'), ('st_fail','FAIL'),
    ('st_timeout','TIMEOUT'), ('st_badresp','BAD RESP'),
    ('st_failmem','FAIL MEM'), ('st_faildata','FAIL DATA')
]:
    blob(label,string.encode()+b'\0')
blob('patterns',struct.pack('<4I',0,0xffffffff,0xaaaaaaaa,0x55555555))
blob('font',Path(__file__).with_name('font.bin').read_bytes())
payload=a.finish()


def main():
    src=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
    original=src.read_bytes();assert hashlib.sha256(original).hexdigest()==BASE_SHA,'Wrong verified display base'
    assert not any(original[ROM_FILE+CAVE:ROM_FILE+CAVE+len(payload)])
    name='Calyps0_V68_NoPS2_Test'
    entry=a.labels['real']
    buf=bytearray(original);changes=[]
    def patch(romoff,expect,replacement,reason):
        off=ROM_FILE+romoff
        old=bytes(buf[off:off+len(replacement)])
        assert expect is None or old==expect,(hex(romoff),old.hex(),expect.hex())
        buf[off:off+len(replacement)]=replacement
        changes.append({'rom_offset':hex(romoff),'file_offset':hex(off),'old':old.hex(),'new':replacement.hex(),'reason':reason})
    # Save actual initializer error in s0 before printf, then restore v0.
    # a0 already contains 9fc40000 from the branch delay slot at 411c4.
    patch(0x411c8,struct.pack('<I',0x3c049fc4),struct.pack('<I',0x0040802d),'Preserve negative actual initializer return across printf')
    patch(0x411dc,struct.pack('<I',0x2402ffff),struct.pack('<I',0x0200102d),'Return actual error instead of generic -1')
    patch(HOOK,struct.pack('<I',0x0440002b),struct.pack('<I',0x08000000|((entry>>2)&0x3ffffff)),'Enter ROM diagnostic after original initialization returns; original delay NOP retained')
    patch(CAVE,bytes(len(payload)),payload,'ROM-resident code, font and packet data in verified zero padding')

    assert len(buf)==len(original)
    # Exact original core initialization, all settings/loops/error numbers retained.
    assert buf[ROM_FILE+0x41268:ROM_FILE+0x43c54]==original[ROM_FILE+0x41268:ROM_FILE+0x43c54]
    # The embedded TESTMODE image is left byte-for-byte unchanged by this stage.
    assert buf[0x2ED818:0x2ED818+0x16a38] == original[0x2ED818:0x2ED818+0x16a38]

    path=out/(name+'.elf');path.write_bytes(buf)
    manifest={'base_sha256':BASE_SHA,'output_sha256':hashlib.sha256(buf).hexdigest(),
              'entry':hex(entry),'payload_size':len(payload),'changes':changes,
              'notes':[
                'Calyps0 V68 NoPS2 diagnostic',
                'Original InitRDRAM implementation and settings remain unchanged',
                'Actual signed InitRDRAM return is preserved and displayed',
                'Negative InitRDRAM returns run only the sequential Channel A/B RDRAM diagnostic',
                'Non-negative InitRDRAM returns skip the 32 MiB sweep and display both channels as REPORTED GOOD',
                'Negative InitRDRAM returns run the sweep and display TEST IN PROGRESS until both measured results are ready',
                'EE, SPR, FPU, cache, DMA, VU0, VU1, GS core and bridge data-path probes execute only after successful RDRAM initialization',
                'All nine enabled component rows are rendered once on the primary held result screen',
                'A persistent STATUS row names the currently running test and changes to TESTS COMPLETE at the end',
                'Channel rows and the single component panel use compact spacing; STATUS is at the bottom',
                'VU memory success is displayed as OK',
                'The bridge result is an EE-to-IOP RAM save/write/read/restore data-path probe, not a complete SIF protocol test',
                'The embedded TESTMODE image is untouched and no continuation hooks are installed',
                'The diagnostic always holds on its result screen; it does not load a game',
                'Initialization hang handling is not implemented']}
    (out/(name+'_manifest.json')).write_text(json.dumps(manifest,indent=2))
    (out/'layout.json').write_text(json.dumps({'labels':a.labels,'payload_size':len(payload)},indent=2))
    print(json.dumps({'payload_bytes':len(payload),'output':path.name,'sha256':manifest['output_sha256']},indent=2))

if __name__=='__main__':main()
