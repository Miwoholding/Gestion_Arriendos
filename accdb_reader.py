"""
Parser binario de .accdb (Jet4/ACE) basado en la especificación de mdbtools.
Sin dependencias externas (solo stdlib de Python).
"""
import struct
import datetime
from pathlib import Path

# ── Constantes Jet4/ACCDB ────────────────────────────────────────────────────
PG_SIZE          = 4096
ROW_COUNT_OFFSET = 0x0C      # = 12
OFFSET_MASK      = 0x1FFF
CATALOG_PG       = 18
TABLE_DEF_PG     = 2          # MSysObjects

# Tamaños fijos de columna (bytes)
COL_ENTRY_SIZE = 25
RIDX_ENTRY_SIZE = 12

# Tipos de página
PG_DB    = 0x00
PG_DATA  = 0x01
PG_TABLE = 0x02

# Tipos de columna
MDB_BOOL     = 0x01
MDB_BYTE     = 0x02
MDB_INT      = 0x03
MDB_LONGINT  = 0x04
MDB_MONEY    = 0x05
MDB_FLOAT    = 0x06
MDB_DOUBLE   = 0x07
MDB_DATETIME = 0x08
MDB_BINARY   = 0x09
MDB_TEXT     = 0x0A
MDB_OLE      = 0x0B
MDB_MEMO     = 0x0C
MDB_REPID    = 0x0F
MDB_NUMERIC  = 0x10

# Offsets tabla de definición (JET4)
TAB_NUM_ROWS_OFFSET   = 16
TAB_NUM_COLS_OFFSET   = 45
TAB_NUM_RIDXS_OFFSET  = 51
TAB_COLS_START_OFFSET = 63
COL_TYPE_OFFSET       = 0
COL_NUM_OFFSET        = 5
COL_VAR_OFFSET        = 7
COL_ROWCOL_OFFSET     = 9
COL_FLAGS_OFFSET      = 15
COL_FIXED_OFF_OFFSET  = 21
COL_SIZE_OFFSET       = 23


# ── RC4 ──────────────────────────────────────────────────────────────────────
def rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    x = y = 0
    out = bytearray(data)
    for i in range(len(out)):
        x = (x + 1) % 256
        y = (state[x] + y) % 256
        state[x], state[y] = state[y], state[x]
        out[i] ^= state[(state[x] + state[y]) % 256]
    return bytes(out)


# ── Lectura de enteros ────────────────────────────────────────────────────────
def u8(buf, off):  return buf[off]
def u16(buf, off): return struct.unpack_from('<H', buf, off)[0]
def i16(buf, off): return struct.unpack_from('<h', buf, off)[0]
def u32(buf, off): return struct.unpack_from('<I', buf, off)[0]
def i32(buf, off): return struct.unpack_from('<i', buf, off)[0]
def dbl(buf, off): return struct.unpack_from('<d', buf, off)[0]
def flt(buf, off): return struct.unpack_from('<f', buf, off)[0]


# ── Lector de páginas ─────────────────────────────────────────────────────────
class AccdbReader:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.db_key = 0
        self._parse_header()

    def _parse_header(self):
        pg0 = bytearray(self.data[:PG_SIZE])
        assert pg0[0] == 0x00, "Página 0 inválida"
        # Desencriptar cabecera con clave fija
        decrypted = rc4(b'\xC7\xDA\x39\x6B', pg0[0x18:0x18 + 128])
        self.db_key = u32(decrypted, 0x3E - 0x18)

    def read_page(self, pg_num: int) -> bytes:
        off = pg_num * PG_SIZE
        page = bytearray(self.data[off:off + PG_SIZE])
        if len(page) < PG_SIZE:
            page.extend(b'\x00' * (PG_SIZE - len(page)))
        # Desencriptar si hay db_key
        if pg_num != 0 and self.db_key != 0:
            key_i = (self.db_key ^ pg_num) & 0xFFFFFFFF
            key = struct.pack('<I', key_i)
            page = bytearray(rc4(key, bytes(page)))
        return bytes(page)

    def total_pages(self) -> int:
        return len(self.data) // PG_SIZE


# ── Definición de tabla ───────────────────────────────────────────────────────
class Column:
    __slots__ = ('name','col_type','col_num','var_col_num','row_col_num',
                 'is_fixed','fixed_offset','col_size','col_scale','col_prec')
    def __repr__(self):
        return f"<Col {self.name!r} type={self.col_type:#x} fixed={self.is_fixed}>"


class TableDef:
    def __init__(self):
        self.table_pg = 0
        self.num_rows = 0
        self.num_cols = 0
        self.num_var_cols = 0
        self.num_ridxs = 0
        self.columns: list[Column] = []


