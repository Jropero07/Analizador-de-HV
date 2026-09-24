import os
import re
import time
import hmac
import hashlib
import secrets
import threading
import sqlite3
import json
from datetime import datetime
import pandas as pd


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "asistente_tthh.db")

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# CONEXIÓN
# ============================================================

def _leer_database_url():
    try:
        import streamlit as st
        valor = st.secrets["DATABASE_URL"]
    except Exception:
        valor = os.getenv("DATABASE_URL", "")
    return str(valor).strip()


DATABASE_URL = _leer_database_url()
USAR_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

_TIPOS_SQL = {"INTEGER", "TEXT", "REAL", "NUMERIC", "VARCHAR", "FLOAT", "DATE", "TIMESTAMP"}
_TABLAS_CON_ID = {
    "vacantes", "candidatos", "postulaciones", "analisis",
    "documentos", "correos_procesados", "historial"
}


def _a_postgres(sql, hay_params):
    """
    Traduce una sentencia escrita para SQLite a PostgreSQL.
    Devuelve (sql_postgres, devuelve_id) o None si la sentencia se omite.
    """
    s = sql.strip()

    if re.match(r"PRAGMA\s+foreign_keys", s, re.I):
        return None

    m = re.match(r"PRAGMA\s+table_info\((\w+)\)", s, re.I)
    if m:
        return (
            "SELECT ordinal_position AS cid, column_name AS name "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            f"AND table_name = '{m.group(1).lower()}' "
            "ORDER BY ordinal_position",
            False
        )

    s = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", s, flags=re.I)
    s = re.sub(r"\bDATETIME\b", "TIMESTAMP", s, flags=re.I)

    if re.match(r"INSERT\s+OR\s+IGNORE\s+INTO", s, re.I):
        s = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.I)
        s += " ON CONFLICT DO NOTHING"
    elif re.match(r"INSERT\s+OR\s+REPLACE\s+INTO", s, re.I):
        s = re.sub(r"^INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.I)
        columnas = [c.strip() for c in re.search(r"\(([^)]*)\)", s).group(1).split(",")]
        asignaciones = ", ".join(f"{c} = EXCLUDED.{c}" for c in columnas if c != "message_id")
        s += f" ON CONFLICT (message_id) DO UPDATE SET {asignaciones}"

    # Conserva mayúsculas de los alias (Postgres los pasa a minúsculas si no van entre comillas)
    s = re.sub(
        r"\bAS\s+([A-Za-z_]\w*)\b(?!\s*\()",
        lambda m: m.group(0) if m.group(1).upper() in _TIPOS_SQL else f'AS "{m.group(1)}"',
        s,
        flags=re.I
    )

    m = re.match(r"INSERT\s+INTO\s+(\w+)", s, re.I)
    devuelve_id = bool(m and m.group(1).lower() in _TABLAS_CON_ID)
    if devuelve_id:
        s += " RETURNING id"

    if hay_params:
        s = s.replace("%", "%%")
    s = s.replace("?", "%s")

    return s, devuelve_id


class _CursorPG:
    """Cursor con la misma interfaz que usa este módulo de sqlite3."""

    def __init__(self, cursor):
        self._cur = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        traducido = _a_postgres(sql, bool(params))
        if traducido is None:
            return self
        sql_pg, devuelve_id = traducido
        self._cur.execute(sql_pg, tuple(params) if params else None)
        if devuelve_id:
            fila = self._cur.fetchone()
            self.lastrowid = fila[0] if fila else None
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class _ConexionPG:
    """Conexión con la misma interfaz que usa este módulo de sqlite3."""

    def __init__(self, conexion):
        self._conn = conexion

    def cursor(self):
        return _CursorPG(self._conn.cursor())

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._conn is not None:
            _devolver_conexion(self._conn)
            self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


_POOL = []
_POOL_LOCK = threading.Lock()
_POOL_MAX_INACTIVAS = 4
_SEGUNDOS_SIN_VALIDAR = 20


def _nueva_conexion_pg():
    import psycopg2
    import psycopg2.extras
    import psycopg2.extensions as ext

    # Las fechas se devuelven como texto "AAAA-MM-DD HH:MM:SS", igual que SQLite
    ext.register_type(ext.new_type(
        (1114, 1184),
        "FECHA_TEXTO",
        lambda valor, cur: valor.split(".")[0].split("+")[0] if valor else valor
    ))
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.DictCursor,
        connect_timeout=15
    )


def _tomar_conexion_pg():
    while True:
        with _POOL_LOCK:
            if not _POOL:
                break
            conexion, desde = _POOL.pop()
        if conexion.closed:
            continue
        if time.time() - desde > _SEGUNDOS_SIN_VALIDAR:
            try:
                cur = conexion.cursor()
                cur.execute("SELECT 1")
                conexion.rollback()
            except Exception:
                try:
                    conexion.close()
                except Exception:
                    pass
                continue
        return conexion
    return _nueva_conexion_pg()


def _devolver_conexion(conexion):
    try:
        conexion.rollback()
        with _POOL_LOCK:
            if not conexion.closed and len(_POOL) < _POOL_MAX_INACTIVAS:
                _POOL.append((conexion, time.time()))
                return
        conexion.close()
    except Exception:
        try:
            conexion.close()
        except Exception:
            pass


def obtener_conexion():
    """
    Devuelve la conexión a la base de datos:
    - PostgreSQL (Neon) si DATABASE_URL está definida en Secrets / .env.
    - SQLite local en caso contrario.
    """
    if USAR_POSTGRES:
        return _ConexionPG(_tomar_conexion_pg())

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ============================================================
# INICIALIZACIÓN Y MIGRACIONES
# ============================================================

