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
from itertools import chain
from typing import Callable, Dict, List, NamedTuple, Set, Tuple

from calibre.db.cache import Cache
from calibre.db.fields import IdentifiersField, ManyToManyField, ManyToOneField, OneToOneField, SeriesField
from calibre.utils.date import is_date_undefined
from calibre.utils.icu import lower as icu_lower

from .common_utils import CALIBRE_VERSION, current_db, debug_print


class CSV(csv.Dialect):
    delimiter = ','
    quotechar = '"'
    doublequote = True
    lineterminator = '\n'
    quoting = csv.QUOTE_ALL


class MatchType(Enum):
    insensitive_equal = auto()
    strict_equal = auto()
    # TODO: regex = auto()
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
            # MatchType.regex: (
            #     _('Regular expressions'),
            #     _('Uses <a href="{}">Python regular expressions</a>.')
            #     .format('https://docs.python.org/library/re.html#regular-expression-syntax'),
            # ),
            MatchType.search: (
                _('Calibre search'),
                _('Uses the <a href="{}">Calibre search engine</a>. No book field to match is required for this mode.')
                .format('https://manual.calibre-ebook.com/gui.html#the-search-interface'),
            ),
        }
    
    @property
    def label(self) -> str:
        return MatchType._data()[self][0]
    
    @property
    def description(self) -> str:
        return MatchType._data()[self][1]


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


class OptionsUpdate(NamedTuple):
    class Item(NamedTuple):
        label: str
        description: str
    
    series_with_index: bool = False
    tags_contain_value: bool = False
    append_tags_value: bool = False
    dont_change_case: bool = False
    
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


class CSVdata(NamedTuple):
    header: List[str]
    rows: List[List[str]]
    errors: List[CSVdataError] = None
    options: OptionsUpdate = OptionsUpdate()
    match_type: MatchType = MatchType.insensitive_equal


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
    if field == MatchType.search:
        return MatchType.search.label
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


series_index_pat = re.compile(r'(.+)\s+\[([0-9]+(\.[0-9]+))\]')


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


def get_adapter(name: str, field_metadata: Dict, *, series_with_index=False) -> Callable:
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
    series, tags, identifiers = get_special_field()
    fm = current_db().field_metadata
    data = []
    errors = []
    adapters = {f:get_adapter(f, fm, series_with_index=options.series_with_index) for f,c in fields_map}
    for r,row in enumerate(csv_data.rows):
        tbl = []
        for f,c in fields_map:
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
                        csv_data.header[c],
                        field_name(f, fm),
                        err,
                    ))
        
        for i,v in enumerate(tbl):
            if v:
                continue
            f, c = fields_map[i]
            if f in identifiers:
                v = ''
            if f in tags and options.append_tags_value:
                v = ''
            tbl[i] = v
        
        if isinstance(tbl[0], (int, float, bool)) or tbl[0]:
            data.append(tbl)
            errors.extend(e for e in tbl if isinstance(e, CSVdataError))
    
    return CSVdata(
        [f for f,c in fields_map],
        data,
        errors=errors,
        options=options,
    )


def parse_preview_data(data: CSVdata) -> CSVdata:
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
    
    return data._replace(header=pre_header, rows=pre_data)


def case_sensitive_key(case_sensitive: bool):
    if case_sensitive:
        return lambda x:x
    def f(x):
        if isinstance(x, str):
            return icu_lower(x)
        return x
    return f


