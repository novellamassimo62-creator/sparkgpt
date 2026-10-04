import base64
import binascii
import io
import json
import mimetypes
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import jwt
from PIL import Image, ImageOps, UnidentifiedImageError

from image_generator import ImageGenerationError, MAX_IMAGE_BYTES, generate_image, generator_status


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8000
MODEL = "qwen2.5:3b"
VISION_MODEL = "qwen2.5vl:3b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
CLERK_CONFIG_FILE = ROOT / "clerk-config.json"
AUTHORIZED_ORIGINS = {"http://localhost:8000", "http://127.0.0.1:8000"}
MAX_REQUEST_BYTES = 64 * 1024
MAX_MESSAGES = 24
MAX_MESSAGE_CHARS = 6000
MAX_CHAT_REQUEST_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + MAX_REQUEST_BYTES
MAX_IMAGE_REQUEST_BYTES = MAX_IMAGE_BYTES + 64 * 1024
MAX_VISION_IMAGE_SIDE = 896
JWKS_CLIENTS = {}


def load_clerk_config():
    try:
        data = json.loads(CLERK_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError("Il file clerk-config.json non è leggibile o contiene JSON non valido.") from error

    publishable_key = data.get("publishable_key") if isinstance(data, dict) else None
    if not isinstance(publishable_key, str) or not publishable_key.startswith(("pk_test_", "pk_live_")):
        raise ValueError("Inserisci una Publishable Key Clerk valida in clerk-config.json.")

    encoded_domain = publishable_key.split("_", 2)[2]
    try:
        domain_bytes = base64.urlsafe_b64decode(encoded_domain + "=" * (-len(encoded_domain) % 4))
        frontend_api = domain_bytes.decode("ascii").rstrip("$")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("La Publishable Key Clerk non è valida.") from error
    if not frontend_api or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for char in frontend_api):
        raise ValueError("La Publishable Key Clerk non contiene un dominio valido.")

    return {
        "publishable_key": publishable_key,
        "frontend_api": frontend_api,
        "jwks_url": f"https://{frontend_api}/.well-known/jwks.json",
    }


def verify_clerk_session(token, origin, config):
    jwks_url = config["jwks_url"]
    jwks_client = JWKS_CLIENTS.get(jwks_url)
    if jwks_client is None:
        jwks_client = jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600, timeout=5)
        JWKS_CLIENTS[jwks_url] = jwks_client

    signing_key = jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=f"https://{config['frontend_api']}",
        leeway=30,
        options={"require": ["exp", "iat", "iss", "sub"]},
    )
    authorized_party = claims.get("azp")
    if authorized_party and authorized_party != origin:
        raise jwt.InvalidTokenError("La sessione non è valida per questa origine.")
    return claims