def inicializar_db():
    """
    Inicializa la base de datos PRO.

    Las tablas nuevas se crean sin eliminar información existente.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()

    # --------------------------------------------------------
    # VACANTES
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vacantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            area TEXT NOT NULL,
            descripcion TEXT,
            formacion TEXT,
            experiencia TEXT,
            habilidades TEXT,
            otros_criterios TEXT,
            estado TEXT NOT NULL DEFAULT 'Abierta',
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_cierre DATETIME
        )
    """)

    # --------------------------------------------------------
    # CANDIDATOS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidatos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            documento TEXT,
            telefono TEXT,
            email TEXT,
            formacion TEXT,
            experiencia TEXT,
            habilidades TEXT,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # POSTULACIONES
    #
    # Una persona puede tener varias postulaciones.
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS postulaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidato_id INTEGER NOT NULL,
            vacante_id INTEGER NOT NULL,

            fuente TEXT NOT NULL DEFAULT 'Manual',

            estado TEXT NOT NULL DEFAULT 'Pendiente',

            archivo_nombre TEXT,
            archivo_ruta TEXT,
            archivo_tipo TEXT,

            texto_hv TEXT,

            fecha_postulacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (candidato_id)
                REFERENCES candidatos(id)
                ON DELETE CASCADE,

            FOREIGN KEY (vacante_id)
                REFERENCES vacantes(id)
                ON DELETE CASCADE,

            UNIQUE(candidato_id, vacante_id)
        )
    """)

    # --------------------------------------------------------
    # ANÁLISIS IA
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analisis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            postulacion_id INTEGER,

            candidato_id INTEGER NOT NULL,
            vacante_id INTEGER NOT NULL,

            puntaje_total INTEGER,

            decision TEXT,

            resumen_ejecutivo TEXT,

            cumplimiento_json TEXT,

            alertas_json TEXT,

            modelo_ia TEXT,

            estado TEXT NOT NULL DEFAULT 'Completado',

            error TEXT,

            fecha_analisis DATETIME DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (postulacion_id)
                REFERENCES postulaciones(id)
                ON DELETE CASCADE,

            FOREIGN KEY (candidato_id)
                REFERENCES candidatos(id)
                ON DELETE CASCADE,

            FOREIGN KEY (vacante_id)
                REFERENCES vacantes(id)
                ON DELETE CASCADE
        )
    """)

    # --------------------------------------------------------
    # PROCESAMIENTO DE ARCHIVOS
    #
    # Permite controlar carga masiva y correo.
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            postulacion_id INTEGER,

            candidato_id INTEGER,

            nombre_archivo TEXT NOT NULL,

            ruta_archivo TEXT,

            tipo_archivo TEXT,

            origen TEXT NOT NULL DEFAULT 'Carga masiva',

            estado_procesamiento TEXT NOT NULL DEFAULT 'Pendiente',

            error TEXT,

            fecha_recepcion DATETIME DEFAULT CURRENT_TIMESTAMP,

            fecha_procesamiento DATETIME,

            FOREIGN KEY (postulacion_id)
                REFERENCES postulaciones(id)
                ON DELETE CASCADE,

            FOREIGN KEY (candidato_id)
                REFERENCES candidatos(id)
                ON DELETE SET NULL
        )
    """)

    # --------------------------------------------------------
    # CORREOS PROCESADOS
    #
    # Evita volver a procesar el mismo correo.
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS correos_procesados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            message_id TEXT NOT NULL UNIQUE,

            asunto TEXT,

            remitente TEXT,

            fecha_correo TEXT,

            cantidad_adjuntos INTEGER DEFAULT 0,

            estado TEXT NOT NULL DEFAULT 'Procesado',

            error TEXT,

            fecha_procesamiento DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # HISTORIAL
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            entidad TEXT NOT NULL,

            entidad_id INTEGER,

            accion TEXT NOT NULL,

            descripcion TEXT,

            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # CONFIGURACIÓN
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)

    # --------------------------------------------------------
    # ÍNDICES
    # --------------------------------------------------------

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_vacantes_estado
        ON vacantes(estado)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_candidatos_documento
        ON candidatos(documento)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_candidatos_email
        ON candidatos(email)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_postulaciones_vacante
        ON postulaciones(vacante_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_postulaciones_candidato
        ON postulaciones(candidato_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_analisis_puntaje
        ON analisis(puntaje_total)
    """)

    # --------------------------------------------------------
    # MIGRACIONES DE COLUMNAS
    # --------------------------------------------------------

    _agregar_columna_si_no_existe(
        cursor,
        "vacantes",
        "fecha_actualizacion",
        "DATETIME"
    )

    cursor.execute("""
        UPDATE vacantes
        SET fecha_actualizacion = fecha_creacion
        WHERE fecha_actualizacion IS NULL
    """)

    _agregar_columna_si_no_existe(
        cursor,
        "vacantes",
        "fecha_cierre",
        "DATETIME"
    )

    _agregar_columna_si_no_existe(
        cursor,
        "analisis",
        "postulacion_id",
        "INTEGER"
    )

    _agregar_columna_si_no_existe(
        cursor,
        "analisis",
        "modelo_ia",
        "TEXT"
    )

    _agregar_columna_si_no_existe(
        cursor,
        "analisis",
        "estado",
        "TEXT"
    )

    cursor.execute("""
        UPDATE analisis
        SET estado = 'Completado'
        WHERE estado IS NULL
    """)

    _agregar_columna_si_no_existe(
        cursor,
        "analisis",
        "error",
        "TEXT"
    )

    _agregar_columna_si_no_existe(
        cursor,
        "analisis",
        "alertas_json",
        "TEXT"
    )

    _agregar_columna_si_no_existe(
        cursor,
        "candidatos",
        "fecha_actualizacion",
        "DATETIME"
    )

    cursor.execute("""
        UPDATE candidatos
        SET fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE fecha_actualizacion IS NULL
    """)

    _agregar_columna_si_no_existe(
        cursor,
        "postulaciones",
        "observaciones_tthh",
        "TEXT"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'lector',
            permisos TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    _agregar_columna_si_no_existe(cursor, "usuarios", "permisos", "TEXT")

    # Usuarios creados antes de tener permisos por casilla: se les asigna el
    # equivalente (el antiguo "admin" pasa a Súper administrador con todo).
    cursor.execute("UPDATE usuarios SET rol = 'super_admin' WHERE rol = 'admin'")
    cursor.execute("""
        UPDATE usuarios SET permisos = ?
        WHERE permisos IS NULL AND rol = 'super_admin'
    """, (json.dumps(PERMISOS_PRESET["super_admin"]),))
    cursor.execute("""
        UPDATE usuarios SET permisos = ?
        WHERE permisos IS NULL AND rol = 'lector'
    """, (json.dumps(PERMISOS_PRESET["lector"]),))

    # --------------------------------------------------------
    # MIGRACIÓN DE ESTADOS ANTIGUOS Y DATOS PREVIOS
    # --------------------------------------------------------

    cursor.execute("""
        UPDATE vacantes
        SET estado = 'Abierta'
        WHERE estado = 'Activa'
    """)

    # Si la tabla candidatos tiene la columna vacante_id de versiones previas,
    # migrar automáticamente a la tabla postulaciones para no perder la relación
    cursor.execute("PRAGMA table_info(candidatos)")
    cand_cols = [row[1] for row in cursor.fetchall()]
    if "vacante_id" in cand_cols:
        cursor.execute("""
            INSERT OR IGNORE INTO postulaciones (candidato_id, vacante_id, fuente, estado)
            SELECT id, vacante_id, 'Migración inicial', 'Pendiente'
            FROM candidatos
            WHERE vacante_id IS NOT NULL
              AND vacante_id IN (SELECT id FROM vacantes)
        """)
        cursor.execute("""
            UPDATE candidatos
            SET vacante_id = NULL
            WHERE vacante_id IS NOT NULL
        """)

    conn.commit()
    conn.close()


def _agregar_columna_si_no_existe(cursor, tabla, columna, definicion):
    """
    Agrega una columna únicamente si todavía no existe.
    """
    cursor.execute(f"PRAGMA table_info({tabla})")
    columnas = [row["name"] for row in cursor.fetchall()]

    if columna not in columnas:
        cursor.execute(
            f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"
        )


# ============================================================
# UTILIDADES
# ============================================================

def _normalizar(valor):
    if valor is None:
        return ""

    return str(valor).strip()


def _normalizar_email(email):
    return _normalizar(email).lower()


def _normalizar_documento(documento):
    return _normalizar(documento).replace(" ", "").replace(".", "")


def _registrar_historial(
    cursor,
    entidad,
    entidad_id,
    accion,
    descripcion=""
):
    cursor.execute("""
        INSERT INTO historial (
            entidad,
            entidad_id,
            accion,
            descripcion
        )
        VALUES (?, ?, ?, ?)
    """, (
        entidad,
        entidad_id,
        accion,
        descripcion
    ))


# ============================================================
# VACANTES
# ============================================================

def crear_vacante(
    titulo,
    area,
    descripcion="",
    formacion="",
    experiencia="",
    habilidades="",
    otros_criterios=""
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO vacantes (
            titulo,
            area,
            descripcion,
            formacion,
            experiencia,
            habilidades,
            otros_criterios,
            estado
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Abierta')
    """, (
        _normalizar(titulo),
        _normalizar(area),
        _normalizar(descripcion),
        _normalizar(formacion),
        _normalizar(experiencia),
        _normalizar(habilidades),
        _normalizar(otros_criterios)
    ))

    vacante_id = cursor.lastrowid

    _registrar_historial(
        cursor,
        "vacante",
        vacante_id,
        "creacion",
        f"Vacante creada: {_normalizar(titulo)}"
    )

    conn.commit()
    conn.close()

    return vacante_id


def obtener_vacantes():
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM vacantes
        ORDER BY fecha_creacion DESC
    """)

    resultado = cursor.fetchall()

    conn.close()

    return resultado


def obtener_vacantes_abiertas():
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM vacantes
        WHERE estado = 'Abierta'
        ORDER BY fecha_creacion DESC
    """)

    resultado = cursor.fetchall()

    conn.close()

    return resultado


def obtener_vacantes_cerradas():
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM vacantes
        WHERE estado = 'Cerrada'
        ORDER BY fecha_cierre DESC
    """)

    resultado = cursor.fetchall()

    conn.close()

    return resultado


def obtener_vacante(vacante_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM vacantes
        WHERE id = ?
    """, (vacante_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def actualizar_vacante(
    vacante_id,
    titulo,
    area,
    descripcion,
    formacion,
    experiencia,
    habilidades,
    otros_criterios
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE vacantes
        SET
            titulo = ?,
            area = ?,
            descripcion = ?,
            formacion = ?,
            experiencia = ?,
            habilidades = ?,
            otros_criterios = ?,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        _normalizar(titulo),
        _normalizar(area),
        _normalizar(descripcion),
        _normalizar(formacion),
        _normalizar(experiencia),
        _normalizar(habilidades),
        _normalizar(otros_criterios),
        vacante_id
    ))

    _registrar_historial(
        cursor,
        "vacante",
        vacante_id,
        "edicion",
        f"Vacante actualizada: {_normalizar(titulo)}"
    )

    conn.commit()
    conn.close()


def cambiar_estado_vacante(vacante_id, nuevo_estado):
    """
    Estados permitidos:
    - Abierta
    - Cerrada
    """

    nuevo_estado = _normalizar(nuevo_estado)

    if nuevo_estado not in ("Abierta", "Cerrada"):
        raise ValueError(
            "El estado de la vacante debe ser 'Abierta' o 'Cerrada'."
        )

    conn = obtener_conexion()
    cursor = conn.cursor()

    if nuevo_estado == "Cerrada":
        cursor.execute("""
            UPDATE vacantes
            SET
                estado = 'Cerrada',
                fecha_cierre = CURRENT_TIMESTAMP,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (vacante_id,))

        accion = "cierre"

    else:
        cursor.execute("""
            UPDATE vacantes
            SET
                estado = 'Abierta',
                fecha_cierre = NULL,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (vacante_id,))

        accion = "apertura"

    _registrar_historial(
        cursor,
        "vacante",
        vacante_id,
        accion,
        f"Vacante cambiada a estado: {nuevo_estado}"
    )

    conn.commit()
    conn.close()


def eliminar_vacante(vacante_id):
    """
    Elimina una vacante y sus datos relacionados.

    IMPORTANTE:
    Esta función debe utilizarse únicamente cuando TTHH
    confirme explícitamente la eliminación.
    """

    conn = obtener_conexion()
    cursor = conn.cursor()

    vacante = cursor.execute("""
        SELECT titulo
        FROM vacantes
        WHERE id = ?
    """, (vacante_id,)).fetchone()

    titulo = vacante["titulo"] if vacante else "Vacante"

    # Eliminar en orden para respetar Foreign Keys en SQLite
    cursor.execute("DELETE FROM analisis WHERE vacante_id = ?", (vacante_id,))
    cursor.execute("DELETE FROM documentos WHERE postulacion_id IN (SELECT id FROM postulaciones WHERE vacante_id = ?)", (vacante_id,))
    cursor.execute("DELETE FROM postulaciones WHERE vacante_id = ?", (vacante_id,))

    cursor.execute("""
        DELETE FROM vacantes
        WHERE id = ?
    """, (vacante_id,))

    _registrar_historial(
        cursor,
        "vacante",
        vacante_id,
        "eliminacion",
        f"Vacante eliminada: {titulo}"
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# CANDIDATOS
# ============================================================

def obtener_candidato(candidato_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM candidatos
        WHERE id = ?
    """, (candidato_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def buscar_candidato_por_documento(documento):
    documento = _normalizar_documento(documento)

    if not documento:
        return None

    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM candidatos
        WHERE documento = ?
        LIMIT 1
    """, (documento,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def buscar_candidato_por_email(email):
    email = _normalizar_email(email)

    if not email:
        return None

    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM candidatos
        WHERE LOWER(email) = ?
        LIMIT 1
    """, (email,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def buscar_candidato(
    documento="",
    email="",
    nombre=""
):
    """
    Busca un candidato utilizando la prioridad:

    1. Documento
    2. Email
    3. Nombre

    El documento NO es obligatorio.
    """

    documento = _normalizar_documento(documento)
    email = _normalizar_email(email)
    nombre = _normalizar(nombre)

    conn = obtener_conexion()

    resultado = None

    if documento:
        resultado = conn.execute("""
            SELECT *
            FROM candidatos
            WHERE documento = ?
            LIMIT 1
        """, (documento,)).fetchone()

    if resultado is None and email:
        resultado = conn.execute("""
            SELECT *
            FROM candidatos
            WHERE LOWER(email) = ?
            LIMIT 1
        """, (email,)).fetchone()

    if resultado is None and nombre:
        resultado = conn.execute("""
            SELECT *
            FROM candidatos
            WHERE LOWER(nombre) = LOWER(?)
            LIMIT 1
        """, (nombre,)).fetchone()

    conn.close()

    return resultado


def crear_candidato(
    nombre,
    documento="",
    telefono="",
    email="",
    formacion="",
    experiencia="",
    habilidades=""
):
    """
    Crea un candidato independiente de la vacante.

    El documento es opcional.
    """

    nombre = _normalizar(nombre)

    if not nombre:
        raise ValueError("El nombre del candidato es obligatorio.")

    documento = _normalizar_documento(documento)
    telefono = _normalizar(telefono)
    email = _normalizar_email(email)
    formacion = _normalizar(formacion)
    experiencia = _normalizar(experiencia)
    habilidades = _normalizar(habilidades)

    # Buscar candidato existente.
    existente = buscar_candidato(
        documento=documento,
        email=email
    )

    if existente:
        return existente["id"]

    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO candidatos (
            nombre,
            documento,
            telefono,
            email,
            formacion,
            experiencia,
            habilidades
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        documento,
        telefono,
        email,
        formacion,
        experiencia,
        habilidades
    ))

    candidato_id = cursor.lastrowid

    _registrar_historial(
        cursor,
        "candidato",
        candidato_id,
        "creacion",
        f"Candidato creado: {nombre}"
    )

    conn.commit()
    conn.close()

    return candidato_id


def actualizar_candidato(
    candidato_id,
    nombre,
    documento="",
    telefono="",
    email="",
    formacion="",
    experiencia="",
    habilidades=""
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE candidatos
        SET
            nombre = ?,
            documento = ?,
            telefono = ?,
            email = ?,
            formacion = ?,
            experiencia = ?,
            habilidades = ?,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        _normalizar(nombre),
        _normalizar_documento(documento),
        _normalizar(telefono),
        _normalizar_email(email),
        _normalizar(formacion),
        _normalizar(experiencia),
        _normalizar(habilidades),
        candidato_id
    ))

    _registrar_historial(
        cursor,
        "candidato",
        candidato_id,
        "edicion",
        f"Candidato actualizado: {_normalizar(nombre)}"
    )

    conn.commit()
    conn.close()


# ============================================================
# POSTULACIONES
# ============================================================

def crear_postulacion(
    candidato_id,
    vacante_id,
    fuente="Manual",
    archivo_nombre="",
    archivo_ruta="",
    archivo_tipo="",
    texto_hv=""
):
    """
    Crea una postulación.

    Un mismo candidato no puede tener dos postulaciones
    idénticas para la misma vacante.
    """

    conn = obtener_conexion()
    cursor = conn.cursor()

    existente = cursor.execute("""
        SELECT *
        FROM postulaciones
        WHERE candidato_id = ?
        AND vacante_id = ?
    """, (
        candidato_id,
        vacante_id
    )).fetchone()

    if existente:
        conn.close()
        return existente["id"]

    cursor.execute("""
        INSERT INTO postulaciones (
            candidato_id,
            vacante_id,
            fuente,
            estado,
            archivo_nombre,
            archivo_ruta,
            archivo_tipo,
            texto_hv
        )
        VALUES (?, ?, ?, 'Pendiente', ?, ?, ?, ?)
    """, (
        candidato_id,
        vacante_id,
        _normalizar(fuente),
        _normalizar(archivo_nombre),
        _normalizar(archivo_ruta),
        _normalizar(archivo_tipo),
        texto_hv or ""
    ))

    postulacion_id = cursor.lastrowid

    _registrar_historial(
        cursor,
        "postulacion",
        postulacion_id,
        "creacion",
        f"Postulación creada para vacante {vacante_id}"
    )

    conn.commit()
    conn.close()

    return postulacion_id


def obtener_postulacion(postulacion_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT
            p.*,
            c.nombre,
            c.documento,
            c.telefono,
            c.email,
            v.titulo AS vacante_titulo,
            v.area AS vacante_area
        FROM postulaciones p
        JOIN candidatos c
            ON c.id = p.candidato_id
        JOIN vacantes v
            ON v.id = p.vacante_id
        WHERE p.id = ?
    """, (postulacion_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def obtener_postulaciones(vacante_id=None):
    """
    Obtiene todas las postulaciones o las filtra por vacante_id.
    Incluye datos del candidato, vacante y análisis IA asociado.
    """
    conn = obtener_conexion()

    query = """
        SELECT
            p.*,
            c.nombre,
            c.documento,
            c.telefono,
            c.email,
            v.titulo AS vacante_titulo,
            v.area AS vacante_area,
            a.id AS analisis_id,
            a.puntaje_total,
            a.decision,
            a.resumen_ejecutivo,
            a.cumplimiento_json,
            a.alertas_json,
            a.fecha_analisis
        FROM postulaciones p
        JOIN candidatos c
            ON c.id = p.candidato_id
        JOIN vacantes v
            ON v.id = p.vacante_id
        LEFT JOIN analisis a
            ON a.id = (
                SELECT MAX(a2.id)
                FROM analisis a2
                WHERE a2.postulacion_id = p.id
                  AND COALESCE(a2.estado, 'Completado') <> 'Error'
            )
    """
    params = ()
    if vacante_id:
        query += " WHERE p.vacante_id = ?"
        params = (vacante_id,)

    query += """
        ORDER BY
            CASE
                WHEN a.puntaje_total IS NULL THEN 1
                ELSE 0
            END,
            a.puntaje_total DESC,
            p.fecha_postulacion DESC
    """

    cursor = conn.execute(query, params)
    resultado = cursor.fetchall()
    conn.close()
    return resultado


def obtener_postulaciones_por_vacante(vacante_id):
    return obtener_postulaciones(vacante_id=vacante_id)


def obtener_postulaciones_por_candidato(candidato_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT
            p.*,
            v.titulo AS vacante_titulo,
            v.area AS vacante_area,
            v.estado AS vacante_estado,
            a.puntaje_total,
            a.decision,
            a.fecha_analisis
        FROM postulaciones p

        JOIN vacantes v
            ON v.id = p.vacante_id

        LEFT JOIN analisis a
            ON a.id = (
                SELECT MAX(a2.id)
                FROM analisis a2
                WHERE a2.postulacion_id = p.id
                  AND COALESCE(a2.estado, 'Completado') <> 'Error'
            )

        WHERE p.candidato_id = ?

        ORDER BY p.fecha_postulacion DESC
    """, (candidato_id,))

    resultado = cursor.fetchall()

    conn.close()

    return resultado


def actualizar_estado_postulacion(
    postulacion_id,
    nuevo_estado
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE postulaciones
        SET
            estado = ?,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        _normalizar(nuevo_estado),
        postulacion_id
    ))

    _registrar_historial(
        cursor,
        "postulacion",
        postulacion_id,
        "cambio_estado",
        f"Nuevo estado: {nuevo_estado}"
    )

    conn.commit()
    conn.close()


# ============================================================
# USUARIOS Y ROLES
#
# Roles: "admin" (control total) y "lector" (solo consulta: puede ver
# candidatos, descargar el informe en PDF y escribir observaciones, pero
# no crear/editar/eliminar vacantes o candidatos, ni ejecutar el análisis).
# ============================================================

# Las 4 casillas de permisos que existen en el sistema. "usuarios" es lo único
# que separa a un Súper administrador de alguien con Control total.
CAPACIDADES = ("candidatos", "proceso", "vacantes", "usuarios")

PERMISOS_PRESET = {
    "super_admin":   {"candidatos": True,  "proceso": True,  "vacantes": True,  "usuarios": True},
    "control_total": {"candidatos": True,  "proceso": True,  "vacantes": True,  "usuarios": False},
    "lector":        {"candidatos": False, "proceso": False, "vacantes": False, "usuarios": False},
}

ROLES_VALIDOS = ("super_admin", "control_total", "lector", "personalizado")

NOMBRES_ROL = {
    "super_admin": "Súper administrador",
    "control_total": "Control total",
    "lector": "Solo lector",
    "personalizado": "Personalizado",
}


def _normalizar_permisos(permisos):
    """Se queda solo con las 4 casillas conocidas; lo demás lo trata como False."""
    permisos = permisos or {}
    return {cap: bool(permisos.get(cap)) for cap in CAPACIDADES}


def _hash_password(password, sal=None):
    sal = sal or secrets.token_hex(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sal), 100_000)
    return f"{sal}${derivado.hex()}"


