@echo off
setlocal

cd /d "%~dp0"

echo Building Calyps0 V68 NoPS2 Test...
echo.

if not exist "build_v68.py" (
    echo ERROR: build_v68.py was not found in this folder.
    goto :failed
)

if not exist "ps2_emu.elf" (
    echo ERROR: ps2_emu.elf was not found in this folder.
    goto :failed
)

if not exist "TEST_rom0" (
    echo ERROR: TEST_rom0 was not found in this folder.
    goto :failed
)

py build_v68.py ps2_emu.elf TEST_rom0 normal build
if errorlevel 1 goto :failed

echo.
echo BUILD COMPLETED SUCCESSFULLY.
echo The final ELF is in the build folder.
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Review the error shown above.
echo.
pause
exit /b 1
