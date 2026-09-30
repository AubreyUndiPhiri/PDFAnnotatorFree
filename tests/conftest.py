"""Keep the tests' settings (e.g. the Dark Mode toggle the run-through
clicks) out of the real user settings in the registry."""
import os
import tempfile

from PySide6.QtCore import QSettings

_settings_dir = tempfile.mkdtemp(prefix="aupedean-test-settings-")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _settings_dir)

# ...and the Google Drive / signing data (tokens, sync list, requests)
os.environ["AUPEDEAN_GOOGLE_DIR"] = tempfile.mkdtemp(prefix="aupedean-test-google-")

# ...and the signature service session
os.environ["AUPEDEAN_SIGN_DIR"] = tempfile.mkdtemp(prefix="aupedean-test-sign-")