# ── Parser de tabla de definición ─────────────────────────────────────────────
def parse_table_def(reader: AccdbReader, table_pg: int) -> TableDef | None:
    """Lee la definición de tabla y sus columnas (puede ocupar páginas encadenadas)."""
    buf = _read_chained(reader, table_pg)
    if not buf or buf[0] != PG_TABLE:
        return None

    td = TableDef()
    td.table_pg    = table_pg
    td.num_rows    = i32(buf, TAB_NUM_ROWS_OFFSET)
    td.num_var_cols = u16(buf, TAB_NUM_COLS_OFFSET - 2)
    td.num_cols    = u16(buf, TAB_NUM_COLS_OFFSET)
    td.num_ridxs   = u32(buf, TAB_NUM_RIDXS_OFFSET)

    # Atributos de columnas
    pos = TAB_COLS_START_OFFSET + td.num_ridxs * RIDX_ENTRY_SIZE
    raw_cols = []
    for _ in range(td.num_cols):
        col_buf = buf[pos:pos + COL_ENTRY_SIZE]
        if len(col_buf) < COL_ENTRY_SIZE:
            break
        c = Column()
        c.col_type    = u8(col_buf, COL_TYPE_OFFSET)
        c.col_num     = u8(col_buf, COL_NUM_OFFSET)
        c.var_col_num = u16(col_buf, COL_VAR_OFFSET)
        c.row_col_num = u16(col_buf, COL_ROWCOL_OFFSET)
        flags         = u8(col_buf, COL_FLAGS_OFFSET)
        c.is_fixed    = bool(flags & 0x01)
        c.fixed_offset = u16(col_buf, COL_FIXED_OFF_OFFSET)
        c.col_size    = u16(col_buf, COL_SIZE_OFFSET) if c.col_type != MDB_BOOL else 0
        c.col_scale   = u8(col_buf, 11)
        c.col_prec    = u8(col_buf, 12)
        c.name        = ''
        raw_cols.append(c)
        pos += COL_ENTRY_SIZE

    # Nombres de columnas (UTF-16 LE, prefijo int16 de tamaño en bytes)
    for c in raw_cols:
        if pos + 2 > len(buf):
            break
        name_sz = u16(buf, pos); pos += 2
        name_bytes = buf[pos:pos + name_sz]; pos += name_sz
        try:
            c.name = name_bytes.decode('utf-16-le')
        except Exception:
            c.name = name_bytes.decode('latin-1', errors='replace')

    # Ordenar por col_num
    raw_cols.sort(key=lambda c: c.col_num)
    td.columns = raw_cols
    return td


def _read_chained(reader: AccdbReader, start_pg: int) -> bytearray:
    """Lee páginas encadenadas (para definiciones de tabla largas)."""
    result = bytearray()
    pg = start_pg
    visited = set()
    while pg and pg not in visited:
        visited.add(pg)
        page = reader.read_page(pg)
        result.extend(page)
        next_pg = u32(page, 4)
        pg = next_pg if next_pg and page[0] == PG_TABLE else 0
    return result


# ── Extracción de filas ───────────────────────────────────────────────────────
ACCESS_EPOCH = datetime.datetime(1899, 12, 30)


def _decompress_unicode(src: bytes) -> bytes:
    """
    Descomprime el formato 'Unicode Compressed' de Jet4/ACCDB.
    Si empieza con BOM (FF FE), descomprime el resto; si no, devuelve tal cual (UTF-16 LE).
    """
    if len(src) >= 2 and src[0] == 0xFF and src[1] == 0xFE:
        # BOM presente → descomprimir el resto
        src = src[2:]
        compressed = True
        out = bytearray()
        i = 0
        while i < len(src):
            if src[i] == 0x00:
                compressed = not compressed
                i += 1
            elif compressed:
                out.append(src[i])
                out.append(0x00)
                i += 1
            elif i + 1 < len(src):
                out.append(src[i])
                out.append(src[i + 1])
                i += 2
            else:
                break
        return bytes(out)
    else:
        # Sin BOM → UTF-16 LE puro
        return src


def _decode_text(raw: bytes) -> str:
    decompressed = _decompress_unicode(raw)
    try:
        return decompressed.decode('utf-16-le').rstrip('\x00')
    except Exception:
        return raw.decode('latin-1', errors='replace').rstrip('\x00')


def _decode_value(buf: bytes, start: int, size: int, col: Column):
    """Convierte bytes crudos al valor Python correspondiente."""
    if size == 0:
        return None
    raw = buf[start:start + size]
    t = col.col_type

    if t == MDB_BOOL:
        return None  # BOOL usa el bit de null
    if t == MDB_BYTE:
        return raw[0]
    if t == MDB_INT:
        return i16(raw, 0)
    if t == MDB_LONGINT:
        return i32(raw, 0)
    if t == MDB_MONEY:
        if len(raw) == 8:
            return round(struct.unpack_from('<q', raw, 0)[0] / 10000, 4)
        return None
    if t == MDB_FLOAT:
        return round(flt(raw, 0), 4)
    if t == MDB_DOUBLE:
        return round(dbl(raw, 0), 4)
    if t == MDB_DATETIME:
        days = dbl(raw, 0)
        try:
            return (ACCESS_EPOCH + datetime.timedelta(days=days)).date()
        except Exception:
            return None
    if t == MDB_TEXT:
        return _decode_text(raw) or None
    if t == MDB_MEMO:
        # Memos inline (<= 12 bytes header) o referencia OLE
        if size <= 12:
            return None
        return _decode_text(raw[12:]) or None
    if t == MDB_NUMERIC:
        if len(raw) >= 17:
            sign = raw[0]
            val = int.from_bytes(raw[1:17], 'little')
            scale = col.col_scale if col.col_scale else 0
            result = val / (10 ** scale)
            return -result if sign else result
    return raw.hex() if raw else None