def _verificar_password(password, hash_guardado):
    try:
        sal, _ = hash_guardado.split("$", 1)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(_hash_password(password, sal), hash_guardado)


def crear_usuario(usuario, nombre, password, rol="lector", permisos=None):
    """
    Crea un usuario. Lanza ValueError si el usuario ya existe o si faltan datos.

    Para los roles predefinidos (super_admin, control_total, lector) los permisos
    son fijos y se ignora lo que llegue en `permisos`. Solo con rol="personalizado"
    se guardan los permisos indicados casilla por casilla.
    """
    usuario = _normalizar(usuario)
    rol = rol if rol in ROLES_VALIDOS else "lector"
    if not usuario or not password:
        raise ValueError("El usuario y la contraseña son obligatorios.")

    permisos_finales = PERMISOS_PRESET[rol] if rol in PERMISOS_PRESET else _normalizar_permisos(permisos)

    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuarios (usuario, nombre, password_hash, rol, permisos) VALUES (?, ?, ?, ?, ?)",
            (usuario, _normalizar(nombre) or usuario, _hash_password(password), rol, json.dumps(permisos_finales))
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise ValueError(f'Ya existe un usuario con el nombre "{usuario}".')
    finally:
        conn.close()


def _permisos_de_fila(fila):
    try:
        datos = json.loads(fila["permisos"]) if fila["permisos"] else None
    except (TypeError, ValueError):
        datos = None
    if datos is None:
        datos = PERMISOS_PRESET.get(fila["rol"], PERMISOS_PRESET["lector"])
    return _normalizar_permisos(datos)


