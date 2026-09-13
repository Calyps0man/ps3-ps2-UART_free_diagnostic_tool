"""Generate the Calyps0 V59 diagnostic payload on a verified V47 image.

The original InitRDRAM implementation is not modified. Requires Pillow for the
compact raster font. Run with a SHA-matched V47 ELF and an output directory.
"""
from pathlib import Path
import sys, struct, hashlib, json
from PIL import Image, ImageDraw, ImageFont

ROM_FILE = 0x1b06e8
CAVE = 0x160000
PC = 0x9fc00000 + CAVE
BASE_SHA = '397fa65fd2e1226cda642e08ef64b16dc2f367040021c9b5db455f590bb2ead7'

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
# s4=preflight (display only); s6=scratch packet, s7=uncached GIF FIFO.
a=Asm()
a.label('real');a.move(16,2);a.move(20,0);a.jump('entry')
a.label('preflight');a.move(16,2);a.i(9,20,0,1);a.jump('entry')
a.label('entry')
a.li(29,0x70003f00);a.li(22,0x70000100);a.li(23,0xb0006000)
a.move(17,0);a.move(18,0);a.move(19,0);a.i(9,30,0,-1)
# Independent self-test states in SPR: 0=N/T, 1=OK, 2=FAIL.
# EE, SPR, FPU, CACHE, VU0, VU1 occupy 0..20.
a.li(8,0x70000380)
for off in range(0,24,4):a.store(0,8,off)
# Direct fixed NTSC interlaced GS timing constants from supplied ROMGSCRT.
# Do not call its patched entry stubs or use its uninitialised stack fields.
def const64(rt,v):
    a.i(13,rt,0,(v>>48)&65535)
    for shift in [32,16,0]: a.r(0x38,rt,0,rt,16);a.i(13,rt,rt,(v>>shift)&65535)
def priv(offset,value):
    a.li(8,0xb2000000+offset);const64(9,value);a.i(0x3f,9,8,0)
priv(0,0)
priv(0x10,0x0000000740834504)
priv(0x40,0x0007f5b61f06f040)
priv(0x50,0x0033a4d8)
priv(0x60,0x00c7800601a01801)
priv(0x20,1);priv(0x30,8)
priv(0x10,0x0000000740814504)
priv(0x90,10<<9)
priv(0xa0,(447<<44)|(2559<<32)|(3<<23)|(50<<12)|636)
priv(0xe0,0x101010)
# Enable read circuit 2, fixed alpha; black framebuffer is cleared below.
priv(0,0x8006)
a.addr(4,'gs_setup');a.i(9,5,0,9);a.jump('send_rom',True)
# Clear 640x448 with a black sprite; then restore white drawing colour.
a.addr(4,'clear');a.i(9,5,0,5);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.addr(4,'visual_bars');a.i(9,5,0,11);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.jump('screen',True)
a.branch(5,20,0,'hold')
# Successful InitRDRAM takes the fast path and skips the long 32 MiB sweep.
# Negative returns continue into the diagnostic A/B sweep.
a.branch(1,16,1,'eegs_tests')
# Run every pattern for Channel A before starting Channel B.
# Each channel owns four words per 32-byte interleaved group.
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
a.i(9,8,0,1);a.r(4,8,30,8);a.r(0x25,19,19,8)
a.jump('screen',True)
# After Channel A, proceed to B. After B, always run ROM/SPR-resident EE, SPR
# and FPU checks. Cache and RDRAM-backed DMA require successful RDRAM.
a.branch(4,30,0,'start_b')
a.branch(1,16,0,'tests_done')
a.label('eegs_tests')
a.jump('ee_test',True);a.li(8,0x70000380);a.store(2,8,0)
a.jump('spr_test',True);a.li(8,0x70000380);a.store(2,8,4)
a.jump('fpu_test',True);a.li(8,0x70000380);a.store(2,8,8)
a.branch(5,17,0,'tests_done');a.branch(5,18,0,'tests_done')
a.jump('cache_test',True);a.li(8,0x70000380);a.store(2,8,12)
a.label('tests_done')
a.jump('screen',True);a.jump('hold')
a.label('start_b');a.i(9,30,0,1);a.jump('screen',True);a.jump('channel')
a.label('hold');a.jump('hold')

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
a.branch(5,11,10,'cache_fail');a.i(9,2,0,1);a.ret()
a.label('cache_fail');a.i(9,2,0,2);a.ret()

