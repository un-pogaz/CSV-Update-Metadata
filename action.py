#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

from csv import QUOTE_ALL, Dialect, reader, writer

try:
    from qt.core import QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget
except ImportError:
    from PyQt5.Qt import QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget

from calibre.gui2.actions import InterfaceAction
from calibre.gui2.widgets2 import Dialog, HTMLDisplay

from .common_utils import GUI, PLUGIN_NAME, PREFS_json, debug_print, get_icon
from .common_utils.menus import create_menu_action_unique
from .common_utils.widgets import ImageTitleLayout

PLUGIN_ICON = 'images/plugin.png'

# This is where all preferences for this plugin are stored
PREFS = PREFS_json()


class CSV(Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    skipinitialspace = False
    lineterminator = '\n'
    quoting = QUOTE_ALL


class CSVformatDialog(Dialog):
    def __init__(self, parent=None):
        Dialog.__init__(self,
            title=_('CSV Format info'),
            name='plugin.CSVMetadata:CSVformatDialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        l.addLayout(ImageTitleLayout(PLUGIN_ICON, 'CSV format', self))
        body = HTMLDisplay(self)
        l.addWidget(body)
        
        import inspect
        lines, num = inspect.getsourcelines(CSV)
        csv_code = ''.join(lines)
        
        rslt = []
        def html_builder(tag, content) -> str:
            return f'<{tag}>{content}</{tag}>'
        def list_builder(*args) -> str:
            lines = '\n'.join([html_builder('li', a) for a in args])
            return html_builder('ul', ('\n'+lines+'\n').strip())
        def append(tag, content):
            rslt.append(html_builder(tag, content))
        
        append('p', _('A comma-separated values (CSV) file is a delimited text file that uses a comma to separate values. '
                      'A CSV file stores tabular data in plain text. Each line of the file is a data record. '
                      'Each record consists of one or more values, separated by commas. '
                      'The use of the comma as a value separator is the source of the name for this file format.'))
        append('p', _('The CSV format supported by the plugin is the following:'))
        rslt.append(list_builder(
            _('The entire file must be "saved" in the Unicode (UTF-8) character set (not ASCII).'),
            _('The value delimiter (column separator) must be a single comma (not tab-separated or fixed width).'),
            _('The CSV require at least two columns.'),
            _('The CSV require at least two rows/lines:')+'\n'+list_builder(
                _('The first row must be a "header" row which contains the double-quoted unique textual name of each column.'),
                _('All rows after the first row must contain either textual values or empty within each and every column.'),
                _('All rows must have the same number of double-quoted textual columns as the "header" row.'),
            ),
            _('All values must be double-quoted.')+'\n'+list_builder(
                _('If you open your CSV file in a simple text editor, you should not find a row that ends with '
                  'a simple comma (,), or has 2 (,,) or more (,,,,) commas together.'),
                _('To include a double-quote character inside a value, write two double-quote consecutively "".'),
            ),
            _('Leading and trailing spaces will be removed from each value automatically.'),
            _('Empty value will be skipped (no edit action).'),
            _('To indicate that you want <i>delete</i> a value, you should use the special keyword "NULL" (full case).'),
        ))
        append('p', '<br>')
        rslt.append('<hr>')
        append('p', _('The plugin use the default library <code>csv</code> to import and convert files. '
                      'For reference, here the code of <code>csv.Dialect</code> class used:'))
        append('pre', csv_code)
        
        body.setHtml('\n'.join(rslt))


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
        
        self.menu.addSeparator()
        create_menu_action_unique(self, m, _('&About the CSV Format'), None,
                                        triggered=self.show_csv_format,
                                        unique_name='&About the CSV Format',
                                        shortcut=False)
        
        GUI.keyboard.finalize()
    
    def toolbar_triggered(self):
        self.update_metadata()
    
    def show_csv_format(self):
        CSVformatDialog(GUI).exec()
    
    def update_metadata(self):
        pass
    
    def export_metadata(self):
        pass
