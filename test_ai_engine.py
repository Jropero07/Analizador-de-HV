from ai_engine import analizar_candidato_vs_vacante


vacante = {
    "titulo": "Auxiliar de Sistemas",
    "area": "Tecnología",
    "descripcion": "Brindar soporte técnico a usuarios y equipos de la organización.",
    "formacion": "Técnico o tecnólogo en sistemas, informática o áreas relacionadas.",
    "experiencia": "Mínimo 1 año de experiencia en soporte técnico.",
    "habilidades": "Soporte de computadores, redes básicas, mantenimiento preventivo y atención a usuarios.",
    "otros_criterios": "Capacidad de trabajo en equipo y buena atención al usuario."
}


hoja_de_vida = """
HOJA DE VIDA

Nombre: Juan Carlos Pérez
Cédula: 1234567890
Teléfono: 3001234567
Correo: juan.perez@gmail.com

FORMACIÓN ACADÉMICA

Técnico en Sistemas.

EXPERIENCIA LABORAL

Auxiliar de Sistemas - Empresa ABC
Enero 2024 - Enero 2026

Funciones:
- Soporte técnico a usuarios.
- Mantenimiento preventivo de computadores.
- Instalación de software.
- Configuración básica de redes.
- Atención y soporte a usuarios.

HABILIDADES

- Soporte técnico.
- Mantenimiento de computadores.
- Redes básicas.
- Atención al usuario.
- Trabajo en equipo.
"""


print("Iniciando análisis candidato vs. vacante...")
print()

resultado = analizar_candidato_vs_vacante(
    vacante,
    hoja_de_vida
)

print("RESULTADO DEL ANÁLISIS:")
print()

print(resultado)