# GS core: clear CSR SIGNAL, send a known SIGNAL ID through GIF, then require
# both CSR completion and the matching SIGLBLID value.
a.label('gs_core_test');a.i(9,29,29,-16);a.i(0x3f,31,29,0);a.li(8,0xb2001000);const64(9,1);a.i(0x3f,9,8,0)
a.addr(4,'gs_signal');a.i(9,5,0,2);a.jump('send_rom',True);a.li(10,0x200000)
a.label('gs_sig_wait');a.i(0x37,9,8,0);a.i(12,9,9,1);a.branch(5,9,0,'gs_sig_check');a.i(9,10,10,-1);a.branch(5,10,0,'gs_sig_wait');a.jump('gs_core_fail')
a.label('gs_sig_check');a.li(8,0xb2001080);a.load(9,8);a.li(10,0x13579bdf);a.branch(5,9,10,'gs_core_fail');a.i(9,2,0,1);a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()
a.label('gs_core_fail');a.i(9,2,0,2);a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# GS VRAM: upload one 128-bit PSMCT32 pattern at DBP 0x3000, configure a
# local-to-host transfer, reverse BUSDIR, and compare the HWREG readback.
a.label('gs_vram_test');a.i(9,29,29,-16);a.i(0x3f,31,29,0);a.addr(4,'gs_upload');a.i(9,5,0,7);a.jump('send_rom',True)
a.li(8,0xb2001000);const64(9,2);a.i(0x3f,9,8,0)
a.addr(4,'gs_download');a.i(9,5,0,6);a.jump('send_rom',True);a.li(10,0x200000)
a.label('gs_finish_wait');a.i(0x37,9,8,0);a.i(12,9,9,2);a.branch(5,9,0,'gs_readback');a.i(9,10,10,-1);a.branch(5,10,0,'gs_finish_wait');a.jump('gs_vram_fail')
a.label('gs_readback');a.li(8,0xb2001040);const64(9,1);a.i(0x3f,9,8,0);a.li(8,0xb2000000);a.i(0x37,9,8,0);a.li(8,0xb2001040);a.i(0x3f,0,8,0)
a.li(10,0x11223344);a.branch(5,9,10,'gs_vram_fail');a.i(9,2,0,1);a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()
a.label('gs_vram_fail');a.li(8,0xb2001040);a.i(0x3f,0,8,0);a.i(9,2,0,2);a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# Execute the same four-instruction microprogram on VU0 or VU1 through its
# corresponding VIF. The program stores 0x1234 in local VU data word zero.
# a0=0 selects VU0/VIF0; a0=1 selects VU1/VIF1.
a.label('vu_test');a.i(9,29,29,-16);a.i(0x3f,31,29,0);a.store(4,29,8)
a.branch(5,4,0,'vu1_addr');a.li(8,0x11000000);a.li(9,0x11004000);a.li(10,0x10003810);a.li(11,0x10004000);a.jump('vu_addr_done')
a.label('vu1_addr');a.li(8,0x11008000);a.li(9,0x1100c000);a.li(10,0x10003c10);a.li(11,0x10005000)
a.label('vu_addr_done')
for off,val in enumerate([0x000002ff10410234,0x000002ff0b010000,0x400002ff8000033c,0x000002ff8000033c]):
    const64(12,val);a.i(0x3f,12,8,off*8)
a.store(0,9);a.i(9,12,0,1);a.store(12,10);a.store(0,10);a.word(0x0000000f)
# VIF command quadword: FLUSHE, MSCAL 0, FLUSHE, NOP.
a.li(13,0x700003c0);const64(12,0x1400000010000000);a.i(0x3f,12,13,0);const64(12,0x0000000010000000);a.i(0x3f,12,13,8)
a.i(0x1e,24,13,0);a.i(0x1f,24,11,0);a.li(14,0x200000)
a.label('vu_wait');a.load(12,10,-0x10);a.i(12,12,12,3);a.branch(4,12,0,'vu_check');a.i(9,14,14,-1);a.branch(5,14,0,'vu_wait');a.jump('vu_fail')
a.label('vu_check');a.load(12,9);a.li(13,0x1234);a.branch(5,12,13,'vu_fail');a.i(9,2,0,1);a.jump('vu_return')
a.label('vu_fail');a.i(9,2,0,2)
a.label('vu_return');a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# 64-byte SPR -> RDRAM -> SPR round trip through EE DMAC channels 8 and 9.
# Every wait is bounded; timeout or a readback mismatch is FAIL.
a.label('dma_test')
a.li(8,0x70000394);a.store(0,8)
a.li(8,0x70000800);a.li(9,0x70000900);a.li(10,0x13579bdf);a.i(9,11,0,16)
a.label('dma_fill');a.store(10,8);a.store(0,9);a.i(9,10,10,0x1111);a.i(9,8,8,4);a.i(9,9,9,4);a.i(9,11,11,-1);a.branch(5,11,0,'dma_fill')
# Stop channels 8/9, clear their stale completion state, release the global
# DMA hold and enable the controller without disturbing unrelated settings.
a.li(8,0x1000d000);a.store(0,8);a.li(8,0x1000d400);a.store(0,8)
a.li(8,0x1000e010);a.li(9,0x300);a.store(9,8)
a.li(8,0x1000f520);a.load(9,8);a.li(10,0xfffeffff);a.r(0x24,9,9,10);a.li(8,0x1000f590);a.store(9,8)
a.li(8,0x1000e000);a.load(9,8);a.i(13,9,9,1);a.store(9,8);a.word(0x0000000f)
# Channel 8 fromSPR: MADR=01fff800, QWC=4, SADR=0800, CHCR=101.
a.li(8,0x1000d000);a.li(9,0x01fff800);a.store(9,8,0x10);a.i(9,9,0,4);a.store(9,8,0x20);a.li(9,0x800);a.store(9,8,0x80);a.li(9,0x101);a.store(9,8)
a.li(10,0x200000)
a.label('dma_wait8');a.load(9,8);a.i(12,9,9,0x100);a.branch(4,9,0,'dma_start9');a.i(9,10,10,-1);a.branch(5,10,0,'dma_wait8');a.li(8,0x70000394);a.i(9,9,0,1);a.store(9,8);a.jump('dma_fail')
# Channel 9 toSPR: MADR=01fff800, QWC=4, SADR=0900, CHCR=100.
a.label('dma_start9');a.li(8,0x1000d400);a.li(9,0x01fff800);a.store(9,8,0x10);a.i(9,9,0,4);a.store(9,8,0x20);a.li(9,0x900);a.store(9,8,0x80);a.li(9,0x100);a.store(9,8)
a.li(10,0x200000)
a.label('dma_wait9');a.load(9,8);a.i(12,9,9,0x100);a.branch(4,9,0,'dma_compare');a.i(9,10,10,-1);a.branch(5,10,0,'dma_wait9');a.li(8,0x70000394);a.i(9,9,0,2);a.store(9,8);a.jump('dma_fail')
a.label('dma_compare');a.li(8,0x70000800);a.li(9,0x70000900);a.i(9,10,0,16)
a.label('dma_cmp_loop');a.load(11,8);a.load(12,9);a.branch(5,11,12,'dma_data_fail');a.i(9,8,8,4);a.i(9,9,9,4);a.i(9,10,10,-1);a.branch(5,10,0,'dma_cmp_loop')
a.i(9,2,0,1);a.ret()
a.label('dma_data_fail');a.li(8,0x70000394);a.i(9,9,0,3);a.store(9,8)
a.label('dma_fail');a.i(9,2,0,2);a.ret()

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
text('a_label',32,168);text('b_label',32,216);text('range_label',32,376)
# Clear status rectangles before replacing NOT TESTED with final results.
a.addr(4,'status_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
text('rate_a_label',32,192);text('rate_b_label',32,240)
# Right-side EEGS summary. GS/RGB are visual evidence, not self-certified OK.
a.addr(4,'eegs_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.addr(4,'visual_bars');a.i(9,5,0,11);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.li(8,0x70000380);a.load(9,8,0);a.addr(4,'ee_nt');a.branch(4,9,0,'ee_draw')
a.i(9,10,0,1);a.addr(4,'ee_ok');a.branch(4,9,10,'ee_draw');a.addr(4,'ee_fail_text')
a.label('ee_draw');a.i(9,5,0,450);a.i(9,6,0,56);a.jump('text',True)
a.li(8,0x70000380);a.load(9,8,4);a.addr(4,'spr_nt');a.branch(4,9,0,'spr_draw')
a.i(9,10,0,1);a.addr(4,'spr_ok');a.branch(4,9,10,'spr_draw');a.addr(4,'spr_fail_text')
a.label('spr_draw');a.i(9,5,0,450);a.i(9,6,0,80);a.jump('text',True)
text('gs_visual',450,104)
a.li(8,0x70000380);a.load(9,8,8);a.addr(4,'fpu_nt');a.branch(4,9,0,'fpu_draw')
a.i(9,10,0,1);a.addr(4,'fpu_ok');a.branch(4,9,10,'fpu_draw');a.addr(4,'fpu_fail_text')
a.label('fpu_draw');a.i(9,5,0,450);a.i(9,6,0,152);a.jump('text',True)
a.li(8,0x70000380);a.load(9,8,12);a.addr(4,'cache_nt');a.branch(4,9,0,'cache_draw')
a.i(9,10,0,1);a.addr(4,'cache_ok');a.branch(4,9,10,'cache_draw');a.addr(4,'cache_fail_text')
a.label('cache_draw');a.i(9,5,0,450);a.i(9,6,0,176);a.jump('text',True)
# Completion bits: bit 0 A, bit 1 B. Current channel is fp (0 A, 1 B).
a.i(12,8,19,1);a.branch(5,8,0,'a_complete')
a.addr(4,'not_tested');a.branch(5,30,0,'a_draw');a.addr(4,'testing');a.jump('a_draw')
a.label('a_complete');a.addr(4,'ok');a.branch(4,17,0,'a_draw');a.addr(4,'fail')
a.label('a_draw');a.i(9,5,0,248);a.i(9,6,0,168);a.jump('text',True)
a.i(12,8,19,2);a.branch(5,8,0,'b_complete')
a.addr(4,'not_tested');a.i(9,8,0,1);a.branch(5,30,8,'b_draw');a.addr(4,'testing');a.jump('b_draw')
a.label('b_complete');a.addr(4,'ok');a.branch(4,18,0,'b_draw');a.addr(4,'fail')
a.label('b_draw');a.i(9,5,0,248);a.i(9,6,0,216);a.jump('text',True)

a.i(12,8,19,1);a.branch(4,8,0,'a_pending_rate')
a.move(4,17);a.i(9,6,0,192);a.jump('percent',True);a.jump('b_rate')
a.label('a_pending_rate');a.addr(4,'pending_rate');a.i(9,5,0,320);a.i(9,6,0,192);a.jump('text',True)
a.label('b_rate');a.i(12,8,19,2);a.branch(4,8,0,'b_pending_rate')
a.move(4,18);a.i(9,6,0,240);a.jump('percent',True);a.jump('rates_done')
a.label('b_pending_rate');a.addr(4,'pending_rate');a.i(9,5,0,320);a.i(9,6,0,240);a.jump('text',True)
a.label('rates_done')
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
a.move(4,11);a.i(9,5,0,320);a.jump('text',True)
a.i(0x37,31,29,0);a.i(9,29,29,16);a.ret()

# Progress routine preserves the worker's volatile registers and return PC.
a.label('progress');a.i(9,29,29,-80)
for reg in range(8,16):a.i(0x3f,reg,29,(reg-8)*8)
a.i(0x3f,31,29,64)
a.addr(4,'progress_clear');a.i(9,5,0,6);a.jump('send_rom',True)
a.addr(4,'white');a.i(9,5,0,2);a.jump('send_rom',True)
a.addr(4,'write_label');a.branch(4,20,0,'stage_draw');a.addr(4,'read_label')
a.label('stage_draw');a.i(9,5,0,32);a.i(9,6,0,272);a.jump('text',True)
a.addr(4,'pass_label');a.i(9,5,0,160);a.i(9,6,0,272);a.jump('text',True)
a.li(4,0x700002c0);a.i(9,8,21,49);a.i(0x28,8,4,0);a.i(0x28,0,4,1)
a.i(9,5,0,256);a.i(9,6,0,272);a.jump('text',True)
# Draw completed MiB as a bar, 16 screen pixels per MiB.
a.li(15,0x70000300);const64(8,0x1000000000008002);a.i(0x3f,8,15,0)
a.i(9,8,0,14);a.i(0x3f,8,15,8)
const64(8,(32*16)|((320*16)<<16));a.i(0x3f,8,15,16)
a.i(9,8,0,5);a.i(0x3f,8,15,24);a.i(0x3f,8,15,40)
a.i(0x37,8,29,24);a.r(2,8,0,8,20);a.i(12,8,8,63)
# a0000000 >>20 has low6 zero; a2000000 has low6 32.
a.r(0,8,0,8,4);a.i(9,8,8,32);a.r(0,8,0,8,4)
a.li(9,(330*16)<<16);a.r(0x25,8,8,9);a.i(0x3f,8,15,32)
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
blob('gs_signal',ad_packet([(0x60,0x13579bdf)]))
blob('gs_upload',ad_packet([(0x50,(0x3000<<32)|(1<<48)),(0x51,0),(0x52,1|(1<<32)),(0x53,0)])+struct.pack('<QQ',0x0800000000008001,0)+struct.pack('<4I',0x11223344,0x55667788,0x99aabbcc,0xddeeff00))
blob('gs_download',ad_packet([(0x50,0x3000|(1<<16)),(0x51,0),(0x52,1|(1<<32)),(0x61,0),(0x53,1)]))
blob('clear',ad_packet([(1,0x80000000),(5,xy(0,0)),(5,xy(640,448)),(1,0x80ffffff)]))
blob('progress_clear',ad_packet([(1,0x80000000),(5,xy(32,272)),(5,xy(560,350)),(1,0x80ffffff),(0x61,0)]))
blob('white',ad_packet([(1,0x80ffffff)]))
blob('visual_bars',ad_packet([(1,0x800000ff),(5,xy(500,128)),(5,xy(536,140)),(1,0x8000ff00),(5,xy(540,128)),(5,xy(576,140)),(1,0x80ff0000),(5,xy(580,128)),(5,xy(616,140)),(1,0x80ffffff)]))
blob('status_clear',ad_packet([(1,0x80000000),(5,xy(248,168)),(5,xy(440,264)),(1,0x80ffffff),(0x61,0)]))
blob('eegs_clear',ad_packet([(1,0x80000000),(5,xy(446,48)),(5,xy(639,252)),(1,0x80ffffff),(0x61,0)]))
for label,string in [('title','RDRAM TEST BY CALYPS0'),('init','RDRAM INIT RETURN:'),('a_label','CHANNEL A:'),('b_label','CHANNEL B:'),('ok','OK'),('fail','FAIL'),('not_tested','NOT TESTED'),('testing','TESTING'),('write_label','WRITE'),('read_label','READ'),('pass_label','PASS'),('init_ok','RDRAM OK'),('init_fail','RDRAM FAIL'),('range_label','RANGE: 32 MIB'),('rate_a_label','FAILURE RATE A:'),('rate_b_label','FAILURE RATE B:'),('pending_rate','--'),('ee_nt','EE:N/T'),('ee_ok','EE:OK'),('ee_fail_text','EE:FAIL'),('spr_nt','SPR:N/T'),('spr_ok','SPR:OK'),('spr_fail_text','SPR:FAIL'),('gs_visual','GS:VIS'),('fpu_nt','FPU:N/T'),('fpu_ok','FPU:OK'),('fpu_fail_text','FPU:FAIL'),('cache_nt','CACHE:N/T'),('cache_ok','CACHE:OK'),('cache_fail_text','CACHE:FAIL'),('gsc_nt','GSCORE:N/T'),('gsc_ok','GSCORE:OK'),('gsc_fail','GSCORE:FAIL'),('vram_nt','VRAM:N/T'),('vram_ok','VRAM:OK'),('vram_fail','VRAM:FAIL')]:blob(label,string.encode()+b'\0')
blob('patterns',struct.pack('<4I',0,0xffffffff,0xaaaaaaaa,0x55555555))
font=ImageFont.load_default();table=bytearray()
for c in range(128):
    im=Image.new('1',(8,12));ImageDraw.Draw(im).text((0,0),chr(c),font=font,fill=1)
    table.extend(sum((1<<x) for x in range(8) if im.getpixel((x,y))) for y in range(12));table.extend(bytes(4))
blob('font',bytes(table))
payload=a.finish()

def main():
    src=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
    original=src.read_bytes();assert hashlib.sha256(original).hexdigest()==BASE_SHA,'Wrong V47 base'
    assert not any(original[ROM_FILE+CAVE:ROM_FILE+CAVE+len(payload)])
    changes=[]
    def patch(buf,romoff,expect,replacement,reason):
        off=ROM_FILE+romoff
        old=bytes(buf[off:off+len(replacement)])
        assert old==expect,(hex(romoff),old.hex(),expect.hex())
        buf[off:off+len(replacement)]=replacement
        changes.append({'rom_offset':hex(romoff),'file_offset':hex(off),'old':old.hex(),'new':replacement.hex(),'reason':reason})
    outputs=[]
    for name,entry,hook in [('Calyps0_V59_Stable_EE_Tests',a.labels['real'],0x89c)]:
        buf=bytearray(original);changes=[]
        # Save actual initializer error in s0 before printf, then restore v0.
        # a0 already contains 9fc40000 from the branch delay slot at 411c4.
        patch(buf,0x411c8,struct.pack('<I',0x3c049fc4),struct.pack('<I',0x0040802d),'Preserve negative actual initializer return across printf')
        patch(buf,0x411dc,struct.pack('<I',0x2402ffff),struct.pack('<I',0x0200102d),'Return actual error instead of generic -1')
        patch(buf,hook,struct.pack('<I',0x0440002b),struct.pack('<I',0x08000000|((entry>>2)&0x3ffffff)),'Enter ROM diagnostic after original initialization returns; original delay NOP retained')
        patch(buf,CAVE,bytes(len(payload)),payload,'ROM-resident code, font and packet data in verified zero padding')
        assert len(buf)==len(original)
        # Exact original core initialization, all settings/loops/error numbers retained.
        assert buf[ROM_FILE+0x41268:ROM_FILE+0x43c54]==original[ROM_FILE+0x41268:ROM_FILE+0x43c54]
        # Existing TESTMODE file payload and its colour selection remain untouched.
        assert buf[0x2ed818:0x2ed818+0x16a38]==original[0x2ed818:0x2ed818+0x16a38]
        path=out/(name+'.elf');path.write_bytes(buf)
        manifest={'base_sha256':BASE_SHA,'output_sha256':hashlib.sha256(buf).hexdigest(),'entry':hex(entry),'payload_size':len(payload),'changes':changes,'notes':['Calyps0 V59','Original InitRDRAM implementation and settings remain unchanged','Actual signed InitRDRAM return is preserved and displayed','Negative InitRDRAM returns enter the sequential Channel A/B RDRAM diagnostic; EE-side tests remain N/T','Nonnegative InitRDRAM skips the long RDRAM sweep and runs EE, scratchpad, FPU, and cache checks','GS:VIS is visual evidence only, not a complete GS test','DMA, VU0/VU1, GS core, and GS VRAM verification are not executed','Diagnostic holds and does not load a game','Initialization hang handling is not implemented']}
        (out/(name+'_manifest.json')).write_text(json.dumps(manifest,indent=2))
        outputs.append({'file':path.name,'sha256':manifest['output_sha256']})
    (out/'layout.json').write_text(json.dumps({'labels':a.labels,'outputs':outputs},indent=2))
    print(json.dumps({'payload_bytes':len(payload),'outputs':outputs},indent=2))

if __name__=='__main__':main()
