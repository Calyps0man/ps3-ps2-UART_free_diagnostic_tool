"""Build V59 from original ps2_emu and PlayStation 2 TEST ROM0."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import struct
import subprocess
import sys

ORIGINAL_SHA = "7506392cad6b9c5829c087d9873c0a2b0c3a85b3f1f1bc8289e5939ffd305a7e"
TEST_ROM_SHA = "79c55576524ee8aae590d85d7581b1b725e6519c427071392e36b3b1f7662856"
TESTMODE_SHA = "4f9c0c33cdb859913cb105ee6dafbe39b605f76b70ca132fe98fec097079d86a"
BUILD_A_SHA = "89b2d0f9db07bb7751456da34d57142619491d568a11b85313f91d255ecde3fa"
V47_SHA = "397fa65fd2e1226cda642e08ef64b16dc2f367040021c9b5db455f590bb2ead7"
OUTPUT_SHA = "3d76f20447c909ee2878d755470e1b1d9eeeca522d83ce4258b8740631ec802c"

OSDSYS_OFFSET = 0x2EC818
OSDSYS_SLOT_SIZE = 0x3DEF8
OUTPUT_NAME = "Calyps0_V59_Stable_EE_Tests.elf"

# Build A -> V47 instruction replacements. Each tuple is
# (file offset, expected little-endian MIPS word, replacement word).
V47_CHANGES = [
    (0x2EDCB4, 0x0C04081C, 0x08040172),
    (0x2EDCB8, 0x00000000, 0x00000000),
    (0x2EDA60, 0x0040202D, 0x00000000),
    (0x2EDA64, 0x3082FFFF, 0x00000000),
    (0x2EDA68, 0x14400005, 0x00000000),
    (0x2EDA6C, 0x24030001, 0x00000000),
    (0x2EDA70, 0x3C02FFFF, 0x00000000),
    (0x2EDA74, 0x24030002, 0x00000000),
    (0x2EDA78, 0x00821024, 0x00000000),
    (0x2EDA7C, 0x0002180A, 0x00000000),
    (0x2EDA84, 0x0060102D, 0x00000000),
    (0x2EDDE8, 0x1040000C, 0x3048FFFF),
    (0x2EDDEC, 0x00000000, 0x00024C02),
    (0x2EDDF0, 0x3C040011, 0x1100000A),
    (0x2EDDF4, 0x0C0417E6, 0x00000000),
    (0x2EDDF8, 0x24844728, 0x11200005),
    (0x2EDDFC, 0x00000000, 0x00000000),
    (0x2EDE00, 0x00000000, 0x3C0400FF),
    (0x2EDE04, 0x00000000, 0x348400FF),
    (0x2EDE08, 0x00000000, 0x1000000A),
    (0x2EDE0C, 0x00000000, 0x00000000),
    (0x2EDE10, 0x00000000, 0x340400FF),
    (0x2EDE14, 0x1000FFFA, 0x10000007),
    (0x2EDE18, 0x00000000, 0x00000000),
    (0x2EDE1C, 0x0C04077A, 0x11200004),
    (0x2EDE20, 0x24040003, 0x00000000),
    (0x2EDE24, 0x0C04077A, 0x3C0400FF),
    (0x2EDE28, 0x0000202D, 0x10000002),
    (0x2EDE2C, 0x0C040074, 0x00000000),
    (0x2EDE30, 0x24040005, 0x3404FF00),
    (0x2EDE34, 0x0C04077A, 0x3C0A1200),
    (0x2EDE38, 0x24040002, 0xFD4400E0),
    (0x2EDE3C, 0x0C040828, 0x340B00A1),
    (0x2EDE40, 0x00000000, 0xFD4B0000),
    (0x2EDE44, 0x3C040011, 0x0000000F),
    (0x2EDE48, 0x0C0417E6, 0x1000FFFF),
    (0x2EDE4C, 0x24845720, 0x00000000),
]


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def require_hash(description: str, data: bytes, expected: str) -> None:
    actual = digest(data)
    if actual != expected:
        raise SystemExit(
            f"Wrong {description}.\nExpected SHA-256: {expected}\n"
            f"Actual SHA-256:   {actual}"
        )


def extract_rom_file(rom: bytes, wanted: str) -> bytes:
    romdir = rom.find(b"RESET\0\0\0\0\0")
    if romdir < 0:
        raise SystemExit("ROMDIR was not found in the PlayStation 2 TEST ROM")
    entries = []
    for index in range(256):
        record = rom[romdir + index * 16:romdir + (index + 1) * 16]
        if len(record) != 16:
            break
        name = record[:10].split(b"\0", 1)[0].decode("ascii", "strict")
        if not name:
            break
        _, size = struct.unpack_from("<HI", record, 10)
        entries.append((name, size))
    offset = 0
    for name, size in entries:
        if name == wanted:
            end = offset + size
            if end > len(rom):
                raise SystemExit(f"{wanted} extends beyond the supplied ROM")
            return rom[offset:end]
        offset = (offset + size + 15) & ~15
    raise SystemExit(f"{wanted} was not found in ROMDIR")


def make_build_a(original: bytes, testmode: bytes) -> bytes:
    if len(testmode) > OSDSYS_SLOT_SIZE:
        raise SystemExit("TESTMODE does not fit in the embedded OSDSYS slot")
    result = bytearray(original)
    result[OSDSYS_OFFSET:OSDSYS_OFFSET + OSDSYS_SLOT_SIZE] = bytes(OSDSYS_SLOT_SIZE)
    result[OSDSYS_OFFSET:OSDSYS_OFFSET + len(testmode)] = testmode
    built = bytes(result)
    require_hash("reconstructed Build A", built, BUILD_A_SHA)
    return built


def apply_v47(build_a: bytes) -> bytes:
    result = bytearray(build_a)
    for offset, old_word, new_word in V47_CHANGES:
        old = old_word.to_bytes(4, "little")
        new = new_word.to_bytes(4, "little")
        if result[offset:offset + 4] != old:
            raise SystemExit(f"V47 prerequisite mismatch at file offset {offset:#x}")
        result[offset:offset + 4] = new
    built = bytes(result)
    require_hash("reconstructed V47", built, V47_SHA)
    return built


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "Usage: py build_v59.py original_ps2_emu.elf TEST_ROM0 [output_directory]"
        )
    root = Path(__file__).resolve().parent
    original = Path(sys.argv[1]).read_bytes()
    test_rom = Path(sys.argv[2]).read_bytes()
    output_directory = Path(sys.argv[3]) if len(sys.argv) == 4 else Path("build")
    require_hash("original ps2_emu ELF", original, ORIGINAL_SHA)
    require_hash("PlayStation 2 TEST ROM0", test_rom, TEST_ROM_SHA)

    testmode = extract_rom_file(test_rom, "TESTMODE")
    require_hash("TESTMODE ROM file", testmode, TESTMODE_SHA)
    build_a = make_build_a(original, testmode)
    v47 = apply_v47(build_a)

    with TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        v47_path = temporary_path / "v47.elf"
        generated_path = temporary_path / "generated"
        v47_path.write_bytes(v47)
        subprocess.run(
            [sys.executable, str(root / "v59_payload.py"), str(v47_path), str(generated_path)],
            check=True,
        )
        result = (generated_path / OUTPUT_NAME).read_bytes()

    require_hash("final V59 output", result, OUTPUT_SHA)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / OUTPUT_NAME
    output_path.write_bytes(result)
    print(f"Created: {output_path}")
    print(f"SHA-256: {digest(result)}")


if __name__ == "__main__":
    main()
