#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

from collections import defaultdict
from typing import Dict, List, Tuple

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
from calibre.utils.icu import sort_key

from .backend import (
    CSV,
    NULL,
    CSVdata,
    CSVdataError,
    MatchType,
    OptionsUpdate,
    build_update_map,
    build_update_row,
    export_csv_file,
    field_name,
    get_all_fields,
    get_seekable_fields,
    get_writable_fields,
    load_csv_file,
    parse_preview_data,
    parse_update_data,
    update_library_data,
)
from .common_utils import PLUGIN_NAME, PREFS_json, PREFS_library, current_db, get_gui, get_icon
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


class HtmlInfoDialog(Dialog):
    title: str
    header: str
    icon: str = PLUGIN_ICON
    
    def __init__(self, parent=None):
        Dialog.__init__(self,
            title=self.title,
            name='plugin.CSVMetadata:'+self.__class__.__name__,
            parent=parent,
        )
    
    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        l.addLayout(ImageTitleLayout(self.icon, self.header, self))
        self.body = HTMLDisplay(self)
        l.addWidget(self.body)
        self.rslt = []
        self.setup_html()
        self.body.setHtml('\n'.join(self.rslt))
        self.body.setOpenLinks(True)
        self.body.setOpenExternalLinks(True)
    
    def setup_html(self):
        return NotImplemented
    
    def html_builder(self, tag, content) -> str:
        if tag and content:
            return f'<{tag}>{content}</{tag}>'
        if tag:
            return f'<{tag}/>'
        if content:
            return content
        return ''
    
    def list_builder(self, *args) -> str:
        lines = '\n'.join([self.html_builder('li', a) for a in args])
        return self.html_builder('ul', ('\n'+lines+'\n').strip())
    
    def append(self, tag, content):
        self.rslt.append(self.html_builder(tag, content))


class CSVformatDialog(HtmlInfoDialog):
    title = _('CSV Format info')
    header = _('CSV format')
    
    def setup_html(self):
        import inspect
        lines, num = inspect.getsourcelines(CSV)
        csv_code = ''.join(lines)
        
        self.append('p', _('A comma-separated values (CSV) file is a delimited text file that uses a comma to separate values. '
                      'A CSV file stores tabular data in plain text. Each line of the file is a data record. '
                      'Each record consists of one or more values, separated by commas. '
                      'The use of the comma as a value separator is the source of the name for this file format.'))
        self.append('p', _('Due to the lack of a strict CSV specification, different applications produce subtly different '
                        'CSV file, but this plugin can tolerate some of such difference as input.'))
        self.append('p', _('The CSV format prefered by the plugin is the following:'))
        self.append('', self.list_builder(
            _('The entire file must be saved in the Unicode (UTF-8) encoding.'),
            _('The value delimiter (column separator) must be a single comma (not tab-separated or fixed width).'),
            _('The CSV require at least two columns.'),
            _('The CSV require at least two rows/lines:')+'\n'+self.list_builder(
                _('The first row must be a "header" row which contains the double-quoted textual name of each column.'),
                _('All rows after the first row must contain either textual values or empty within each and every column.'),
                _('All rows must have the same number of double-quoted textual columns as the "header" row.'),
            ),
            _('All values should preferably be double-quoted.')+'\n'+self.list_builder(
                _('To include a double-quote character inside a value, write two double-quote consecutively <code>""</code>.'),
            ),
            _('Leading and trailing spaces will be removed from each value automatically.'),
            _('Empty value will be skipped (no edit action).'),
            _('To indicate that you want <i>delete</i> a value, you should use the special keyword "NULL" (full case).'),
        ))
        self.append('hr', '')
        self.append('p', _('The plugin use the default library <code>csv</code> to import and convert files. '
                      'For reference, here the code of <code>csv.Dialect</code> class used:'))
        self.append('pre', csv_code)


class UpdateOptionHelp(HtmlInfoDialog):
    title = _('Update option help')
    header = _('Update option')
    icon = 'dialog_information.png'
    
    def setup_html(self):
        fm = current_db().field_metadata
        identifiers = field_name('identifiers', fm)
        
        self.append('p', _('Empty cell value will be skipped (no edit action).'))
        self.append('p', _('The cell with the special keyword "NULL" (full case) will <i>delete</i> the value.'))
        self.append('p', _('The field "{}" behave a bit differently, since it require a <code>id:value</code> pair '
                        'to represent a value and that a sole id without a value is ignored. Therefore, to delete '
                        'a identifier, you need to used "id:NULL" to delete this id.')
                        .format(identifiers=identifiers))
        self.append('hr', '')
        self.append('p', _('To update the metadata, you need to specified wich columns of CSV values the plugin need '
                        'to seek in the library, and wich field this values need to match, depending of several mode:'))
        self.append('', self.list_builder(*(
            _('<b>{}</b>: {}').format(e.label, e.description) for e in MatchType.__members__.values()
        )))
        self.append('p', _('If the cell of the seeked value is empty, the line is ignored.'))
        self.append('hr', '')
        self.append('p', _('The following options allow to control how to update some specific field. '
                        'Note that these options are global and are apply to the entire file.'))
        self.append('', self.list_builder(*(
            _('<b>{}</b>: {}').format(e.label, e.description) for e in OptionsUpdate.values()
        )))