def verificar_usuario(usuario, password):
    """Devuelve los datos del usuario (sin la contraseña) si las credenciales son válidas y está activo."""
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, usuario, nombre, password_hash, rol, permisos, activo FROM usuarios WHERE usuario = ?",
        (_normalizar(usuario),)
    )
    fila = cursor.fetchone()
    conn.close()

    if not fila or not fila["activo"] or not _verificar_password(password, fila["password_hash"]):
        return None
    return {
        "id": fila["id"], "usuario": fila["usuario"], "nombre": fila["nombre"],
        "rol": fila["rol"], "permisos": _permisos_de_fila(fila)
    }


def obtener_usuarios():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT id, usuario, nombre, rol, permisos, activo, fecha_creacion FROM usuarios ORDER BY id")
    filas = [dict(f) for f in cursor.fetchall()]
    conn.close()
    for f in filas:
        f["permisos"] = _permisos_de_fila(f)
    return filas


def hay_usuarios_registrados():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
    total = cursor.fetchone()[0]
    conn.close()
    return total > 0


def alternar_estado_usuario(usuario_id, activo):
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (1 if activo else 0, usuario_id))
    conn.commit()
    conn.close()


def eliminar_usuario(usuario_id):
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    conn.commit()
    conn.close()


def actualizar_observacion_postulacion(postulacion_id, observacion):
    """Guarda la observación manual del líder de TTHH sobre una postulación."""
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE postulaciones
        SET
            observaciones_tthh = ?,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        _normalizar(observacion),
        postulacion_id
    ))

    _registrar_historial(
        cursor,
        "postulacion",
        postulacion_id,
        "observacion_tthh",
        "Observación de TTHH actualizada"
    )

    conn.commit()
    conn.close()


