"""Print the Firebase project identifier from a user-supplied Able Android APK."""

from argparse import ArgumentParser
from pathlib import Path
import re
import subprocess


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, help="path to your copy of the Able to Use APK")
    parser.add_argument("--aapt", default="aapt", help="Android SDK aapt executable")
    args = parser.parse_args()
    try:
        resources = subprocess.run(
            [args.aapt, "dump", "--values", "resources", str(args.apk)],
            capture_output=True, check=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        parser.exit(1, "Could not read the APK. Check the APK path and the Android SDK aapt executable.\n")
    match = re.search(
        rb'string/google_api_key:[^\r\n]*\s+\(string\d*\)\s+"(AIza[\w-]{35})"', resources,
    )
    if not match:
        parser.exit(1, "No Firebase project API key was found in the APK resources.\n")
    print(match[1].decode("ascii"))


if __name__ == "__main__":
    main()
