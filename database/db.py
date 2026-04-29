"""
database/db.py
Soporte dual: SQLite (desarrollo local) + PostgreSQL (Railway producción)
"""
import os
import hashlib
from flask import g

DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Railway a veces entrega 'postgres://' pero psycopg2 necesita 'postgresql://'
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL)


def get_db():
    if 'db' not in g:
        if USE_POSTGRES:
            import psycopg2
            import psycopg2.extras
            g.db = psycopg2.connect(DATABASE_URL)
            g.db.autocommit = False
            g._db_type = 'postgres'
        else:
            import sqlite3
            BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path  = os.path.join(BASE_DIR, 'database', 'ventix.db')
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            g.db = sqlite3.connect(db_path)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys = ON')
            g._db_type = 'sqlite'
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()


def hash_pw(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


def query(sql: str, params=(), one=False):
    db = get_db()
    if USE_POSTGRES:
        import psycopg2.extras
        sql = sql.replace('?', '%s')
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        result = [dict(r) for r in rows]
    else:
        cur = db.execute(sql, params)
        rows = cur.fetchall()
        result = [dict(r) for r in rows]

    return result[0] if one and result else (None if one else result)


def execute(sql: str, params=()):
    db = get_db()
    if USE_POSTGRES:
        sql = sql.replace('?', '%s')
        if sql.strip().upper().startswith('INSERT') and 'RETURNING' not in sql.upper():
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'
        cur = db.cursor()
        cur.execute(sql, params)
        try:
            row = cur.fetchone()
            lastrowid = row[0] if row else None
        except Exception:
            lastrowid = None
        cur.close()
        db.commit()
        return lastrowid
    else:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid


def executescript(sql: str):
    db = get_db()
    if USE_POSTGRES:
        cur = db.cursor()
        cur.execute(sql)
        cur.close()
        db.commit()
    else:
        db.executescript(sql)


def init_db():
    if USE_POSTGRES:
        sql = """
        CREATE TABLE IF NOT EXISTS tiendas (
            id          SERIAL PRIMARY KEY,
            nombre      TEXT    NOT NULL,
            ciudad      TEXT    DEFAULT '',
            direccion   TEXT    DEFAULT '',
            telefono    TEXT    DEFAULT '',
            plan        TEXT    DEFAULT 'basico',
            activa      INTEGER DEFAULT 1,
            fecha_alta  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            mensaje_ticket TEXT DEFAULT '¡Gracias por tu compra!'
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id          SERIAL PRIMARY KEY,
            tienda_id   INTEGER REFERENCES tiendas(id),
            nombre      TEXT    NOT NULL,
            username    TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            rol         TEXT    NOT NULL DEFAULT 'cajero',
            activo      INTEGER DEFAULT 1,
            fecha_alta  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS categorias (
            id        SERIAL PRIMARY KEY,
            tienda_id INTEGER NOT NULL REFERENCES tiendas(id),
            nombre    TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS productos (
            id             SERIAL PRIMARY KEY,
            tienda_id      INTEGER NOT NULL REFERENCES tiendas(id),
            nombre         TEXT    NOT NULL,
            codigo_barras  TEXT,
            precio         REAL    NOT NULL DEFAULT 0,
            precio_costo   REAL    NOT NULL DEFAULT 0,
            stock          INTEGER NOT NULL DEFAULT 0,
            stock_minimo   INTEGER NOT NULL DEFAULT 5,
            categoria_id   INTEGER REFERENCES categorias(id),
            activo         INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS ventas (
            id          SERIAL PRIMARY KEY,
            tienda_id   INTEGER NOT NULL REFERENCES tiendas(id),
            usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
            fecha       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total       REAL    NOT NULL DEFAULT 0,
            metodo_pago TEXT    NOT NULL DEFAULT 'Efectivo',
            caja        TEXT    DEFAULT 'Caja 1'
        );
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id              SERIAL PRIMARY KEY,
            venta_id        INTEGER NOT NULL REFERENCES ventas(id),
            producto_id     INTEGER NOT NULL REFERENCES productos(id),
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_productos_tienda    ON productos(tienda_id);
        CREATE INDEX IF NOT EXISTS idx_ventas_tienda_fecha ON ventas(tienda_id, fecha);
        CREATE INDEX IF NOT EXISTS idx_detalle_venta       ON detalle_ventas(venta_id);
        """
    else:
        sql = """
        CREATE TABLE IF NOT EXISTS tiendas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT    NOT NULL,
            ciudad      TEXT    DEFAULT '',
            direccion   TEXT    DEFAULT '',
            telefono    TEXT    DEFAULT '',
            plan        TEXT    DEFAULT 'basico',
            activa      INTEGER DEFAULT 1,
            fecha_alta  TEXT    DEFAULT CURRENT_TIMESTAMP,
            mensaje_ticket TEXT DEFAULT '¡Gracias por tu compra!'
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id   INTEGER REFERENCES tiendas(id),
            nombre      TEXT    NOT NULL,
            username    TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            rol         TEXT    NOT NULL DEFAULT 'cajero',
            activo      INTEGER DEFAULT 1,
            fecha_alta  TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS categorias (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id INTEGER NOT NULL REFERENCES tiendas(id),
            nombre    TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS productos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id      INTEGER NOT NULL REFERENCES tiendas(id),
            nombre         TEXT    NOT NULL,
            codigo_barras  TEXT,
            precio         REAL    NOT NULL DEFAULT 0,
            precio_costo   REAL    NOT NULL DEFAULT 0,
            stock          INTEGER NOT NULL DEFAULT 0,
            stock_minimo   INTEGER NOT NULL DEFAULT 5,
            categoria_id   INTEGER REFERENCES categorias(id),
            activo         INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS ventas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id   INTEGER NOT NULL REFERENCES tiendas(id),
            usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
            fecha       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total       REAL    NOT NULL DEFAULT 0,
            metodo_pago TEXT    NOT NULL DEFAULT 'Efectivo',
            caja        TEXT    DEFAULT 'Caja 1'
        );
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id        INTEGER NOT NULL REFERENCES ventas(id),
            producto_id     INTEGER NOT NULL REFERENCES productos(id),
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_productos_tienda    ON productos(tienda_id);
        CREATE INDEX IF NOT EXISTS idx_ventas_tienda_fecha ON ventas(tienda_id, fecha);
        CREATE INDEX IF NOT EXISTS idx_detalle_venta       ON detalle_ventas(venta_id);
        """

    db = get_db()
    if USE_POSTGRES:
        cur = db.cursor()
        cur.execute(sql)
        cur.close()
        db.commit()
    else:
        db.executescript(sql)

    existing = query("SELECT id FROM usuarios WHERE username='superadmin'", one=True)
    if not existing:
        execute(
            "INSERT INTO usuarios (tienda_id, nombre, username, password, rol) VALUES (?,?,?,?,?)",
            (None, 'Super Admin', 'superadmin', hash_pw('ventix2024'), 'superadmin')
        )
    print('✅ Ventix DB inicializada')