class CSVMetadataAction(InterfaceAction):
    name = PLUGIN_NAME
    # Create our top-level menu/toolbar action (text, icon_path, tooltip, keyboard shortcut)
    action_spec = (PLUGIN_NAME, None, _('Update Metadata from a CSV file template'), None)
    popup_type = QToolButton.MenuButtonPopup
    action_type = 'current'
    dont_add_to = frozenset(['context-menu-device'])
    
    def genesis(self):
        self.menu = QMenu(get_gui())
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
        
        get_gui().keyboard.finalize()
    
    def toolbar_triggered(self):
        self.update_metadata()
    
    def show_csv_format(self):
        CSVformatDialog(get_gui()).exec()
    
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
                get_gui(),
                _('Malformed CSV format'),
                f'<p>{msg}\n'+
                f'<p><b>{err.__class__.__name__}:</b> {err}',
                show=True,
                show_copy_button=False,
            )
            return
        
        d = UpdateCSVdialog(path, data, parent=get_gui())
        rslt = d.exec()
        if rslt == Dialog.DialogCode.Accepted and d.output:
            UpdateDataProgress(*d.output)
    
    def export_metadata(self):
        ids = get_BookIds_selected(True)
        if not ids:
            return
        ExportCSVdialog(ids, get_gui()).exec()


class UpdateDataProgress(QProgressDialog):
    def __init__(self, data: CSVdata, options: OptionsUpdate):
        self.data = data
        self.options = options
        QProgressDialog.__init__(self, '', None, 0, 0, get_gui())
        self.setMinimumWidth(500)
        self.setMinimumHeight(100)
        self.setMinimumDuration(100)
        self.setWindowIcon(get_icon(PLUGIN_ICON))
        self.setWindowTitle(_('Updating data from CSV'))
        self.setLabelText(_('Updating Library…'))
        self.setValue(-1)
        self.show()
        QTimer.singleShot(1, self.run_job)
        self.exec()

    def run_job(self):
        try:
            update_row = build_update_row(self.data, self.options)
            update_map = build_update_map(update_row, self.options)
            update_ids = update_library_data(update_map, self.options)
        finally:
            self.close()


def pick_csv_to_load(parent=None) -> str:
    archives = choose_files(parent or get_gui(),
        name='csv dialog',
        title=_('Select a CSV file to load…'),
        filters=[('CSV Files', ['csv'])],
        all_files=False, select_only_single_file=True,
    )
    if not archives:
        return None
    return archives[0]


def pick_csv_to_export(parent=None) -> str:
    fd = FileDialog(parent=parent or get_gui(),
        name='csv dialog',
        title=_('Export CSV file as…'),
        filters=[('CSV Files', ['csv'])],
        add_all_files_filter=False, mode=QFileDialog.FileMode.AnyFile,
    )
    fd.setParent(None)
    if not fd.accepted:
        return None
    return fd.get_files()[0]


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
        state = get_gui().library_view.get_state()
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
        options: OptionsUpdate = None,
        validate: bool=False,
        has_reference: bool=False,
        parent=None,
    ):
        self.data = data
        self.options = options
        self.validate = validate
        self.has_reference = has_reference
        Dialog.__init__(self,
            title=_('View CSV content data'),
            name='plugin.CSVMetadata:ViewCSVdataDialog',
            parent=parent,
        )

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.setLayout(l)
        
        opt = []
        if self.options:
            opt.append(self.options.match_type.label)
            for k,v in OptionsUpdate.items():
                if getattr(self.options, k):
                    opt.append(v.label)
        
        if opt:
            label = QLabel(_('Update options:')+' '+ ', '.join(opt))
            label.setWordWrap(True)
            l.addWidget(label)
        
        t = QTableWidget(self)
        t.setAlternatingRowColors(True)
        t.setSelectionMode(QTableWidget.ExtendedSelection)
        t.setSortingEnabled(False)
        t.setMinimumSize(400, 200)
        l.addWidget(t)
        
        t.setColumnCount(len(self.data.headers))
        t.setHorizontalHeaderLabels([(h if h else f'[{i}]') for i,h in enumerate(self.data.headers, 1)])
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
                    if not data:
                        pass
                    elif data == NULL:
                        item_style(item, italic=True)
                        item.setToolTip('<i>'+NULL)
                    else:
                        item.setToolTip(
                            '<p style="white-space:pre">'+
                            data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        )
                t.setItem(idr, idc, item)
        if self.has_reference:
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


