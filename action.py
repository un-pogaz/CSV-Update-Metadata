#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

import csv
from typing import List

try:
    from qt.core import (
        QAbstractItemView,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPushButton,
        Qt,
        QToolButton,
        QVBoxLayout,
    )
except ImportError:
    from PyQt5.Qt import (
        QAbstractItemView,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPushButton,
        Qt,
        QToolButton,
        QVBoxLayout,
    )

from calibre.constants import ismacos
from calibre.gui2 import FileDialog, choose_files
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.widgets2 import Dialog, HTMLDisplay

from .common_utils import GUI, PLUGIN_NAME, PREFS_json, PREFS_library, current_db, debug_print, get_icon
from .common_utils.librarys import get_BookIds_selected, no_launch_error
from .common_utils.menus import create_menu_action_unique
from .common_utils.widgets import ImageTitleLayout

PLUGIN_ICON = 'images/plugin.png'

# This is where all preferences for this plugin are stored
PREFS = PREFS_json()

LIBRARY_PREFS = PREFS_library()
LIBRARY_PREFS.defaults['sort_order'] = {'id':0, 'authors':1, 'series':2, 'series_index':3, 'title':4}
LIBRARY_PREFS.defaults['fields'] = ['id', 'authors', 'series', 'series_index', 'title']


class CSV(csv.Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    skipinitialspace = False
    lineterminator = '\n'
    quoting = csv.QUOTE_ALL


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
        ids = get_BookIds_selected(True)
        if not ids:
            return
        ExportCSVdialog(ids, GUI).exec()


def pick_csv_to_load(parent=None) -> str:
    archives = choose_files(parent or GUI,
        name='csv dialog',
        title=_('Select a CSV file to load…'),
        filters=[('CSV Files', ['csv'])],
        all_files=False, select_only_single_file=True,
    )
    if not archives:
        return None
    return archives[0]


def field_name(field):
    if field == 'isbn':
        return 'ISBN'
    if field == 'library_name':
        return _('Library name')
    if field.endswith('_index'):
        return field_name(field[:-len('_index')]) + ' ' + _('Number')
    fm = current_db().field_metadata
    return fm[field].get('name') or field


def pick_csv_to_export(parent=None) -> str:
    fd = FileDialog(parent=parent or GUI,
        name='csv dialog',
        title=_('Export CSV file as…'),
        filters=[('CSV Files', ['csv'])],
        add_all_files_filter=False, mode=QFileDialog.FileMode.AnyFile,
    )
    fd.setParent(None)
    if not fd.accepted:
        return None
    return fd.get_files()[0]


class ListColumnItem(QListWidgetItem):
    def __init__(self, field: str, name: str, parent=None):
        self.field = field
        self.name = name
        self.display_name = f'{name} ({field})'
        super().__init__(self.display_name, parent)


class ExportCSVdialog(Dialog):
    def __init__(self, ids: List[int]=[], parent=None):
        self.ids = ids or []
        Dialog.__init__(self,
            title=_('Export metadata to CSV'),
            name='plugin.CSVMetadata:ExportCSVdialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        l.addWidget(QLabel(_('Fields to export in output:'), self))
        self.list = QListWidget(self)
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.CopyAction if ismacos else Qt.DropAction.MoveAction)
        self.list.setAlternatingRowColors(True)
        
        l.addWidget(self.list)
        
        h = QHBoxLayout(self)
        l.addLayout(h)
        h.addWidget(QLabel(_('Drag and drop to re-arrange fields'), self))
        h.addStretch()
        self.select_all_button = QPushButton(_('Select &all'))
        self.select_all_button.clicked.connect(self.select_all)
        self.select_none_button = QPushButton(_('Select &none'))
        self.select_none_button.clicked.connect(self.select_none)
        self.select_visible_button = QPushButton(_('Select &visible'))
        self.select_visible_button.clicked.connect(self.select_visible)
        h.addWidget(self.select_all_button)
        h.addWidget(self.select_none_button)
        h.addWidget(self.select_visible_button)
        
        l.addWidget(self.bb)
        
        self.poplate_list()

    def poplate_list(self):
        from calibre.library.catalogs import FIELDS
        db = current_db()
        self.all_fields = {x for x in FIELDS if x not in ['all', 'ondevice', 'cover']}
        self.all_fields.update(db.custom_field_keys())
        sort_order = LIBRARY_PREFS['sort_order']
        fields = LIBRARY_PREFS['fields']
        fm = current_db().field_metadata

        def key_buider(field):
            return sort_order.get(field, 1000), field_name(field), field

        self.list.clear()
        for idx, name, field in sorted(map(key_buider, self.all_fields)):
            item = ListColumnItem(field, name, self.list)
            item.setCheckState(Qt.CheckState.Checked if field in fields else Qt.CheckState.Unchecked)
            if field.startswith('#') and fm[field]['datatype'] == 'series':
                field += '_index'
                item = ListColumnItem(field, name, self.list)
                item.setCheckState(Qt.CheckState.Checked if field in fields else Qt.CheckState.Unchecked)

    def select_all(self):
        for row in range(self.list.count()):
            self.list.item(row).setCheckState(Qt.CheckState.Checked)

    def select_none(self):
        for row in range(self.list.count()):
            self.list.item(row).setCheckState(Qt.CheckState.Unchecked)

    def select_visible(self):
        state = GUI.library_view.get_state()
        hidden = set(state['hidden_columns'])
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setCheckState(Qt.CheckState.Unchecked if item.field in hidden else Qt.CheckState.Checked)

    def accept(self):
        sort_order = {}
        fields = {}
        for row in range(self.list.count()):
            item = self.list.item(row)
            sort_order[row] = item.field
            if item.checkState() == Qt.CheckState.Checked:
                fields[item.field] = item.display_name
        if not fields:
            return no_launch_error(_('No field selected'))
        
        file = pick_csv_to_export()
        if not file:
            return
        
        LIBRARY_PREFS['sort_order'] = sort_order
        LIBRARY_PREFS['fields'] = list(fields.keys())
        db = current_db().new_api
        
        with open(file, 'w', encoding='utf-8', newline='\n') as f:
            writer = csv.writer(f, CSV)
            writer.writerow(fields.values())
            for id in self.ids:
                row = []
                mi = db.get_metadata(id)
                for field in fields.keys():
                    row.append(mi.format_field(field, False)[1])
                writer.writerow(row)
        
        Dialog.accept(self)
