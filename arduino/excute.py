import subprocess
import serial.tools.list_ports
import sys
import os

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
BOARD       = "arduino:avr:uno"
SKETCH      = "arduino/source"
ARDUINO_CLI = "/home/john-victors5/Desktop/Prototypye/arduino/arduino-cli"

ARDUINO_USB_IDS = [
    "USB", "ACM", "usbserial",
    "wchusbserial", "CH340", "Arduino",
]

REQUIRED_LIBRARIES = [
    "Servo",
]

# ─────────────────────────────────────────
#  CHECKS
# ─────────────────────────────────────────
def check_arduino_cli():
    if not os.path.isfile(ARDUINO_CLI):
        print(f"❌ ERROR: arduino-cli not found at:")
        print(f"   {ARDUINO_CLI}")
        sys.exit(1)
    # Make sure it's executable
    if not os.access(ARDUINO_CLI, os.X_OK):
        print(f"❌ ERROR: arduino-cli is not executable.")
        print(f"   Run: chmod +x {ARDUINO_CLI}")
        sys.exit(1)
    print(f"✅ arduino-cli found: {ARDUINO_CLI}")

def check_sketch_exists():
    if not os.path.isdir(SKETCH):
        print(f"❌ ERROR: Sketch folder not found: '{SKETCH}'")
        sys.exit(1)

    sketch_name = os.path.basename(os.path.abspath(SKETCH))
    ino_file    = os.path.join(SKETCH, f"{sketch_name}.ino")

    if not os.path.isfile(ino_file):
        ino_files = [f for f in os.listdir(SKETCH) if f.endswith(".ino")]
        if not ino_files:
            print(f"❌ ERROR: No .ino file found inside '{SKETCH}'")
            sys.exit(1)
        print(f"⚠️  Warning: Expected '{sketch_name}.ino' but found '{ino_files[0]}'")
    else:
        print(f"✅ Sketch found: {ino_file}")

# ─────────────────────────────────────────
#  LIBRARIES
# ─────────────────────────────────────────
def install_libraries():
    if not REQUIRED_LIBRARIES:
        return

    print("\n📦 Checking required libraries...")
    result = subprocess.run(
        [ARDUINO_CLI, "lib", "list"],
        capture_output=True, text=True
    )
    installed = result.stdout.upper()

    for lib in REQUIRED_LIBRARIES:
        if lib.upper() in installed:
            print(f"   ✅ Already installed: {lib}")
        else:
            print(f"   ⬇️  Installing: {lib} ...")
            install = subprocess.run(
                [ARDUINO_CLI, "lib", "install", lib],
                capture_output=True, text=True
            )
            if install.returncode == 0:
                print(f"   ✅ Installed: {lib}")
            else:
                print(f"   ❌ Failed to install: {lib}")
                print(f"      {install.stderr.strip()}")
                sys.exit(1)

# ─────────────────────────────────────────
#  PORT DETECTION
# ─────────────────────────────────────────
def find_arduino_port():
    print("\n🔍 Scanning for Arduino...")
    ports = serial.tools.list_ports.comports()

    if not ports:
        print("❌ ERROR: No serial ports detected.")
        print("   - Check USB cable (data cable, not charge-only)")
        print("   - Install CH340 driver if using a clone board")
        sys.exit(1)

    matched = []
    for port in ports:
        combined = f"{port.device} {port.description} {port.hwid}".upper()
        if any(uid.upper() in combined for uid in ARDUINO_USB_IDS):
            matched.append(port)
            print(f"   Found: {port.device} — {port.description}")

    if not matched:
        print("❌ ERROR: No Arduino detected. Available ports:")
        for port in ports:
            print(f"   {port.device} — {port.description}")
        sys.exit(1)

    if len(matched) > 1:
        print(f"⚠️  Multiple found. Using: {matched[0].device}")

    print(f"✅ Using port: {matched[0].device}")
    return matched[0].device

# ─────────────────────────────────────────
#  COMPILE & UPLOAD
# ─────────────────────────────────────────
def compile_sketch():
    print(f"\n🔨 Compiling: {SKETCH}  [{BOARD}]\n")
    result = subprocess.run(
        [ARDUINO_CLI, "compile", "--fqbn", BOARD, SKETCH],
        capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print("❌ Compile FAILED:")
        print(result.stderr)
        print("\n   Fix: arduino-cli core install arduino:avr")
        return False
    print("✅ Compile successful!")
    return True

def upload_sketch(port):
    print(f"\n📤 Uploading to {port}...")
    result = subprocess.run(
        [ARDUINO_CLI, "upload", "-p", port, "--fqbn", BOARD, SKETCH],
        capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print("❌ Upload FAILED:")
        print(result.stderr)
        print("\n   Fix: Close Serial Monitor, replug Arduino")
        return False
    print("✅ Upload successful!")
    return True

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────
def main():
    print("=" * 45)
    print("   Arduino CLI — Python Uploader")
    print("=" * 45)

    check_arduino_cli()
    check_sketch_exists()
    install_libraries()
    port = find_arduino_port()

    if compile_sketch():
        upload_sketch(port)
    else:
        print("\n⛔ Upload skipped due to compile error.")
        sys.exit(1)

if __name__ == "__main__":
    main()