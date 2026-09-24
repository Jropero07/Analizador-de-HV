import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

api_key = os.getenv("GROQ_API_KEY")

print("API KEY encontrada:", bool(api_key))
print("Longitud:", len(api_key) if api_key else 0)

url = "https://api.groq.com/openai/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

payload = {
    "model": "openai/gpt-oss-20b",
    "messages": [
        {
            "role": "user",
            "content": 'Responde únicamente con este JSON: {"ok": true}',
        }
    ],
    "temperature": 0,
    "response_format": {
        "type": "json_object"
    },
}

print("\nEnviando petición a Groq...")

try:
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    print("HTTP:", response.status_code)
    print("Respuesta:")
    print(response.text)

except requests.exceptions.Timeout:
    print("ERROR: La petición tardó demasiado.")

except requests.exceptions.RequestException as e:
    print("ERROR DE CONEXIÓN:")
    print(e)

except Exception as e:
    print("ERROR:")
    print(e)