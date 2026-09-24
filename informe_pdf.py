"""
Informe en PDF de la evaluación de un candidato.

No depende de Streamlit ni de la base de datos: recibe diccionarios y devuelve
los bytes del PDF.
"""

import io
import json
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import ponderacion as pond

AZUL = colors.HexColor("#1E3A8A")
AZUL_CLARO = colors.HexColor("#DBEAFE")
GRIS = colors.HexColor("#64748B")
BORDE = colors.HexColor("#E2E8F0")

_ESTILOS = getSampleStyleSheet()
_NORMAL = ParagraphStyle("Normal2", parent=_ESTILOS["Normal"], fontSize=9.5, leading=13, textColor=colors.HexColor("#0F172A"))
_PEQUENO = ParagraphStyle("Pequeno", parent=_NORMAL, fontSize=8.5, leading=11)
_ENCABEZADO_TABLA = ParagraphStyle("EncTabla", parent=_PEQUENO, textColor=colors.white, fontName="Helvetica-Bold")
_TITULO = ParagraphStyle("Titulo2", parent=_NORMAL, fontSize=17, leading=21, textColor=AZUL, fontName="Helvetica-Bold")
_SUBTITULO = ParagraphStyle("Subtitulo2", parent=_NORMAL, fontSize=10, textColor=GRIS)
_SECCION = ParagraphStyle("Seccion", parent=_NORMAL, fontSize=11.5, leading=15, textColor=AZUL, fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=5)


def _t(valor):
    """Texto seguro para Paragraph: escapa XML y reemplaza caracteres que Helvetica no dibuja."""
    texto = "" if valor is None else str(valor)
    texto = texto.replace("≈", "~").replace("≥", ">=").replace("≤", "<=")
    texto = texto.encode("cp1252", "replace").decode("cp1252")
    return escape(texto).replace("\n", "<br/>")


def _p(valor, estilo=_NORMAL):
    return Paragraph(_t(valor), estilo)


def _lista(valor):
    if isinstance(valor, list):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            datos = json.loads(valor)
            return datos if isinstance(datos, list) else [str(datos)]
        except Exception:
            return [valor]
    return []


def _filas_matriz(vacante, analisis):
    """Filas de la matriz de ponderación (mismos cálculos que la tabla de la app)."""
    ponderaciones = pond.obtener_ponderaciones(vacante.get("otros_criterios", "") or "")
    cumplimiento = pond._cargar_cumplimiento(analisis)

    filas = []
    criterios_calc = {}
    hay_estimados = False

    for c in ponderaciones:
        entrada = pond._buscar_entrada(cumplimiento, c["clave"])
        puntaje, estimado = pond._puntaje_de_entrada(entrada)
        hay_estimados = hay_estimados or estimado
        criterios_calc[c["clave"]] = {"puntaje": puntaje}

        if puntaje is None:
            resultado = "Sin evaluar"
        elif isinstance(entrada, dict) and entrada.get("resultado") and estimado:
            resultado = str(entrada["resultado"])
        else:
            resultado = pond.nivel_resultado(puntaje)

        detalle = ""
        if isinstance(entrada, dict):
            detalle = " ".join(str(entrada.get("detalle", "") or "").split())
        if not detalle:
            detalle = "La IA no evaluó este criterio." if puntaje is None else "Sin detalle registrado."

        if puntaje is None:
            celda_puntaje, celda_aporte = "-", "-"
        else:
            marca = "~" if estimado else ""
            celda_puntaje = f"{marca}{puntaje}/100"
            celda_aporte = f"{marca}{c['peso'] * puntaje / 100.0:.1f}"

        nombre = c["nombre"][:1].upper() + c["nombre"][1:]
        filas.append([nombre, f"{c['peso']:g}%", resultado, celda_puntaje, celda_aporte, detalle])

    total, _ = pond.calcular_puntaje_total(criterios_calc, ponderaciones)
    total_txt = "-" if total is None else f"{'~' if hay_estimados else ''}{total} / 100"
    return filas, total_txt, hay_estimados


