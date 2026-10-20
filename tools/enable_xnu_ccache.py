#!/usr/bin/env python3
"""Connect the pinned darwin-xnu-build script to CI compiler/download caches."""
import argparse
from pathlib import Path


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise SystemExit("Pinned build script changed: expected exactly one cache hook")
    return source.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_script", type=Path)
    args = parser.parse_args()
    source = args.build_script.read_text()
    compiler = '    export CXX="$(xcrun -find clang++)"\n'
    source = replace_once(source, compiler, compiler + '''
    # Keep the selected Apple compiler; ccache hashes its actual binary.
    if [[ -n "${SWEET_CCACHE:-}" ]]; then
        if [[ ! -x "${SWEET_CCACHE}" ]]; then
            error "Configured ccache executable is missing"
            return 1
        fi
        export CC="${SWEET_CCACHE} ${CC}"
        export CXX="${SWEET_CCACHE} ${CXX}"
        info "Compiler cache enabled: ${SWEET_CCACHE}"
    fi
''')
    download = '        curl --progress-bar --max-time 900 --connect-timeout 60 -L -o /tmp/KDK.dmg "${KDK_URL}"\n'
    source = replace_once(source, download, '''        if [[ -n "${SWEET_KDK_DMG:-}" && -s "${SWEET_KDK_DMG}" ]]; then
            info "Restoring the cached KDK disk image"
            cp "${SWEET_KDK_DMG}" /tmp/KDK.dmg
        else
''' + download + '''            if [[ -n "${SWEET_KDK_DMG:-}" ]]; then
                mkdir -p "$(dirname "${SWEET_KDK_DMG}")"
                cp /tmp/KDK.dmg "${SWEET_KDK_DMG}"
            fi
        fi
''')
    args.build_script.write_text(source)


if __name__ == "__main__":
    main()