def eliminar_postulacion(postulacion_id):
    """
    Elimina una postulación y sus análisis/documentos asociados.
    Si el candidato no cuenta con más postulaciones, lo elimina también de candidatos.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()

    row = cursor.execute(
        "SELECT candidato_id FROM postulaciones WHERE id = ?",
        (postulacion_id,)
    ).fetchone()
    candidato_id = row["candidato_id"] if row else None

    cursor.execute("DELETE FROM analisis WHERE postulacion_id = ?", (postulacion_id,))
    cursor.execute("DELETE FROM documentos WHERE postulacion_id = ?", (postulacion_id,))
    cursor.execute("DELETE FROM postulaciones WHERE id = ?", (postulacion_id,))

    if candidato_id:
        otras = cursor.execute(
            "SELECT COUNT(*) FROM postulaciones WHERE candidato_id = ?",
            (candidato_id,)
        ).fetchone()[0]
        if otras == 0:
            cursor.execute("DELETE FROM candidatos WHERE id = ?", (candidato_id,))
            _registrar_historial(
                cursor,
                "candidato",
                candidato_id,
                "eliminacion",
                f"Candidato {candidato_id} eliminado al no tener postulaciones activas"
            )

    _registrar_historial(
        cursor,
        "postulacion",
        postulacion_id,
        "eliminacion",
        f"Postulación {postulacion_id} eliminada"
    )

    conn.commit()
    conn.close()
    return True


def eliminar_candidato(candidato_id):
    """
    Elimina completamente a un candidato y todas sus postulaciones, análisis y documentos.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM analisis WHERE candidato_id = ?", (candidato_id,))
    cursor.execute("DELETE FROM documentos WHERE candidato_id = ?", (candidato_id,))
    cursor.execute("DELETE FROM postulaciones WHERE candidato_id = ?", (candidato_id,))
    cursor.execute("DELETE FROM candidatos WHERE id = ?", (candidato_id,))

    _registrar_historial(
        cursor,
        "candidato",
        candidato_id,
        "eliminacion",
        f"Candidato {candidato_id} eliminado con todas sus dependencias"
    )

    conn.commit()
    conn.close()
    return True