class UpdateCSVdialog(Dialog):
    def __init__(self, csv_path: str, data: CSVdata, parent=None):
        self.csv_path = csv_path
        self.csv_data = data
        self.csv_widget: Dict[int, KeyValueComboBox] = {}
        self.output: Tuple[CSVdata, OptionsUpdate] = None
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
        
        self.button_show_help = QPushButton(get_icon('dialog_information.png'), '', self)
        self.button_show_help.setToolTip(_('Show the help about the options and the update policies.'))
        self.button_show_help.clicked.connect(self.show_option_help)
        
        button_layout.addStretch()
        button_layout.addWidget(self.button_show_help)
        button_layout.addWidget(self.button_raw_data)
        button_layout.addWidget(self.button_reload_data)
        button_layout.addStretch()
        
        fm = current_db().field_metadata
        seekable_fields = dict(sorted(
            ((n,field_name(n, fm)) for n in get_seekable_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        self.writable_fields = {'':''}
        self.writable_fields.update(sorted(
            ((n,field_name(n, fm)) for n in get_writable_fields()),
            key=lambda x:sort_key(x[1]),
        ))
        
        self.reference_header = NoWheelComboBox(self)
        self.reference_field = KeyValueComboBox(seekable_fields, parent=self)
        self.reference_field.setCurrentIndex(-1)
        
        self.match_type = KeyValueComboBox({e:e.label for e in MatchType.__members__.values()}, self)
        self.match_type.currentIndexChanged.connect(self.match_type_changed)
        
        self.reference_selector = rs = QFormLayout()
        rs.addRow(_('CSV column to seek:'), self.reference_header)
        rs.addRow(_('Book field to match:'), self.reference_field)
        self.reference_field_row = rs.rowCount()-1
        rs.addRow(_('Book match mode:'), self.match_type)
        l.addLayout(rs)
        
        fl = FlowLayout()
        l.addLayout(fl)
        
        self.options = {}
        for k,v in OptionsUpdate.items():
            self.options[k] = w = QCheckBox(v.label, self)
            fl.addWidget(w)
        
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
                get_gui(),
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
        self.button_raw_data.setText(' '+_('Column: {} | Row: {}').format(len(self.csv_data.headers), len(self.csv_data.rows)))
        
        all_headers = {i:f'[{i+1}] {h}' for i,h in enumerate(self.csv_data.headers)}
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

    def show_option_help(self):
        UpdateOptionHelp(self).exec()

    def match_type_changed(self, idx):
        hide = self.match_type.selected_key() not in MatchType.no_book_field()
        self.options['tags_contain_value'].setVisible(hide)
        self.reference_selector.setRowVisible(self.reference_field_row, hide)

    def get_data_update_map(self) -> Tuple[CSVdata, OptionsUpdate]:
        if not self.csv_data.headers or not self.csv_data.rows:
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
        
        no_book_field = self.match_type.selected_key() in MatchType.no_book_field()
        if not no_book_field and self.reference_field.currentIndex() == -1:
            error_dialog(
                self,
                _('No reference field selected'),
                _('Select a reference field to seek the books to update.'),
                show=True,
                show_copy_button=False,
            )
            return None
        
        fm = current_db().field_metadata
        fields_map = []
        data_map = []
        
        data_map.append(self.reference_header.currentIndex())
        if no_book_field:
            field = self.match_type.selected_key()
        else:
            field = self.reference_field.selected_key()
        fields_map.append((field, self.reference_header.currentIndex()))
        
        for i,w in self.csv_widget.items():
            if k := w.selected_key():
                if k in {kv[0] for kv in fields_map[1:]}:
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
                fields_map.append((k, i))
        
        options_map = {k:v.isChecked() for k,v in self.options.items()}
        options_map['match_type'] = self.match_type.selected_key()
        options = OptionsUpdate(**options_map)
        data = parse_update_data(
            self.csv_data,
            fields_map,
            options,
        )
        return data, options

    def accept(self):
        rslt = self.preview_update_data(validate=True)
        if not rslt or rslt[0].errors:
            return
        self.output = rslt
        Dialog.accept(self)

    def preview_update_data(self, validate: bool=False) ->  Tuple[CSVdata, OptionsUpdate]:
        rslt = self.get_data_update_map()
        if not rslt:
            return None
        data, options = rslt
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
        
        d = ViewCSVdataDialog(
            parse_preview_data(data),
            options,
            validate=validate,
            has_reference=True,
            parent=self,
        ).exec()
        if d != Dialog.DialogCode.Accepted or data.errors:
            return None
        return data, options
