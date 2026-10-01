#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

import csv
from typing import Dict, List, Set, Tuple

try:
    from qt.core import (
        QAbstractItemView,
        QFileDialog,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPushButton,
        QScrollArea,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt5.Qt import (
        QAbstractItemView,
        QFileDialog,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPushButton,
        QScrollArea,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

from calibre.constants import ismacos
from calibre.db.write import get_adapter
from calibre.gui2 import FileDialog, choose_files, error_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.widgets2 import Dialog, HTMLDisplay
from calibre.utils.icu import sort_key

from .common_utils import CALIBRE_VERSION, GUI, PLUGIN_NAME, PREFS_json, PREFS_library, current_db, debug_print, get_icon
from .common_utils.columns import ColumnMetadata, get_columns_where
from .common_utils.librarys import get_BookIds_selected, no_launch_error
from .common_utils.menus import create_menu_action_unique
from .common_utils.widgets import ImageTitleLayout, KeyValueComboBox, NoWheelComboBox

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
            _('The entire file must be "saved" in the Unicode (UTF-8) character set.'),
            _('The value delimiter (column separator) must be a single comma (not tab-separated or fixed width).'),
            _('The CSV require at least two columns.'),
            _('The CSV require at least two rows/lines:')+'\n'+list_builder(
                _('The first row must be a "header" row which contains the double-quoted unique textual name of each column.'),
                _('All rows after the first row must contain either textual values or empty within each and every column.'),
                _('All rows must have the same number of double-quoted textual columns as the "header" row.'),
            ),
            _('All values should preferably be double-quoted.')+'\n'+list_builder(
                _('To include a double-quote character inside a value, write two double-quote consecutively "".'),
            ),
            _('Leading and trailing spaces will be removed from each value automatically.'),
            _('Empty value will be skipped (no edit action).'),
            _('To indicate that you want <i>delete</i> a value, you should use the special keyword "NULL" (full case).'),
        ))
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
        path = pick_csv_to_load()
        if not path:
            return
        try:
            header, data = load_csv_file(path)
            if not data:
                raise ValueError(_('The input CSV need at least 2 rows (one for the header and the others for the data).'))
            if len(header) < 2:
                raise ValueError(_('The input CSV need at least 2 columns (one has reference and the others for the data).'))
            for i in range(len(header)):
                header[i] = header[i].strip()
                if not header[i]:
                    raise ValueError(_('One column header is empty, index {}.').format(i+1))
            for v in header:
                if header.count(v) > 1:
                    raise ValueError(_('Their is a duplicate column header.'))
        except Exception as err:
            msg = '<br>'.join([
                _('The selected CSV fail to be loaded because is a malformed format.'),
                _('To be sure to use a valid format, check the section "About the CSV Format".'),
            ])
            error_dialog(
                GUI,
                _('Malformed CSV format'),
                f'<p>{msg}\n'+
                f'<p><b>{err.__class__.__name__}:</b> {err}',
                show=True,
                show_copy_button=False,
            )
            return
        
        h = len(header)
        for i,row in enumerate(data):
            if len(row) < h:
                row.extend('' for x in range(h-len(row)))
            data[i] = [e.strip() for e in row[:h]]
        
        UpdateCSVdialog(path, header, data, parent=GUI).exec()
    
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


def get_all_fields() -> Set[str]:
    from calibre.library.catalogs import FIELDS
    db = current_db()
    rslt = {x for x in FIELDS if x not in ['all', 'ondevice', 'cover']}
    rslt.update(db.custom_field_keys())
    if CALIBRE_VERSION >= (9,00,0):
        rslt.add('pages')
    return rslt


def get_writable_fields() -> Set[str]:
    all_fields = get_all_fields()
    def predicate(col: ColumnMetadata):
        if col.name not in all_fields:
            return False
        if col.is_composite:
            return False
        if col.name in {'library_name', 'id', 'uuid', 'formats', 'size', 'pages'}:
            return False
        return True
    return set(get_columns_where(predicate).keys())


def field_name(fm, field):
    if field == 'isbn':
        return 'ISBN'
    if field == 'library_name':
        return _('Library name')
    if field.endswith('_index'):
        return field_name(fm, field[:-len('_index')]) + ' ' + _('Index')
    return fm[field].get('name') or field


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
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        
        l.addWidget(self.list)
        
        h = QHBoxLayout()
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
        sort_order = LIBRARY_PREFS['sort_order']
        fields = LIBRARY_PREFS['fields']
        fm = current_db().field_metadata

        def key_buider(field):
            return sort_order.get(field, 1000), field_name(fm, field), field

        self.list.clear()
        for idx, name, field in sorted(map(key_buider, get_all_fields())):
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
            sort_order[item.field] = row
            if item.checkState() == Qt.CheckState.Checked:
                fields[item.field] = item.display_name
        if not fields:
            return no_launch_error(_('No field selected'))
        
        file = pick_csv_to_export()
        if not file:
            return
        
        LIBRARY_PREFS['sort_order'] = sort_order
        LIBRARY_PREFS['fields'] = list(fields.keys())
        export_csv_file(file, fields, self.ids)
        Dialog.accept(self)