def _color_resultado(texto):
    r = pond.quitar_tildes(texto)
    if r.startswith("no"):
        return colors.HexColor("#991B1B")
    if "parcial" in r:
        return colors.HexColor("#92400E")
    if "cumple" in r:
        return colors.HexColor("#166534")
    return GRIS


def _pie_de_pagina(generado_por, fecha_txt):
    def dibujar(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDE)
        canvas.line(2 * cm, 1.7 * cm, letter[0] - 2 * cm, 1.7 * cm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        autor = f" por {_sin_xml(generado_por)}" if generado_por else ""
        canvas.drawString(2 * cm, 1.25 * cm, f"Informe generado el {fecha_txt}{autor}. El análisis de IA es un apoyo; la decisión final es de Talento Humano.")
        canvas.drawRightString(letter[0] - 2 * cm, 1.25 * cm, f"Página {doc.page}")
        canvas.restoreState()
    return dibujar


def _sin_xml(valor):
    return str(valor).encode("cp1252", "replace").decode("cp1252")


def generar_informe_pdf(postulacion, vacante, observaciones="", generado_por=""):
    """
    postulacion: dict con nombre, documento, email, telefono, estado, vacante_titulo,
                 vacante_area, puntaje_total, decision, resumen_ejecutivo,
                 cumplimiento_json, alertas_json
    vacante:     dict de la vacante (usa otros_criterios para los pesos)
    Devuelve los bytes del PDF.
    """
    zona = timezone(timedelta(hours=-5))
    fecha_txt = datetime.now(zona).strftime("%d/%m/%Y %H:%M")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=2.3 * cm,
        title=f"Informe de evaluación - {_sin_xml(postulacion.get('nombre', ''))}",
        author="Talento Humano"
    )
    ancho = letter[0] - 4 * cm
    historia = []

    # Encabezado
    historia.append(Paragraph("Informe de Evaluación de Candidato", _TITULO))
    historia.append(Paragraph("Colegio Americano de Barranquilla - Talento Humano", _SUBTITULO))
    historia.append(Spacer(1, 4))
    linea = Table([[""]], colWidths=[ancho], rowHeights=[3])
    linea.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL)]))
    historia.append(linea)

    # Datos del candidato y la vacante
    historia.append(Paragraph("Datos del candidato", _SECCION))
    datos = [
        [_p("Nombre", _PEQUENO), _p(postulacion.get("nombre") or "-"), _p("Vacante", _PEQUENO), _p(postulacion.get("vacante_titulo") or vacante.get("titulo") or "-")],
        [_p("Documento", _PEQUENO), _p(postulacion.get("documento") or "-"), _p("Área", _PEQUENO), _p(postulacion.get("vacante_area") or vacante.get("area") or "-")],
        [_p("Correo", _PEQUENO), _p(postulacion.get("email") or "-"), _p("Estado del proceso", _PEQUENO), _p(postulacion.get("estado") or "-")],
        [_p("Teléfono", _PEQUENO), _p(postulacion.get("telefono") or "-"), "", ""],
    ]
    t_datos = Table(datos, colWidths=[2.4 * cm, 6.4 * cm, 3.2 * cm, ancho - 12 * cm])
    t_datos.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), AZUL_CLARO),
        ("BACKGROUND", (2, 0), (2, 2), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    historia.append(t_datos)

    # Veredicto
    try:
        score = int(postulacion.get("puntaje_total"))
    except (TypeError, ValueError):
        score = None
    decision = postulacion.get("decision") or "En revisión"
    if score is None:
        fondo, texto_color = colors.HexColor("#F1F5F9"), GRIS
    elif score >= 70:
        fondo, texto_color = colors.HexColor("#DCFCE7"), colors.HexColor("#166534")
    elif score >= 50:
        fondo, texto_color = colors.HexColor("#FEF3C7"), colors.HexColor("#92400E")
    else:
        fondo, texto_color = colors.HexColor("#FEE2E2"), colors.HexColor("#991B1B")

    historia.append(Paragraph("Veredicto de la Inteligencia Artificial", _SECCION))
    est_veredicto = ParagraphStyle("Ver", parent=_NORMAL, fontSize=13, leading=16, textColor=texto_color, fontName="Helvetica-Bold")
    est_puntaje = ParagraphStyle("Pun", parent=est_veredicto, fontSize=20, leading=24, alignment=2)
    t_ver = Table(
        [[Paragraph(_t(decision), est_veredicto), Paragraph("-" if score is None else f"{score} / 100", est_puntaje)]],
        colWidths=[ancho * 0.65, ancho * 0.35]
    )
    t_ver.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fondo),
        ("BOX", (0, 0), (-1, -1), 0.8, texto_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    historia.append(t_ver)

    # Resumen ejecutivo
    historia.append(Paragraph("Resumen ejecutivo", _SECCION))
    historia.append(_p(postulacion.get("resumen_ejecutivo") or "Sin resumen disponible."))

    # Matriz de requisitos y ponderación
    filas, total_txt, hay_estimados = _filas_matriz(vacante, postulacion)
    historia.append(Paragraph("Matriz de requisitos y ponderación", _SECCION))
    encabezado = [Paragraph(t, _ENCABEZADO_TABLA) for t in ["Criterio", "Peso", "Resultado", "Puntaje", "Aporte", "Evidencia"]]
    cuerpo = []
    for nombre, peso, resultado, punt, aporte, detalle in filas:
        est_res = ParagraphStyle("Res", parent=_PEQUENO, textColor=_color_resultado(resultado), fontName="Helvetica-Bold")
        cuerpo.append([_p(nombre, _PEQUENO), _p(peso, _PEQUENO), Paragraph(_t(resultado), est_res), _p(punt, _PEQUENO), _p(aporte, _PEQUENO), _p(detalle, _PEQUENO)])
    est_total = ParagraphStyle("Tot", parent=_PEQUENO, fontName="Helvetica-Bold")
    cuerpo.append([Paragraph("Total ponderado", est_total), "", "", "", Paragraph(_t(total_txt), est_total), ""])
    t_matriz = Table([encabezado] + cuerpo, colWidths=[3.4 * cm, 1.3 * cm, 2.0 * cm, 1.8 * cm, 1.8 * cm, ancho - 10.3 * cm], repeatRows=1)
    t_matriz.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDE),
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("SPAN", (0, -1), (3, -1)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    historia.append(t_matriz)
    if hay_estimados:
        historia.append(Spacer(1, 3))
        historia.append(_p("Los valores con ~ son una estimación de un análisis anterior. Vuelva a analizar con IA para obtener el puntaje ponderado real.", _PEQUENO))

    # Alertas
    alertas = _lista(postulacion.get("alertas_json"))
    historia.append(Paragraph("Puntos de atención detectados por la IA", _SECCION))
    if alertas:
        for a in alertas:
            historia.append(Paragraph("- " + _t(a), _NORMAL))
    else:
        historia.append(_p("No se detectaron inconsistencias ni alertas documentales críticas."))

    # Observaciones del líder de TTHH
    historia.append(Paragraph("Observaciones del líder de Talento Humano", _SECCION))
    texto_obs = (observaciones or "").strip() or "Sin observaciones registradas."
    t_obs = Table([[_p(texto_obs)]], colWidths=[ancho])
    t_obs.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFBEB")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#FCD34D")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    historia.append(t_obs)

    pie = _pie_de_pagina(generado_por, fecha_txt)
    doc.build(historia, onFirstPage=pie, onLaterPages=pie)
    return buffer.getvalue()
