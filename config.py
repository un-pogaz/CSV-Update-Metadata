#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9


try:
    from qt.core import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
except ImportError:
    from PyQt5.Qt import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from calibre.gui2.widgets2 import Dialog

from .common_utils import CALIBRE_VERSION, GUI, PREFS_json, debug_print, get_icon
from .common_utils.dialogs import KeyboardConfigDialogButton
from .common_utils.widgets import ImageTitleLayout

PLUGIN_ICON = 'images/plugin.png'

# This is where all preferences for this plugin are stored
PREFS = PREFS_json()


class ConfigWidget(QWidget):
    def __init__(self):
        QWidget.__init__(self)
    
    def save_settings(self):
        pass
