#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

from csv import reader, unix_dialect, writer

try:
    from qt.core import QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget
except ImportError:
    from PyQt5.Qt import QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget

from calibre.gui2.actions import InterfaceAction
from calibre.gui2.widgets2 import Dialog

from .common_utils import GUI, PLUGIN_NAME, PREFS_json, debug_print, get_icon
from .common_utils.menus import create_menu_action_unique
from .common_utils.widgets import ImageTitleLayout

PLUGIN_ICON = 'images/plugin.png'

# This is where all preferences for this plugin are stored
PREFS = PREFS_json()


class CSVMetadataAction(InterfaceAction):
    
    name = PLUGIN_NAME
    # Create our top-level menu/toolbar action (text, icon_path, tooltip, keyboard shortcut)
    action_spec = (PLUGIN_NAME, None, _('Update Metadata from a CSV file template'), None)
    popup_type = QToolButton.MenuButtonPopup
    action_type = 'current'
    dont_add_to = frozenset(['context-menu-device'])
    
    def genesis(self):
        self.menu = QMenu(GUI)
        self.qaction.setMenu(self.menu)
        self.qaction.setIcon(get_icon(PLUGIN_ICON))
        self.qaction.triggered.connect(self.toolbar_triggered)
        
        self.rebuild_menus()
    
    def initialization_complete(self):
        return
    
    def rebuild_menus(self):
        m = self.menu
        m.clear()
        
        create_menu_action_unique(self, m, _('&Update Metadata from CSV'), PLUGIN_ICON,
                                        triggered=self.update_metadata,
                                        unique_name='&Export CSV')
        
        create_menu_action_unique(self, m, _('&Export CSV'), PLUGIN_ICON,
                                        triggered=self.export_metadata,
                                        unique_name='&Export CSV')
        
        GUI.keyboard.finalize()
    
    def toolbar_triggered(self):
        self.update_metadata()
    
    def update_metadata(self):
        pass
    
    def export_metadata(self):
        pass