def _book_ids_identifiers(db: Cache, field: str, values: List[Dict[str, str]], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, IdentifiersField):
        raise TypeError(f'Target field to seek is not a {IdentifiersField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    rslt = []
    for iden in values:
        if not iden:
            rslt.append([])
            continue
        
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
    
    rslt = _book_ids_many_one(db, field, values, case_sensitive=case_sensitive)
    if not series_with_index:
        return rslt
    
    for i,v in enumerate(indexs):
        def f(x):
            return db.field_for(f'{field}_index', x) == v
        rslt[i] = list(filter(f, rslt[i]))
    return rslt


def _book_ids_one_one(db: Cache, field: str, values: List[str], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, OneToOneField):
        raise TypeError(f'Target field {field!r} is not a {OneToOneField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    # registre the values
    value_map = {}
    for v in values:
        value_map[key(v)] = []
    
    if field == 'series_index':
        bcm = db.fields['series'].table.book_col_map
        def f(kv):
            return kv[0] in bcm and key(kv[1]) in value_map
    else:
        def f(kv):
            return key(kv[1]) in value_map
    
    for k,v in filter(f, field_table.table.book_col_map.items()):
        value_map[key(v)].append(k)
    return [sorted(value_map[key(v)]) for v in values]


def _book_ids_many_one(db: Cache, field: str, values: List[str], case_sensitive: bool) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, ManyToOneField):
        raise TypeError(f'Target field {field!r} is not a {ManyToOneField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    rmap = {key(v): k for k, v in field_table.table.id_map.items()}
    item_map = {v: rmap.get(key(v), None) for v in values}
    
    rslt = []
    for v in values:
        item_ids = field_table.table.col_book_map.get(item_map[v], set())
        rslt.append(sorted(item_ids))
    return rslt


def _book_ids_many_many(
    db: Cache,
    field: str,
    values: List[List[str]],
    case_sensitive: bool,
    tags_contain_value: bool,
) -> List[List[int]]:
    field_table = db.fields[field]
    if not isinstance(field_table, ManyToManyField):
        raise TypeError(f'Target field {field!r} is not a {ManyToManyField.__name__!r}')
    
    key = case_sensitive_key(case_sensitive)
    rmap = {key(v): k for k, v in field_table.table.id_map.items()}
    item_map = {v: rmap.get(key(v), None) for v in set(chain(*values))}
    
    rslt = []
    cbm = field_table.table.col_book_map
    bcm = field_table.table.book_col_map
    for tags in values:
        if not tags:
            rslt.append([])
            continue
        
        # filter intersection
        all_ids = set()
        for t in tags:
            all_ids.update(cbm.get(item_map[t], set()))
        for t in tags:
            all_ids.intersection_update(cbm.get(item_map[t], set()))
        
        # filter exact collections
        if tags_contain_value:
            f = None
        else:
            def f(x):
                return len(bcm.get(x, ())) == len(tags)
        
        rslt.append(sorted(filter(f, all_ids)))
    return rslt


def _book_ids_search(db: Cache, values: List[str]) -> List[List[int]]:
    rslt = []
    for v in values:
        if not v:
            rslt.append([])
            continue
        rslt.append(sorted(db.search(v)))
    return rslt


def get_book_ids(field: str, values: List, match_type: MatchType, options: OptionsUpdate) -> List[List[int]]:
    db = current_db().new_api
    
    if field == MatchType.search:
        return _book_ids_search(db, values)
    
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
        return _book_ids_series(db, field, values, case_sensitive=case_sensitive, series_with_index=options.series_with_index)
    
    fm = db.field_metadata[field]
    dt = fm['datatype']
    
    if dt in {'int', 'float', 'bool'}:
        return _book_ids_one_one(db, field, values, case_sensitive=None)
    
    if dt in {'rating'}:
        return _book_ids_many_one(db, field, values, case_sensitive=None)
    if dt in {'enumeration'}:
        return _book_ids_many_one(db, field, values, case_sensitive=case_sensitive)
    
    if dt == 'text':
        if isinstance(field_table, ManyToOneField):
            return _book_ids_many_one(db, field, values, case_sensitive=case_sensitive)
        if isinstance(field_table, OneToOneField):
            return _book_ids_one_one(db, field, values, case_sensitive=case_sensitive)
        if isinstance(field_table, ManyToManyField):
            return _book_ids_many_many(
                db,
                field,
                values,
                case_sensitive=case_sensitive,
                tags_contain_value=options.tags_contain_value,
            )
    
    raise NotImplementedError(_('The type of the field {!r} is not supported.').format(field))


def update_library_data(data: CSVdata):
    if not data or not data.header or not data.rows or len(data.header) < 2:
        return
    
    db = current_db().new_api
    book_id = get_book_ids(
        data.header[0],
        [r[0] for r in data.rows],
        data.match_type,
        data.options,
    )
    # db.set_field(self, name, book_id_to_val_map, allow_case_change=True, do_path_update=True)
