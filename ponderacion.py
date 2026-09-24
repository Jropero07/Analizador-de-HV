"""
Ponderación y puntaje de candidatos.

Este módulo NO depende de Streamlit ni de la base de datos, así que se puede
probar de forma aislada (ver test_ponderacion.py).

Idea central de la versión corregida:
  - La IA solo puntúa cada criterio de 0 a 100 y justifica con evidencia.
  - El puntaje total y la decisión se calculan aquí, en Python, aplicando los
    porcentajes de la vacante. Así el total siempre coincide con la tabla.
"""

import html
import json
import re
import unicodedata


# =============================================================================
# CONFIGURACIÓN (ajusta estos valores si quieres cambiar las reglas)
# =============================================================================

# Decisión según el puntaje total ponderado (coinciden con los colores de la app)
UMBRAL_RECOMENDADO = 70   # >= 70  -> Recomendado para entrevista
UMBRAL_REVISION = 50      # 50-69  -> Revisión manual

# Resultado por criterio según su puntaje individual
NIVEL_CUMPLE = 70         # >= 70  -> Cumple
NIVEL_PARCIAL = 40        # 40-69  -> Parcial, < 40 -> No cumple

# Ponderación estándar cuando la vacante no define porcentajes
CRITERIOS_ESTANDAR = [
    ("experiencia", "Experiencia laboral", 30),
    ("formacion", "Formación académica", 25),
    ("habilidades_tecnicas", "Habilidades técnicas / software", 25),
    ("habilidades_blandas", "Habilidades blandas", 10),
    ("portafolio", "Portafolio / Proyectos", 10),
]

# Equivalencia para análisis antiguos que solo guardaron "Cumple/Parcial/No cumple"
PUNTAJE_LEGADO = {"cumple": 100, "parcial": 50, "no cumple": 0}


# =============================================================================
# UTILIDADES DE TEXTO
# =============================================================================

def quitar_tildes(texto):
    """Minúsculas y sin tildes, para comparar textos de forma robusta."""
    if not texto:
        return ""
    limpio = unicodedata.normalize("NFD", str(texto))
    limpio = "".join(c for c in limpio if unicodedata.category(c) != "Mn")
    return limpio.lower().strip()


def _slug(texto):
    s = re.sub(r"[^a-z0-9]+", "_", quitar_tildes(texto)).strip("_")
    return s or "criterio"


def clasificar_criterio(nombre):
    """
    Convierte el nombre que escribió el usuario ("Habilidades técnicas (software)",
    "Blandas", "Trayectoria"...) en una clave estándar. Si no reconoce el criterio,
    devuelve un slug propio para que igual se evalúe como criterio personalizado.

    Importante: NO se compara por palabras sueltas. El error de la versión anterior
    era que "habilidades blandas" coincidía con "habilidades técnicas" solo porque
    ambas contienen la palabra "habilidades".
    """
    n = quitar_tildes(nombre)

    es_tecnico = any(p in n for p in ("tecnic", "software", "herramienta"))
    es_blando = any(p in n for p in ("blanda", "interpersonal", "actitudinal", "soft skill"))

    if es_blando and not es_tecnico:
        return "habilidades_blandas"
    if any(p in n for p in ("portafolio", "portfolio", "proyecto", "muestra")):
        return "portafolio"
    if any(p in n for p in ("experiencia", "trayectoria", "laboral")):
        return "experiencia"
    if any(p in n for p in ("formacion", "academ", "estudio", "titulo", "educacion")):
        return "formacion"
    if es_tecnico or any(p in n for p in ("habilidad", "conocimiento")):
        return "habilidades_tecnicas"
    return _slug(n)


# =============================================================================
# PONDERACIONES DE LA VACANTE
# =============================================================================

