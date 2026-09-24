"""
Pruebas de la ponderación. No usan internet ni la API de Groq.

Ejecutar:   python test_ponderacion.py
"""

import json
import os
import shutil
import tempfile

import ai_engine
import ponderacion as pd_

# Las pruebas del motor simulan la IA: sin caché para que cada prueba sea independiente
ai_engine.USAR_CACHE = False


VAC_DISENO = {
    "titulo": "Diseñador(a) gráfico(a)",
    "area": "comunicaciones",
    "descripcion": "Diseño de piezas",
    "formacion": "Diseñador(a) gráfico(a)",
    "experiencia": "Mínimo 1 año",
    "habilidades": "Capcut, Canva",
    "otros_criterios": (
        "- Experiencia: 35%\n- Portafolio: 20%\n- Habilidades técnicas (software): 20%\n"
        "- Formación: 15%\n- Habilidades blandas: 10%"
    ),
}


def test_clasificacion_no_mezcla_blandas_con_tecnicas():
    # Este era el bug de la tabla: ambas contienen la palabra "habilidades"
    assert pd_.clasificar_criterio("Habilidades blandas") == "habilidades_blandas"
    assert pd_.clasificar_criterio("Habilidades técnicas (software)") == "habilidades_tecnicas"
    assert pd_.clasificar_criterio("Habilidades técnicas / software") == "habilidades_tecnicas"
    assert pd_.clasificar_criterio("Portafolio / Proyectos") == "portafolio"
    assert pd_.clasificar_criterio("Formación académica") == "formacion"
    assert pd_.clasificar_criterio("Experiencia laboral") == "experiencia"


def test_extraer_ponderaciones_formatos():
    p = pd_.extraer_ponderaciones("- Experiencia: 35%\n* Portafolio (20%)\n45% Formación\nNota: sin porcentaje")
    assert [c["clave"] for c in p] == ["experiencia", "portafolio", "formacion"]
    assert [c["peso"] for c in p] == [35.0, 20.0, 45.0]


def test_ponderaciones_que_no_suman_100_se_reescalan():
    p = pd_.extraer_ponderaciones("Experiencia: 50%\nFormación: 25%")
    assert abs(sum(c["peso"] for c in p) - 100) < 0.05
    assert p[0]["peso"] == 66.67


def test_sin_porcentajes_usa_estandar():
    p = pd_.obtener_ponderaciones("Pruebas automáticas")
    assert [c["clave"] for c in p] == [
        "experiencia", "portafolio", "habilidades_tecnicas", "formacion", "habilidades_blandas"
    ]
    assert sum(c["peso"] for c in p) == 100


def test_puntaje_ponderado():
    p = pd_.obtener_ponderaciones(VAC_DISENO["otros_criterios"])
    crit = {
        "experiencia": {"puntaje": 100},
        "portafolio": {"puntaje": 100},
        "habilidades_tecnicas": {"puntaje": 50},
        "formacion": {"puntaje": 100},
        "habilidades_blandas": {"puntaje": 100},
    }
    total, faltantes = pd_.calcular_puntaje_total(crit, p)
    assert total == 90 and faltantes == []   # 35+20+10+15+10


def test_criterio_sin_evaluar_no_castiga_con_cero():
    p = pd_.obtener_ponderaciones(VAC_DISENO["otros_criterios"])
    crit = {c["clave"]: {"puntaje": 80} for c in p}
    crit["portafolio"] = {"puntaje": None}
    total, faltantes = pd_.calcular_puntaje_total(crit, p)
    assert total == 80 and faltantes == ["portafolio"]


def test_normalizar_puntaje():
    assert pd_.normalizar_puntaje(85) == 85
    assert pd_.normalizar_puntaje("85%") == 85
    assert pd_.normalizar_puntaje("92,4") == 92
    assert pd_.normalizar_puntaje(150) == 100
    assert pd_.normalizar_puntaje(-5) == 0
    assert pd_.normalizar_puntaje("sin dato") is None
    assert pd_.normalizar_puntaje(None) is None


def test_decision_por_umbral():
    assert pd_.decision_desde_puntaje(70) == "Recomendado para entrevista"
    assert pd_.decision_desde_puntaje(69).startswith("Revisión manual")
    assert pd_.decision_desde_puntaje(49).startswith("No recomendado")


def _simular_ia(monkeypatch_respuestas):
    """Reemplaza la llamada a Groq por respuestas fijas."""
    original = ai_engine.peticion_groq_directa
    respuestas = list(monkeypatch_respuestas)

    def falsa(prompt, temperature=0.1):
        return respuestas.pop(0)

    ai_engine.peticion_groq_directa = falsa
    return original