def load_csv_file(csv_path: str) -> Tuple[List[str], List[List[str]]]:
    with open(csv_path, encoding='utf-8') as f:
        raw = f.read().splitlines(False)
    raw = list(csv.reader(raw, CSV))
    header = raw[0]
    data = raw[1:]
    return header, data


def export_csv_file(csv_path: str, fields: Dict[str, str], ids: List[int]) -> None:
    db = current_db().new_api
    with open(csv_path, 'w', encoding='utf-8', newline='\n') as f:
        writer = csv.writer(f, CSV)
        writer.writerow(fields.values())
        for id in ids:
            row = []
            mi = db.get_metadata(id)
            for field in fields.keys():
                row.append(mi.format_field(field, False)[1])
            writer.writerow(row)


def item_style(item: QWidget, *, bold=False, italic=False):
    font = item.font()
    font.setBold(bold)
    font.setItalic(italic)
    item.setFont(font)


class ViewCSVdataDialog(Dialog):
    def __init__(self, header: List[str], data: List[List[str]], parent=None):
        self.header = header or []
        self.data = data or []
        Dialog.__init__(self,
            title=_('View CSV content data'),
            name='plugin.CSVMetadata:ViewCSVdataDialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        self.table = t = QTableWidget()
        t.setAlternatingRowColors(True)
        t.setSelectionMode(QTableWidget.ExtendedSelection)
        t.setSortingEnabled(False)
        t.setMinimumSize(400, 200)
        l.addWidget(t)
        
        t.setColumnCount(len(self.header))
        t.setHorizontalHeaderLabels(self.header)
        t.verticalHeader().setDefaultSectionSize(24)
        
        t.setRowCount(len(self.data))
        for idr,row in enumerate(self.data):
            for idc,data in enumerate(row):
                item = QTableWidgetItem(data)
                item.setFlags(Qt.ItemIsEnabled)
                if data == 'NULL':
                    item_style(item, italic=True)
                t.setItem(idr, idc, item)


class UpdateCSVdialog(Dialog):
    def __init__(self, csv_path: str, header: List[str], data: List[List[str]], parent=None):
        self.csv_path = csv_path
        self.csv_header = header
        self.csv_data = data
        self.csv_widget: Dict[int, KeyValueComboBox] = {}
        Dialog.__init__(self,
            title=_('Update metadata from CSV'),
            name='plugin.CSVMetadata:UpdateCSVdialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        path_label = QLabel(self.csv_path)
        item_style(path_label, bold=True)
        path_label.setAlignment(Qt.AlignCenter)
        l.addWidget(path_label)
        
        view_layout = QHBoxLayout()
        l.addLayout(view_layout)
        view_layout.setAlignment(Qt.AlignCenter)
        self.button_raw_data = QPushButton(
            get_icon(PLUGIN_ICON),
            ' '+_('Column: {} | Row: {}').format(len(self.csv_header), len(self.csv_data)),
        )
        self.button_raw_data.setToolTip(_('View the raw content of the loaded CSV file.'))
        self.button_raw_data.setMinimumWidth(200)
        item_style(self.button_raw_data, bold=True)
        self.button_raw_data.clicked.connect(self.view_raw_data)
        view_layout.addStretch()
        view_layout.addWidget(self.button_raw_data)
        view_layout.addStretch()
        
        fm = current_db().field_metadata
        scroll = QScrollArea(self)
        l.addWidget(scroll)
        layout = QVBoxLayout(scroll)
        scroll.setLayout(layout)
        
        all_headers = {i:f'[{i+1}] {h}' for i,h in enumerate(self.csv_header)}
        all_fields = dict(sorted(
            ((n,f'{field_name(fm, n)} ({n})') for n in get_all_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        for n in ['library_name']:
            all_fields.pop(n, None)
        writable_fields = {'':''}
        writable_fields.update(sorted(
            ((n,f'{field_name(fm, n)} ({n})') for n in get_writable_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        
        self.reference_header = NoWheelComboBox(scroll)
        self.reference_header.addItems(all_headers.values())
        self.reference_header.setCurrentIndex(-1)
        
        self.reference_field = KeyValueComboBox(all_fields, parent=scroll)
        self.reference_field.setCurrentIndex(-1)
        
        reference_selector = QFormLayout()
        reference_selector.addRow(_('CSV column to seek:'), self.reference_header)
        reference_selector.addRow(_('Book field to match:'), self.reference_field)
        layout.addLayout(reference_selector)
        
        self.frame = QFrame()
        self.frame.setFrameShape(QFrame.HLine)
        self.frame.setFrameShadow(QFrame.Sunken)
        layout.addWidget(self.frame)
        
        self.data_selector = QFormLayout()
        layout.addLayout(self.data_selector)
        layout.addStretch()
        
        for idx, header in all_headers.items():
            field_out = KeyValueComboBox(writable_fields, parent=scroll)
            field_out.setCurrentIndex(-1)
            h = QHBoxLayout()
            h.addWidget(QLabel('⟹'))
            h.addWidget(field_out)
            self.data_selector.addRow(header, h)
            self.csv_widget[idx] = field_out
        
        button_layout = QHBoxLayout()
        l.addLayout(button_layout)
        
        self.button_preview_data = QPushButton(get_icon('search.png'), _('Preview update'))
        self.button_preview_data.clicked.connect(self.preview_update_data)
        self.button_update_data = QPushButton(get_icon('ok.png'), _('Update the metadata'))
        self.button_update_data.clicked.connect(self.accept)
        
        button_layout.addWidget(self.button_preview_data)
        button_layout.addStretch()
        button_layout.addWidget(self.button_update_data)

    def get_data_update_map(self) -> Tuple[List[str], List[List[str]]]:
        if not self.csv_header or not self.csv_data:
            error_dialog(
                self,
                _('Source CSV is empty'),
                _('The source CSV is empty.'),
                show=True,
                show_copy_button=False,
            )
            return [], []
        
        if self.reference_header.currentIndex() == -1:
            error_dialog(
                self,
                _('No reference header selected'),
                _('Select a reference header to seek the books to update.'),
                show=True,
                show_copy_button=False,
            )
            return [], []
        if self.reference_field.currentIndex() == -1:
            error_dialog(
                self,
                _('No reference field selected'),
                _('Select a reference field to seek the books to update.'),
                show=True,
                show_copy_button=False,
            )
            return [], []
        
        header, data = [], []
        data_map = []
        
        data_map.append(self.reference_header.currentIndex())
        header.append(self.reference_field.selected_key())
        
        for i,w in self.csv_widget.items():
            if k := w.selected_key():
                header.append(k)
                data_map.append(i)
        
        fm = current_db().field_metadata
        adapters = [get_adapter(k, fm[k]) for k in header]
        try:
            r, f, c = 0, 0, 0
            for r,row in enumerate(self.csv_data):
                tbl = []
                for f,c in enumerate(data_map):
                    if row[c] == '':
                        tbl.append('')
                    elif row[c] == 'NULL':
                        tbl.append(None)
                    else:
                        tbl.append(adapters[f](row[c]))
                if tbl[0] not in {'', None, 'NULL'}:
                    data.append(tbl)
        except Exception as err:
            msg = '<br>'.join([
                _('Invalid data for the field {} ({}).').format(field_name(fm, header[f]), header[f]),
                _('Column: [{}] {}').format(c, self.csv_header[c]),
                _('Line: {}').format(r),
            ])
            error_dialog(
                self,
                _('Invalid data to update'),
                f'<p>{msg}\n'+
                f'<p><b>{err.__class__.__name__}:</b> {err}',
                show=True,
                show_copy_button=False,
            )
            return [], []
        
        return header, data

    def accept(self):
        Dialog.accept(self)

    def view_raw_data(self):
        ViewCSVdataDialog(self.csv_header, self.csv_data, parent=self).exec()

    def preview_update_data(self):
        header, data = self.get_data_update_map()
        if not header:
            return
        
        # convert
        fm = current_db().field_metadata
        for row in data:
            for i in range(len(row)):
                if row[i] is None:
                    row[i] = 'NULL'
                elif isinstance(row[i], (list, tuple)):
                    if not row[i]:
                        row[i] = 'NULL'
                    else:
                        sv = fm.get('is_multiple', {}).get('list_to_ui', ', ')
                        row[i] = sv.join(row[i])
                elif isinstance(row[i], (int, float, bool)):
                    row[i] = str(row[i]).lower()
                elif isinstance(row[i], dict):
                    if not row[i]:
                        row[i] = 'NULL'
                    else:
                        sv = fm.get('is_multiple', {}).get('list_to_ui', ', ')
                        row[i] = sv.join([f'{k}:{v}' for k,v in row[i]])
        
        ViewCSVdataDialog(header, data, parent=self).exec()