def extraer_ponderaciones(texto_criterios):
    """
    Lee el texto de "Criterios de ponderación" de la vacante y devuelve una lista:
        [{"clave", "nombre", "peso", "peso_original"}, ...]

    Solo cuenta las líneas que tengan un porcentaje ("- Experiencia: 35%",
    "Experiencia (35%)", "35% Experiencia"). Las demás se ignoran.
    Si los porcentajes no suman 100, se reescalan proporcionalmente.
    """
    if not texto_criterios:
        return []

    encontrados = {}   # clave -> dict (mantiene el orden de aparición)
    for linea in str(texto_criterios).splitlines():
        linea = linea.strip().lstrip("-*•·– ").strip()
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", linea)
        if not m:
            continue

        peso = float(m.group(1).replace(",", "."))
        if peso <= 0:
            continue

        nombre = linea[:m.start()].strip(" :=-–—(")
        if not nombre:   # formato "35% Experiencia"
            nombre = linea[m.end():].strip(" :=-–—)")
        if not nombre:
            continue

        clave = clasificar_criterio(nombre)
        if clave in encontrados:
            encontrados[clave]["peso_original"] += peso
        else:
            encontrados[clave] = {"clave": clave, "nombre": nombre, "peso_original": peso}

    lista = list(encontrados.values())
    total = sum(c["peso_original"] for c in lista)
    if total <= 0:
        return []

    for c in lista:
        c["peso"] = round(c["peso_original"] * 100.0 / total, 2)
    return lista


def ponderaciones_estandar():
    return [
        {"clave": k, "nombre": n, "peso": float(p), "peso_original": float(p)}
        for k, n, p in CRITERIOS_ESTANDAR
    ]


def obtener_ponderaciones(texto_criterios):
    """Ponderaciones de la vacante o, si no define porcentajes, las estándar."""
    return extraer_ponderaciones(texto_criterios) or ponderaciones_estandar()


# =============================================================================
# PUNTAJES
# =============================================================================

