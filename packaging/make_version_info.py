"""Write the Windows version resource PyInstaller embeds in VLClone.exe."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "vlclone" / "__init__.py").read_text()).group(1)
nums = tuple((list(map(int, re.findall(r"\d+", version))) + [0, 0, 0, 0])[:4])
text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'VLClone'),
      StringStruct('FileDescription', 'VLClone media player'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('InternalName', 'VLClone'),
      StringStruct('OriginalFilename', 'VLClone.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
out = ROOT / "build" / "version_info.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(text, encoding="utf-8")
print(version if "--print" in sys.argv else f"wrote {out}")
