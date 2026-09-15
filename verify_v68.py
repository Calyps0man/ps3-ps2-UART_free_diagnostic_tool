"""Static verifier for the cleaned Calyps0 V68 NoPS2 diagnostic."""

from hashlib import sha256
from pathlib import Path
import importlib.util
import struct
import sys

EXPECTED_SHA = "2f8c17586b977b1eb0752c8a89abca7985c8f459e54569614a2bf76485d0a581"
TESTMODE_SHA = "bad778360139b961b6692c97e1578e65eae331f30c6bd86f48e1629d07962b3d"
ROM_FILE = 0x1B06E8
TESTMODE_FILE = 0x2ED818
TESTMODE_SIZE = 0x16A38


def branch_target(word: int, pc: int) -> int:
    displacement = struct.unpack("<h", struct.pack("<H", word & 0xFFFF))[0]
    return pc + 4 + displacement * 4


def require_text(payload: bytes, values: tuple[str, ...], description: str) -> None:
    for value in values:
        if value.encode() + b"\0" not in payload:
            raise SystemExit(f"Missing {description}: {value}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: py verify_v68.py Calyps0_V68_NoPS2_Test.elf")

    path = Path(sys.argv[1])
    data = path.read_bytes()
    actual = sha256(data).hexdigest()
    if actual != EXPECTED_SHA:
        raise SystemExit(
            f"SHA-256 mismatch\nExpected: {EXPECTED_SHA}\nActual:   {actual}"
        )

    module_path = Path(__file__).with_name("v68_payload.py")
    spec = importlib.util.spec_from_file_location("v68_payload", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit("Could not load v68_payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    embedded = data[
        ROM_FILE + module.CAVE:
        ROM_FILE + module.CAVE + len(module.payload)
    ]
    if embedded != module.payload:
        raise SystemExit("Embedded payload does not match v68_payload.py")

    testmode = data[TESTMODE_FILE:TESTMODE_FILE + TESTMODE_SIZE]
    if sha256(testmode).hexdigest() != TESTMODE_SHA:
        raise SystemExit("Embedded TESTMODE image was unexpectedly modified")

    # A non-negative InitRDRAM return must branch directly to component tests,
    # bypassing the destructive RDRAM sweep.
    component_branches = []
    for offset in range(0, len(module.payload), 4):
        word = struct.unpack_from("<I", module.payload, offset)[0]
        pc = module.PC + offset
        is_bgez_s0 = (
            (word >> 26) == 1
            and ((word >> 21) & 31) == 16
            and ((word >> 16) & 31) == 1
        )
        if is_bgez_s0 and branch_target(word, pc) == module.a.labels["eegs_tests"]:
            component_branches.append(offset)
    if len(component_branches) != 1:
        raise SystemExit(
            "Positive InitRDRAM sweep-bypass branch is missing or ambiguous"
        )

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

    print(f"Verified: {path}")
    print(f"SHA-256: {actual}")
    print("Negative InitRDRAM: Channel A/B sweep; components N/T")
    print("Non-negative InitRDRAM: REPORTED GOOD; sweep skipped; components tested")
    print("Current V68 payload, display text and control flow verified")


if __name__ == "__main__":
    main()