class SparkGPTHandler(BaseHTTPRequestHandler):
    server_version = "sparkgpt-local/1.0"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-SparkGPT-Provider", "Ollama")
        self.send_header("X-SparkGPT-Model", MODEL)
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            print("Il client ha interrotto la connessione durante l'invio della risposta.")

    def do_GET(self):
        request_path = unquote(urlsplit(self.path).path)
        if request_path == "/api/config":
            try:
                config = load_clerk_config()
            except ValueError as error:
                self.send_json(500, {"configured": False, "error": str(error)})
                return
            self.send_json(200, {
                "configured": config is not None,
                "publishableKey": config["publishable_key"] if config else None,
            })
            return
        if request_path == "/api/status":
            self.send_json(200, {
                "provider": "Ollama",
                "model": MODEL,
                "visionModel": VISION_MODEL,
                "imageGenerator": generator_status(),
            })
            return
        if request_path == "/":
            request_path = "/index.html"

        if request_path != "/index.html":
            self.send_error(404, "File non trovato")
            return

        requested_file = (ROOT / request_path.lstrip("/")).resolve()
        if not requested_file.is_file():
            self.send_error(404, "File non trovato")
            return

        content_type = mimetypes.guess_type(requested_file.name)[0] or "application/octet-stream"
        body = requested_file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-SparkGPT-Provider", "Ollama")
        self.send_header("X-SparkGPT-Model", MODEL)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        request_path = urlsplit(self.path).path
        if request_path not in {"/api/chat", "/api/images/generate"}:
            self.send_json(404, {"error": "Endpoint non trovato."})
            return

        if not self.authenticate_request():
            return
        if request_path == "/api/images/generate":
            self.generate_image()
            return

        self.chat()

    def authenticate_request(self):
        try:
            config = load_clerk_config()
        except ValueError as error:
            self.send_json(503, {"error": str(error)})
            return False
        if config is None:
            self.send_json(503, {"error": "Accesso non configurato. Completa la configurazione Clerk prima di usare la chat."})
            return False

        origin = self.headers.get("Origin", "")
        if origin not in AUTHORIZED_ORIGINS:
            self.send_json(403, {"error": "Origine della richiesta non autorizzata."})
            return False
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            self.send_json(401, {"error": "Accedi al tuo account per usare la chat."})
            return False
        try:
            verify_clerk_session(token, origin, config)
        except jwt.PyJWKClientConnectionError as error:
            print(f"Impossibile contattare Clerk per verificare la sessione: {error}")
            self.send_json(503, {"error": "Non riesco a verificare l'accesso con Clerk. Controlla la connessione e riprova."})
            return False
        except (jwt.PyJWTError, URLError, TimeoutError, OSError, ValueError) as error:
            print(f"Sessione Clerk rifiutata: {error}")
            self.send_json(401, {"error": "La sessione non è valida o è scaduta. Accedi di nuovo."})
            return False
        return True

    def read_request_body(self, maximum_bytes):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Dimensione della richiesta non valida."})
            return None
        if content_length < 1 or content_length > maximum_bytes:
            self.send_json(413, {"error": "La richiesta è vuota o supera il limite consentito."})
            return None
        return self.rfile.read(content_length)

    def generate_image(self):
        if self.headers.get_content_type() != "multipart/form-data":
            self.send_json(415, {"error": "La richiesta deve contenere una descrizione e una foto facoltativa."})
            return

        request_body = self.read_request_body(MAX_IMAGE_REQUEST_BYTES)
        if request_body is None:
            return

        try:
            mime_message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {self.headers.get('Content-Type')}\r\n"
                "MIME-Version: 1.0\r\n\r\n".encode("ascii") + request_body
            )
            if not mime_message.is_multipart() or mime_message.defects:
                raise ValueError("Formato multipart non valido.")

            fields = {}
            for part in mime_message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                name = part.get_param("name", header="content-disposition")
                if name not in {"prompt", "image"} or name in fields:
                    raise ValueError("Il modulo contiene un campo duplicato o non supportato.")
                fields[name] = part

            prompt_part = fields.get("prompt")
            if prompt_part is None or prompt_part.get_filename() is not None:
                self.send_json(400, {"error": "Scrivi una descrizione valida per l'immagine."})
                return
            prompt_bytes = prompt_part.get_payload(decode=True) or b""
            prompt = prompt_bytes.decode(prompt_part.get_content_charset() or "utf-8").strip()
            if not prompt or len(prompt) > 1000:
                self.send_json(400, {"error": "La descrizione deve contenere da 1 a 1000 caratteri."})
                return

            image_bytes = None
            image_part = fields.get("image")
            if image_part is not None:
                image_bytes = image_part.get_payload(decode=True) or b""
                if image_part.get_filename() is None:
                    self.send_json(400, {"error": "Il file caricato non è una foto valida."})
                    return
                if image_part.get_content_type() not in {"image/png", "image/jpeg", "image/webp"}:
                    self.send_json(415, {"error": "Carica una foto PNG, JPG o WebP."})
                    return

        except (ValueError, UnicodeDecodeError) as error:
            self.send_json(400, {"error": f"Richiesta immagine non valida: {error}"})
            return

        try:
            image_data = generate_image(prompt, image_bytes)
        except ImageGenerationError as error:
            self.send_json(503, {"error": str(error)})
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(image_data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(image_data)
        except (BrokenPipeError, ConnectionResetError):
            print("Il client ha interrotto il download dell'immagine generata.")

    def chat(self):

        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "La richiesta deve essere in formato JSON."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Dimensione della richiesta non valida."})
            return
        if content_length < 1 or content_length > MAX_CHAT_REQUEST_BYTES:
            self.send_json(413, {"error": "La richiesta è vuota o troppo grande."})
            return

        try:
            data = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Il messaggio inviato non è un JSON valido."})
            return

        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            self.send_json(400, {"error": "La conversazione non contiene messaggi validi."})
            return

        conversation = []
        contains_image = False
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                self.send_json(400, {"error": "La conversazione contiene un messaggio non valido."})
                return
            text = message.get("content")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
                self.send_json(400, {"error": "Un messaggio è vuoto o supera il limite consentito."})
                return
            ollama_message = {"role": message["role"], "content": text.strip()}
            images = message.get("images", [])
            if not isinstance(images, list) or len(images) > 1:
                self.send_json(400, {"error": "Puoi allegare una sola immagine per messaggio."})
                return
            if images:
                if message["role"] != "user" or contains_image:
                    self.send_json(400, {"error": "Puoi includere una sola immagine, allegata a un messaggio dell'utente."})
                    return
                if not isinstance(images[0], str):
                    self.send_json(400, {"error": "L'immagine allegata non è codificata correttamente."})
                    return
                try:
                    image_bytes = base64.b64decode(images[0], validate=True)
                    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
                        raise ValueError("dimensione non valida")
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        if image.format not in {"PNG", "JPEG", "WEBP"}:
                            raise ValueError("formato non supportato")
                        if image.width * image.height > 40_000_000:
                            raise ValueError("risoluzione troppo grande")
                        image.load()
                        vision_image = ImageOps.exif_transpose(image).convert("RGB")
                        vision_image.thumbnail(
                            (MAX_VISION_IMAGE_SIDE, MAX_VISION_IMAGE_SIDE),
                            Image.Resampling.LANCZOS,
                        )
                        optimized_image = io.BytesIO()
                        vision_image.save(
                            optimized_image,
                            format="JPEG",
                            quality=90,
                        )
                except (binascii.Error, ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
                    self.send_json(400, {"error": "L'immagine non è valida oppure supera i limiti consentiti. Carica PNG, JPG o WebP fino a 8 MB."})
                    return
                ollama_message["images"] = [
                    base64.b64encode(optimized_image.getvalue()).decode("ascii")
                ]
                contains_image = True
            conversation.append(ollama_message)

        request_body = {
            "model": VISION_MODEL if contains_image else MODEL,
            "messages": [{
                "role": "system",
                "content": (
                    "Sei sparkgpt, un assistente AI utile, intelligente e naturale. "
                    "Rispondi in italiano se l'utente scrive in italiano e usa la conversazione precedente "
                    "per capire il contesto. Rispondi direttamente alla domanda specifica: a un saluto "
                    "come 'Ciao, come stai?' rispondi con un saluto cordiale, per esempio 'Ciao! Tutto "
                    "bene, grazie 😊. Tu come stai?', non con un'offerta generica di aiuto. Non descrivere "
                    "il tuo processo, non ripetere la richiesta e non mostrare ragionamenti o appunti "
                    "interni. Per compiti e studio spiega i passaggi in modo chiaro e con esempi utili. "
                    "Quando ricevi un'immagine, osservala attentamente e rispondi alla domanda "
                    "dell'utente basandoti sui dettagli visibili; descrivi il testo leggibile, gli oggetti "
                    "e la scena, senza inventare dettagli che non si vedono. "
                    "Adatta il tono alla situazione: se l'utente parla di un problema serio, doloroso, "
                    "spaventoso o delicato, rispondi con calma, serietà ed empatia, senza battute né "
                    "emoji. Nelle chiacchiere e nelle situazioni leggere sii amichevole e spontaneo; "
                    "usa un umorismo gentile quando ci sta e inserisci almeno un'emoji adatta, "
                    "senza esagerare. "
                    "Per studio, compiti e richieste pratiche resta chiaro e incoraggiante, senza "
                    "trasformare la risposta in una battuta. "
                    "Di regola rispondi in modo conciso, in 1-4 frasi; aggiungi dettagli ed esempi "
                    "solo quando servono alla richiesta. Per i compiti spiega il procedimento "
                    "essenziale, senza dilungarti. Esprimiti con sicurezza, chiarezza e naturalezza; "
                    "evita premesse esitanti e vai dritto al punto. Fai domande di chiarimento solo "
                    "se necessarie. Non inventare fatti: distingui con chiarezza ciò che sai da ciò "
                    "che è un'ipotesi e dichiara l'incertezza solo quando è davvero rilevante. "
                    "Se l'utente allega una foto, rispondi alla sua domanda in una o due frasi brevi, "
                    "concentrandoti sui dettagli visibili più importanti."
                ),
            }, *conversation[-MAX_MESSAGES:]],
            "stream": True,
            "keep_alive": "30m",
            "options": {
                "num_predict": 64 if contains_image else 128,
                "num_ctx": 2048,
                "num_thread": 8,
                "num_batch": 256,
            },
        }
        request = Request(
            OLLAMA_CHAT_URL,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        stream_started = False
        try:
            with urlopen(request, timeout=240) as response:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-SparkGPT-Provider", "Ollama")
                self.send_header("X-SparkGPT-Model", request_body["model"])
                self.end_headers()
                stream_started = True
                try:
                    for line in response:
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            payload = json.dumps(
                                {"token": content}, ensure_ascii=False
                            ).encode("utf-8") + b"\n"
                            self.wfile.write(payload)
                            self.wfile.flush()
                        if chunk.get("done"):
                            self.wfile.write(b'{"done":true}\n')
                            self.wfile.flush()
                            break
                except (BrokenPipeError, ConnectionResetError):
                    print("Il client ha interrotto lo streaming della risposta.")
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    print(f"Risposta di streaming Ollama non valida: {error}")
                    self.wfile.write(json.dumps({
                        "error": "Ollama ha restituito un frammento di risposta non valido."
                    }).encode("utf-8") + b"\n")
                    self.wfile.flush()
                return
        except HTTPError as error:
            upstream_message = ""
            try:
                error_body = json.loads(error.read())
                upstream_error = error_body.get("error", "")
                if isinstance(upstream_error, dict):
                    upstream_message = upstream_error.get("message", "")
                elif isinstance(upstream_error, str):
                    upstream_message = upstream_error
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            if error.code == 404:
                selected_model = request_body["model"]
                message = f"Il modello locale {selected_model} non è installato. Avvia avvia_sparkgpt.bat per scaricarlo automaticamente."
            else:
                message = upstream_message or f"Ollama ha restituito l'errore {error.code}."
            self.send_json(502, {"error": message})
            return
        except (URLError, TimeoutError) as error:
            print(f"Errore di connessione Ollama: {error}")
            message = (
                "Non riesco a contattare Ollama sul PC. Controlla che Ollama sia aperto "
                "e in esecuzione; non servono account, chiavi API o credito."
            )
            if stream_started:
                try:
                    self.wfile.write(json.dumps({"error": message}).encode("utf-8") + b"\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    print("Il client ha interrotto lo streaming della risposta.")
            else:
                self.send_json(503, {"error": message})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(502, {"error": "Ollama ha restituito una risposta non valida."})
            return

    def log_message(self, format_string, *args):
        print(f"{self.log_date_time_string()} {self.address_string()} {format_string % args}")


if __name__ == "__main__":
    print(f"sparkgpt è disponibile su http://localhost:{PORT}")
    print(f"Modello AI locale: {MODEL}")
    ThreadingHTTPServer((HOST, PORT), SparkGPTHandler).serve_forever()
