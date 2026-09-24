import os
import re
import html
import json
import time
import unicodedata
import streamlit as st
import pandas as pd
import database
import parser
from ai_engine import extraer_datos_candidato, analizar_candidato_vs_vacante, resumir_hoja_de_vida
from ponderacion import generar_tabla_ponderacion_html
from informe_pdf import generar_informe_pdf

def _leer_secreto(nombre, defecto=""):
    try:
        valor = st.secrets[nombre]
    except Exception:
        valor = os.getenv(nombre, defecto)
    return str(valor).strip()


# Inicializar Base de Datos al arrancar y sembrar el usuario administrador
# (una sola vez: si ya hay usuarios creados, no hace nada).
@st.cache_resource(show_spinner=False)
def _iniciar_db():
    database.inicializar_db()
    _usuario_semilla = _leer_secreto("APP_USER", "admin")
    if not database.hay_usuarios_registrados():
        _clave_semilla = _leer_secreto("APP_PASSWORD", "admin")
        database.crear_usuario(_usuario_semilla, "Administrador", _clave_semilla, "super_admin")
    else:
        # Autocorrección: la cuenta definida en Secrets siempre debe quedar
        # como Súper administrador, aunque algo la haya dejado con otro rol.
        database.asegurar_super_admin(_usuario_semilla)


_iniciar_db()

# Configuración de página
st.set_page_config(
    page_title="Sistema de Gestión — Talento Humano",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)


_VARS_CLARO = """
    --bg-main: #F8FAFC; --surface: #FFFFFF; --surface-alt: #F1F5F9; --border-color: #E2E8F0;
    --text-dark: #0F172A; --text-muted: #64748B; --text-soft: #475569;
    --sidebar-bg: #FFFFFF; --nav-hover: #EEF2FF; --nav-active-bg: #DBEAFE; --nav-active-text: #1E3A8A;
    --input-bg: #EEF2F7; --primary-blue: #0047FF; --accent-blue: #2563EB;
    --ok-bg: #DCFCE7; --ok-text: #166534; --ok-border: #BBF7D0;
    --warn-bg: #FEF3C7; --warn-text: #92400E; --warn-border: #FDE68A;
    --bad-bg: #FEE2E2; --bad-text: #991B1B; --bad-border: #FECACA;
    --info-bg: #EFF6FF; --info-text: #1D4ED8; --info-border: #BFDBFE;
    --login-grad: linear-gradient(180deg, #DBEAFE 0%, #EFF6FF 55%, #F8FAFC 100%);
"""

_VARS_OSCURO = """
    --bg-main: #0E1117; --surface: #161B26; --surface-alt: #1F2633; --border-color: #2E3648;
    --text-dark: #F1F5F9; --text-muted: #94A3B8; --text-soft: #CBD5E1;
    --sidebar-bg: #111827; --nav-hover: #1F2937; --nav-active-bg: #1E3A8A; --nav-active-text: #FFFFFF;
    --input-bg: #232B3B; --primary-blue: #60A5FA; --accent-blue: #3B82F6;
    --ok-bg: #12351F; --ok-text: #86EFAC; --ok-border: #1F5C36;
    --warn-bg: #3A2A0B; --warn-text: #FCD34D; --warn-border: #6B4A10;
    --bad-bg: #3B1416; --bad-text: #FCA5A5; --bad-border: #7F1D1D;
    --info-bg: #172554; --info-text: #93C5FD; --info-border: #1E3A8A;
    --login-grad: linear-gradient(180deg, #0B1220 0%, #111827 60%, #0E1117 100%);
"""


def _css_tema():
    """Variables de color claro/oscuro: siguen el tema activo de Streamlit o, si no se conoce, el del sistema."""
    try:
        tipo = st.context.theme.type
    except Exception:
        tipo = None
    css = "<style>:root {" + _VARS_CLARO + "}"
    if tipo == "dark":
        css += ":root {" + _VARS_OSCURO + "}"
    elif tipo != "light":
        css += "@media (prefers-color-scheme: dark) { :root {" + _VARS_OSCURO + "} }"
    return css + "</style>"


_TEMA_CSS = _css_tema()

_LOGIN_CSS = """
<style>
    .stApp {
        background: var(--login-grad) !important;
    }
    [data-testid="stHeader"] { background: transparent !important; }
    [data-testid="stMainBlockContainer"], .block-container {
        max-width: 420px !important;
        margin: 110px auto 0 auto !important;
        padding: 0 40px 36px 40px !important;
        background: var(--surface);
        border-radius: 28px;
        box-shadow: 0 18px 50px rgba(30, 58, 138, 0.18);
    }
    .login-avatar {
        width: 88px; height: 88px; border-radius: 50%;
        background: linear-gradient(135deg, #1E3A8A, #2563EB);
        display: flex; align-items: center; justify-content: center;
        position: relative; top: -44px; margin: 0 auto -20px auto;
        box-shadow: 0 10px 24px rgba(37, 99, 235, 0.35);
    }
    .login-titulo {
        text-align: center; font-size: 1.25rem; font-weight: 700; color: var(--text-dark);
    }
    .login-sub {
        text-align: center; font-size: 0.85rem; color: var(--text-muted); margin-bottom: 22px;
    }
    [data-testid="stForm"] { border: none !important; padding: 0 !important; }
    [data-baseweb="input"], [data-baseweb="base-input"] {
        background-color: var(--input-bg) !important;
        border: none !important;
        border-radius: 999px !important;
    }
    [data-testid="stTextInput"] input {
        background-color: transparent !important;
        color: var(--text-dark) !important;
        padding: 12px 18px !important;
    }
    [data-testid="stFormSubmitButton"] button {
        background: linear-gradient(90deg, #1E3A8A, #2563EB) !important;
        border: none !important;
        border-radius: 999px !important;
        padding: 10px 0 !important;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.30) !important;
    }
    [data-testid="stFormSubmitButton"] button p {
        color: #FFFFFF !important; font-weight: 700 !important; letter-spacing: 1.5px !important;
    }
</style>
"""

_LOGIN_ENCABEZADO = """
<div class="login-avatar">
    <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0z"/>
    </svg>
</div>
<div class="login-titulo">Sistema de Talento Humano</div>
<div class="login-sub">Colegio Americano de Barranquilla</div>
"""

if database.hay_usuarios_registrados() and not st.session_state.get("autenticado"):
    st.markdown(_TEMA_CSS, unsafe_allow_html=True)
    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)
    st.markdown(_LOGIN_ENCABEZADO, unsafe_allow_html=True)
    with st.form("form_login"):
        _usuario = st.text_input("Usuario", placeholder="Usuario", label_visibility="collapsed")
        _clave = st.text_input("Contraseña", type="password", placeholder="Contraseña", label_visibility="collapsed")
        _entrar = st.form_submit_button("INGRESAR", type="primary", use_container_width=True)
    if _entrar:
        _datos_usuario = database.verificar_usuario(_usuario.strip(), _clave.strip())
        if _datos_usuario:
            st.session_state.autenticado = True
            st.session_state.usuario_id = _datos_usuario["id"]
            st.session_state.nombre_usuario = _datos_usuario["nombre"]
            st.session_state.rol = _datos_usuario["rol"]
            st.session_state.permisos = _datos_usuario["permisos"]
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()

if "rol" not in st.session_state:
    # No hay usuarios configurados todavía (modo de desarrollo sin login): control total.
    st.session_state.rol = "super_admin"
    st.session_state.nombre_usuario = st.session_state.get("nombre_usuario", "Administrador")
    st.session_state.permisos = database.PERMISOS_PRESET["super_admin"]

PERMISOS = st.session_state.get("permisos") or {}
PUEDE_CANDIDATOS = bool(PERMISOS.get("candidatos"))
PUEDE_PROCESO = bool(PERMISOS.get("proceso"))
PUEDE_VACANTES = bool(PERMISOS.get("vacantes"))
PUEDE_USUARIOS = bool(PERMISOS.get("usuarios"))
NOMBRE_ROL = database.NOMBRES_ROL.get(st.session_state.get("rol"), "Personalizado")

