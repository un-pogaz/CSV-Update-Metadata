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
from enum import Enum
from functools import lru_cache
from typing import Callable, Dict, List, NamedTuple, Set, Tuple, Union

from calibre.db.cache import Cache
from calibre.db.fields import IdentifiersField, ManyToManyField, ManyToOneField, OneToOneField, SeriesField
from calibre.utils.date import is_date_undefined

from .common_utils import CALIBRE_VERSION, current_db, debug_print


class CSV(csv.Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    lineterminator = '\n'
    quoting = csv.QUOTE_ALL


class MatchType(Enum):
    insensitive_equal = _('Case insensitive equal')
    strict_equal = _('Case sensitive equal')


MATCHTYPE_TOOLTIP = '\n'.join((
    '<p>'+_('{}: Will match the complete value seeked, regardeless of the case. Calibre default behavior.')
        .format(MatchType.insensitive_equal._value_),
    '<p>'+_('{}: Strictly match the value seeked, with taking account of the case.')
        .format(MatchType.strict_equal._value_),
))


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
    match_type: MatchType = MatchType.insensitive_equal


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


def get_seekable_fields() -> Set[str]:
    db = current_db()
    fm = db.field_metadata
    rslt = get_all_fields()
    exclude = set()
    exclude.add('library_name')
    for field in rslt:
        if field in fm and fm[field]['datatype'] == 'datetime':
            exclude.add(field)
    return rslt.difference(exclude)


def get_writable_fields() -> Set[str]:
    rslt = get_all_fields()
    for n in ('library_name', 'id', 'uuid', 'size', 'pages'):
        rslt.discard(n)
    return rslt


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
    from .action import CSV
    
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


def _get_case_func(case_sensitive: bool) -> Tuple[Callable[[str], bytes], Callable[[str, str], int]]:
    from calibre.utils.icu import case_sensitive_sort_key, case_sensitive_strcmp, sort_key, strcmp
    
    if case_sensitive:
        return case_sensitive_sort_key, case_sensitive_strcmp
    return sort_key, strcmp


def _book_ids_identifiers(db: Cache, field: str, values: List[Dict[str, str]], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    key, cmp = _get_case_func(case_sensitive)
    rslt = []
    for iden in values:
        # map for this identifiers list
        rslt_map = defaultdict(dict)
        for k,v in iden.items():
            rslt_map[k][key(v)] = []
            all_ids = field_table.table.col_book_map[k]
            for i in all_ids:
                vv = field_table.table.book_col_map[i][k]
                kk = key(vv)
                if kk in rslt_map[k]:
                    rslt_map[k][kk].append(i)
        
        # filter intersection
        all_ids = set()
        for v in rslt_map.values():
            for vv in v.values():
                all_ids.update(vv)
        for v in rslt_map.values():
            for vv in v.values():
                if vv:
                    all_ids.intersection_update(vv)
        
        rslt.append(sorted(all_ids))
    return rslt


def _book_ids_series(db: Cache, field: str, values: List[str], case_sensitive: bool, series_with_index: bool) -> List[List[int]]:
    indexs = []
    if series_with_index:
        indexs = [None for x in range(len(values))]
        for i,v in enumerate(values):
            sn, si = _split_series_with_index(v)
            values[i] = sn
            indexs[i] = si
    
    item_map = db.get_item_ids(field, values, case_sensitive=case_sensitive)
    rslt = []
    for v in values:
        item_ids = db.books_for_field(field, item_map[v])
        rslt.append(sorted(item_ids or []))
    if not series_with_index:
        return rslt
    
    for i,v in enumerate(indexs):
        def f(x):
            return db.field_for(f'{field}_index', x) == v
        rslt[i] = list(filter(f, rslt[i]))
    return rslt


def _book_ids_numeric(db: Cache, field: str, values: List) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, OneToOneField):
        raise TypeError(f'Target field {field!r} is not a {OneToOneField.__name__!r}')
    
    rslt = [[] for x in range(len(values))]
    lst = set(values)
    def f(x):
        return x[1] in lst
    
    for k,v in filter(f, field_table.table.book_col_map.items()):
        rslt[values.index(v)].append(k)
    return rslt


def get_book_ids(field: str, values: List, match_type: MatchType, series_with_index: bool) -> List[List[int]]:
    db = current_db().new_api
    
    case_sensitive = match_type == MatchType.strict_equal
    
    if field == 'id':
        return [list(filter(db.has_id, (i,))) for i in values]
    if field == 'uuid':
        def f(x):
            return db.lookup_by_uuid(x.lower())
        return [list(filter(None, (u,))) for u in map(f, values)]
    if field == 'isbn':
        tbl = [{'isbn': v for v in values}]
        return _book_ids_identifiers(db, 'identifiers', tbl, case_sensitive=case_sensitive)
    
    field_table = db.fields[field]
    if isinstance(field_table, IdentifiersField):
        return _book_ids_identifiers(db, field, values, case_sensitive=case_sensitive)
    if isinstance(field_table, SeriesField):
        return _book_ids_series(db, field, values, case_sensitive=case_sensitive, series_with_index=series_with_index)
    
    fm = db.field_metadata[field]
    dt = fm['datatype']
    
    if dt in {'int', 'float', 'bool'}:
        return _book_ids_numeric(db, field, values)
    
    raise NotImplementedError(f'{field!r} is not supported.')


def update_library_data(data: CSVdata):
    if not data or not data.header or not data.rows or len(data.header) < 2:
        return
    
    db = current_db().new_api
    book_id = get_book_ids(
        data.header[0],
        [r[0] for r in data.rows],
        data.match_type,
        data.series_with_index,
    )
    # db.set_field(self, name, book_id_to_val_map, allow_case_change=True, do_path_update=True)
