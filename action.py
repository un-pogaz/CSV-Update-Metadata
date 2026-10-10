#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Callable, Dict, List, NamedTuple, Set

try:
    from qt.core import (
        QCheckBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QProgressDialog,
        QPushButton,
        QScrollArea,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QTextBrowser,
        QTimer,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt5.Qt import (
        QCheckBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QProgressDialog,
        QPushButton,
        QScrollArea,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QTextBrowser,
        QTimer,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

from calibre.constants import ismacos
from calibre.gui2 import FileDialog, choose_files, error_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.widgets2 import Dialog, FlowLayout, HTMLDisplay
from calibre.utils.date import is_date_undefined
from calibre.utils.icu import sort_key

from .common_utils import CALIBRE_VERSION, GUI, PLUGIN_NAME, PREFS_json, PREFS_library, current_db, debug_print, get_icon
from .common_utils.librarys import get_BookIds_selected, no_launch_error
from .common_utils.menus import create_menu_action_unique
from .common_utils.widgets import ImageTitleLayout, KeyValueComboBox, NoWheelComboBox

PLUGIN_ICON = 'images/plugin.png'

# This is where all preferences for this plugin are stored
PREFS = PREFS_json()

LIBRARY_PREFS = PREFS_library()
LIBRARY_PREFS.defaults['sort_order'] = {'id':0, 'authors':1, 'series':2, 'series_index':3, 'title':4}
LIBRARY_PREFS.defaults['fields'] = ['id', 'authors', 'series', 'series_index', 'title']
LIBRARY_PREFS.defaults['series_with_index'] = False


class CSV(csv.Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    lineterminator = '\n'
    quoting = csv.QUOTE_ALL


@dataclass
class CSVdataError:
    column: int
    line: int
    header: str
    field: str
    exc: Exception
    
    @property
    def exc_text(self):
        return f'{self.exc.__class__.__name__}: {self.exc}'


class CSVdata(NamedTuple):
    header: List[str]
    rows: List[List[str]]
    errors: List[CSVdataError] = None
    has_reference: bool = False
    series_with_index: bool = False
    append_tags_value: bool = False


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
        append('p', _('Due to the lack of a strict CSV specification, different applications produce subtly different CSV file, '
                      'but this plugin can tolerate some of such difference as input.'))
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
            data = load_csv_file(path)
        except Exception as err:
            msg = '<br>'.join([
                _('The selected CSV file fail to be loaded because is a malformed format.'),
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
        
        d = UpdateCSVdialog(path, data, parent=GUI)
        rslt = d.exec()
        if rslt == Dialog.DialogCode.Accepted and d.data:
            UpdateDataProgress(d.data)
    
    def export_metadata(self):
        ids = get_BookIds_selected(True)
        if not ids:
            return
        ExportCSVdialog(ids, GUI).exec()


class UpdateDataProgress(QProgressDialog):
    def __init__(self, data: CSVdata):
        self.data = data
        QProgressDialog.__init__(self, '', None, 0, 0, GUI)
        self.setMinimumWidth(500)
        self.setMinimumHeight(100)
        self.setMinimumDuration(100)
        self.setWindowIcon(get_icon(PLUGIN_ICON))
        self.setWindowTitle(_('Updating data from CSV'))
        self.setLabelText('Updating Library…')
        self.setValue(-1)
        self.show()
        QTimer.singleShot(1, self.run_job)
        self.exec()

    def run_job(self):
        update_library_data(self.data)
        self.close()


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
    fm = db.field_metadata
    rslt = {x for x in FIELDS if x not in {'all', 'ondevice', 'formats', 'cover'}}
    for field in db.custom_field_keys():
        if fm[field]['datatype'] == 'composite':
            continue
        rslt.add(field)
        if fm[field]['datatype'] == 'series':
            rslt.add(field + '_index')
    if CALIBRE_VERSION >= (9,00,0):
        rslt.add('pages')
    return rslt


def get_writable_fields() -> Set[str]:
    all_fields = get_all_fields()
    excluded = {'library_name', 'id', 'uuid', 'size', 'pages'}
    return {k for k in get_all_fields() if k not in excluded}


def field_name(field: str, field_metadata: Dict) -> str:
    name = None
    if field == 'isbn':
        name = 'ISBN'
    if field == 'uuid':
        name = 'UUID'
    if field == 'library_name':
        name = _('Library name')
    if field.endswith('_index'):
        field_serie = field[:-len('_index')]
        name = field_metadata.get(field_serie, {}).get('name')
        if name:
            name = name + ' ' + _('Index')
    if not name:
        name = field_metadata[field].get('name') or field
    return f'{name} ({field})'


def _parse_isbn(x):
    from calibre.db.write import single_text
    from calibre.ebooks.metadata import check_isbn
    
    x = single_text(x)
    if not x:
        return None
    
    rslt = check_isbn(x)
    if not rslt:
        return x
    return rslt


def _split_series_with_index(x):
    from calibre.db.write import adapt_series_index, single_text
    
    x = single_text(x)
    if not x:
        return None
    
    m = re.fullmatch(r'(.+)\s+\[(\d+(\.\d+))\]', x)
    if not m:
        raise ValueError(f'invalid value for series with index: {x!r}')
    return m.group(1), adapt_series_index(m.group(2))


def _series_with_index(x):
    x = _split_series_with_index(x)
    if not x:
        return None
    return f'{x[0]} [{x[1]}]'


@lru_cache(maxsize=2)
def unknown_author():
    from calibre.db.write import get_adapter
    metadata = {'datatype':'text', 'is_multiple': {'cache_to_list': ',', 'ui_to_list': '&', 'list_to_ui': ' & '}}
    return get_adapter('authors', metadata)(None)


def get_adapter(name: str, field_metadata: Dict, *, series_with_index=False) -> Callable:
    from calibre.db.write import get_adapter
    
    if name == 'isbn':
        return _parse_isbn
    metadata = field_metadata[name]
    if metadata['datatype'] == 'series' and series_with_index:
        return _series_with_index
    adapter = get_adapter(name, metadata)
    def f(x):
        rslt = adapter(x)
        if isinstance(rslt, (list, tuple, dict)) and not rslt:
            return None
        if isinstance(rslt, datetime) and is_date_undefined(rslt):
            return None
        if name == 'authors' and rslt == unknown_author():
            return None
        if name == 'author_sort' and not rslt:
            return None
        return rslt
    return f


def load_csv_file(csv_path: str, validate: bool=True, sanitize: bool=True) -> CSVdata:
    '''
    Load a CSV file from the given path.
    
    validate: perform additional check of the content (2 columns, 2 rows, no empty header)
    sanitize:
        1) ensure that the content is a "rectangle table" (every row as the header length)
        2) skip empty blank lines (0 value)
        3) strip headers and values
    '''
    # pass the file reader without newline detection
    # csv.reader handle universal newline sheniganies
    with open(csv_path, newline='', encoding='utf-8') as f:
        raw = list(csv.reader(f, CSV))
    
    header = raw[0]
    data = raw[1:]
    
    if validate:
        if not data:
            raise ValueError(_('The input CSV need at least 2 rows (one for the header and the others for the data).'))
        if len(header) < 2:
            raise ValueError(_('The input CSV need at least 2 columns (one has reference and the others for the data).'))
    
    def strip(val, join):
        return join.join(val.strip().splitlines())
    
    if sanitize:
        header = [strip(h, ' ') for h in header]
        data = [d for d in data if d]
        h = len(header)
        for i,row in enumerate(data):
            if len(row) < h:
                row.extend('' for x in range(h-len(row)))
            data[i] = [strip(e, '\n') for e in row[:h]]
    
    return CSVdata(header, data)


def export_csv_file(csv_path: str, fields: Dict[str, str], ids: List[int], *, series_with_index: bool=False) -> None:
    db = current_db().new_api
    with open(csv_path, 'w', encoding='utf-8', newline='\n') as f:
        writer = csv.writer(f, CSV)
        writer.writerow(fields.values())
        for id in ids:
            row = []
            mi = db.get_metadata(id)
            for field in fields.keys():
                value = mi.format_field(field, series_with_index=series_with_index)[1] or ''
                row.append(value.strip())
            writer.writerow(row)


def update_library_data(data: CSVdata):
    if not data or not data.header or not data.rows or len(data.header) < 2:
        return
    
    db = current_db().new_api


def item_style(item: QWidget, *, bold: bool=False, italic: bool=False):
    font = item.font()
    font.setBold(bold)
    font.setItalic(italic)
    item.setFont(font)


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
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.CopyAction if ismacos else Qt.DropAction.MoveAction)
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        
        l.addWidget(self.list)
        l.addWidget(QLabel(_('Drag and drop to re-arrange fields'), self))
        
        h = QHBoxLayout()
        l.addLayout(h)
        self.series_with_index = QCheckBox(_('Add the index to the series-type fields'), self)
        self.series_with_index.setChecked(LIBRARY_PREFS['series_with_index'])
        h.addWidget(self.series_with_index)
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
            name = field_name(field, fm)
            return (sort_order.get(field, 1000), sort_key(name)), name, field

        self.list.clear()
        for _x, name, field in sorted(map(key_buider, get_all_fields())):
            item = QListWidgetItem(name, self.list)
            item.setData(Qt.ItemDataRole.UserRole, field)
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
            field = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Unchecked if field in hidden else Qt.CheckState.Checked)

    def accept(self):
        sort_order = {}
        fields = {}
        for row in range(self.list.count()):
            item = self.list.item(row)
            field = item.data(Qt.ItemDataRole.UserRole)
            sort_order[field] = row
            if item.checkState() == Qt.CheckState.Checked:
                fields[field] = item.text()
        if not fields:
            return no_launch_error(_('No field selected'))
        
        file = pick_csv_to_export()
        if not file:
            return
        
        LIBRARY_PREFS['sort_order'] = sort_order
        LIBRARY_PREFS['fields'] = list(fields.keys())
        LIBRARY_PREFS['series_with_index'] = swi = self.series_with_index.isChecked()
        export_csv_file(file, fields, self.ids, series_with_index=swi)
        Dialog.accept(self)


class DataErrorViewer(QDialog):
    def __init__(self, errors: List[CSVdataError] = [], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('Invalid data to update'))
        self.setMinimumSize(600, 500)
        l = QVBoxLayout(self)
        self.setLayout(l)
        t = QTextBrowser(self)
        l.addWidget(t)
        
        rslt = defaultdict(list)
        for e in errors:
            k = (e.column, e.header, e.field)
            rslt[k].append((e.line, e.exc_text))
        
        msg = []
        for k in sorted(rslt.keys()):
            column, header, field = k
            msg.append(f'column: [{column}] {header}')
            msg.append(f'field: {field}')
            for (line, txt) in sorted(rslt[k]):
                msg.append(f'line: {line} :: {txt}')
            msg.append('\n')
        t.setPlainText('\n'.join(msg).strip())


class ViewCSVdataDialog(Dialog):
    def __init__(
        self,
        data: CSVdata,
        validate: bool=False,
        parent=None,
    ):
        self.data = data
        self.validate = validate or False
        Dialog.__init__(self,
            title=_('View CSV content data'),
            name='plugin.CSVMetadata:ViewCSVdataDialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        opt = []
        if self.data.series_with_index:
            opt.append(SERIES_INCLUDE_INDEX)
        if self.data.append_tags_value:
            opt.append(APPEND_TAGS_VALUE)
        
        if opt:
            fl = FlowLayout()
            l.addLayout(fl)
            fl.addWidget(QLabel('Update options:'))
            for o in opt:
                fl.addWidget(QLabel(o))
        
        t = QTableWidget(self)
        t.setAlternatingRowColors(True)
        t.setSelectionMode(QTableWidget.ExtendedSelection)
        t.setSortingEnabled(False)
        t.setMinimumSize(400, 200)
        l.addWidget(t)
        
        t.setColumnCount(len(self.data.header))
        t.setHorizontalHeaderLabels([(h if h else f'[{i}]') for i,h in enumerate(self.data.header)])
        t.verticalHeader().setDefaultSectionSize(24)
        
        t.setRowCount(len(self.data.rows))
        for idr,row in enumerate(self.data.rows):
            for idc,data in enumerate(row):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsEnabled)
                if isinstance(data, CSVdataError):
                    item.setText('ERROR')
                    msg = [
                        f'column: [{data.column}] {data.header}',
                        f'field: {data.field}',
                        f'line: {data.line}',
                        data.exc_text,
                    ]
                    item.setToolTip('\n'.join(msg))
                    item.setIcon(get_icon('dialog_error.png'))
                    item_style(item, italic=True)
                else:
                    item.setText(data)
                    item.setToolTip(
                        '<p style="white-space:pre">'+
                        data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    )
                if data == 'NULL':
                    item_style(item, italic=True)
                t.setItem(idr, idc, item)
        if self.data.has_reference:
            item_style(t.horizontalHeaderItem(0), italic=True)
            for r in range(t.rowCount()):
                item_style(t.item(r, 0), italic=True)
        
        if self.data.errors:
            btn = QPushButton(get_icon('dialog_error.png'), _('View errors'))
            btn.setMinimumWidth(100)
            btn.clicked.connect(self.show_data_error_viewer)
            h = QHBoxLayout()
            h.addWidget(btn)
            h.addStretch()
            l.addLayout(h)
        elif self.validate:
            l.addWidget(self.bb)
    
    def show_data_error_viewer(self):
        DataErrorViewer(self.data.errors, self).exec()
    
    def accept(self):
        rslt = question_dialog(
            self,
            _('Are you sure?'),
            _('Are you sure you want to update the library with this values? There is no undo.'),
        )
        if rslt:
            Dialog.accept(self)
        else:
            Dialog.reject(self)


SERIES_INCLUDE_INDEX = _('Series-type fields include index')
APPEND_TAGS_VALUE = _('Append value for tags-type fields')


class UpdateCSVdialog(Dialog):
    def __init__(self, csv_path: str, data: CSVdata, parent=None):
        self.csv_path = csv_path
        self.csv_data = data
        self.csv_widget: Dict[int, KeyValueComboBox] = {}
        self.data: CSVdata = None
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
        
        button_layout = QHBoxLayout()
        l.addLayout(button_layout)
        l.addSpacing(10)
        
        self.button_raw_data = QPushButton(get_icon(PLUGIN_ICON), '', self)
        self.button_raw_data.setToolTip(_('View the raw content of the loaded CSV file.'))
        self.button_raw_data.setMinimumWidth(200)
        self.button_raw_data.clicked.connect(self.view_raw_data)
        item_style(self.button_raw_data, bold=True)
        
        self.button_reload_data = QPushButton(get_icon('view-refresh.png'), '', self)
        self.button_reload_data.setToolTip(_('Reload the content from the CSV file.'))
        self.button_reload_data.clicked.connect(self.reload_data)
        
        button_layout.addStretch()
        button_layout.addWidget(self.button_raw_data)
        button_layout.addWidget(self.button_reload_data)
        button_layout.addStretch()
        
        fm = current_db().field_metadata
        all_fields = dict(sorted(
            ((n,field_name(n, fm)) for n in get_all_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        for n in ('library_name',):
            all_fields.pop(n, None)
        self.writable_fields = {'':''}
        self.writable_fields.update(sorted(
            ((n,field_name(n, fm)) for n in get_writable_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        
        self.reference_header = NoWheelComboBox(self)
        self.reference_field = KeyValueComboBox(all_fields, parent=self)
        self.reference_field.setCurrentIndex(-1)
        
        reference_selector = QFormLayout()
        reference_selector.addRow(_('CSV column to seek:'), self.reference_header)
        reference_selector.addRow(_('Book field to match:'), self.reference_field)
        l.addLayout(reference_selector)
        
        fl = FlowLayout()
        l.addLayout(fl)
        
        self.series_include_index = QCheckBox(SERIES_INCLUDE_INDEX, self)
        fl.addWidget(self.series_include_index)
        
        self.append_tags_value = QCheckBox(APPEND_TAGS_VALUE, self)
        fl.addWidget(self.append_tags_value)
        
        self.scroll = QScrollArea(self)
        l.addWidget(self.scroll)
        sc = QWidget()
        self.scroll.setWidget(sc)
        self.scroll.setWidgetResizable(True)
        layout = QVBoxLayout(self.scroll)
        sc.setLayout(layout)
        
        self.data_selector = QFormLayout()
        layout.addLayout(self.data_selector)
        layout.addStretch()
        
        button_layout = QHBoxLayout()
        l.addLayout(button_layout)
        
        self.button_preview_data = QPushButton(get_icon('search.png'), _('Preview update'))
        self.button_preview_data.clicked.connect(self.preview_data)
        self.button_update_data = QPushButton(get_icon('ok.png'), _('Update the metadata'))
        self.button_update_data.clicked.connect(self.accept)
        
        button_layout.addWidget(self.button_preview_data)
        button_layout.addStretch()
        button_layout.addWidget(self.button_update_data)
        
        self.populate()

    def reload_data(self):
        if not question_dialog(
            self,
            _('Are you sure?'),
            _('Are you sure you want to reload the CSV file?'),
            skip_dialog_name='plugin.CSVMetadata:reload_data',
        ):
            return
        try:
            data = load_csv_file(self.csv_path)
        except Exception as err:
            msg = '<br>'.join([
                _('The reload of the CSV file fail is a malformed format.'),
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
        self.csv_data = data
        self.populate()

    def populate(self):
        self.button_raw_data.setText(' '+_('Column: {} | Row: {}').format(len(self.csv_data.header), len(self.csv_data.rows)))
        
        all_headers = {i:f'[{i+1}] {h}' for i,h in enumerate(self.csv_data.header)}
        self.reference_header.clear()
        self.reference_header.addItems(all_headers.values())
        self.reference_header.setCurrentIndex(-1)
        
        self.reference_field.setCurrentIndex(-1)
        
        self.csv_widget.clear()
        while self.data_selector.rowCount() > 0:
            self.data_selector.removeRow(0)
        
        for idx, header in all_headers.items():
            field_out = KeyValueComboBox(self.writable_fields, parent=self.scroll)
            field_out.setCurrentIndex(-1)
            h = QHBoxLayout()
            h.addWidget(QLabel('⟹'))
            h.addWidget(field_out)
            self.data_selector.addRow(header, h)
            self.csv_widget[idx] = field_out

    def view_raw_data(self):
        ViewCSVdataDialog(self.csv_data, parent=self).exec()

    def preview_data(self):
        self.preview_update_data(validate=False)

    def get_data_update_map(self) -> CSVdata:
        if not self.csv_data.header or not self.csv_data.rows:
            error_dialog(
                self,
                _('Source CSV is empty'),
                _('The source CSV is empty.'),
                show=True,
                show_copy_button=False,
            )
            return None
        
        if self.reference_header.currentIndex() == -1:
            error_dialog(
                self,
                _('No reference header selected'),
                _('Select a reference header to seek the books to update.'),
                show=True,
                show_copy_button=False,
            )
            return None
        if self.reference_field.currentIndex() == -1:
            error_dialog(
                self,
                _('No reference field selected'),
                _('Select a reference field to seek the books to update.'),
                show=True,
                show_copy_button=False,
            )
            return None
        
        fm = current_db().field_metadata
        header, data = [], []
        errors = []
        data_map = []
        
        data_map.append(self.reference_header.currentIndex())
        header.append(self.reference_field.selected_key())
        
        for i,w in self.csv_widget.items():
            if k := w.selected_key():
                if k in header[1:]:
                    error_dialog(
                        self,
                        _('Duplicate updated field'),
                        _(
                            'The field {} is reference twice as destination for the updating, '
                            'and therefore cannot be reliably updated.'
                        ).format(field_name(k, fm)),
                        show=True,
                        show_copy_button=False,
                    )
                    return None
                header.append(k)
                data_map.append(i)
        
        swi = self.series_include_index.isChecked()
        adapters = [get_adapter(k, fm, series_with_index=swi) for k in header]
        r, f, c = 0, 0, 0
        for r,row in enumerate(self.csv_data.rows):
            tbl = []
            for f,c in enumerate(data_map):
                if row[c] == '':
                    tbl.append('')
                elif row[c] == 'NULL':
                    tbl.append(None)
                else:
                    try:
                        tbl.append(adapters[f](row[c]))
                    except Exception as err:
                        tbl.append(CSVdataError(
                            c, r,
                            self.csv_data.header[c],
                            field_name(header[f], fm),
                            err,
                        ))
            if isinstance(tbl[0], (int, float, bool)) or tbl[0]:
                data.append(tbl)
                errors.extend(e for e in tbl if isinstance(e, CSVdataError))
        
        return CSVdata(
            header,
            data,
            errors=errors,
            series_with_index=self.series_include_index.isChecked(),
            append_tags_value=self.append_tags_value.isChecked(),
        )

    def accept(self):
        self.data = self.preview_update_data(validate=True)
        if not self.data or self.data.errors:
            return
        Dialog.accept(self)

    def preview_update_data(self, validate: bool=False) -> CSVdata:
        data = self.get_data_update_map()
        if not data:
            return None
        if validate and data.errors:
            error_dialog(
                self,
                _('Invalid data to update'),
                _('The table of updated values contain {} errors. Use "{}" to see the details of them.').format(
                    len(data.errors),
                    self.button_preview_data.text(),
                ),
                show=True,
                show_copy_button=False,
            )
            return None
        
        fm = current_db().field_metadata
        pre_header = [field_name(h, fm) for h in data.header]
        pre_data = []
        for row in data.rows:
            tbl = []
            pre_data.append(tbl)
            for value in row:
                if value is None:
                    value = 'NULL'
                elif isinstance(value, (int, float, bool)):
                    value = str(value).lower()
                elif isinstance(value, datetime):
                    value = value.isoformat(' ')
                    value = value.replace('+00:00', '')
                    value = value.replace('00:00:00', '')
                elif isinstance(value, (list, tuple)):
                    sv = fm.get('is_multiple', {}).get('list_to_ui', ', ')
                    value = sv.join(value)
                elif isinstance(value, dict):
                    sv = fm.get('is_multiple', {}).get('list_to_ui', ', ')
                    value = sv.join([f'{k}:{v}' for k,v in value])
                if isinstance(value, str):
                    value = value.strip()
                tbl.append(value)
        
        d = ViewCSVdataDialog(
            data._replace(header=pre_header, rows=pre_data, has_reference=True),
            validate=validate,
            parent=self,
        ).exec()
        if d != Dialog.DialogCode.Accepted or data.errors:
            return None
        return data
