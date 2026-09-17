"""Build either Calyps0 V68 NoPS2 diagnostic variant from verified inputs.

Variants:
  normal - skip the RDRAM sweep after a non-negative InitRDRAM return.
  forced - run the RDRAM sweep for every returned InitRDRAM value.
"""

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
DISPLAY_BASE_SHA = "397fa65fd2e1226cda642e08ef64b16dc2f367040021c9b5db455f590bb2ead7"

OSDSYS_OFFSET = 0x2EC818
OSDSYS_SLOT_SIZE = 0x3DEF8

VARIANTS = {
    "normal": {
        "payload": "v68_payload.py",
        "output": "Calyps0_V68_NoPS2_Test.elf",
        "sha256": "2d0904edf22335c965af940c5ad43a7b6184f28ffcc4c712a2f4e44a11d7af73",
    },
    "forced": {
        "payload": "v68_forced_payload.py",
        "output": "Calyps0_V68_NoPS2_Forced_Sweep_Fixed.elf",
        "sha256": "9ea4c16065b4cc2835c21f9c6a50ba283fea39e198aed15b7d47b6d4ef0a1dd7",
    },
}

# Instruction replacements required by the verified display base.
# Each tuple is (file offset, expected little-endian MIPS word, replacement).
DISPLAY_BASE_CHANGES = [
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
    require_hash("reconstructed display-program base", built, BUILD_A_SHA)
    return built


def make_display_base(build_a: bytes) -> bytes:
    result = bytearray(build_a)
    for offset, old_word, new_word in DISPLAY_BASE_CHANGES:
        old = old_word.to_bytes(4, "little")
        if result[offset:offset + 4] != old:
            raise SystemExit(f"Display-base prerequisite mismatch at {offset:#x}")
        result[offset:offset + 4] = new_word.to_bytes(4, "little")
    built = bytes(result)
    require_hash("reconstructed display base", built, DISPLAY_BASE_SHA)
    return built


def parse_arguments() -> tuple[Path, Path, str, Path]:
    args = sys.argv[1:]
    usage = (
        "Usage:\n"
        "  py build_v68.py original_ps2_emu.elf TEST_ROM0 normal [output_directory]\n"
        "  py build_v68.py original_ps2_emu.elf TEST_ROM0 forced [output_directory]\n\n"
        "Compatibility: omitting the variant selects normal."
    )
    if len(args) < 2 or len(args) > 4:
        raise SystemExit(usage)
    original, test_rom = map(Path, args[:2])
    variant = "normal"
    output_directory = Path("build")
    if len(args) >= 3:
        if args[2].lower() in VARIANTS:
            variant = args[2].lower()
            if len(args) == 4:
                output_directory = Path(args[3])
        elif len(args) == 3:
            # Preserve the old command: the third argument was the output dir.
            output_directory = Path(args[2])
        else:
            raise SystemExit(usage)
    return original, test_rom, variant, output_directory


def main() -> None:
    original_path, test_rom_path, variant, output_directory = parse_arguments()
    root = Path(__file__).resolve().parent
    config = VARIANTS[variant]
    payload_script = root / config["payload"]
    font_path = root / "font.bin"
    if not payload_script.is_file():
        raise SystemExit(f"Missing payload source: {payload_script.name}")
    if not font_path.is_file():
        raise SystemExit("Missing font.bin beside the payload scripts")

    original = original_path.read_bytes()
    test_rom = test_rom_path.read_bytes()
    require_hash("original ps2_emu ELF", original, ORIGINAL_SHA)
    require_hash("PlayStation 2 TEST ROM0", test_rom, TEST_ROM_SHA)
    testmode = extract_rom_file(test_rom, "TESTMODE")
    require_hash("TESTMODE ROM file", testmode, TESTMODE_SHA)
    display_base = make_display_base(make_build_a(original, testmode))

    with TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        display_base_path = temporary_path / "display_base.elf"
        generated_path = temporary_path / "generated"
        display_base_path.write_bytes(display_base)
        subprocess.run(
            [sys.executable, str(payload_script), str(display_base_path), str(generated_path)],
            check=True,
        )
        result_path = generated_path / config["output"]
        if not result_path.is_file():
            raise SystemExit(f"Payload did not create expected file: {config['output']}")
        result = result_path.read_bytes()

    require_hash(f"final V68 {variant} output", result, config["sha256"])

    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / config["output"]
    output_path.write_bytes(result)
    print(f"Variant: {variant}")
    print(f"Created: {output_path}")
    print(f"SHA-256: {digest(result)}")


if __name__ == "__main__":
    main()