def test_motor_calcula_total_y_decision_en_python():
    respuesta = json.dumps({
        "resumen_ejecutivo": "Perfil sólido.",
        "criterios": {
            "experiencia": {"puntaje": 95, "detalle": "2 años"},
            "portafolio": {"puntaje": 90, "detalle": "Behance"},
            "habilidades_tecnicas": {"puntaje": 40, "detalle": "Falta Capcut y Canva"},
            "formacion": {"puntaje": 100, "detalle": "Diseño gráfico"},
            "habilidades_blandas": {"puntaje": 80, "detalle": "Trabajo en equipo"},
        },
        "alertas_detectadas": ["Falta Capcut"],
        # aunque la IA "invente" un total, se ignora:
        "puntaje_total": 99,
    })
    original = _simular_ia([respuesta])
    try:
        res = ai_engine.analizar_candidato_vs_vacante(VAC_DISENO, "HV...")
    finally:
        ai_engine.peticion_groq_directa = original

    # 35*.95 + 20*.90 + 20*.40 + 15*1.0 + 10*.80 = 33.25+18+8+15+8 = 82.25
    assert res["puntaje_total"] == 82
    assert res["decision"] == "Recomendado para entrevista"
    assert res["cumplimiento_criterios"]["habilidades_tecnicas"]["resultado"] == "Parcial"
    assert res["cumplimiento_criterios"]["habilidades_blandas"]["puntaje"] == 80
    assert res["alertas_detectadas"] == ["Falta Capcut"]


def test_motor_reintenta_si_json_invalido():
    buena = json.dumps({"criterios": {"experiencia": 60, "formacion": 60, "habilidades_tecnicas": 60,
                                       "habilidades_blandas": 60, "portafolio": 60}})
    original = _simular_ia(["esto no es json", buena])
    try:
        res = ai_engine.analizar_candidato_vs_vacante({"titulo": "X"}, "HV")
    finally:
        ai_engine.peticion_groq_directa = original
    assert res["puntaje_total"] == 60
    assert res["decision"].startswith("Revisión manual")


def test_motor_criterio_faltante_genera_alerta():
    parcial = json.dumps({"criterios": {"experiencia": {"puntaje": 80}}})
    original = _simular_ia([parcial])
    try:
        res = ai_engine.analizar_candidato_vs_vacante({"titulo": "X"}, "HV")
    finally:
        ai_engine.peticion_groq_directa = original
    assert res["puntaje_total"] == 80
    assert len(res["alertas_detectadas"]) == 4


def test_motor_error_si_nunca_hay_json():
    original = _simular_ia(["basura", "más basura"])
    try:
        res = ai_engine.analizar_candidato_vs_vacante({"titulo": "X"}, "HV")
    finally:
        ai_engine.peticion_groq_directa = original
    assert "error" in res


def test_tabla_no_mezcla_criterios_y_muestra_total():
    analisis = {
        "puntaje_total": 82,
        "cumplimiento_json": json.dumps({
            "experiencia": {"puntaje": 95, "detalle": "EVID_EXP"},
            "portafolio": {"puntaje": 90, "detalle": "EVID_PORT"},
            "habilidades_tecnicas": {"puntaje": 40, "detalle": "EVID_TEC"},
            "formacion": {"puntaje": 100, "detalle": "EVID_FOR"},
            "habilidades_blandas": {"puntaje": 80, "detalle": "EVID_BLA"},
        }),
    }
    html_ = pd_.generar_tabla_ponderacion_html(VAC_DISENO, analisis)
    # cada evidencia en su propia fila (antes "blandas" mostraba la de "técnicas")
    fila_blandas = [f for f in html_.split("<tr") if "Habilidades blandas" in f][0]
    assert "EVID_BLA" in fila_blandas and "EVID_TEC" not in fila_blandas
    assert "82 / 100" in html_


def test_tabla_con_analisis_antiguo_avisa_y_estima():
    # Formato de la versión anterior (lo que hay guardado en la BD real)
    antiguo = {
        "puntaje_total": 90,
        "cumplimiento_json": json.dumps({
            "experiencia": {"resultado": "Cumple", "detalle": "2 años"},
            "portafolio": {"resultado": "Cumple", "detalle": "Behance"},
            "habilidades_tecnicas": {"resultado": "Parcial", "detalle": "Sin Canva"},
            "formacion": {"resultado": "Cumple", "detalle": "Diseño"},
            "habilidades_blandas": {"resultado": "Cumple", "detalle": "Equipo"},
        }),
    }
    html_ = pd_.generar_tabla_ponderacion_html(VAC_DISENO, antiguo)
    assert "Volver a analizar" in html_
    assert "≈ 90 / 100" in html_


