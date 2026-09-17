"""Static verifier for normal and forced Calyps0 V68 NoPS2 diagnostics."""

from hashlib import sha256
from pathlib import Path
import importlib.util
import struct
import sys

ROM_FILE = 0x1B06E8
TESTMODE_FILE = 0x2ED818
TESTMODE_SIZE = 0x16A38
TESTMODE_SHA = "bad778360139b961b6692c97e1578e65eae331f30c6bd86f48e1629d07962b3d"

VARIANTS = {
    "normal": {
        "payload": "v68_payload.py",
        "sha256": "2d0904edf22335c965af940c5ad43a7b6184f28ffcc4c712a2f4e44a11d7af73",
    },
    "forced": {
        "payload": "v68_forced_payload.py",
        "sha256": "9ea4c16065b4cc2835c21f9c6a50ba283fea39e198aed15b7d47b6d4ef0a1dd7",
    },
}


def branch_target(word: int, pc: int) -> int:
    displacement = struct.unpack("<h", struct.pack("<H", word & 0xFFFF))[0]
    return pc + 4 + displacement * 4


def require_text(payload: bytes, values: tuple[str, ...], description: str) -> None:
    for value in values:
        if value.encode() + b"\0" not in payload:
            raise SystemExit(f"Missing {description}: {value}")


def load_payload(root: Path, variant: str):
    module_path = root / VARIANTS[variant]["payload"]
    if not module_path.is_file():
        raise SystemExit(f"Missing payload source: {module_path.name}")
    spec = importlib.util.spec_from_file_location(f"v68_{variant}_payload", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load {module_path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_arguments() -> tuple[str | None, Path]:
    args = sys.argv[1:]
    usage = (
        "Usage:\n"
        "  py verify_v68.py diagnostic.elf             (auto-detect)\n"
        "  py verify_v68.py normal diagnostic.elf\n"
        "  py verify_v68.py forced diagnostic.elf"
    )
    if len(args) == 1:
        return None, Path(args[0])
    if len(args) == 2 and args[0].lower() in VARIANTS:
        return args[0].lower(), Path(args[1])
    raise SystemExit(usage)


def identify_variant(data: bytes, root: Path, requested: str | None):
    candidates = [requested] if requested else list(VARIANTS)
    loaded = {}
    for variant in candidates:
        module = load_payload(root, variant)
        loaded[variant] = module
        start = ROM_FILE + module.CAVE
        if data[start:start + len(module.payload)] == module.payload:
            return variant, module
    names = ", ".join(VARIANTS[name]["payload"] for name in candidates)
    raise SystemExit(f"Embedded payload does not match: {names}")


def verify_common(data: bytes, module) -> None:
    testmode = data[TESTMODE_FILE:TESTMODE_FILE + TESTMODE_SIZE]
    if sha256(testmode).hexdigest() != TESTMODE_SHA:
        raise SystemExit("Embedded display-program image was unexpectedly modified")

    # Verify all three non-payload patches made by either payload generator.
    expected_words = {
        0x411C8: 0x0040802D,  # move s0,v0: preserve actual InitRDRAM return
        0x411DC: 0x0200102D,  # move v0,s0: return the actual value
        module.HOOK: 0x08000000 | ((module.a.labels["real"] >> 2) & 0x03FFFFFF),
    }
    for rom_offset, expected in expected_words.items():
        actual = struct.unpack_from("<I", data, ROM_FILE + rom_offset)[0]
        if actual != expected:
            raise SystemExit(f"Required patch mismatch at ROM offset {rom_offset:#x}")

    require_text(
        module.payload,
        ("EE:", "SPR:", "FPU:", "CACHE:", "DMA:", "VU0:", "VU1:",
         "GSCORE:", "BRIDGE:"),
        "component label",
    )
    require_text(
        module.payload,
        ("RUNNING RDRAM", "RUNNING EE", "RUNNING SPR", "RUNNING FPU",
         "RUNNING CACHE", "RUNNING DMA", "RUNNING VU0", "RUNNING VU1",
         "RUNNING GSCORE", "RUNNING BRIDGE", "TESTS COMPLETE"),
        "status text",
    )
    require_text(
        module.payload,
        ("CH. A:", "CH. B:", "NG RATE A:", "NG RATE B:",
         "REPORTED GOOD", "TEST IN PROGRESS"),
        "channel text",
    )
    require_text(
        module.payload,
        ("N/T", "OK", "FAIL", "TIMEOUT", "BAD RESP", "FAIL MEM",
         "FAIL DATA"),
        "result text",
    )


def verify_variant_control_flow(variant: str, module) -> None:
    bypasses = []
    for offset in range(0, len(module.payload) - 3, 4):
        word = struct.unpack_from("<I", module.payload, offset)[0]
        pc = module.PC + offset
        is_bgez_s0 = (
            (word >> 26) == 1
            and ((word >> 21) & 31) == 16
            and ((word >> 16) & 31) == 1
        )
        if is_bgez_s0 and branch_target(word, pc) == module.a.labels["eegs_tests"]:
            bypasses.append(offset)

    if variant == "normal" and len(bypasses) != 1:
        raise SystemExit("Normal variant sweep-bypass branch is missing or ambiguous")
    if variant == "forced" and bypasses:
        raise SystemExit("Forced variant unexpectedly contains the normal sweep bypass")


def main() -> None:
    requested, path = parse_arguments()
    data = path.read_bytes()
    root = Path(__file__).resolve().parent
    if not (root / "font.bin").is_file():
        raise SystemExit("Missing font.bin beside the payload scripts")

    variant, module = identify_variant(data, root, requested)
    actual = sha256(data).hexdigest()
    expected = VARIANTS[variant]["sha256"]
    if actual != expected:
        raise SystemExit(
            f"SHA-256 mismatch for {variant} variant\n"
            f"Expected: {expected}\nActual:   {actual}"
        )
    verify_common(data, module)
    verify_variant_control_flow(variant, module)

    print(f"Verified: {path}")
    print(f"Variant: {variant}")
    print(f"SHA-256: {actual}")
    if variant == "normal":
        print("Negative InitRDRAM: Channel A/B sweep; components N/T")
        print("Non-negative InitRDRAM: REPORTED GOOD; sweep skipped; components tested")
    else:
        print("All InitRDRAM returns: Channel A/B sweep")
        print("Components run only after non-negative initialization and zero channel errors")
    print("Payload, display text, return preservation and control flow verified")


if __name__ == "__main__":
    main()