def _crack_row(page: bytes, row_start: int, row_size: int, columns: list[Column]):
    """Descompone una fila en valores por columna (JET4)."""
    if row_size < 2:
        return None
    row_end   = row_start + row_size - 1
    row_cols  = u16(page, row_start)          # total columnas en la fila
    bitmask_sz = (row_cols + 7) // 8

    if bitmask_sz + 2 >= row_end - row_start:
        return None

    # Null mask (1 = not null, 0 = null)
    null_mask = page[row_end - bitmask_sz + 1: row_end + 1]

    # Columnas variables
    num_var = 0
    var_offsets = []
    if any(not c.is_fixed for c in columns):
        var_count_pos = row_end - bitmask_sz - 1
        if var_count_pos >= row_start:
            num_var = u16(page, var_count_pos)
            var_offsets = []
            for i in range(num_var + 1):
                voff_pos = row_end - bitmask_sz - 3 - i * 2
                if voff_pos >= row_start:
                    var_offsets.append(u16(page, voff_pos))
                else:
                    var_offsets.append(0)

    result = {}
    for col in columns:
        col_num = col.col_num
        byte_num = col_num // 8
        bit_num  = col_num % 8
        is_null  = not (null_mask[byte_num] & (1 << bit_num)) if byte_num < len(null_mask) else True

        if col.col_type == MDB_BOOL:
            result[col.name] = not is_null  # BOOL: null bit IS the value
            continue

        if is_null:
            result[col.name] = None
            continue

        if col.is_fixed:
            col_start = row_start + col.fixed_offset + 2  # +2 = col_count_size
            result[col.name] = _decode_value(page, col_start, col.col_size, col)
        else:
            vn = col.var_col_num
            if vn < num_var and vn + 1 < len(var_offsets):
                col_start = row_start + var_offsets[vn]
                col_size  = var_offsets[vn + 1] - var_offsets[vn]
                result[col.name] = _decode_value(page, col_start, col_size, col)
            else:
                result[col.name] = None
    return result


def read_table_rows(reader: AccdbReader, td: TableDef) -> list[dict]:
    """Extrae todas las filas de una tabla escaneando las páginas de datos."""
    rows = []
    total_pgs = reader.total_pages()
    for pg_num in range(1, total_pgs):
        page = reader.read_page(pg_num)
        # La página de datos debe apuntar a la página de definición de la tabla
        if page[0] != PG_DATA:
            continue
        parent_pg = u32(page, 4)
        if parent_pg != td.table_pg:
            continue

        row_count = u16(page, ROW_COUNT_OFFSET)
        for row_idx in range(row_count):
            row_start_raw = u16(page, ROW_COUNT_OFFSET + 2 + row_idx * 2)
            # Flags del offset
            deleted   = bool(row_start_raw & 0x4000)
            # lookup/overflow (0x8000): el dato igual puede estar inline → no saltar
            row_start = row_start_raw & OFFSET_MASK

            if deleted:
                continue

            # Tamaño de la fila
            if row_idx == 0:
                row_end_excl = PG_SIZE
            else:
                prev_raw = u16(page, ROW_COUNT_OFFSET + row_idx * 2)
                row_end_excl = prev_raw & OFFSET_MASK

            row_size = row_end_excl - row_start
            if row_size <= 0 or row_start + row_size > PG_SIZE:
                continue

            row = _crack_row(page, row_start, row_size, td.columns)
            if row is not None:
                rows.append(row)
    return rows


# ── Catálogo (MSysObjects) ────────────────────────────────────────────────────
def read_catalog(reader: AccdbReader) -> dict[str, int]:
    """Devuelve {nombre_tabla: tabla_pg} para las tablas de usuario."""
    td = parse_table_def(reader, TABLE_DEF_PG)
    if not td:
        return {}
    rows = read_table_rows(reader, td)
    catalog = {}
    for row in rows:
        name = row.get('Name', '')
        obj_type = row.get('Type', -1)
        obj_id   = row.get('Id', 0) or 0
        # type 1 = user table
        if obj_type == 1 and name and not name.startswith('MSys'):
            table_pg = int(obj_id) & 0x00FFFFFF
            catalog[name] = table_pg
    return catalog


# ── API pública ───────────────────────────────────────────────────────────────
def extract_all(db_path: str) -> dict[str, list[dict]]:
    """
    Extrae todas las tablas de usuario del .accdb.
    Devuelve {nombre_tabla: [fila_dict, ...]}.
    """
    reader = AccdbReader(db_path)
    catalog = read_catalog(reader)
    result = {}
    for table_name, table_pg in catalog.items():
        td = parse_table_def(reader, table_pg)
        if td:
            rows = read_table_rows(reader, td)
            result[table_name] = rows
    return result