# ============================================================
# DOCUMENTOS / HOJAS DE VIDA
# ============================================================

def registrar_documento(
    postulacion_id,
    candidato_id,
    nombre_archivo,
    ruta_archivo="",
    tipo_archivo="",
    origen="Carga masiva"
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO documentos (
            postulacion_id,
            candidato_id,
            nombre_archivo,
            ruta_archivo,
            tipo_archivo,
            origen,
            estado_procesamiento
        )
        VALUES (?, ?, ?, ?, ?, ?, 'Pendiente')
    """, (
        postulacion_id,
        candidato_id,
        _normalizar(nombre_archivo),
        _normalizar(ruta_archivo),
        _normalizar(tipo_archivo),
        _normalizar(origen)
    ))

    documento_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return documento_id


def actualizar_estado_documento(
    documento_id,
    estado,
    error=""
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    if estado == "Procesado":
        cursor.execute("""
            UPDATE documentos
            SET
                estado_procesamiento = ?,
                error = ?,
                fecha_procesamiento = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            estado,
            _normalizar(error),
            documento_id
        ))

    else:
        cursor.execute("""
            UPDATE documentos
            SET
                estado_procesamiento = ?,
                error = ?
            WHERE id = ?
        """, (
            estado,
            _normalizar(error),
            documento_id
        ))

    conn.commit()
    conn.close()