def test_tabla_escapa_html_de_la_ia():
    analisis = {"cumplimiento_json": {"experiencia": {"puntaje": 80, "detalle": "<script>alert(1)</script>"}}}
    html_ = pd_.generar_tabla_ponderacion_html(VAC_DISENO, analisis)
    assert "<script>" not in html_


def test_consulta_usa_el_ultimo_analisis_completado():
    """Con varios análisis por postulación ya no se duplican filas ni se elige el puntaje más alto."""
    import database

    tmp = tempfile.mkdtemp()
    ruta_original = database.DB_PATH
    try:
        db_real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "asistente_tthh.db")
        if os.path.exists(db_real):
            shutil.copy(db_real, os.path.join(tmp, "t.db"))
        database.DB_PATH = os.path.join(tmp, "t.db")
        database.inicializar_db()

        cid = database.crear_candidato("Test Uno", "1", "300", "uno@test.com")
        vid = database.crear_vacante("V", "A", "d", "f", "e", "h", "- Experiencia: 100%")
        pid = database.crear_postulacion(cid, vid, "Carga Manual", "", "", "", "texto")

        database.guardar_analisis(cid, vid, 90, "Viejo", "r", {}, [], postulacion_id=pid)
        database.guardar_error_analisis(cid, vid, "fallo de red", postulacion_id=pid)
        database.guardar_analisis(cid, vid, 60, "Nuevo", "r", {}, [], postulacion_id=pid)

        filas = [dict(f) for f in database.obtener_postulaciones() if f["id"] == pid]
        assert len(filas) == 1, f"filas duplicadas: {len(filas)}"
        assert filas[0]["puntaje_total"] == 60 and filas[0]["decision"] == "Nuevo"
    finally:
        database.DB_PATH = ruta_original
        shutil.rmtree(tmp, ignore_errors=True)


def test_tabla_html_es_una_sola_linea_sin_lineas_en_blanco():
    """Con líneas en blanco o sangría, st.markdown pinta el HTML como código en vez de tabla."""
    analisis = {"cumplimiento_json": json.dumps({
        "experiencia": {"puntaje": 80, "detalle": "Línea 1\n\nLínea 2 tras línea en blanco"},
    })}
    html_tabla = pd_.generar_tabla_ponderacion_html(VAC_DISENO, analisis)
    assert "\n" not in html_tabla
    assert html_tabla.startswith("<div")
    assert "Línea 1 Línea 2 tras línea en blanco" in html_tabla


def test_mismo_documento_da_siempre_el_mismo_resultado_gracias_a_la_cache():
    llamadas = []
    respuestas = [
        json.dumps({"criterios": {"experiencia": 86, "formacion": 86, "habilidades_tecnicas": 86,
                                   "habilidades_blandas": 86, "portafolio": 86}}),
        json.dumps({"criterios": {"experiencia": 93, "formacion": 93, "habilidades_tecnicas": 93,
                                   "habilidades_blandas": 93, "portafolio": 93}}),
    ]

    def falsa(prompt, temperature=0.1):
        llamadas.append(prompt)
        return respuestas[len(llamadas) - 1]

    tmp = tempfile.mkdtemp()
    original_peticion = ai_engine.peticion_groq_directa
    original_ruta, original_uso = ai_engine.RUTA_CACHE, ai_engine.USAR_CACHE
    ai_engine.peticion_groq_directa = falsa
    ai_engine.RUTA_CACHE = os.path.join(tmp, "cache.json")
    ai_engine.USAR_CACHE = True
    try:
        r1 = ai_engine.analizar_candidato_vs_vacante(VAC_DISENO, "HV de Ana")
        r2 = ai_engine.analizar_candidato_vs_vacante(VAC_DISENO, "HV de Ana")   # mismo documento
        assert len(llamadas) == 1                      # no volvió a llamar a la IA
        assert r1["puntaje_total"] == r2["puntaje_total"] == 86

        r3 = ai_engine.analizar_candidato_vs_vacante(VAC_DISENO, "HV de Ana con cambios")
        assert len(llamadas) == 2                      # documento distinto -> análisis nuevo
        assert r3["puntaje_total"] == 93
    finally:
        ai_engine.peticion_groq_directa = original_peticion
        ai_engine.RUTA_CACHE, ai_engine.USAR_CACHE = original_ruta, original_uso
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"OK    {nombre}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"FALLA {nombre}: {type(e).__name__}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas correctas")
    raise SystemExit(1 if fallos else 0)
