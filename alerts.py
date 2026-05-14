import os
import requests
from dotenv import load_dotenv

load_dotenv()

def enviar_telegram(mensaje: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("⚠️  Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("✅ Alerta enviada a Telegram")
            return True
        else:
            print(f"❌ Error Telegram: {r.text}")
            return False
    except Exception as e:
        print(f"❌ Error enviando alerta: {e}")
        return False

if __name__ == "__main__":
    enviar_telegram("✅ *Test CAN SLIM Trader*\nSistema de alertas funcionando correctamente.")
