import os
import io
import re
from datetime import datetime
import pdfplumber
import pypdf
import docx
from PIL import Image

def extraer_texto_pdf(fuente):
    """
    Extrae texto de un archivo PDF usando pdfplumber con fallback a pypdf.
    fuente puede ser una ruta (str) o un objeto archivo en memoria (BytesIO / UploadedFile).
    """
    texto = ""
    # 1. Intento primario con pdfplumber
    try:
        if hasattr(fuente, "seek"):
            fuente.seek(0)
        with pdfplumber.open(fuente) as pdf:
            for pagina in pdf.pages:
                t = pagina.extract_text()
                if t:
                    texto += t + "\n"
    except Exception as e:
        print(f"Aviso: pdfplumber no pudo leer el PDF ({e}), intentando con pypdf...")

    # 2. Fallback con pypdf si pdfplumber no obtuvo texto
    if not texto.strip():
        try:
            if hasattr(fuente, "seek"):
                fuente.seek(0)
            reader = pypdf.PdfReader(fuente)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    texto += t + "\n"
        except Exception as e:
            print(f"Error al leer PDF con pypdf: {e}")

    return texto.strip()


def extraer_texto_docx(fuente):
    """
    Extrae texto de un archivo Word (.docx).
    fuente puede ser una ruta (str) o un objeto archivo en memoria.
    """
    texto = ""
    try:
        if hasattr(fuente, "seek"):
            fuente.seek(0)
        doc = docx.Document(fuente)
        for p in doc.paragraphs:
            if p.text:
                texto += p.text + "\n"
    except Exception as e:
        print(f"Error al leer DOCX: {e}")
    return texto.strip()


def extraer_texto_txt(fuente):
    """Extrae texto de un archivo plano .txt probando codificaciones comunes."""
    try:
        if hasattr(fuente, "read"):
            if hasattr(fuente, "seek"):
                fuente.seek(0)
            raw = fuente.read()
        else:
            with open(fuente, "rb") as f:
                raw = f.read()

        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                return raw.decode(encoding).strip()
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        print(f"Error al leer TXT: {e}")
        return ""


def procesar_documento(fuente, nombre_archivo=None):
    """
    Detecta la extensión del archivo y extrae su contenido de texto.
    Acepta rutas de archivo (str) o archivos en memoria (UploadedFile / BytesIO).
    """
    if nombre_archivo is None:
        if isinstance(fuente, str):
            nombre_archivo = fuente
        elif hasattr(fuente, "name"):
            nombre_archivo = fuente.name
        else:
            nombre_archivo = ""

    ext = os.path.splitext(nombre_archivo)[1].lower()

    if ext == ".pdf":
        return extraer_texto_pdf(fuente)
    elif ext in [".docx", ".doc"]:
        return extraer_texto_docx(fuente)
    elif ext == ".txt":
        return extraer_texto_txt(fuente)
    elif ext in [".jpg", ".jpeg", ".png"]:
        try:
            if hasattr(fuente, "seek"):
                fuente.seek(0)
            Image.open(fuente)
            return "[Imagen o portafolio visual subido correctamente. Se evaluará la información de texto presente]."
        except Exception as e:
            return f"Error al procesar la imagen: {e}"
    else:
        return "Formato de archivo no soportado."


def guardar_archivo_subido(uploaded_file, destino_dir="data/uploads"):
    """
    Guarda de forma segura un archivo subido (UploadedFile de Streamlit o bytes)
    en el directorio destino evitando colisiones y sanitizando el nombre.
    Retorna (nombre_guardado, ruta_completa).
    """
    os.makedirs(destino_dir, exist_ok=True)

    nombre_original = getattr(uploaded_file, "name", "documento.pdf")
    nombre_limpio = re.sub(r'[^\w\.\-\(\) ]', '_', nombre_original).strip()

    # Prevenir sobreescritura agregando timestamp si ya existe
    ruta = os.path.join(destino_dir, nombre_limpio)
    if os.path.exists(ruta):
        base, ext = os.path.splitext(nombre_limpio)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_limpio = f"{base}_{timestamp}{ext}"
        ruta = os.path.join(destino_dir, nombre_limpio)

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    with open(ruta, "wb") as f:
        if hasattr(uploaded_file, "getbuffer"):
            f.write(uploaded_file.getbuffer())
        elif hasattr(uploaded_file, "read"):
            f.write(uploaded_file.read())
        else:
            f.write(uploaded_file)

    return nombre_limpio, ruta