def normalizar_puntaje(valor):
    """Convierte lo que devuelva la IA (85, 85.0, "85", "85%") en int 0-100, o None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        m = re.search(r"\d+(?:[.,]\d+)?", str(valor))
        if not m:
            return None
        numero = float(m.group(0).replace(",", "."))
    return int(round(max(0.0, min(100.0, numero))))


def nivel_resultado(puntaje):
    if puntaje is None:
        return "Sin evaluar"
    if puntaje >= NIVEL_CUMPLE:
        return "Cumple"
    if puntaje >= NIVEL_PARCIAL:
        return "Parcial"
    return "No cumple"


def calcular_puntaje_total(criterios, ponderaciones):
    """
    Puntaje total ponderado (0-100).

    criterios: {clave: {"puntaje": int | None, ...}}
    Si algún criterio no fue evaluado, se recalcula solo con los evaluados
    (reescalando sus pesos) en vez de castigar al candidato con un 0.
    Devuelve (total_int | None, lista_claves_no_evaluadas).
    """
    suma = 0.0
    peso_evaluado = 0.0
    faltantes = []
    for c in ponderaciones:
        datos = criterios.get(c["clave"]) or {}
        puntaje = datos.get("puntaje")
        if puntaje is None:
            faltantes.append(c["clave"])
            continue
        suma += c["peso"] * puntaje / 100.0
        peso_evaluado += c["peso"]

    if peso_evaluado <= 0:
        return None, faltantes
    total = suma * 100.0 / peso_evaluado
    return int(round(max(0.0, min(100.0, total)))), faltantes


def decision_desde_puntaje(puntaje):
    if puntaje >= UMBRAL_RECOMENDADO:
        return "Recomendado para entrevista"
    if puntaje >= UMBRAL_REVISION:
        return "Revisión manual: cumple parcialmente"
    return "No recomendado por afinidad documental"


# =============================================================================
# TABLA HTML DE LA FICHA
# =============================================================================

def _cargar_cumplimiento(analisis):
    crudo = analisis.get("cumplimiento_json", {})
    if isinstance(crudo, dict):
        return crudo
    if isinstance(crudo, str):
        try:
            datos = json.loads(crudo)
            return datos if isinstance(datos, dict) else {}
        except Exception:
            return {}
    return {}


def _buscar_entrada(cumplimiento, clave):
    """Busca el criterio por clave exacta y, si no, por su clasificación estándar."""
    if clave in cumplimiento:
        return cumplimiento[clave]
    for k, v in cumplimiento.items():
        if clasificar_criterio(str(k).replace("_", " ")) == clave:
            return v
    return None


def _puntaje_de_entrada(entrada):
    """Devuelve (puntaje, es_estimado). es_estimado=True si viene de un análisis antiguo."""
    if isinstance(entrada, dict):
        p = normalizar_puntaje(entrada.get("puntaje"))
        if p is not None:
            return p, False
        texto = quitar_tildes(entrada.get("resultado", ""))
    elif isinstance(entrada, str):
        texto = quitar_tildes(entrada)
    else:
        return None, False

    if texto.startswith("no"):
        return PUNTAJE_LEGADO["no cumple"], True
    if "parcial" in texto:
        return PUNTAJE_LEGADO["parcial"], True
    if "cumple" in texto:
        return PUNTAJE_LEGADO["cumple"], True
    return None, False


_BADGES = {
    "Cumple": ("#DCFCE7", "#166534", "#BBF7D0"),
    "Parcial": ("#FEF3C7", "#92400E", "#FDE68A"),
    "No cumple": ("#FEE2E2", "#991B1B", "#FECACA"),
    "Sin evaluar": ("#F1F5F9", "#64748B", "#E2E8F0"),
}


def _badge(texto):
    bg, fg, borde = _BADGES.get(texto, _BADGES["Sin evaluar"])
    return (
        f'<span style="background:{bg}; color:{fg}; padding:3px 10px; border-radius:12px; '
        f'font-weight:700; font-size:0.75rem; border:1px solid {borde};">{html.escape(texto)}</span>'
    )


def _compactar_html(fragmento):
    """
    Deja el HTML en UNA sola línea, sin sangrías ni líneas en blanco.

    Por qué: st.markdown interpreta el texto como Markdown. Una línea en blanco
    cierra el bloque HTML, y lo que venga después con 4+ espacios de sangría se
    pinta como bloque de código (se ve el HTML en crudo en vez de la tabla).
    """
    return "".join(linea.strip() for linea in fragmento.splitlines())


def generar_tabla_ponderacion_html(vacante, analisis):
    """
    Tabla: criterio | peso | resultado | puntaje | aporte | evidencia,
    más una fila final con el total ponderado (la misma cuenta que hace la app).
    """
    ponderaciones = obtener_ponderaciones(vacante.get("otros_criterios", "") or "")
    cumplimiento = _cargar_cumplimiento(analisis)

    filas = ""
    criterios_calc = {}
    hay_estimados = False

    for c in ponderaciones:
        entrada = _buscar_entrada(cumplimiento, c["clave"])
        puntaje, estimado = _puntaje_de_entrada(entrada)
        hay_estimados = hay_estimados or estimado
        criterios_calc[c["clave"]] = {"puntaje": puntaje}

        if puntaje is None:
            resultado = "Sin evaluar"
        elif isinstance(entrada, dict) and entrada.get("resultado") and estimado:
            # análisis antiguo: se respeta lo que dijo la IA
            resultado = str(entrada["resultado"])
        else:
            resultado = nivel_resultado(puntaje)

        # Badge según texto normalizado
        r_norm = quitar_tildes(resultado)
        if r_norm.startswith("no"):
            etiqueta = "No cumple"
        elif "parcial" in r_norm:
            etiqueta = "Parcial"
        elif "cumple" in r_norm:
            etiqueta = "Cumple"
        else:
            etiqueta = "Sin evaluar"

        detalle = ""
        if isinstance(entrada, dict):
            detalle = str(entrada.get("detalle", "") or "")
        # La IA puede devolver saltos de línea o líneas en blanco: se aplanan
        detalle = " ".join(detalle.split())
        if not detalle:
            detalle = "La IA no evaluó este criterio." if puntaje is None else "Sin detalle registrado."

        if puntaje is None:
            celda_puntaje, celda_aporte = "—", "—"
        else:
            aporte = c["peso"] * puntaje / 100.0
            marca = "≈ " if estimado else ""
            celda_puntaje = f"{marca}{puntaje}/100"
            celda_aporte = f"{marca}{aporte:.1f}"

        filas += f"""
        <tr style="border-bottom: 1px solid #F1F5F9;">
            <td style="padding: 10px 14px; font-weight: 600; color: #0F172A;">{html.escape(c['nombre'][:1].upper() + c['nombre'][1:])}</td>
            <td style="padding: 10px 14px; text-align: center;"><span style="background:#EFF6FF; color:#1D4ED8; padding:3px 8px; border-radius:6px; font-weight:700; font-size:0.78rem; border:1px solid #BFDBFE;">{c['peso']:g}%</span></td>
            <td style="padding: 10px 14px; text-align: center;">{_badge(etiqueta)}</td>
            <td style="padding: 10px 14px; text-align: center; color:#0F172A;">{celda_puntaje}</td>
            <td style="padding: 10px 14px; text-align: center; font-weight:600; color:#0F172A;">{celda_aporte}</td>
            <td style="padding: 10px 14px; color: #475569; font-size: 0.82rem;">{html.escape(detalle)}</td>
        </tr>
        """

    total_calc, _ = calcular_puntaje_total(criterios_calc, ponderaciones)
    total_txt = "—" if total_calc is None else f"{'≈ ' if hay_estimados else ''}{total_calc} / 100"

    nota = ""
    guardado = analisis.get("puntaje_total")
    if hay_estimados:
        nota = (
            "Este análisis se hizo con la versión anterior, que no guardaba puntaje por criterio: "
            "los valores con ≈ son una estimación (Cumple=100, Parcial=50, No cumple=0). "
            "Usa «Volver a analizar con IA» para obtener el puntaje ponderado real."
        )
        if guardado is not None and total_calc is not None and abs(int(guardado) - total_calc) > 1:
            nota += f" El puntaje que la IA registró en aquel momento fue {int(guardado)}."

    fila_nota = ""
    if nota:
        fila_nota = (
            f'<div style="padding:10px 14px; font-size:0.78rem; color:#92400E; background:#FFFBEB; '
            f'border-top:1px solid #FDE68A;">{html.escape(nota)}</div>'
        )

    return _compactar_html(f"""
    <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; overflow:hidden; margin-top:8px; margin-bottom:18px; box-shadow:0 1px 2px rgba(0,0,0,0.02);">
        <table style="width:100%; border-collapse:collapse; font-size:0.86rem;">
            <thead>
                <tr style="background-color:#F8FAFC; border-bottom:2px solid #E2E8F0; color:#475569; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.5px;">
                    <th style="padding:10px 14px; text-align:left;">Requisito de Ponderación</th>
                    <th style="padding:10px 14px; text-align:center;">Peso</th>
                    <th style="padding:10px 14px; text-align:center;">Resultado IA</th>
                    <th style="padding:10px 14px; text-align:center;">Puntaje</th>
                    <th style="padding:10px 14px; text-align:center;">Aporte</th>
                    <th style="padding:10px 14px; text-align:left;">Criterio Evaluado / Evidencia</th>
                </tr>
            </thead>
            <tbody>
                {filas}
                <tr style="background:#F8FAFC; border-top:2px solid #E2E8F0;">
                    <td style="padding:10px 14px; font-weight:700; color:#0F172A;">Total ponderado</td>
                    <td style="padding:10px 14px; text-align:center; font-weight:700;">100%</td>
                    <td></td><td></td>
                    <td style="padding:10px 14px; text-align:center; font-weight:800; color:#0F172A;">{total_txt}</td>
                    <td></td>
                </tr>
            </tbody>
        </table>
        {fila_nota}
    </div>
    """)