def obtener_documentos_pendientes():
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM documentos
        WHERE estado_procesamiento = 'Pendiente'
        ORDER BY fecha_recepcion ASC
    """)

    resultado = cursor.fetchall()

    conn.close()

    return resultado


# ============================================================
# ANÁLISIS IA
# ============================================================

def guardar_analisis(
    candidato_id,
    vacante_id,
    puntaje_total,
    decision,
    resumen_ejecutivo,
    cumplimiento_json,
    alertas,
    postulacion_id=None,
    modelo_ia="openai/gpt-oss-20b"
):
    """
    Guarda un nuevo análisis IA.

    El análisis pertenece a una postulación.
    """

    try:
        puntaje_total = int(puntaje_total)
    except (TypeError, ValueError):
        puntaje_total = 0

    puntaje_total = max(0, min(100, puntaje_total))

    if isinstance(cumplimiento_json, (dict, list)):
        cumplimiento_json = json.dumps(
            cumplimiento_json,
            ensure_ascii=False
        )

    if isinstance(alertas, list):
        alertas = json.dumps(
            alertas,
            ensure_ascii=False
        )

    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO analisis (
            postulacion_id,
            candidato_id,
            vacante_id,
            puntaje_total,
            decision,
            resumen_ejecutivo,
            cumplimiento_json,
            alertas_json,
            modelo_ia,
            estado
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Completado')
    """, (
        postulacion_id,
        candidato_id,
        vacante_id,
        puntaje_total,
        _normalizar(decision),
        _normalizar(resumen_ejecutivo),
        cumplimiento_json or "{}",
        alertas or "[]",
        _normalizar(modelo_ia)
    ))

    analisis_id = cursor.lastrowid

    if postulacion_id:
        cursor.execute("""
            UPDATE postulaciones
            SET
                estado = 'Analizado',
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (postulacion_id,))

    _registrar_historial(
        cursor,
        "analisis",
        analisis_id,
        "analisis_ia",
        f"Análisis realizado. Puntaje: {puntaje_total}"
    )

    conn.commit()
    conn.close()

    return analisis_id


def guardar_error_analisis(
    candidato_id,
    vacante_id,
    error,
    postulacion_id=None
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO analisis (
            postulacion_id,
            candidato_id,
            vacante_id,
            estado,
            error
        )
        VALUES (?, ?, ?, 'Error', ?)
    """, (
        postulacion_id,
        candidato_id,
        vacante_id,
        _normalizar(error)
    ))

    analisis_id = cursor.lastrowid

    if postulacion_id:
        cursor.execute("""
            UPDATE postulaciones
            SET
                estado = 'Error',
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (postulacion_id,))

    conn.commit()
    conn.close()

    return analisis_id


def obtener_analisis_candidato(candidato_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM analisis
        WHERE candidato_id = ?
        ORDER BY fecha_analisis DESC
        LIMIT 1
    """, (candidato_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


def obtener_analisis_postulacion(postulacion_id):
    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT *
        FROM analisis
        WHERE postulacion_id = ?
        ORDER BY fecha_analisis DESC
        LIMIT 1
    """, (postulacion_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado


# ============================================================
# CORREO MICROSOFT 365
# ============================================================

def correo_ya_procesado(message_id):
    if not message_id:
        return False

    conn = obtener_conexion()

    cursor = conn.execute("""
        SELECT id
        FROM correos_procesados
        WHERE message_id = ?
        LIMIT 1
    """, (message_id,))

    resultado = cursor.fetchone()

    conn.close()

    return resultado is not None


def registrar_correo_procesado(
    message_id,
    asunto="",
    remitente="",
    fecha_correo="",
    cantidad_adjuntos=0,
    estado="Procesado",
    error=""
):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO correos_procesados (
            message_id,
            asunto,
            remitente,
            fecha_correo,
            cantidad_adjuntos,
            estado,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        message_id,
        _normalizar(asunto),
        _normalizar(remitente),
        _normalizar(fecha_correo),
        cantidad_adjuntos,
        _normalizar(estado),
        _normalizar(error)
    ))

    conn.commit()
    conn.close()


# ============================================================
# HISTORIAL
# ============================================================

def obtener_historial(
    entidad=None,
    entidad_id=None,
    limite=200
):
    conn = obtener_conexion()

    query = """
        SELECT *
        FROM historial
        WHERE 1 = 1
    """

    parametros = []

    if entidad:
        query += " AND entidad = ?"
        parametros.append(entidad)

    if entidad_id is not None:
        query += " AND entidad_id = ?"
        parametros.append(entidad_id)

    query += """
        ORDER BY fecha DESC
        LIMIT ?
    """

    parametros.append(limite)

    cursor = conn.execute(query, parametros)

    resultado = cursor.fetchall()

    conn.close()

    return resultado


# ============================================================
# DASHBOARD
# ============================================================

def obtener_metricas_dashboard():
    conn = obtener_conexion()
    cursor = conn.cursor()

    vacantes_abiertas = cursor.execute("""
        SELECT COUNT(*)
        FROM vacantes
        WHERE estado = 'Abierta'
    """).fetchone()[0]

    total_candidatos = cursor.execute("""
        SELECT COUNT(*)
        FROM candidatos
    """).fetchone()[0]

    total_postulaciones = cursor.execute("""
        SELECT COUNT(*)
        FROM postulaciones
    """).fetchone()[0]

    analisis_realizados = cursor.execute("""
        SELECT COUNT(DISTINCT candidato_id || '-' || vacante_id)
        FROM analisis
        WHERE estado = 'Completado'
    """).fetchone()[0]

    pendientes = cursor.execute("""
        SELECT COUNT(*)
        FROM postulaciones
        WHERE estado = 'Pendiente'
    """).fetchone()[0]

    requieren_revision = cursor.execute("""
        SELECT COUNT(*)
        FROM postulaciones
        WHERE estado IN ('Error', 'Revisión')
    """).fetchone()[0]

    conn.close()

    return {
        "vacantes_abiertas": vacantes_abiertas,
        "total_candidatos": total_candidatos,
        "total_postulaciones": total_postulaciones,
        "analisis_realizados": analisis_realizados,
        "pendientes": pendientes,
        "requieren_revision": requieren_revision
    }


# ============================================================
# REPORTES
# ============================================================

def obtener_reporte_completo_excel(vacante_id=None):
    conn = obtener_conexion()

    query = """
        SELECT
            v.titulo AS Vacante,
            v.area AS Area,

            c.nombre AS Candidato,
            c.documento AS Documento,
            c.email AS Correo,
            c.telefono AS Telefono,

            p.fuente AS Fuente,
            p.estado AS Estado_Postulacion,
            p.observaciones_tthh AS Observaciones_TTHH,
            p.fecha_postulacion AS Fecha_Postulacion,

            a.puntaje_total AS Puntaje_IA,
            a.decision AS Decision_IA,
            a.resumen_ejecutivo AS Resumen_Evaluacion,
            a.alertas_json AS Alertas_Detectadas,
            a.fecha_analisis AS Fecha_Evaluacion

        FROM postulaciones p

        JOIN candidatos c
            ON c.id = p.candidato_id

        JOIN vacantes v
            ON v.id = p.vacante_id

        LEFT JOIN analisis a
            ON a.id = (
                SELECT MAX(a2.id)
                FROM analisis a2
                WHERE a2.postulacion_id = p.id
                  AND COALESCE(a2.estado, 'Completado') <> 'Error'
            )
    """

    parametros = []

    if vacante_id is not None:
        query += " WHERE p.vacante_id = ?"
        parametros.append(vacante_id)

    query += """
        ORDER BY
            CASE
                WHEN a.puntaje_total IS NULL THEN 1
                ELSE 0
            END,
            a.puntaje_total DESC
    """

    cursor = conn.execute(query, parametros)
    columnas = [d[0] for d in cursor.description]
    df = pd.DataFrame(
        [tuple(fila) for fila in cursor.fetchall()],
        columns=columnas
    )

    conn.close()

    return df


# ============================================================
# INICIALIZAR AUTOMÁTICAMENTE
# ============================================================

inicializar_db()
