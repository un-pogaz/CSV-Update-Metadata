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
from enum import Enum, auto
from functools import lru_cache
from typing import Callable, Dict, List, NamedTuple, Set, Tuple, Union

from calibre.db.cache import Cache
from calibre.db.fields import IdentifiersField, ManyToManyField, ManyToOneField, OneToOneField, SeriesField
from calibre.utils.date import is_date_undefined
from calibre.utils.icu import lower as icu_lower

from .common_utils import CALIBRE_VERSION, current_db, debug_print, get_gui

DataCell = Union[str, int, float, bool, datetime, Tuple[str, ...], Dict[str, Union[str, None]], None]

NULL = 'NULL'


class CSV(csv.Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    lineterminator = '\n'
    quoting = csv.QUOTE_ALL


class MatchType(Enum):
    insensitive_equal = auto()
    strict_equal = auto()
    search = auto()
    
    @staticmethod
    def _data() -> Dict['MatchType', Tuple[str, str]]:
        return {
            MatchType.insensitive_equal: (
                _('Case insensitive equal'),
                _('Will match the complete seeked value, regardeless of the case. Calibre default behavior.'),
            ),
            MatchType.strict_equal: (
                _('Case sensitive equal'),
                _('Strictly match the seeked value, with taking account of the case.'),
            ),
            MatchType.search: (
                _('Calibre search'),
                _('Use the <a href="{}">Calibre search engine</a>. No book field to match is required for this mode.')
                .format('https://manual.calibre-ebook.com/gui.html#the-search-interface'),
            ),
        }
    
    @property
    def label(self) -> str:
        return MatchType._data()[self][0]
    
    @property
    def description(self) -> str:
        return MatchType._data()[self][1]
    
    @staticmethod
    def no_book_field() -> Set['MatchType']:
        return {MatchType.search,}


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
    headers: List[str]
    rows: List[List[DataCell]]
    errors: List[CSVdataError] = None


class OptionsUpdate(NamedTuple):
    class Item(NamedTuple):
        label: str
        description: str
    
    series_with_index: bool = False
    tags_contain_value: bool = False
    append_tags_value: bool = False
    dont_change_case: bool = False
    
    match_type: MatchType = MatchType.insensitive_equal
    
    @staticmethod
    def _data() -> Dict[str, Item]:
        return {
            'series_with_index': OptionsUpdate.Item(
                _('Series-type fields include index'),
                _('The value contain both the series name and the series index with the Calibre format "{}".')
                .format(_('&lt;Series name&gt; [&lt;Index&gt;]')),
            ),
            'tags_contain_value': OptionsUpdate.Item(
                _('Tags-type contain the values'),
                _('When the plugin seek matching values on a Tags-type field, by default it only match books that have '
                'the <i>exact</i> same collection of values than the one in the CSV. This option allow to match any book '
                'that contain the seeked value, regardeless if the book have more values, instead of the exact collection.<br>'
                'For example, the CSV value "green, blue" will macth any book with the same value, but also "green, blue, red".'),
            ),
            'append_tags_value': OptionsUpdate.Item(
                _('Append value for tags-type fields'),
                _('By default, the plugin completely replace the values of the tags-type fields by the one provide in the CSV. '
                'This option allow to merge the CSV value to the one inside the library.'),
            ),
            'dont_change_case': OptionsUpdate.Item(
                _('Do not change the case if existing'),
                _('When updating a value that it similar to a value already existing in the library, '
                'if the values are textualy the same but only the case is different, by default '
                'the plugin will replace the case of the library value by the one in the CSV. '
                'This option have to purpose to preserve the case of the libray version '
                'of the value instead to use the CSV one.'),
            ),
        }
    
    @staticmethod
    def items() -> List[Tuple[str, Item]]:
        yield from OptionsUpdate._data().items()
    
    @staticmethod
    def values() -> List[Item]:
        yield from OptionsUpdate._data().values()


class UpdateDataRow(NamedTuple):
    seek: DataCell
    data: Dict[str, DataCell]
    book_ids: Set[int]


def get_all_fields() -> Set[str]:
    from calibre.library.catalogs import FIELDS
    db = current_db()
    fm = db.field_metadata
    rslt = set(FIELDS)
    rslt.difference_update(('all', 'ondevice', 'formats', 'cover'))
    for field in db.custom_field_keys():
        if fm[field]['datatype'] == 'composite':
            continue
        rslt.add(field)
        if fm[field]['datatype'] == 'series':
            rslt.add(field + '_index')
    if CALIBRE_VERSION >= (9,00,0):
        rslt.add('pages')
    return rslt


def get_seekable_fields() -> Set[str]:
    db = current_db()
    fm = db.field_metadata
    rslt = get_all_fields()
    exclude = set()
    exclude.add('library_name')
    exclude.add('comments')
    for field in rslt:
        if field not in fm:
            continue
        if fm[field]['datatype'] == 'datetime':
            exclude.add(field)
        if fm[field]['datatype'] == 'comments' and fm[field]['display'].get('interpret_as') != 'short-text':
            exclude.add(field)
    return rslt.difference(exclude)


def get_writable_fields() -> Set[str]:
    rslt = get_all_fields()
    return rslt.difference(('library_name', 'id', 'uuid', 'size', 'pages'))


def get_special_field() -> Tuple[Set[str], Set[str], Set[str]]:
    db = current_db().new_api
    series_field = set()
    tags_field = set()
    identifiers_field = set()
    for k,v in db.fields.items():
        if isinstance(v, SeriesField):
            series_field.add(k)
        if isinstance(v, ManyToManyField):
            tags_field.add(k)
        if isinstance(v, IdentifiersField):
            identifiers_field.add(k)
    return series_field, tags_field.difference(identifiers_field), identifiers_field


def field_name(field: str, field_metadata: Dict) -> str:
    if isinstance(field, MatchType):
        return field.label
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


series_index_pat = re.compile(r'(.+)\s+\[([0-9]+(\.[0-9]+)?)\]')


def _split_series_with_index(x):
    from calibre.db.write import adapt_series_index, single_text
    
    x = single_text(x)
    if not x:
        return None
    
    m = series_index_pat.fullmatch(x)
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


def get_adapter(name: str, field_metadata: Dict, *, series_with_index=False) -> Callable[[str], DataCell]:
    from calibre.db.write import get_adapter
    
    if name == MatchType.search:
        def ans(x):
            if not x:
                return None
            x = x.strip()
            if not x:
                return None
            return x
        return ans
    
    if name == 'isbn':
        metadata = defaultdict(dict)
        adapter =  _parse_isbn
    else:
        metadata = field_metadata[name]
        if metadata['datatype'] == 'series' and series_with_index:
            adapter = _series_with_index
        else:
            adapter = get_adapter(name, metadata)
    
    def ans(x):
        if x == '':
            return ''
        elif x == NULL:
            return None
        
        rslt = adapter(x)
        if metadata['datatype'] == 'rating' and rslt == 0:
            return None
        if isinstance(rslt, (list, tuple, set, dict)) and not rslt:
            return None
        if isinstance(rslt, datetime) and is_date_undefined(rslt):
            return None
        if name == 'authors' and rslt == unknown_author():
            return None
        if name == 'author_sort' and not rslt:
            return None
        if isinstance(rslt, (list, tuple, set)):
            return tuple(rslt)
        if isinstance(rslt, dict):
            for k,v in rslt.items():
                if v == NULL or not v:
                    rslt[k] = None
        return rslt
    return ans


def load_csv_file(csv_path: str, validate: bool=True, sanitize: bool=True) -> CSVdata:
    '''
    Load a CSV file from the given path.
    
    validate: perform additional check of the content (2 columns, 2 rows, no empty header)
    sanitize:
        1) ensure that the content is a "rectangle table" (every row as the header length)
        2) skip empty blank lines (0 value)
        3) strip headers and values
    '''
    from .action import CSV
    
    # pass the file reader without newline detection
    # csv.reader handle universal newline sheniganies
    with open(csv_path, newline='', encoding='utf-8') as f:
        raw = list(csv.reader(f, CSV))
    
    if not raw:
        raise ValueError(_('The input CSV is empty.'))
    
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
    from .action import CSV
    
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


def parse_update_data(csv_data: CSVdata, fields_map: List[Tuple[str, int]], options: OptionsUpdate) -> CSVdata:
    x, tags_field, identifiers_field = get_special_field()
    fm = current_db().field_metadata
    data = []
    errors = []
    adapters = {f:get_adapter(f, fm, series_with_index=options.series_with_index) for f,c in fields_map}
    for r,row in enumerate(csv_data.rows):
        tbl = []
        for f,c in fields_map:
            try:
                tbl.append(adapters[f](row[c]))
            except Exception as err:
                tbl.append(CSVdataError(
                    c+1, r+1,
                    csv_data.headers[c],
                    field_name(f, fm),
                    err,
                ))
        
        for i,v in enumerate(tbl):
            if v:
                continue
            f, c = fields_map[i]
            if f in identifiers_field:
                v = ''
            if f in tags_field and options.append_tags_value:
                v = ''
            tbl[i] = v
        
        if tbl.count('') == len(tbl)-1:
            # skip empty row to update
            continue
        
        if isinstance(tbl[0], (int, float, bool)) or tbl[0]:
            data.append(tbl)
            errors.extend(e for e in tbl if isinstance(e, CSVdataError))
    
    return CSVdata(
        [f for f,c in fields_map],
        data,
        errors=errors,
    )


def parse_preview_data(data: CSVdata) -> CSVdata:
    fm = current_db().field_metadata
    pre_header = [field_name(h, fm) for h in data.headers]
    pre_data = []
    
    def map_dict(kv):
        k,v = kv
        if not v:
            v = NULL
        return f'{k}:{v}'
    
    for row in data.rows:
        tbl = []
        pre_data.append(tbl)
        for value in row:
            if value is None:
                value = NULL
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
                value = sv.join(map(map_dict, value.items()))
            if isinstance(value, str):
                value = value.strip()
            tbl.append(value)
    
    return data._replace(headers=pre_header, rows=pre_data)


def case_sensitive_key(case_sensitive: bool) -> Callable[[DataCell], Union[bytes, DataCell]]:
    if case_sensitive:
        return lambda x:x
    def f(x):
        if isinstance(x, str):
            return icu_lower(x)
        return x
    return f


def _book_ids_identifiers(db: Cache, field: str, rows: List[UpdateDataRow], case_sensitive: bool):
    field_table = db.fields[field]
    if not isinstance(field_table, IdentifiersField):
        raise TypeError(f'Target field to seek is not a {IdentifiersField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    rslt = []
    for r in rows:
        r.book_ids.clear()
        if not r.seek:
            continue
        
        # map for this identifiers list
        rslt_map = defaultdict(dict)
        for k,v in r.seek.items():
            rslt_map[k][key(v)] = []
            book_ids = field_table.table.col_book_map[k]
            for i in book_ids:
                vv = field_table.table.book_col_map[i][k]
                kk = key(vv)
                if kk in rslt_map[k]:
                    rslt_map[k][kk].append(i)
        
        # filter intersection
        book_ids = set()
        for v in rslt_map.values():
            for vv in v.values():
                book_ids.update(vv)
        for v in rslt_map.values():
            for vv in v.values():
                if vv:
                    book_ids.intersection_update(vv)
        
        r.book_ids.update(book_ids)


def _book_ids_series(db: Cache, field: str, rows: List[UpdateDataRow], case_sensitive: bool, series_with_index: bool):
    indexs = []
    values = rows
    if series_with_index:
        values = []
        indexs = []
        for r in rows:
            sn, si = _split_series_with_index(r.seek)
            values.append(UpdateDataRow(seek=sn, data={}, book_ids=set()))
            indexs.append(si)
    
    _book_ids_many_one(db, field, values, case_sensitive=case_sensitive)
    if not series_with_index:
        return
    
    for i,v,r in zip(indexs, values, rows):
        r.book_ids.clear()
        def f(x):
            return db.field_for(f'{field}_index', x) == i
        r.book_ids.update(filter(f, v.book_ids))


def _book_ids_one_one(db: Cache, field: str, rows: List[UpdateDataRow], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, OneToOneField):
        raise TypeError(f'Target field {field!r} is not a {OneToOneField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    # registre the values
    value_map = {}
    for r in rows:
        value_map[key(r.seek)] = []
    
    if field == 'series_index':
        bcm = db.fields['series'].table.book_col_map
        def f(kv):
            return kv[0] in bcm and key(kv[1]) in value_map
    else:
        def f(kv):
            return key(kv[1]) in value_map
    
    for k,v in filter(f, field_table.table.book_col_map.items()):
        value_map[key(v)].append(k)
    
    for r in rows:
        r.book_ids.clear()
        r.book_ids.update(value_map[key(r.seek)])


def _book_ids_many_one(db: Cache, field: str, rows: List[UpdateDataRow], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, ManyToOneField):
        raise TypeError(f'Target field {field!r} is not a {ManyToOneField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    rmap = {key(v): k for k, v in field_table.table.id_map.items()}
    item_map = {r.seek: rmap.get(key(r.seek), None) for r in rows}
    
    for r in rows:
        r.book_ids.clear()
        book_ids = field_table.table.col_book_map.get(item_map[r.seek], set())
        r.book_ids.update(book_ids)


def _book_ids_many_many(db: Cache, field: str, rows: List[UpdateDataRow], case_sensitive: bool, tags_contain_value: bool):
    field_table = db.fields[field]
    if not isinstance(field_table, ManyToManyField):
        raise TypeError(f'Target field {field!r} is not a {ManyToManyField.__name__!r}')
    
    flat_values = set()
    for r in rows:
        flat_values.update(r.seek)
    
    key = case_sensitive_key(case_sensitive)
    rmap = {key(v): k for k, v in field_table.table.id_map.items()}
    item_map = {v: rmap.get(key(v), None) for v in flat_values}
    
    cbm = field_table.table.col_book_map
    bcm = field_table.table.book_col_map
    for r in rows:
        r.book_ids.clear()
        if not r.seek:
            continue
        
        # filter intersection
        book_ids = set()
        for t in r.seek:
            book_ids.update(cbm.get(item_map[t], set()))
        for t in r.seek:
            book_ids.intersection_update(cbm.get(item_map[t], set()))
        
        # filter exact collections
        if tags_contain_value:
            f = None
        else:
            def f(x):
                return len(bcm.get(x, ())) == len(r.seek)
        
        r.book_ids.update(filter(f, book_ids))


def _book_ids_search(db: Cache, rows: List[UpdateDataRow]):
    for r in rows:
        r.book_ids.clear()
        if not r.seek:
            continue
        r.book_ids.update(db.search(r.seek))


def process_book_ids(field: str, rows: List[UpdateDataRow], options: OptionsUpdate):
    db = current_db().new_api
    
    if field == MatchType.search:
        _book_ids_search(db, rows)
        return
    
    case_sensitive = options.match_type == MatchType.strict_equal
    
    if field == 'id':
        for r in rows:
            if db.has_id(r.seek):
                r.book_ids.add(r.seek)
        return
    if field == 'uuid':
        for r in rows:
            id = db.lookup_by_uuid(r.seek.lower())
            if id:
                r.book_ids.add(id)
        return
    if field == 'isbn':
        tbl = [r._replace(seek={'isbn': r.seek}) for r in rows]
        _book_ids_identifiers(db, 'identifiers', tbl, case_sensitive=case_sensitive)
        return
    
    field_table = db.fields[field]
    if isinstance(field_table, IdentifiersField):
        _book_ids_identifiers(db, field, rows, case_sensitive=case_sensitive)
        return
    if isinstance(field_table, SeriesField):
        _book_ids_series(db, field, rows, case_sensitive=case_sensitive, series_with_index=options.series_with_index)
        return
    
    fm = db.field_metadata[field]
    dt = fm['datatype']
    
    if dt in {'int', 'float', 'bool'}:
        _book_ids_one_one(db, field, rows, case_sensitive=None)
        return
    
    if dt in {'rating'}:
        _book_ids_many_one(db, field, rows, case_sensitive=None)
        return
    if dt in {'enumeration'}:
        _book_ids_many_one(db, field, rows, case_sensitive=case_sensitive)
        return
    
    if dt == 'text':
        if isinstance(field_table, ManyToOneField):
            _book_ids_many_one(db, field, rows, case_sensitive=case_sensitive)
            return
        if isinstance(field_table, OneToOneField):
            _book_ids_one_one(db, field, rows, case_sensitive=case_sensitive)
            return
        if isinstance(field_table, ManyToManyField):
            _book_ids_many_many(db, field, rows, case_sensitive=case_sensitive, tags_contain_value=options.tags_contain_value)
            return
    
    raise NotImplementedError(_('The type of the field {!r} is not supported.').format(field))


def build_update_row(data: CSVdata, options: OptionsUpdate) -> List[UpdateDataRow]:
    if not data or not data.headers or not data.rows or len(data.headers) < 2:
        return []
    
    def f(headers, data_row):
        data = {}
        for i, cell in enumerate(data_row):
            if i == 0:
                continue
            if cell == '':
                continue
            data[headers[i]] = cell
        return UpdateDataRow(
            seek=data_row[0],
            data=data,
            book_ids=set(),
        )
    
    update_data = [f(data.headers, r) for r in data.rows]
    process_book_ids(data.headers[0], update_data, options)
    return update_data


def build_update_map(data: List[UpdateDataRow], options: OptionsUpdate) -> Dict[str, Dict[str, DataCell]]:
    db = current_db().new_api
    append_tags_value = options.append_tags_value
    
    x, tags_field, identifiers_field = get_special_field()
    update_map = defaultdict(dict)
    
    def update(book_id, field, value):
        if field in identifiers_field:
            src = db.field_for(field, book_id)
            src.update(value)
            value = src
        
        if field in tags_field and append_tags_value:
            src = db.field_for(field, book_id)
            if field == 'authors':
                if src == unknown_author():
                    src = ()
                if value == unknown_author():
                    value = ()
            lst = list(src)
            for a in value:
                if a not in lst:
                    lst.append(a)
            value = tuple(lst)
            
        update_map[field][book_id] = value
    
    for row in data:
        for id in sorted(row.book_ids):
            for k,v in row.data.items():
                update(id, k, v)
    
    return update_map


def update_library_data(update_map: Dict[str, Dict[str, DataCell]], options: OptionsUpdate) -> Set[int]:
    db = current_db().new_api
    allow_case_change = not options.dont_change_case
    series_with_index = options.series_with_index
    
    series_field, x, x = get_special_field()
    # ensure that the series index are updated after the series name
    update_index_map = uim = defaultdict(dict)
    for series in series_field:
        index = f'{series}_index'
        update_index_map[index] = update_map.pop(index, {})
    
    # series field allway try to parse with index format
    # sanitize the value if contain bracket []
    if not series_with_index:
        for field, value_map in update_map.items():
            if field not in series_field:
                continue
            index = f'{series}_index'
            for k,v in filter(lambda x: '[' in x[1]), value_map.items():
                i = uim[index].get(k)
                if i is None:
                    i = db.field_for(index, k)
                if i is None:
                    i = 1.0
                value_map[k] = f'{v} [{i}]'
    
    update_map = {k:v for k,v in update_map.items() if v}
    update_index_map = {k:v for k,v in update_index_map.items() if v}
    
    updated_ids = set()
    with db.write_lock, db.backend.conn:
        for field, value_map in update_map.items():
            db.set_field(field, value_map, allow_case_change=allow_case_change, do_path_update=True)
        for field, value_map in update_index_map.items():
            db.set_field(field, value_map, allow_case_change=allow_case_change, do_path_update=True)
    
    gui = get_gui()
    if gui:
        gui.iactions['Edit Metadata'].refresh_gui(updated_ids, covers_changed=False)
    return updated_ids