# =============================================================================
# ESTILOS CSS SAAS / ERP (Sin círculos en radio, solo sombreado, sin emojis)
# =============================================================================
STYLING_ERP = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .stApp {
        background-color: var(--bg-main);
        color: var(--text-dark);
    }

    /* BARRA LATERAL (Sidebar Dark Navy ERP) */
    [data-testid="stSidebar"] {
        background-color: var(--sidebar-bg) !important;
        border-right: 1px solid var(--border-color);
        padding-top: 0.5rem;
    }

    .sidebar-brand-box {
        padding: 12px 14px;
        margin-bottom: 18px;
        border-bottom: 1px solid var(--border-color);
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .sidebar-logo-badge {
        width: 38px;
        height: 38px;
        background-color: var(--primary-blue);
        border-radius: 8px;
        color: #FFFFFF;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.05rem;
        font-weight: 700;
        box-shadow: 0 2px 6px rgba(0, 71, 255, 0.4);
    }

    .sidebar-brand-text {
        display: flex;
        flex-direction: column;
    }

    .sidebar-brand-title {
        color: var(--text-dark);
        font-size: 0.95rem;
        font-weight: 700;
        letter-spacing: 0.2px;
    }

    .sidebar-brand-sub {
        color: var(--text-muted);
        font-size: 0.75rem;
    }

    .perfil-box {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 8px 10px 8px;
        margin-top: 6px;
        border-top: 1px solid var(--border-color);
    }

    .perfil-avatar {
        width: 38px;
        height: 38px;
        border-radius: 50%;
        background-color: var(--nav-active-bg);
        color: var(--nav-active-text);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 1rem;
    }

    .perfil-nombre {
        display: block;
        color: var(--text-dark);
        font-weight: 600;
        font-size: 0.9rem;
    }

    .perfil-rol {
        display: block;
        color: var(--text-muted);
        font-size: 0.75rem;
    }

    [data-testid="stSidebar"] .st-key-btn_cerrar_sesion button {
        background-color: var(--bad-bg) !important;
        border: 1px solid var(--bad-border) !important;
        justify-content: center !important;
    }

    [data-testid="stSidebar"] .st-key-btn_cerrar_sesion button p {
        color: var(--bad-text) !important;
        font-weight: 600 !important;
        text-align: center !important;
    }

    /* MENÚ LATERAL CON BOTONES (texto siempre visible) */
    [data-testid="stSidebar"] .stButton > button {
        background-color: transparent !important;
        border: 1px solid transparent !important;
        box-shadow: none !important;
        justify-content: flex-start !important;
        text-align: left !important;
        padding: 10px 16px !important;
        border-radius: 8px !important;
    }

    [data-testid="stSidebar"] .stButton > button p,
    [data-testid="stSidebar"] .stButton > button div,
    [data-testid="stSidebar"] .stButton > button span {
        color: var(--text-dark) !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        text-align: left !important;
    }

    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: var(--nav-hover) !important;
    }

    [data-testid="stSidebar"] .stButton > button[kind="primary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
        background-color: var(--nav-active-bg) !important;
        border-left: 4px solid var(--accent-blue) !important;
    }

    [data-testid="stSidebar"] .stButton > button[kind="primary"] p,
    [data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] p {
        color: var(--nav-active-text) !important;
        font-weight: 700 !important;
    }

    [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
        color: var(--text-muted) !important;
    }

    /* OCULTAR CÍRCULO DEL RADIO BUTTON EN SIDEBAR POR COMPLETO */
    [data-testid="stSidebar"] div[data-testid="stRadio"] > label {
        display: none !important;
    }

    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] {
        gap: 4px;
    }

    /* Eliminar el círculo y el bullet del radio */
    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 0 !important;
        visibility: hidden !important;
    }

    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label input[type="radio"] {
        display: none !important;
    }

    /* Elemento de menú en sidebar estilo ERP */
    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label {
        background-color: transparent !important;
        color: #94A3B8 !important;
        padding: 10px 16px !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
        font-size: 0.92rem !important;
        transition: all 0.15s ease !important;
        border: 1px solid transparent !important;
        display: flex !important;
        align-items: center !important;
        cursor: pointer !important;
        width: 100% !important;
        margin-bottom: 2px !important;
    }

    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label:hover {
        background-color: rgba(255, 255, 255, 0.06) !important;
        color: #FFFFFF !important;
    }

    /* SOLO SOMBREADO EN LA OPCIÓN SELECCIONADA (SIN CÍRCULO) */
    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label[data-checked="true"],
    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) {
        background-color: var(--text-dark) !important;
        color: #FFFFFF !important;
        font-weight: 600 !important;
        border-left: 4px solid var(--accent-blue) !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2) !important;
    }

    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label[data-checked="true"] p,
    [data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) p {
        color: #FFFFFF !important;
        font-weight: 600 !important;
    }

    /* ENCABEZADOS Y BREADCRUMB */
    .top-breadcrumb {
        color: var(--primary-blue);
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.6px;
        text-transform: uppercase;
        margin-bottom: 2px;
    }

    .top-title {
        color: var(--text-dark);
        font-size: 1.65rem;
        font-weight: 700;
        margin-bottom: 1.2rem;
    }

    /* TARJETAS DE MÉTRICAS (KPI Cards idénticas a la imagen) */
    .kpi-row {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 16px;
        margin-bottom: 24px;
    }

    .kpi-card {
        background-color: var(--surface);
        border: 1px solid var(--border-color);
        border-left: 4px solid var(--primary-blue);
        border-radius: 8px;
        padding: 14px 18px;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
    }

    .kpi-label {
        font-size: 0.78rem;
        color: var(--text-muted);
        font-weight: 500;
        margin-bottom: 6px;
    }

    .kpi-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: var(--text-dark);
        line-height: 1.1;
    }

    /* BADGES */
    .badge-status {
        display: inline-block;
        padding: 3px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .status-pendiente { background-color: var(--surface-alt); color: var(--text-soft); }
    .status-revision { background-color: var(--warn-bg); color: var(--warn-text); }
    .status-entrevista { background-color: var(--ok-bg); color: var(--ok-text); }
    .status-aprobado { background-color: var(--ok-bg); color: var(--ok-text); }
    .status-descartado { background-color: var(--bad-bg); color: var(--bad-text); }

    .badge-score-pill {
        display: inline-block;
        padding: 3px 9px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 700;
    }
    .score-high { background-color: var(--ok-bg); color: var(--ok-text); border: 1px solid var(--ok-border); }
    .score-mid { background-color: var(--warn-bg); color: var(--warn-text); border: 1px solid var(--warn-border); }
    .score-low { background-color: var(--bad-bg); color: var(--bad-text); border: 1px solid var(--bad-border); }
    .score-none { background-color: var(--surface-alt); color: var(--text-muted); }

    /* BOTONES */
    div.stButton > button {
        border-radius: 6px !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        padding: 0.5rem 1rem !important;
        transition: all 0.15s ease !important;
    }
</style>
"""
st.markdown(_TEMA_CSS, unsafe_allow_html=True)
st.markdown(STYLING_ERP, unsafe_allow_html=True)

# Inicialización de buffers en session_state
if 'buffer_contacto' not in st.session_state:
    st.session_state.buffer_contacto = {"nombre": "", "documento": "", "telefono": "", "email": ""}

if 'texto_cv' not in st.session_state:
    st.session_state.texto_cv = ""

if 'texto_cv_original' not in st.session_state:
    st.session_state.texto_cv_original = ""

if 'uploader_nonce' not in st.session_state:
    st.session_state.uploader_nonce = 0

if 'ultimo_archivo' not in st.session_state:
    st.session_state.ultimo_archivo = None

if 'ver_analisis_id' not in st.session_state:
    st.session_state.ver_analisis_id = None


# =============================================================================
# CARGA MASIVA DE HOJAS DE VIDA
# =============================================================================
MAX_LOTE_MASIVO = 30       # máximo de archivos por carga
PAUSA_MASIVO_SEG = 40      # segundos mínimos por hoja de vida (límite de tokens por minuto de Groq gratis)


def _con_reintento(funcion, *args):
    """Llama a la IA y, si Groq responde por límite de uso, espera y reintenta."""
    res = funcion(*args)
    for _ in range(2):
        if isinstance(res, dict) and "límite" in str(res.get("error", "")).lower():
            time.sleep(65)
            res = funcion(*args)
        else:
            break
    return res


def _procesar_hv_masiva(archivo, vacante):
    """Procesa una hoja de vida: extrae datos, crea/actualiza candidato y postulación, y la analiza."""
    fila = {"Archivo": archivo.name, "Candidato": "", "Correo": "", "Resultado": "", "Puntaje": None, "Decisión": ""}
    try:
        nombre_guardado, ruta = parser.guardar_archivo_subido(archivo)
        archivo.seek(0)
        texto = parser.procesar_documento(archivo, nombre_archivo=archivo.name)
        if not texto.strip():
            fila["Resultado"] = "Sin texto legible (posible PDF escaneado)"
            return fila

        datos = _con_reintento(extraer_datos_candidato, texto[:3500])
        if "error" in datos:
            fila["Resultado"] = f"Error al extraer datos: {datos['error']}"
            return fila

        nombre = datos["nombre"] or os.path.splitext(archivo.name)[0]
        fila["Candidato"] = nombre
        fila["Correo"] = datos["email"]
        if not datos["email"]:
            fila["Resultado"] = "Sin correo en la HV: cárguela manualmente"
            return fila

        ya_existia = database.buscar_candidato(documento=datos["documento"], email=datos["email"]) is not None
        candidato_id = database.crear_candidato(
            nombre=nombre,
            documento=datos["documento"],
            telefono=datos["telefono"],
            email=datos["email"]
        )
        postulacion_id = database.crear_postulacion(
            candidato_id=candidato_id,
            vacante_id=vacante["id"],
            fuente="Carga Masiva",
            archivo_nombre=nombre_guardado,
            archivo_ruta=ruta,
            archivo_tipo=os.path.splitext(nombre_guardado)[1].lower(),
            texto_hv=texto
        )
        database.registrar_documento(
            postulacion_id=postulacion_id,
            candidato_id=candidato_id,
            nombre_archivo=nombre_guardado,
            ruta_archivo=ruta,
            tipo_archivo=os.path.splitext(nombre_guardado)[1].lower(),
            origen="Carga Masiva"
        )

        res = _con_reintento(analizar_candidato_vs_vacante, dict(vacante), texto)
        if "error" in res:
            database.guardar_error_analisis(
                candidato_id=candidato_id,
                vacante_id=vacante["id"],
                error=res["error"],
                postulacion_id=postulacion_id
            )
            fila["Resultado"] = f"Error de IA: {res['error']}"
            return fila

        database.guardar_analisis(
            candidato_id=candidato_id,
            vacante_id=vacante["id"],
            puntaje_total=res.get("puntaje_total", 0),
            decision=res.get("decision", "Pendiente"),
            resumen_ejecutivo=res.get("resumen_ejecutivo", ""),
            cumplimiento_json=res.get("cumplimiento_criterios", {}),
            alertas=res.get("alertas_detectadas", []),
            postulacion_id=postulacion_id
        )
        fila["Resultado"] = "Re-analizado (ya existía)" if ya_existia else "Analizado (nuevo)"
        fila["Puntaje"] = res.get("puntaje_total", 0)
        fila["Decisión"] = res.get("decision", "")
    except Exception as e:
        fila["Resultado"] = f"Error: {e}"
    return fila


# =============================================================================
# BARRA LATERAL (SIDEBAR ERP)
# =============================================================================
with st.sidebar:
    st.markdown("""
        <div class="sidebar-brand-box">
            <div class="sidebar-logo-badge">TH</div>
            <div class="sidebar-brand-text">
                <span class="sidebar-brand-title">Sistema de Gestión</span>
                <span class="sidebar-brand-sub">Talento Humano & Selección</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    if "opcion_menu" not in st.session_state:
        st.session_state.opcion_menu = "Candidatos"

    _items_menu = ["Candidatos", "Vacantes", "Dashboard", "Reportes & Historial"]
    if PUEDE_USUARIOS:
        _items_menu.append("Usuarios")

    for _item in _items_menu:
        if st.button(
            _item,
            key=f"nav_{_item}",
            use_container_width=True,
            type="primary" if st.session_state.opcion_menu == _item else "secondary"
        ):
            st.session_state.opcion_menu = _item
            st.rerun()

    opcion = st.session_state.opcion_menu
    st.markdown("<br><br>", unsafe_allow_html=True)

    if st.session_state.get("autenticado"):
        _nombre_usuario = st.session_state.get("nombre_usuario", "Usuario")
        _etiqueta_rol = NOMBRE_ROL
        st.markdown(f"""
            <div class="perfil-box">
                <div class="perfil-avatar">{html.escape(_nombre_usuario[:1].upper())}</div>
                <div>
                    <span class="perfil-nombre">{html.escape(_nombre_usuario)}</span>
                    <span class="perfil-rol">{html.escape(_etiqueta_rol)}</span>
                </div>
            </div>
        """, unsafe_allow_html=True)
        if st.button("Cerrar sesión", key="btn_cerrar_sesion", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    st.caption("v2.1 — Asistente IA TTHH")


# =============================================================================
# VISTA 1: GESTIÓN DE CANDIDATOS
# =============================================================================
if opcion == "Candidatos":
    # 1. Cabecera y Buscador
    c_head1, c_head2, c_head3 = st.columns([3, 1.6, 1.6])
    with c_head1:
        st.markdown('<div class="top-breadcrumb">SISTEMA DE TALENTO HUMANO</div>', unsafe_allow_html=True)
        st.markdown('<div class="top-title">Gestión de Selección de Personal</div>', unsafe_allow_html=True)
    with c_head2:
        st.markdown("<br>", unsafe_allow_html=True)
        busqueda = st.text_input("Buscar:", placeholder="Buscar por nombre, cargo o correo...", label_visibility="collapsed")
    with c_head3:
        st.markdown("<br>", unsafe_allow_html=True)
        _vacs_filtro = [dict(v) for v in database.obtener_vacantes()]
        _opciones_filtro = ["Todas las vacantes"] + [f"#{v['id']} - {v['titulo']} ({v['area']})" for v in _vacs_filtro]
        vac_sel_filtro = st.selectbox("Filtrar por vacante:", _opciones_filtro, label_visibility="collapsed")

    # 2. Fila de Tarjetas de Métricas (KPI Cards como en la imagen)
    metricas = database.obtener_metricas_dashboard()
    total_por_revisar = metricas.get("pendientes", 0) + metricas.get("requieren_revision", 0)

    st.markdown(f"""
        <div class="kpi-row">
            <div class="kpi-card">
                <div class="kpi-label">Total vacantes</div>
                <div class="kpi-value">{metricas.get('vacantes_abiertas', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Postulaciones</div>
                <div class="kpi-value">{metricas.get('total_postulaciones', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Analizados con IA</div>
                <div class="kpi-value">{metricas.get('analisis_realizados', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Por revisar</div>
                <div class="kpi-value">{total_por_revisar}</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 3. Sub-encabezado
    st.markdown("""
        <div style="margin-bottom: 14px;">
            <span style="font-size: 1.15rem; font-weight: 700; color: var(--text-dark);">Registro y Evaluación de Candidatos</span><br>
            <span style="font-size: 0.85rem; color: var(--text-muted);">Registre candidatos, procese su Hoja de Vida y consulte el estudio automatizado de afinidad con IA.</span>
        </div>
    """, unsafe_allow_html=True)

    vacantes_abiertas = database.obtener_vacantes_abiertas()
    if not vacantes_abiertas:
        vacantes_abiertas = database.obtener_vacantes()

    if not vacantes_abiertas:
        st.warning("No existen vacantes registradas en el sistema. Vaya a la sección 'Vacantes' para crear la primera.")
    else:
        # 4. Formulario estilo Card (Como en la imagen de referencia)
        with st.expander("+ Formulario de Registro y Extracción de Hoja de Vida", expanded=False):
            if not PUEDE_CANDIDATOS:
                st.info("Su usuario no tiene permiso para registrar candidatos. Puede consultarlos en la tabla de abajo.")
            else:
                v_dict = {f"#{v['id']} - {v['titulo']} ({v['area']})": dict(v) for v in vacantes_abiertas}
                v_sel_nombre = st.selectbox("Vacante Objetivo *", list(v_dict.keys()))
                v_actual = v_dict[v_sel_nombre]

                col_up1, col_up2 = st.columns([3, 1])
                with col_up1:
                    adjunto = st.file_uploader(
                        "Cargar Hoja de Vida (PDF, DOCX, TXT):",
                        type=["pdf", "docx", "txt"],
                        key=f"uploader_cv_main_{st.session_state.uploader_nonce}"
                    )
                    if adjunto is not None and st.session_state.get("ultimo_archivo", {}).get("nombre_original") != adjunto.name:
                        nombre_archivo, ruta_archivo = parser.guardar_archivo_subido(adjunto)
                        st.session_state.ultimo_archivo = {
                            "nombre": nombre_archivo,
                            "nombre_original": adjunto.name,
                            "ruta": ruta_archivo,
                            "tipo": os.path.splitext(nombre_archivo)[1].lower()
                        }
                        texto_extraido = parser.procesar_documento(adjunto, nombre_archivo=adjunto.name)
                        st.session_state.texto_cv_original = texto_extraido
                        if texto_extraido.strip():
                            with st.spinner("Generando resumen de la Hoja de Vida..."):
                                st.session_state.texto_cv = resumir_hoja_de_vida(texto_extraido)
                        else:
                            st.session_state.texto_cv = texto_extraido

                with col_up2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Extraer Datos con IA", use_container_width=True, help="Extrae automáticamente los datos de contacto desde el texto"):
                        _texto_para_extraer = st.session_state.texto_cv_original.strip() or st.session_state.texto_cv.strip()
                        if not _texto_para_extraer:
                            st.warning("Adjunte un archivo o ingrese el texto de la HV primero.")
                        else:
                            with st.spinner("Extrayendo datos de contacto..."):
                                dc = extraer_datos_candidato(_texto_para_extraer)
                                if "error" in dc:
                                    st.error(dc["error"])
                                else:
                                    st.session_state.buffer_contacto = {
                                        "nombre": dc.get("nombre", ""),
                                        "documento": dc.get("documento", ""),
                                        "telefono": dc.get("telefono", ""),
                                        "email": dc.get("email", "")
                                    }
                                    st.toast("Campos extraídos con éxito", icon="✔")
                                    st.rerun()

                col_f1, col_f2, col_f3 = st.columns(3)
                with col_f1:
                    c_nom = st.text_input("Nombre Completo *", value=st.session_state.buffer_contacto["nombre"])
                    c_tel = st.text_input("Teléfono", value=st.session_state.buffer_contacto["telefono"])
                with col_f2:
                    # CAMPO DE DOCUMENTO DE IDENTIDAD OPCIONAL
                    c_doc = st.text_input(
                        "Documento de Identidad (Opcional)",
                        value=st.session_state.buffer_contacto["documento"],
                        placeholder="Solo si la HV lo contiene"
                    )
                    c_mail = st.text_input("Correo Electrónico *", value=st.session_state.buffer_contacto["email"])
                with col_f3:
                    st.markdown("**Resumen de Vacante Seleccionada:**")
                    st.caption(f"**Cargo:** {v_actual['titulo']}\n\n**Área:** {v_actual['area']}\n\n**Requisitos:** {v_actual['experiencia'] or 'No especificados'}")

                c_texto_doc = st.text_area(
                    "Contenido textual de la Hoja de Vida:",
                    value=st.session_state.texto_cv,
                    height=150,
                    placeholder="El texto extraído de la hoja de vida aparecerá aquí..."
                )

                col_btn_l, col_btn_r1, col_btn_r2 = st.columns([4, 2, 2])
                with col_btn_r1:
                    if st.button("Limpiar Formulario", use_container_width=True):
                        st.session_state.buffer_contacto = {"nombre": "", "documento": "", "telefono": "", "email": ""}
                        st.session_state.texto_cv = ""
                        st.session_state.texto_cv_original = ""
                        st.session_state.ultimo_archivo = None
                        st.session_state.uploader_nonce += 1
                        st.rerun()

                with col_btn_r2:
                    btn_guardar_analizar = st.button("Guardar y Analizar con IA", type="primary", use_container_width=True)

                if btn_guardar_analizar:
                    if not c_nom.strip() or not c_mail.strip():
                        st.error("Diligencie los campos obligatorios: Nombre Completo y Correo Electrónico.")
                    elif not c_texto_doc.strip():
                        st.error("Se requiere el texto de la Hoja de Vida para poder realizar el análisis.")
                    else:
                        # 1. Crear o actualizar candidato (documento es opcional)
                        candidato_id = database.crear_candidato(
                            nombre=c_nom,
                            documento=c_doc,
                            telefono=c_tel,
                            email=c_mail
                        )

                        # 2. Crear postulación
                        arch = st.session_state.ultimo_archivo or {}
                        postulacion_id = database.crear_postulacion(
                            candidato_id=candidato_id,
                            vacante_id=v_actual["id"],
                            fuente="Carga Manual",
                            archivo_nombre=arch.get("nombre", ""),
                            archivo_ruta=arch.get("ruta", ""),
                            archivo_tipo=arch.get("tipo", ""),
                            texto_hv=c_texto_doc
                        )

                        # 3. Registrar documento si existe
                        if arch:
                            database.registrar_documento(
                                postulacion_id=postulacion_id,
                                candidato_id=candidato_id,
                                nombre_archivo=arch.get("nombre", ""),
                                ruta_archivo=arch.get("ruta", ""),
                                tipo_archivo=arch.get("tipo", ""),
                                origen="Carga Manual"
                            )

                        # 4. Ejecutar Análisis IA
                        with st.spinner("La IA está estudiando el perfil frente a los requisitos de la vacante..."):
                            res_ia = analizar_candidato_vs_vacante(dict(v_actual), c_texto_doc)

                        if "error" in res_ia:
                            database.guardar_error_analisis(
                                candidato_id=candidato_id,
                                vacante_id=v_actual["id"],
                                error=res_ia["error"],
                                postulacion_id=postulacion_id
                            )
                            st.error(f"Error en el análisis de IA: {res_ia['error']}")
                        else:
                            database.guardar_analisis(
                                candidato_id=candidato_id,
                                vacante_id=v_actual["id"],
                                puntaje_total=res_ia.get("puntaje_total", 0),
                                decision=res_ia.get("decision", "Pendiente"),
                                resumen_ejecutivo=res_ia.get("resumen_ejecutivo", ""),
                                cumplimiento_json=res_ia.get("cumplimiento_criterios", {}),
                                alertas=res_ia.get("alertas_detectadas", []),
                                postulacion_id=postulacion_id
                            )
                            st.session_state.ver_analisis_id = postulacion_id
                            st.toast("Candidato guardado y analizado exitosamente", icon="✔")

                        # Limpiar formulario por completo, incluido el archivo cargado
                        st.session_state.buffer_contacto = {"nombre": "", "documento": "", "telefono": "", "email": ""}
                        st.session_state.texto_cv = ""
                        st.session_state.texto_cv_original = ""
                        st.session_state.ultimo_archivo = None
                        st.session_state.uploader_nonce += 1
                        st.rerun()

        # 4b. CARGA MASIVA DE HOJAS DE VIDA
        with st.expander("+ Carga Masiva de Hojas de Vida (varias a la vez)", expanded=False):
            if not PUEDE_CANDIDATOS:
                st.info("Su usuario no tiene permiso para realizar cargas masivas de hojas de vida.")
            else:
                if st.session_state.get("resultado_masivo"):
                    st.markdown("**Resultado de la última carga masiva:**")
                    st.dataframe(pd.DataFrame(st.session_state.resultado_masivo), use_container_width=True, hide_index=True)
                    if st.button("Cerrar resumen", key="cerrar_resumen_masivo"):
                        st.session_state.resultado_masivo = None
                        st.rerun()

                st.caption(
                    f"Cada archivo se lee, se extraen sus datos de contacto, se crea el candidato y su postulación y se analiza con IA. "
                    f"Máximo {MAX_LOTE_MASIVO} archivos por carga, unos {PAUSA_MASIVO_SEG} segundos por hoja de vida por el límite de la IA gratuita. "
                    f"Mantenga esta pestaña abierta mientras procesa."
                )
                v_sel_masivo = st.selectbox("Vacante Objetivo *", list(v_dict.keys()), key="vacante_masiva")
                archivos_masivos = st.file_uploader(
                    "Cargar varias Hojas de Vida (PDF, DOCX, TXT):",
                    type=["pdf", "docx", "txt"],
                    accept_multiple_files=True,
                    key="uploader_cv_masivo"
                )
                if archivos_masivos:
                    st.caption(f"{len(archivos_masivos)} archivo(s) seleccionado(s).")

                if st.button("Procesar y Analizar Todas", type="primary", key="btn_masivo", disabled=not archivos_masivos):
                    if len(archivos_masivos) > MAX_LOTE_MASIVO:
                        st.error(f"Seleccionó {len(archivos_masivos)} archivos; el máximo por carga es {MAX_LOTE_MASIVO}.")
                    else:
                        v_masiva = v_dict[v_sel_masivo]
                        resultados = []
                        total_archivos = len(archivos_masivos)
                        barra = st.progress(0.0)
                        estado_masivo = st.empty()
                        for i, archivo in enumerate(archivos_masivos, start=1):
                            estado_masivo.caption(f"Procesando {i} de {total_archivos}: {archivo.name}")
                            inicio = time.time()
                            resultados.append(_procesar_hv_masiva(archivo, v_masiva))
                            barra.progress(i / total_archivos)
                            if i < total_archivos:
                                time.sleep(max(0, PAUSA_MASIVO_SEG - (time.time() - inicio)))
                        st.session_state.resultado_masivo = resultados
                        st.rerun()

        # 5. FICHA DETALLADA DE ANÁLISIS IA (Resultado claro y tabla de ponderación)
    if st.session_state.ver_analisis_id:
        p_sel_raw = database.obtener_postulacion(st.session_state.ver_analisis_id)
        if p_sel_raw:
            p_sel = dict(p_sel_raw)
            vac_obj = dict(database.obtener_vacante(p_sel["vacante_id"]))

            st.markdown("---")
            col_f_title, col_f_close = st.columns([5, 1])
            with col_f_title:
                st.markdown(f"### Ficha de Evaluación IA: {p_sel['nombre']}")
                st.caption(f"Postulación a: **{p_sel['vacante_titulo']}** ({p_sel['vacante_area']}) | Estado actual en proceso: **{p_sel['estado']}**")
            with col_f_close:
                if st.button("Cerrar Ficha", use_container_width=True):
                    st.session_state.ver_analisis_id = None
                    st.rerun()

            # Obtener datos de análisis
            postuls_totales = [dict(p) for p in database.obtener_postulaciones()]
            p_actual_analisis = next((p for p in postuls_totales if p["id"] == st.session_state.ver_analisis_id), None)

            if p_actual_analisis and p_actual_analisis.get("puntaje_total") is not None:
                score = int(p_actual_analisis.get("puntaje_total", 0))
                decision = p_actual_analisis.get("decision") or "En revisión"
                resumen = p_actual_analisis.get("resumen_ejecutivo") or "Sin resumen disponible."

                # Banner de Veredicto y Score
                color_score = "var(--ok-text)" if score >= 70 else "var(--warn-text)" if score >= 50 else "var(--bad-text)"
                bg_score = "var(--ok-bg)" if score >= 70 else "var(--warn-bg)" if score >= 50 else "var(--bad-bg)"

                st.markdown(f"""
                    <div style="background-color: {bg_score}; border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-size: 0.8rem; font-weight: 600; text-transform: uppercase; color: {color_score};">Veredicto de la Inteligencia Artificial</span><br>
                            <span style="font-size: 1.3rem; font-weight: 700; color: {color_score};">{decision}</span>
                        </div>
                        <div style="text-align: right;">
                            <span style="font-size: 0.8rem; font-weight: 600; color: {color_score};">Afinidad Documental</span><br>
                            <span style="font-size: 1.9rem; font-weight: 800; color: {color_score};">{score} / 100</span>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                col_res1, col_res2 = st.columns([3, 2])
                with col_res1:
                    st.markdown("##### Resumen Ejecutivo para Selección")
                    st.info(resumen)

                    # TABLA CON EL RESULTADO DE CADA REQUISITO DE LA PONDERACIÓN
                    st.markdown("##### Matriz de Requisitos y Ponderación")
                    tabla_html = generar_tabla_ponderacion_html(vac_obj, p_actual_analisis)
                    st.markdown(tabla_html, unsafe_allow_html=True)

                with col_res2:
                    st.markdown("##### Observaciones y Puntos Clave para la Entrevista")
                    alertas_raw = p_actual_analisis.get("alertas_json")
                    if alertas_raw:
                        try:
                            alertas_lista = json.loads(alertas_raw) if isinstance(alertas_raw, str) else alertas_raw
                            if alertas_lista:
                                for a in alertas_lista:
                                    st.warning(f"Punto de atención: {a}")
                            else:
                                st.success("No se detectaron inconsistencias ni alertas documentales críticas.")
                        except Exception:
                            st.write(alertas_raw)
                    else:
                        st.success("Sin alertas registradas.")

                    if PUEDE_PROCESO:
                        st.markdown("---")
                        st.markdown("##### Decisión Humana (Revisión del Proceso)")
                        st.caption("Seleccione el estado en el que avanzará este candidato:")

                        estado_actual = p_sel["estado"]
                        opciones_estado = ["Pendiente", "En Revisión", "Entrevista", "Aprobado", "Descartado"]
                        idx_estado = opciones_estado.index(estado_actual) if estado_actual in opciones_estado else 0

                        nuevo_est_sel = st.selectbox(
                            "Estado de la postulación:",
                            opciones_estado,
                            index=idx_estado,
                            key="select_estado_ficha"
                        )

                        if nuevo_est_sel != estado_actual:
                            database.actualizar_estado_postulacion(p_sel["id"], nuevo_est_sel)
                            st.toast(f"Estado actualizado a: {nuevo_est_sel}", icon="✔")
                            st.rerun()

                    else:
                        st.caption("Su usuario no tiene permiso para cambiar el estado del proceso.")
                    obs_tthh = st.text_area(
                        "Observaciones del líder de TTHH:",
                        value=p_sel.get("observaciones_tthh") or "",
                        key=f"obs_tthh_{p_sel['id']}",
                        height=120
                    )
                    if st.button("Guardar observación", key=f"guardar_obs_{p_sel['id']}", use_container_width=True):
                        database.actualizar_observacion_postulacion(p_sel["id"], obs_tthh)
                        st.toast("Observación guardada", icon="✔")
                        st.rerun()

                    try:
                        _autor = st.session_state.get("nombre_usuario", "")
                        _nombre_arch = re.sub(r"[^A-Za-z0-9]+", "_", unicodedata.normalize("NFKD", p_sel["nombre"]).encode("ascii", "ignore").decode()).strip("_") or "candidato"
                        st.download_button(
                            "Descargar informe en PDF",
                            generar_informe_pdf(p_actual_analisis, vac_obj, obs_tthh, _autor),
                            file_name=f"Informe_{_nombre_arch}.pdf",
                            mime="application/pdf",
                            key=f"pdf_informe_{p_sel['id']}",
                            use_container_width=True
                        )
                    except Exception as e:
                        st.warning(f"No se pudo generar el informe PDF: {e}")

                    if PUEDE_PROCESO:
                        st.markdown("---")
                        st.caption("Recalcula el puntaje con la ponderación actual de la vacante. No cambia el estado de la postulación.")
                        if st.button("Volver a analizar con IA", key=f"reanalizar_{p_sel['id']}", use_container_width=True):
                            texto_hv_re = p_sel.get("texto_hv") or ""
                            if not texto_hv_re.strip():
                                st.error("No se encontró texto de la Hoja de Vida para este candidato.")
                            else:
                                with st.spinner("Analizando concordancia con IA..."):
                                    res_re = analizar_candidato_vs_vacante(vac_obj, texto_hv_re)
                                if "error" in res_re:
                                    st.error(res_re["error"])
                                else:
                                    database.guardar_analisis(
                                        candidato_id=p_sel["candidato_id"],
                                        vacante_id=p_sel["vacante_id"],
                                        puntaje_total=res_re.get("puntaje_total", 0),
                                        decision=res_re.get("decision", "Pendiente"),
                                        resumen_ejecutivo=res_re.get("resumen_ejecutivo", ""),
                                        cumplimiento_json=res_re.get("cumplimiento_criterios", {}),
                                        alertas=res_re.get("alertas_detectadas", []),
                                        postulacion_id=p_sel["id"]
                                    )
                                    # guardar_analisis pone el estado en 'Analizado'; se conserva la decisión humana previa
                                    database.actualizar_estado_postulacion(p_sel["id"], estado_actual)
                                    st.toast("Análisis actualizado", icon="✔")
                                    st.rerun()

            else:
                st.warning("Esta postulación aún no tiene un análisis registrado por la IA.")
                if st.button("Ejecutar Análisis IA Ahora Mismo", type="primary"):
                    texto_para_analizar = p_sel["texto_hv"]
                    if not texto_para_analizar:
                        cand_db = database.obtener_candidato(p_sel["candidato_id"])
                        cand_dict = dict(cand_db) if cand_db else {}
                        texto_para_analizar = cand_dict.get("formacion", "")

                    if not texto_para_analizar.strip():
                        st.error("No se encontró texto de la Hoja de Vida para este candidato.")
                    else:
                        with st.spinner("Analizando concordancia con IA..."):
                            res = analizar_candidato_vs_vacante(vac_obj, texto_para_analizar)
                            if "error" in res:
                                st.error(res["error"])
                            else:
                                database.guardar_analisis(
                                    candidato_id=p_sel["candidato_id"],
                                    vacante_id=p_sel["vacante_id"],
                                    puntaje_total=res.get("puntaje_total", 0),
                                    decision=res.get("decision", "Pendiente"),
                                    resumen_ejecutivo=res.get("resumen_ejecutivo", ""),
                                    cumplimiento_json=res.get("cumplimiento_criterios", {}),
                                    alertas=res.get("alertas_detectadas", []),
                                    postulacion_id=p_sel["id"]
                                )
                                st.toast("Análisis completado exitosamente", icon="✔")
                                st.rerun()

    # 6. TABLA DE REGISTROS (GRID ERP como en la imagen)
    st.markdown("---")
    st.markdown("##### Listado de Postulaciones y Candidatos")

    vacante_id_filtro = None
    if vac_sel_filtro != "Todas las vacantes":
        vacante_id_filtro = int(vac_sel_filtro.split(" - ")[0].replace("#", "").strip())

    postulaciones = [dict(p) for p in database.obtener_postulaciones(vacante_id=vacante_id_filtro)]

    # Filtro de búsqueda
    if busqueda.strip():
        term = busqueda.strip().lower()
        postulaciones = [
            p for p in postulaciones
            if term in str(p.get("nombre") or "").lower()
            or term in str(p.get("vacante_titulo") or "").lower()
            or term in str(p.get("email") or "").lower()
            or term in str(p.get("documento") or "").lower()
        ]

    if not postulaciones:
        st.info("No hay postulaciones registradas para la vacante y búsqueda seleccionadas.")
    else:
        # Cabecera de la tabla
        st.markdown("""
            <div style="background-color: var(--bg-main); border: 1px solid var(--border-color); border-radius: 8px 8px 0 0; padding: 10px 16px; display: grid; grid-template-columns: 0.8fr 2.2fr 1.8fr 2fr 1.1fr 1fr; font-size: 0.78rem; font-weight: 700; color: var(--text-muted);">
                <div>CÓDIGO</div>
                <div>NOMBRE / CANDIDATO</div>
                <div>VACANTE / ÁREA</div>
                <div>CONTACTO</div>
                <div>ESTADO</div>
                <div>AFINIDAD IA</div>
            </div>
        """, unsafe_allow_html=True)

        for idx, cand in enumerate(postulaciones):
            score = cand.get("puntaje_total")
            if score is not None:
                sc_int = int(score)
                badge_class = "score-high" if sc_int >= 70 else "score-mid" if sc_int >= 50 else "score-low"
                score_html = f"<span class='badge-score-pill {badge_class}'>{sc_int} pts</span>"
            else:
                score_html = "<span class='badge-score-pill score-none'>Pendiente</span>"

            est = cand.get("estado") or "Pendiente"
            est_class = "status-entrevista" if est in ["Entrevista", "Aprobado"] else "status-descartado" if est == "Descartado" else "status-revision" if est == "En Revisión" else "status-pendiente"

            doc_display = f"CC: {cand['documento']}" if cand.get("documento") else "Sin documento"
            tel_display = cand.get("telefono") or "Sin teléfono"
            email_display = cand.get("email") or "Sin correo"

            # Fila de datos
            st.markdown(f"""
                <div style="background-color: var(--surface); border-left: 1px solid var(--border-color); border-right: 1px solid var(--border-color); border-bottom: 1px solid var(--border-color); padding: 10px 16px; display: grid; grid-template-columns: 0.8fr 2.2fr 1.8fr 2fr 1.1fr 1fr; align-items: center; font-size: 0.86rem;">
                    <div style="color: var(--text-muted); font-weight: 600;">#{cand['id']}</div>
                    <div>
                        <strong style="color: var(--text-dark);">{cand.get('nombre') or 'Sin nombre'}</strong><br>
                        <span style="font-size: 0.75rem; color: var(--text-muted);">{doc_display}</span>
                    </div>
                    <div>
                        <span style="color: var(--text-dark); font-weight: 500;">{cand.get('vacante_titulo', '')}</span><br>
                        <span style="font-size: 0.75rem; color: var(--text-muted);">{cand.get('vacante_area', '')}</span>
                    </div>
                    <div>
                        <span style="color: var(--text-dark); font-size: 0.82rem;">{email_display}</span><br>
                        <span style="font-size: 0.75rem; color: var(--text-muted);">{tel_display}</span>
                    </div>
                    <div>
                        <span class="badge-status {est_class}">{est}</span>
                    </div>
                    <div>
                        {score_html}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            # Barra de Acciones inline por fila (sin emojis)
            c_act_view, c_act_ai, c_act_del, _ = st.columns([1.2, 1.4, 1.2, 3])
            with c_act_view:
                if st.button("Ver Análisis", key=f"btn_ver_{cand['id']}_{idx}", use_container_width=True):
                    st.session_state.ver_analisis_id = cand["id"]
                    st.rerun()

            if PUEDE_PROCESO:
                with c_act_ai:
                    if st.button("Analizar con IA", key=f"btn_ai_{cand['id']}_{idx}", use_container_width=True):
                        texto_cand = cand.get("texto_hv")
                        if not texto_cand:
                            c_db = database.obtener_candidato(cand["candidato_id"])
                            c_dict = dict(c_db) if c_db else {}
                            texto_cand = c_dict.get("formacion", "")

                        if not texto_cand.strip():
                            st.error(f"No hay texto registrado de la hoja de vida para {cand.get('nombre')}.")
                        else:
                            with st.spinner(f"Analizando perfil de {cand.get('nombre')} con IA..."):
                                v_obj = dict(database.obtener_vacante(cand["vacante_id"]))
                                res = analizar_candidato_vs_vacante(v_obj, texto_cand)
                                if "error" in res:
                                    st.error(res["error"])
                                else:
                                    database.guardar_analisis(
                                        candidato_id=cand["candidato_id"],
                                        vacante_id=cand["vacante_id"],
                                        puntaje_total=res.get("puntaje_total", 0),
                                        decision=res.get("decision", "Pendiente"),
                                        resumen_ejecutivo=res.get("resumen_ejecutivo", ""),
                                        cumplimiento_json=res.get("cumplimiento_criterios", {}),
                                        alertas=res.get("alertas_detectadas", []),
                                        postulacion_id=cand["id"]
                                    )
                                    st.session_state.ver_analisis_id = cand["id"]
                                    st.toast(f"Análisis completado para {cand.get('nombre')}", icon="✔")
                                    st.rerun()

                with c_act_del:
                    if st.button("Eliminar", key=f"btn_del_{cand['id']}_{idx}", use_container_width=True):
                        database.eliminar_postulacion(cand["id"])
                        st.toast(f"Candidato #{cand['id']} eliminado definitivamente", icon="✔")
                        st.rerun()

            st.markdown("<div style='margin-bottom: 8px;'></div>", unsafe_allow_html=True)


# =============================================================================
# VISTA 2: GESTIÓN DE VACANTES
# =============================================================================
elif opcion == "Vacantes":
    st.markdown('<div class="top-breadcrumb">SISTEMA DE TALENTO HUMANO</div>', unsafe_allow_html=True)
    st.markdown('<div class="top-title">Gestión de Vacantes y Perfiles</div>', unsafe_allow_html=True)

    if PUEDE_VACANTES:
        with st.expander("+ Crear Nueva Vacante", expanded=False):
            with st.form("form_vacante_erp", clear_on_submit=True):
                col_v1, col_v2 = st.columns(2)
                with col_v1:
                    v_tit = st.text_input("Título del Cargo / Vacante *")
                    v_are = st.text_input("Área / Departamento *")
                    v_for = st.text_area("Formación Académica Requerida")
                with col_v2:
                    v_exp = st.text_area("Experiencia Laboral Requerida")
                    v_hab = st.text_area("Habilidades Técnicas y Software")
                    v_otr = st.text_area("Criterios de Ponderación / Habilidades Blandas", help="Ejemplo:\n- Experiencia: 35%\n- Portafolio: 20%\n- Habilidades técnicas: 20%\n- Formación: 15%\n- Habilidades blandas: 10%")

                v_des = st.text_area("Descripción General del Cargo *")

                col_sub1, col_sub2 = st.columns([4, 1])
                with col_sub2:
                    if st.form_submit_button("Guardar Vacante", type="primary", use_container_width=True):
                        if v_tit.strip() and v_are.strip() and v_des.strip():
                            nueva_id = database.crear_vacante(
                                titulo=v_tit,
                                area=v_are,
                                descripcion=v_des,
                                formacion=v_for,
                                experiencia=v_exp,
                                habilidades=v_hab,
                                otros_criterios=v_otr
                            )
                            st.toast(f"Vacante #{nueva_id} guardada con éxito", icon="✔")
                            st.rerun()
                        else:
                            st.error("Diligencie los campos requeridos marcados con (*).")

    st.markdown("---")
    st.markdown("##### Catálogo de Vacantes")

    vacantes = [dict(v) for v in database.obtener_vacantes()]
    if not vacantes:
        st.info("No existen vacantes registradas.")
    else:
        for idx, vac in enumerate(vacantes):
            col_v_info, col_v_act = st.columns([5, 1.5])
            with col_v_info:
                postuls = database.obtener_postulaciones_por_vacante(vac['id'])
                badge_v = "Abierta" if vac["estado"] == "Abierta" else "Cerrada"
                with st.expander(f"#{vac['id']} - {vac['titulo']} ({vac['area']}) — [{badge_v}] ({len(postuls)} postulaciones)"):
                    st.write(f"**Descripción:** {vac.get('descripcion') or 'No especificada'}")
                    st.write(f"**Formación:** {vac.get('formacion') or 'No especificada'}")
                    st.write(f"**Experiencia:** {vac.get('experiencia') or 'No especificada'}")
                    st.write(f"**Habilidades:** {vac.get('habilidades') or 'No especificadas'}")
                    if vac.get('otros_criterios'):
                        st.write(f"**Ponderación:**\n{vac['otros_criterios']}")

            with col_v_act:
                if PUEDE_VACANTES:
                    c_b1, c_b2 = st.columns(2)
                    with c_b1:
                        if vac["estado"] == "Abierta":
                            if st.button("Cerrar", key=f"vc_close_{vac['id']}_{idx}"):
                                database.cambiar_estado_vacante(vac["id"], "Cerrada")
                                st.rerun()
                        else:
                            if st.button("Abrir", key=f"vc_open_{vac['id']}_{idx}"):
                                database.cambiar_estado_vacante(vac["id"], "Abierta")
                                st.rerun()
                    with c_b2:
                        if st.button("Eliminar", key=f"vc_del_{vac['id']}_{idx}"):
                            database.eliminar_vacante(vac["id"])
                            st.toast("Vacante eliminada", icon="✔")
                            st.rerun()


# =============================================================================
# VISTA 3: DASHBOARD
# =============================================================================
elif opcion == "Dashboard":
    st.markdown('<div class="top-breadcrumb">SISTEMA DE TALENTO HUMANO</div>', unsafe_allow_html=True)
    st.markdown('<div class="top-title">Métricas y Resumen Ejecutivo</div>', unsafe_allow_html=True)

    metricas = database.obtener_metricas_dashboard()
    total_rev = metricas.get('pendientes', 0) + metricas.get('requieren_revision', 0)

    st.markdown(f"""
        <div class="kpi-row">
            <div class="kpi-card">
                <div class="kpi-label">Vacantes Abiertas</div>
                <div class="kpi-value">{metricas.get('vacantes_abiertas', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Total Candidatos</div>
                <div class="kpi-value">{metricas.get('total_candidatos', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Evaluaciones IA Realizadas</div>
                <div class="kpi-value">{metricas.get('analisis_realizados', 0)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Pendientes de Decisión</div>
                <div class="kpi-value">{total_rev}</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.markdown("##### Resumen de Vacantes Activas")
        vac_abiertas = [dict(v) for v in database.obtener_vacantes_abiertas()]
        if not vac_abiertas:
            st.info("No hay vacantes activas.")
        else:
            for v in vac_abiertas:
                pts = database.obtener_postulaciones_por_vacante(v["id"])
                st.markdown(f"""
                    <div style="background: var(--surface); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;">
                        <strong style="color: var(--text-dark);">{v['titulo']}</strong> ({v['area']})<br>
                        <span style="font-size: 0.8rem; color: var(--text-muted);">Postulaciones asociadas: <strong>{len(pts)}</strong></span>
                    </div>
                """, unsafe_allow_html=True)

    with col_d2:
        st.markdown("##### Últimas Actividades")
        postuls = [dict(p) for p in database.obtener_postulaciones()][:5]
        if not postuls:
            st.info("Sin postulaciones recientes.")
        else:
            for p in postuls:
                score_str = f"Puntaje IA: {p['puntaje_total']}/100" if p.get('puntaje_total') is not None else "Pendiente IA"
                st.markdown(f"""
                    <div style="background: var(--surface); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;">
                        <strong style="color: var(--text-dark);">{p.get('nombre')}</strong> — {p.get('vacante_titulo')}<br>
                        <span style="font-size: 0.8rem; color: var(--text-muted);">Estado: <em>{p.get('estado')}</em> | {score_str}</span>
                    </div>
                """, unsafe_allow_html=True)


# =============================================================================
# VISTA: GESTIÓN DE USUARIOS (solo administradores)
# =============================================================================
elif opcion == "Usuarios":
    st.markdown('<div class="top-breadcrumb">SISTEMA DE TALENTO HUMANO</div>', unsafe_allow_html=True)
    st.markdown('<div class="top-title">Usuarios del Sistema</div>', unsafe_allow_html=True)

    if not PUEDE_USUARIOS:
        st.warning("Su usuario no tiene permiso para gestionar usuarios.")
    else:
        st.caption(
            "**Súper administrador**: control total, incluida la gestión de usuarios. "
            "**Control total**: igual que el anterior, pero no puede crear ni administrar usuarios. "
            "**Solo lector**: puede ver candidatos, descargar el informe en PDF y escribir observaciones, "
            "pero no registrar, analizar, eliminar ni gestionar vacantes. "
            "**Personalizado**: usted elige exactamente qué puede hacer."
        )

        if "crear_usuario_nonce" not in st.session_state:
            st.session_state.crear_usuario_nonce = 0
        _n = st.session_state.crear_usuario_nonce

        with st.expander("+ Crear Nuevo Usuario", expanded=False):
            nu_nombre = st.text_input("Nombre completo *", placeholder="Ej: María Rectora", key=f"nu_nombre_{_n}")
            nu_usuario = st.text_input("Usuario para iniciar sesión *", placeholder="Ej: rectora", key=f"nu_usuario_{_n}")
            nu_clave = st.text_input("Contraseña *", type="password", key=f"nu_clave_{_n}")
            nu_tipo = st.selectbox(
                "Tipo de acceso",
                ["super_admin", "control_total", "lector", "personalizado"],
                format_func=lambda r: database.NOMBRES_ROL[r],
                key=f"nu_tipo_{_n}"
            )

            permisos_elegidos = None
            if nu_tipo == "personalizado":
                st.caption("Marque lo que este usuario podrá hacer:")
                cp1, cp2, cp3, cp4 = st.columns(4)
                with cp1:
                    perm_candidatos = st.checkbox("Registrar candidatos", key=f"perm_cand_{_n}")
                with cp2:
                    perm_proceso = st.checkbox("Gestionar proceso (analizar, estado, eliminar)", key=f"perm_proc_{_n}")
                with cp3:
                    perm_vacantes = st.checkbox("Gestionar vacantes", key=f"perm_vac_{_n}")
                with cp4:
                    perm_usuarios = st.checkbox("Gestionar usuarios", key=f"perm_usr_{_n}")
                permisos_elegidos = {
                    "candidatos": perm_candidatos, "proceso": perm_proceso,
                    "vacantes": perm_vacantes, "usuarios": perm_usuarios
                }

            if st.button("Crear Usuario", type="primary", use_container_width=True, key=f"btn_crear_usuario_{_n}"):
                if not nu_nombre.strip() or not nu_usuario.strip() or not nu_clave.strip():
                    st.error("Diligencie nombre, usuario y contraseña.")
                else:
                    try:
                        database.crear_usuario(nu_usuario, nu_nombre, nu_clave, nu_tipo, permisos_elegidos)
                        st.toast(f'Usuario "{nu_usuario}" creado con éxito', icon="✔")
                        st.session_state.crear_usuario_nonce += 1
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        st.markdown("---")
        st.markdown("##### Usuarios Registrados")

        for u in database.obtener_usuarios():
            es_yo_mismo = u["id"] == st.session_state.get("usuario_id")

            c_u1, c_u2, c_u3, c_u4 = st.columns([2.5, 2.2, 1.3, 1.8])
            with c_u1:
                st.write(f"**{u['nombre']}**")
                st.caption(f"Usuario: {u['usuario']}")
            with c_u2:
                st.write(database.NOMBRES_ROL.get(u["rol"], "Personalizado"))
                if u["rol"] == "personalizado":
                    otorgados = [cap for cap, val in u["permisos"].items() if val]
                    st.caption(", ".join(otorgados) if otorgados else "Sin permisos adicionales")
            with c_u3:
                st.write("Activo" if u["activo"] else "Inactivo")
            with c_u4:
                if es_yo_mismo:
                    st.caption("Sesión actual")
                else:
                    c_e1, c_e2, c_e3 = st.columns(3)
                    with c_e1:
                        if u["activo"]:
                            if st.button("Desactivar", key=f"usr_desact_{u['id']}", use_container_width=True):
                                database.alternar_estado_usuario(u["id"], False)
                                st.rerun()
                        else:
                            if st.button("Activar", key=f"usr_act_{u['id']}", use_container_width=True):
                                database.alternar_estado_usuario(u["id"], True)
                                st.rerun()
                    with c_e2:
                        if st.button("Editar", key=f"usr_edit_{u['id']}", use_container_width=True):
                            st.session_state.editando_usuario_id = (
                                None if st.session_state.get("editando_usuario_id") == u["id"] else u["id"]
                            )
                            st.rerun()
                    with c_e3:
                        if st.button("Eliminar", key=f"usr_del_{u['id']}", use_container_width=True):
                            database.eliminar_usuario(u["id"])
                            st.toast(f'Usuario "{u["usuario"]}" eliminado', icon="✔")
                            st.rerun()

            if not es_yo_mismo and st.session_state.get("editando_usuario_id") == u["id"]:
                with st.container(border=True):
                    st.caption(f"Editando el tipo de acceso de **{u['nombre']}**")
                    ed_tipo = st.selectbox(
                        "Tipo de acceso",
                        ["super_admin", "control_total", "lector", "personalizado"],
                        index=["super_admin", "control_total", "lector", "personalizado"].index(u["rol"]),
                        format_func=lambda r: database.NOMBRES_ROL[r],
                        key=f"edit_tipo_{u['id']}"
                    )

                    permisos_editados = None
                    if ed_tipo == "personalizado":
                        st.caption("Marque lo que este usuario podrá hacer:")
                        ce1, ce2, ce3, ce4 = st.columns(4)
                        with ce1:
                            ep_cand = st.checkbox("Registrar candidatos", value=u["permisos"]["candidatos"], key=f"edit_cand_{u['id']}")
                        with ce2:
                            ep_proc = st.checkbox("Gestionar proceso", value=u["permisos"]["proceso"], key=f"edit_proc_{u['id']}")
                        with ce3:
                            ep_vac = st.checkbox("Gestionar vacantes", value=u["permisos"]["vacantes"], key=f"edit_vac_{u['id']}")
                        with ce4:
                            ep_usr = st.checkbox("Gestionar usuarios", value=u["permisos"]["usuarios"], key=f"edit_usr_{u['id']}")
                        permisos_editados = {
                            "candidatos": ep_cand, "proceso": ep_proc,
                            "vacantes": ep_vac, "usuarios": ep_usr
                        }

                    cg1, cg2 = st.columns(2)
                    with cg1:
                        if st.button("Guardar cambios", key=f"edit_guardar_{u['id']}", type="primary", use_container_width=True):
                            database.actualizar_rol_usuario(u["id"], ed_tipo, permisos_editados)
                            st.session_state.editando_usuario_id = None
                            st.toast("Tipo de acceso actualizado", icon="✔")
                            st.rerun()
                    with cg2:
                        if st.button("Cancelar", key=f"edit_cancelar_{u['id']}", use_container_width=True):
                            st.session_state.editando_usuario_id = None
                            st.rerun()


# =============================================================================
# VISTA 4: REPORTES & HISTORIAL
# =============================================================================
elif opcion == "Reportes & Historial":
    st.markdown('<div class="top-breadcrumb">SISTEMA DE TALENTO HUMANO</div>', unsafe_allow_html=True)
    st.markdown('<div class="top-title">Reportes Consolidados de Selección</div>', unsafe_allow_html=True)

    col_rf, _ = st.columns([2, 3])
    with col_rf:
        opciones_r = ["Todas las vacantes"] + [f"#{v['id']} - {v['titulo']}" for v in database.obtener_vacantes()]
        rep_sel = st.selectbox("Filtrar reporte por vacante:", opciones_r)

    rep_vac_id = None
    if rep_sel != "Todas las vacantes":
        rep_vac_id = int(rep_sel.split("-")[0].replace("#", "").strip())

    df = database.obtener_reporte_completo_excel(vacante_id=rep_vac_id)

    if df.empty:
        st.info("No existen postulaciones para generar el reporte.")
    else:
        st.dataframe(df, use_container_width=True)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("Descargar Reporte en CSV", csv, "reporte_seleccion_tthh.csv", "text/csv")

        import io
        buffer_xlsx = io.BytesIO()
        df.to_excel(buffer_xlsx, index=False, sheet_name="Reporte")
        st.download_button(
            "Descargar Reporte en Excel",
            buffer_xlsx.getvalue(),
            "reporte_seleccion_tthh.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )