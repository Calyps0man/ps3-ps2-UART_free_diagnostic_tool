# Calyps0 PS2 Hardware Diagnostic

This diagnostic runs inside the PS2 subsystem of a compatible PS3. It preserves
the real `InitRDRAM` return value and displays the result instead of replacing an
initialization error with a fake success.

## What the abbreviations mean

### EE — Emotion Engine

The **Emotion Engine** is the PS2's main processor. It executes the PS2 program
code and coordinates the other PS2 hardware.

The V59 EE check performs a small deterministic set of integer arithmetic,
bitwise, multiplication, division, and shift operations. The calculated values
are compared with known correct values.

- `EE: OK` means those tested CPU instructions returned the expected results.
- `EE: FAIL` means at least one result was wrong.
- This is a functional spot check, not a complete test of every EE instruction,
  register, interface, or operating condition.

### SPR — Scratchpad RAM

**SPR** means the EE's small, fast **scratchpad memory**. It is separate from the
32 MiB RDRAM and can be addressed directly by the processor.

The diagnostic writes four data patterns across the unused scratchpad test area
and reads them back:

- `00000000`
- `FFFFFFFF`
- `AAAAAAAA`
- `55555555`

`SPR: OK` means every tested location retained all four patterns correctly.
It does not test every possible access timing or interaction with other units.

### FPU — Floating-Point Unit

The **Floating-Point Unit** is part of the EE and performs calculations involving
fractional numbers.

The diagnostic enables the FPU, calculates `1.5 + 2.25`, and checks that the
exact expected single-precision result, `3.75`, is returned.

- `FPU: OK` confirms this calculation and its register transfer path worked.
- It is a basic functional check, not an exhaustive FPU accuracy test.

### CACHE — EE Data Cache

The **cache** is very fast memory inside the EE that temporarily holds data from
main memory. Software must keep cached and uncached views of memory consistent.

The diagnostic writes known values through cached and uncached RDRAM addresses,
uses EE data-cache writeback and invalidation operations, and verifies that the
expected values become visible through both views.

- `CACHE: OK` means the tested cache-coherency sequence behaved correctly.
- This is not a complete test of every cache line, tag, replacement condition,
  or instruction-cache function.

### GS — Graphics Synthesizer

The **Graphics Synthesizer** is the PS2 graphics processor. It generates the
video signal and has its own VRAM.

V59 displays `GS: VIS`. **VIS means visible.** This is deliberately not labelled
`GS: OK`, because V59 does not run a complete GS core or GS VRAM verification.
The readable screen and red/green/blue bars provide visible evidence that the
basic graphics path is working well enough to configure a display and draw the
diagnostic interface.

`GS: VIS` does **not** prove that every GS function or every VRAM location works.
The experimental GS core and VRAM readback checks from V58 were removed because
they reported false failures on a console with a working PS2 subsystem. At this
early interception point, the normal later graphics initialization has not
necessarily occurred.

## RDRAM behavior

`RDRAM INIT RETURN` is the actual value returned by the original `InitRDRAM`
routine. The initialization sequence and its settings are not changed.

- A zero or positive return displays `RDRAM OK`, skips the long 32 MiB sweep,
  and runs EE, SPR, FPU, and CACHE checks.
- A negative return displays `RDRAM FAIL` and attempts the Channel A and Channel
  B memory sweep. The other component checks remain `N/T` because they are not
  considered trustworthy without successfully initialized RDRAM.
- `N/T` means **not tested**. It never means OK.

`FAILURE RATE A` and `FAILURE RATE B` are calculated from actual RDRAM data
mismatches observed during the corresponding channel test.

## Understanding the result

An `OK` result means the specific operation performed by this program passed.
It does not certify the entire component under every workload. A failed result
is useful diagnostic evidence, but the surrounding initialization state and
test limitations must also be considered before declaring a chip defective.

V59 intentionally omits the unreliable DMA, VU0/VU1, GS core, and GS VRAM
checks rather than showing misleading failures.

## Source-code contents

- `build_v59.py` — constructs the ROM-resident diagnostic payload and applies
  the small hooks to a matching V47 ELF.
- `verify_v59.py` — instruction-level control-flow/display model with injected
  RDRAM channel failures. It is not a PS3 hardware emulator.
- `requirements.txt` — Python dependency used for the compact raster font and
  verification screenshots.

The builder contains some unreachable experimental routine definitions retained
for exact V59 binary reproducibility. The V59 execution path does not call the
DMA, VU0/VU1, GS core, or GS VRAM checks.

## Build

Python 3 is required.

```bash
python -m pip install -r requirements.txt
python build_v59.py /path/to/ps2_emu_testmode_v47_auto_rdram_colour.elf build
```

The input ELF must have this SHA-256 value:

```text
397fa65fd2e1226cda642e08ef64b16dc2f367040021c9b5db455f590bb2ead7
```

The normal output is:

```text
build/Calyps0_V59_Stable_EE_Tests.elf
```

Its expected SHA-256 value is:

```text
3d76f20447c909ee2878d755470e1b1d9eeeca522d83ce4258b8740631ec802c
```

To run the instruction-level checks, copy the generated ELF and `layout.json`
beside `verify_v59.py`, then run:

```bash
python verify_v59.py
```

## Binary and copyright notice

This repository intentionally does **not** include a PS2 BIOS, Sony `ps2_emu`
binary, the V47 base ELF, SCETool keys, or any other proprietary Sony file. The
user must legally provide the exact base ELF. The MIT license applies only to
the original source code and documentation in this package—not to Sony software
or to a patched ELF produced from it.
