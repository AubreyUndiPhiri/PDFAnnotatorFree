"""Google Drive for desktop: Google's own sync app.

Once it's installed and signed in, Google Drive appears on the PC (a "Google
Drive" drive, usually G:, with "My Drive" and "Shared drives" in it; or, in
mirror mode, a "My Drive" folder in the user folder). Files opened from
there are ordinary files to this app, and Google syncs every save, so no
Google setup is needed in AUPedean. This module finds those folders.

No Qt here.
"""
import glob
import json
import os
import string
import subprocess
import time

DOWNLOAD_URL = "https://www.google.com/drive/download/"
VOLUME_LABEL = "Google Drive"
_INSTALL_GLOBS = [r"C:\Program Files\Google\Drive File Stream\*\GoogleDriveFS.exe",
                  r"C:\Program Files (x86)\Google\Drive File Stream\*\GoogleDriveFS.exe"]


def _volume_label(root):
    if os.name != "nt":
        return ""
    import ctypes

    name = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, None, None, None, None, 0)
    return name.value if ok else ""


def _drive_letters():
    if os.name != "nt":
        return []
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1 and c not in "AB"]


def _registry_mount_points():
    """Mount letters / mirror folders from Drive for desktop's settings."""
    found = []
    if os.name != "nt":
        return found
    import winreg

    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Google\DriveFS"),
                      (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\DriveFS")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("DefaultMountPoint", "PerAccountPreferences"):
                    try:
                        value, _type = winreg.QueryValueEx(k, name)
                    except OSError:
                        continue
                    if name == "DefaultMountPoint" and value:
                        found.append(value.rstrip(":\\") + ":\\" if len(value) <= 3 else value)
                        continue
                    try:
                        prefs = json.loads(value)
                    except (TypeError, ValueError):
                        continue
                    for account in prefs.get("per_account_preferences", []):
                        v = account.get("value", {})
                        mount = v.get("mount_point_path")
                        if mount:
                            found.append(mount.rstrip(":\\") + ":\\" if len(mount) <= 3 else mount)
                        for key_name in ("mirror_path", "my_drive_mirror_path"):
                            if v.get(key_name):
                                found.append(v[key_name])
        except OSError:
            continue
    return found


_cache = (0.0, None)
CACHE_SECONDS = 20


def find_roots(refresh=False):
    """[(label, folder)] of Google Drive folders on this PC, e.g.
    [("My Drive", "G:\\My Drive"), ("Shared drives", "G:\\Shared drives")].
    Cached for a few seconds (asking every drive for its label is slow)."""
    global _cache
    if not refresh and _cache[1] is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return list(_cache[1])
    roots = _scan()
    _cache = (time.monotonic(), roots)
    return list(roots)


def _scan():
    roots, seen = [], set()

    def add(label, path):
        key = os.path.normcase(os.path.abspath(path))
        if os.path.isdir(path) and key not in seen:
            seen.add(key)
            roots.append((label, path))

    candidates = _registry_mount_points() + [d for d in _drive_letters() if _volume_label(d) == VOLUME_LABEL]
    for base in candidates:
        if os.path.isdir(os.path.join(base, "My Drive")):
            add("My Drive", os.path.join(base, "My Drive"))
            add("Shared drives", os.path.join(base, "Shared drives"))
            add("Other computers", os.path.join(base, "Other computers"))
        elif os.path.basename(base.rstrip("\\/")).lower() == "my drive":
            add("My Drive", base)
    add("My Drive", os.path.join(os.path.expanduser("~"), "My Drive"))  # mirror mode's default folder
    return roots


def installed_exe():
    for pattern in _INSTALL_GLOBS:
        hits = sorted(glob.glob(pattern), reverse=True)  # newest version folder first
        if hits:
            return hits[0]
    return None


def start():
    """Start Google Drive for desktop if it's installed but not running."""
    exe = installed_exe()
    if not exe:
        return False
    subprocess.Popen([exe], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
    return True


def containing_root(path):
    """(label, root) of the Google Drive folder that holds `path`, or None."""
    if not path:
        return None
    target = os.path.normcase(os.path.abspath(path))
    for label, root in find_roots():
        r = os.path.normcase(os.path.abspath(root))
        if target == r or target.startswith(r.rstrip("\\/") + os.sep):
            return label, root
    return None
