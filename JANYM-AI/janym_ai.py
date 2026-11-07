import os
import re
import json
import time
import base64
import secrets
import threading
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from contextlib import asynccontextmanager
from difflib import SequenceMatcher

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ============================================================
# JARVIS AI 4.0 — Windows + Internet + voice + proactive chat
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_URL = os.getenv(
    "GROQ_URL",
    "https://api.groq.com/openai/v1/chat/completions"
).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CODEX_MODEL = os.getenv("OPENAI_CODEX_MODEL", "").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

async def ask_codex(task: str):
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Не задан OPENAI_API_KEY"
        )

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENAI_CODEX_MODEL,
                "input": task
            }
        )

        response.raise_for_status()
        data = response.json()

        return data.get("output_text", "Нет ответа от Codex")

HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_PORT", "9014"))

JANYM_USERNAME = os.getenv("JARVIS_USERNAME", "").strip()
JANYM_PASSWORD = os.getenv("JARVIS_PASSWORD", "")

MAX_REQUEST_BYTES = 256 * 1024
AUTH_FAILURE_LIMIT = 5
AUTH_FAILURE_WINDOW = 300
AUTH_LOCKOUT_SECONDS = 900
RATE_WINDOW_SECONDS = 60

security_lock = threading.Lock()
auth_failures = {}
rate_buckets = {}

# Never execute arbitrary shell commands from AI.
SAFE_APPS = {
    "калькулятор": ["calc.exe"],
    "calculator": ["calc.exe"],
    "блокнот": ["notepad.exe"],
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "паинт": ["mspaint.exe"],
    "пейнт": ["mspaint.exe"],
    "проводник": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "диспетчер задач": ["taskmgr.exe"],
    "task manager": ["taskmgr.exe"],
    "командная строка": ["cmd.exe"],
    "cmd": ["cmd.exe"],
}

ALLOWED_FILE_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "OneDrive" / "Documents",
]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".html", ".css", ".json",
    ".xml", ".ini", ".cfg", ".log", ".csv", ".yaml", ".yml"
}
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "AppData", "Windows", "Program Files", "Program Files (x86)"
}

def _trim_times(values, cutoff):
    return [x for x in values if x > cutoff]

def _client_ip(request: Request):
    # Do not trust X-Forwarded-For from arbitrary clients.
    return request.client.host if request.client else "unknown"

def _rate_limit(ip: str, path: str):
    now = time.monotonic()
    if path == "/api/chat":
        bucket, limit = "chat", 12
    elif path == "/api/proactive":
        bucket, limit = "proactive", 5
    else:
        bucket, limit = "general", 90

    key = (ip, bucket)
    with security_lock:
        for old_key, stamps in list(rate_buckets.items()):
            if not stamps or now - stamps[-1] > RATE_WINDOW_SECONDS * 2:
                rate_buckets.pop(old_key, None)
        stamps = _trim_times(rate_buckets.get(key, []), now - RATE_WINDOW_SECONDS)
        if len(stamps) >= limit:
            rate_buckets[key] = stamps
            return max(1, int(RATE_WINDOW_SECONDS - (now - stamps[0])))
        stamps.append(now)
        rate_buckets[key] = stamps
    return 0

def _record_auth_failure(ip: str):
    now = time.monotonic()
    with security_lock:
        state = auth_failures.setdefault(
            ip, {"times": [], "blocked_until": 0}
        )
        state["times"] = _trim_times(
            state["times"], now - AUTH_FAILURE_WINDOW
        )
        state["times"].append(now)
        if len(state["times"]) >= AUTH_FAILURE_LIMIT:
            state["blocked_until"] = now + AUTH_LOCKOUT_SECONDS
            state["times"] = []

def _is_blocked(ip: str):
    with security_lock:
        state = auth_failures.get(ip)
        return bool(state and state.get("blocked_until", 0) > time.monotonic())

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"JANYM AI 4.0: http://{HOST}:{PORT}")
    yield
    print("JANYM stopped.")

app = FastAPI(
    title="JANYM AI 4.0",
    version="4.0",
    lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    ip = _client_ip(request)

    def secure(response):
        # Защитные HTTP-заголовки
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(self), microphone=(self), geolocation=()"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https:; "
            "media-src 'self' blob:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )

        # Удаляем заголовок Server безопасным способом
        if "Server" in response.headers:
            del response.headers["Server"]

        return response

    if not JANYM_USERNAME or len(JANYM_PASSWORD) < 16:
        return secure(JSONResponse(
            {"detail": "Настрой .env: JARVIS_USERNAME и JARVIS_PASSWORD (минимум 16 символов)."},
            status_code=503
        ))

    if _is_blocked(ip):
        return secure(JSONResponse(
            {"detail": "Слишком много неверных паролей. Попробуй позже."},
            status_code=429,
            headers={"Retry-After": str(AUTH_LOCKOUT_SECONDS)}
        ))

    auth = request.headers.get("authorization", "")
    valid = False
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            valid = (
                secrets.compare_digest(username, JANYM_USERNAME )
                and secrets.compare_digest(password, JANYM_PASSWORD)
            )
        except (ValueError, UnicodeDecodeError):
            valid = False

    if not valid:
        _record_auth_failure(ip)
        return secure(PlainTextResponse(
            "Требуется вход в JANYM.",
            status_code=401,
            headers={
                "WWW-Authenticate": 'Basic realm="JANYM AI", charset="UTF-8"',
                "Cache-Control": "no-store"
            }
        ))

    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and origin:
        from urllib.parse import urlsplit
        origin_host = (urlsplit(origin).netloc or "").casefold()
        request_host = request.headers.get("host", "").casefold()
        if not origin_host or origin_host != request_host:
            return secure(JSONResponse(
                {"detail": "Запрос с другого сайта заблокирован."},
                status_code=403
            ))

    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_REQUEST_BYTES:
                return secure(JSONResponse(
                    {"detail": "Слишком большой запрос."}, status_code=413
                ))
        except ValueError:
            return secure(JSONResponse(
                {"detail": "Некорректный Content-Length."}, status_code=400
            ))

    wait = _rate_limit(ip, request.url.path)
    if wait:
        return secure(JSONResponse(
            {"detail": "Слишком много запросов. Подожди немного."},
            status_code=429,
            headers={"Retry-After": str(wait)}
        ))

    try:
        return secure(await call_next(request))
    except Exception:
        return secure(JSONResponse(
            {"detail": "Внутренняя ошибка сервера."}, status_code=500
        ))

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)

def clean_ai_text(value: str) -> str:
    """Convert common Markdown formatting into plain readable text."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```", r"\1", value)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(\d+)\.\s+", r"\1) ", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)[*_~](.*?)[*_~](?!\w)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _inside_allowed_root(path: Path):
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in ALLOWED_FILE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False

def search_local_files(query: str, limit: int = 10):
    """Search filenames in Desktop/Documents/Downloads, not the whole disk."""
    query = re.sub(r"\s+", " ", query).strip().casefold()
    if not query:
        return {"matches": []}

    matches = []
    visited = 0
    for root in ALLOWED_FILE_ROOTS:
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for filename in files:
                visited += 1
                if visited > 30000:
                    break
                path = Path(current) / filename
                name = path.stem.casefold()
                full = filename.casefold()
                score = 1.0 if query == name else (
                    0.9 if query in full else
                    SequenceMatcher(None, query, name).ratio()
                )
                if score >= 0.48:
                    try:
                        matches.append({
                            "name": filename,
                            "path": str(path),
                            "type": path.suffix.lower(),
                            "score": round(score, 2)
                        })
                    except OSError:
                        pass
            if visited > 30000:
                break

    matches.sort(key=lambda x: (-x["score"], x["name"].casefold()))
    return {"matches": matches[:limit]}

def read_local_text_file(path_text: str):
    path = Path(path_text)
    if not _inside_allowed_root(path):
        raise ValueError("Разрешено читать только файлы в Desktop, Documents и Downloads.")
    if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError("Этот тип файла нельзя безопасно прочитать как текст.")
    if path.stat().st_size > 300_000:
        raise ValueError("Файл слишком большой для чтения.")
    return path.read_text(encoding="utf-8", errors="replace")[:20000]

def discover_app_shortcuts():
    """Find Start Menu/Desktop shortcuts; only launch a selected shortcut."""
    folders = [
        Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.getenv("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop"
    ]
    items = []
    for folder in folders:
        if not folder.is_dir():
            continue
        try:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".lnk", ".url"):
                    label = path.stem
                    if any(x in label.casefold() for x in ("uninstall", "удалить", "readme")):
                        continue
                    items.append({"label": label, "path": str(path)})
                    if len(items) >= 5000:
                        return items
        except OSError:
            continue
    return items

def search_apps(query: str, limit: int = 8):
    query = query.strip().casefold()
    if not query:
        return []
    results = []
    for item in discover_app_shortcuts():
        label = item["label"].casefold()
        score = 1.0 if label == query else (
            0.9 if label.startswith(query) else
            0.8 if query in label else
            SequenceMatcher(None, query, label).ratio()
        )
        if score >= 0.55:
            results.append({**item, "score": round(score, 2)})
    results.sort(key=lambda x: (-x["score"], x["label"].casefold()))
    return results[:limit]

def open_app(app_name: str):
    name = app_name.strip().casefold()
    if name in SAFE_APPS:
        subprocess.Popen(SAFE_APPS[name], shell=False)
        return f"Запускаю {app_name}."

    matches = search_apps(app_name, limit=5)
    if not matches:
        return "Не нашёл такую программу в меню Пуск или на рабочем столе."
    if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 0.12:
        names = ", ".join(x["label"] for x in matches[:5])
        return f"Нашёл несколько похожих программ: {names}. Уточни название."

    selected = matches[0]
    if os.name != "nt":
        raise ValueError("Запуск ярлыков доступен только в Windows.")
    os.startfile(selected["path"])
    return f"Открываю {selected['label']}."

def open_website(target: str):
    target = target.strip()
    if not target:
        raise ValueError("Не указано название сайта.")
    if re.match(r"^https?://", target, re.I):
        url = target
    elif re.match(r"^www\.", target, re.I):
        url = "https://" + target
    elif re.match(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/.*)?$", target, re.I):
        url = "https://" + target
    else:
        # Search by site/topic rather than interpreting natural language as a command.
        url = "https://www.google.com/search?q=" + quote_plus(target)
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Недопустимый адрес сайта.")
    webbrowser.open(url, new=2)
    return f"Открываю в браузере: {url}"

async def search_web(query: str, limit: int = 6):
    """Retrieve public web search snippets using DuckDuckGo HTML."""
    query = re.sub(r"\s+", " ", query).strip()[:180]
    if not query:
        return {"results": []}
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()

    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items = []
            self.current = None
            self.in_title = False
            self.in_snippet = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            classes = attrs.get("class", "").split()
            if tag == "a" and "result__a" in classes:
                self.current = {
                    "title": "", "url": attrs.get("href", ""), "snippet": ""
                }
                self.in_title = True
            elif self.current is not None and "result__snippet" in classes:
                self.in_snippet = True

        def handle_data(self, data):
            if self.current is None:
                return
            value = re.sub(r"\s+", " ", data).strip()
            if not value:
                return
            if self.in_title:
                self.current["title"] += (" " if self.current["title"] else "") + value
            elif self.in_snippet:
                self.current["snippet"] += (" " if self.current["snippet"] else "") + value

        def handle_endtag(self, tag):
            if tag == "a" and self.in_title and self.current:
                self.in_title = False
                self.items.append(self.current)
                self.current = None
            if tag in ("div", "span"):
                self.in_snippet = False

    parser = Parser()
    parser.feed(response.text)
    results = []
    for item in parser.items:
        link = item["url"].strip()
        host = (urlparse(link).hostname or "").lower()
        if not link.startswith(("http://", "https://")):
            continue
        if not host or "duckduckgo.com" in host:
            continue
        results.append({
            "title": item["title"][:180],
            "url": link,
            "snippet": item["snippet"][:350]
        })
        if len(results) >= limit:
            break
    return {"results": results}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Найти актуальную информацию в открытом интернете. Используй для вопросов о фактах, новостях, сайтах и информации.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Найти файлы по имени в папках Рабочий стол, Документы и Загрузки пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название или часть названия файла"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "Прочитать найденный текстовый файл из разрешённых пользовательских папок. Сначала найди файл через search_files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Полный путь из результата поиска"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_apps",
            "description": "Найти установленную программу по ярлыкам меню Пуск и рабочего стола.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название программы"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Открыть программу Windows по названию. Не запускай неизвестные команды и не подбирай похожее приложение при неоднозначности.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Название программы"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Открыть сайт или поиск в браузере по названию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Адрес сайта или название"}
                },
                "required": ["target"]
            }
        }
    }
]

SYSTEM_PROMPT = """
Ты JANYM — голосовой ИИ-помощник на компьютере пользователя Windows.
Отвечай на русском, если пользователь пишет по-русски.
Пиши простым обычным текстом без Markdown, заголовков с решётками,
звёздочек, таблиц и обратных кавычек. Будь дружелюбным и естественным.

Отвечай на вопросы только по законодательству
"""

async def execute_tool(name: str, arguments: dict):
    if name == "search_web":
        return await search_web(str(arguments.get("query", "")))
    if name == "search_files":
        return search_local_files(str(arguments.get("query", "")))
    if name == "read_text_file":
        try:
            return {"content": read_local_text_file(str(arguments.get("path", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    if name == "search_apps":
        return {"matches": search_apps(str(arguments.get("query", "")))}
    if name == "open_app":
        try:
            return {"result": open_app(str(arguments.get("name", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    if name == "open_website":
        try:
            return {"result": open_website(str(arguments.get("target", "")))}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
    return {"error": "Неизвестный инструмент."}

async def ask_groq(user_message: str, history: list):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY не задан в .env")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-12:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content[:6000]})
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(4):
                response = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "temperature": 0.4,
                        "max_tokens": 1400
                    }
                )
                if response.status_code >= 400:
                    try:
                        err = response.json().get("error", {}).get("message")
                    except Exception:
                        err = None
                    raise HTTPException(
                        status_code=502,
                        detail=(err or "Ошибка Groq API.")[:800]
                    )

                result = response.json()
                message = result["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []

                if not tool_calls:
                    return clean_ai_text(message.get("content") or "")

                messages.append(message)
                for call in tool_calls:
                    function = call.get("function", {})
                    tool_name = function.get("name", "")
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_result = await execute_tool(tool_name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(
                            tool_result, ensure_ascii=False
                        )[:12000]
                    })

            return "Я выполнил доступные поисковые действия, но не смог завершить ответ. Попробуй уточнить запрос."

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Groq не ответил вовремя.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Нет подключения к Groq API.")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Не удалось обработать ответ Groq API.")

@app.post("/api/chat")
async def chat(data: ChatRequest):
    reply = await ask_groq(data.message.strip(), data.history)
    return {"reply": reply or "Я здесь. Чем могу помочь?"}

@app.post("/api/proactive")
async def proactive():
    prompt = (
        "Начни короткий естественный разговор с пользователем. "
        "Скажи что-нибудь уместное: дружелюбное приветствие, интересный факт "
        "или предложение помочь с компьютером. Не повторяй одинаковые фразы. "
        "Не утверждай, что знаешь, чем пользователь занят. Один-два предложения."
    )
    reply = await ask_groq(prompt, [])
    return {"reply": reply or "Я здесь, если понадоблюсь."}

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JANYM AI 4.0</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#030814;color:#e9f8ff;font:16px Segoe UI,Arial;
background-image:linear-gradient(rgba(0,5,20,.55),rgba(0,5,20,.75)),url("/static/ai-scaled.jpg");
background-size:cover;background-position:center;background-attachment:fixed}
.wrap{max-width:1000px;margin:auto;padding:22px}
.top,.panel{border:1px solid #26bfff66;background:rgba(3,14,32,.9);border-radius:16px;padding:18px;box-shadow:0 0 28px #0078ff16}
.top{display:flex;flex-direction:column;gap:6px}
.brand{font-size:34px;font-weight:900;letter-spacing:7px;color:#7be3ff;text-shadow:0 0 20px #078dff}
.muted{color:#9fc9e8;font-size:13px}
.panel{margin-top:16px}
.messages{height:55vh;min-height:330px;max-height:650px;overflow:auto;display:flex;flex-direction:column;gap:10px;margin:12px 0}
.bubble{padding:12px 14px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:95%;line-height:1.5}
.bot{background:#0a2440;border:1px solid #28aaff44;align-self:flex-start}
.user{background:#075a83aa;align-self:flex-end}
.composer{display:flex;flex-direction:column;gap:9px}
textarea{width:100%;min-height:85px;resize:vertical;background:#020a17;color:white;border:1px solid #2b9fcf88;border-radius:10px;padding:12px;font:inherit}
button{border:1px solid #39cfff66;border-radius:10px;padding:11px 14px;background:linear-gradient(135deg,#00a9d8,#1264ff);color:white;font-weight:700;cursor:pointer}
button.secondary{background:#0a2440}
button:disabled{opacity:.5;cursor:wait}
.tools{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}
select,input[type=range]{background:#061020;color:white;border:1px solid #39cfff66;border-radius:10px;padding:8px}
#voiceState{color:#5fffd0;margin-top:10px}
.status{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
@media(max-width:600px){.wrap{padding:12px}.brand{font-size:28px}.messages{height:48vh}}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="brand">JANYM</div>
  <div class="muted">AI ASSISTANT · WINDOWS · INTERNET · VOICE</div>
  <div class="status muted"><span>JARVIS AI 4.0</span><span>Локальный компьютер · порт 9014</span></div>
</header>

<main class="panel">
  <b id="modeTitle">Чат JAMYM</b>
  <div class="messages" id="messages">
    <div class="bubble bot">Привет! Я JANYM. Могу искать информацию в интернете, находить файлы и программы на компьютере и открывать сайты. Напиши или произнеси команду.</div>
  </div>
  <form id="form" class="composer">
    <textarea id="message" placeholder="Напиши вопрос или команду..." required maxlength="6000"></textarea>
    <button id="send" type="submit">➤ Отправить</button>
  </form>
  <div class="tools">
    <button class="secondary" id="speakToggle" type="button">🔊 Голос: ВКЛ</button>
    <button class="secondary" id="proactiveToggle" type="button">🤖 Самостоятельная речь: ВКЛ</button>
  </div>
  <div class="tools">
    <label class="muted">Голос</label><select id="voiceSelect"></select>
    <label class="muted">Скорость</label><input id="rate" type="range" min=".7" max="1.3" step=".05" value=".95">
    <label class="muted">Тон</label><input id="pitch" type="range" min=".5" max="1.5" step=".1" value=".9">
  </div>
  <div id="voiceState" class="muted">
  Скажи «Джарвис, включись», чтобы начать общение.
</div>
</main>
<section class="panel">
  <b>Умная камера JARVIS</b>
  <p class="muted">Камера включается только после нажатия кнопки. Распознавание объектов требует ultralytics.</p>
  <div class="tools">
    <button id="cameraStart" type="button">📷 Включить камеру</button>
    <button id="cameraStop" class="secondary" type="button">Остановить</button>
  </div>
  <img id="camera" alt="Поток камеры" style="display:none;width:100%;max-height:420px;object-fit:contain;border-radius:12px;margin-top:12px;background:#020711">
  <div id="cameraStatus" class="muted" style="margin-top:10px">Камера выключена.</div>
</section>
<p class="muted" style="text-align:center;margin-top:14px">
  JARVIS обращается к Groq для ответов. Голос и самостоятельные сообщения работают, пока эта страница открыта.
</p>
</div>

<script>
const $=id=>document.getElementById(id);
const messages=$('messages'), input=$('message');
let history=[], voices=[], recognition=null, speakEnabled=true;
let autonomousSpeech=true, proactiveTimer=null, lastProactive=0;
let listening=false, sending=false;

function bubble(text, kind='bot'){
  const el=document.createElement('div');
  el.className='bubble '+kind;
  el.textContent=text;
  messages.appendChild(el);
  messages.scrollTop=messages.scrollHeight;
  return el;
}
function loadVoices(){
  if(!('speechSynthesis' in window))return;
  const old=$('voiceSelect').value;
  voices=speechSynthesis.getVoices();
  const ru=voices.filter(v=>v.lang.toLowerCase().startsWith('ru'));
  const ordered=[...ru,...voices.filter(v=>!ru.includes(v))];
  $('voiceSelect').replaceChildren();
  ordered.forEach(v=>{
    const option=document.createElement('option');
    option.value=v.name;
    option.textContent=v.name+' ('+v.lang+')';
    $('voiceSelect').appendChild(option);
  });
  if(old)$('voiceSelect').value=old;
}
if('speechSynthesis' in window){
  loadVoices();
  speechSynthesis.addEventListener('voiceschanged',loadVoices);
}
function plainText(text){
  return String(text||'')
    .replace(/```[\s\S]*?```/g,'')
    .replace(/[*#_`~]/g,'')
    .trim();
}
function say(text){
  if(!speakEnabled||!('speechSynthesis' in window))return;
  speechSynthesis.cancel();
  const utterance=new SpeechSynthesisUtterance(plainText(text));
  const voice=voices.find(v=>v.name===$('voiceSelect').value);
  utterance.lang='ru-RU';
  utterance.rate=Number($('rate').value);
  utterance.pitch=Number($('pitch').value);
  if(voice){utterance.voice=voice;utterance.lang=voice.lang;}
  speechSynthesis.speak(utterance);
}
async function sendMessage(value){
  const message=String(value??input.value).trim();
  if(!message||sending)return;
  sending=true;
  bubble(message,'user');
  input.value='';
  $('send').disabled=true;
  try{
    const wait=bubble('Думаю…');
    const response=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message,history:history.slice(-12)})
    });
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||'Ошибка запроса');
    const reply=plainText(data.reply||'Я здесь.');
    wait.textContent=reply;
    history.push({role:'user',content:message});
    history.push({role:'assistant',content:reply});
    history=history.slice(-20);
    say(reply);
    lastProactive=Date.now();
  }catch(error){
    bubble('Ошибка: '+error.message);
  }finally{
    sending=false;
    $('send').disabled=false;
    input.focus();
  }
}
$('form').addEventListener('submit',event=>{
  event.preventDefault();
  sendMessage(input.value);
});
input.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey){
    event.preventDefault();
    $('form').requestSubmit();
  }
});

const SpeechRecognition =
    window.SpeechRecognition ||
    window.webkitSpeechRecognition;

if (SpeechRecognition) {
    recognition = new SpeechRecognition();

    recognition.lang = "ru-RU";
    recognition.continuous = true;
    recognition.interimResults = false;

    recognition.onresult = function(event) {
        const result =
            event.results[event.results.length - 1];

        if (result.isFinal) {
            const text = result[0].transcript;
            handleVoiceCommand(text);
        }
    };

    recognition.onend = function() {
        setTimeout(() => {
            try {
                recognition.start();
            } catch (error) {}
        }, 1000);
    };
}


let cameraPoll=null;
$('cameraStart').onclick=async()=>{
  try{
    const response=await fetch('/api/camera/start',{method:'POST'});
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||'Камера недоступна');
    $('camera').src='/api/camera/stream?ts='+Date.now();
    $('camera').style.display='block';
    $('cameraStatus').textContent=data.message;
    if(cameraPoll)clearInterval(cameraPoll);
    cameraPoll=setInterval(async()=>{
      try{
        const r=await fetch('/api/camera/status');
        const d=await r.json();
        if(!d.active)return;
        const objects=Object.entries(d.objects||{}).map(([k,v])=>k+': '+v).join(', ');
        $('cameraStatus').textContent=d.vision_available
          ?('Людей: '+d.people+' · '+(objects||'Объекты не обнаружены'))
          :'Видео работает. Для распознавания установи ultralytics.';
        if(d.error)$('cameraStatus').textContent+=' · '+d.error;
      }catch(e){}
    },1500);
  }catch(error){$('cameraStatus').textContent=error.message;}
};
$('cameraStop').onclick=async()=>{
  if(cameraPoll)clearInterval(cameraPoll);
  try{await fetch('/api/camera/stop',{method:'POST'});}catch(e){}
  $('camera').src='';
  $('camera').style.display='none';
  $('cameraStatus').textContent='Камера выключена.';
};
let jarvisActive = false;

function normalizeVoice(text) {
    return text.toLowerCase()
        .replace(/ё/g, "е")
        .replace(/[.,!?]/g, "")
        .trim();
}

function handleVoiceCommand(text) {
    const command = normalizeVoice(text);

    if (
        command.includes("джарвис включись") ||
        command.includes("джарвис проснись")
    ) {
        jarvisActive = true;
        speak("Я слушаю. Чем помочь?");
        return;
    }

    if (
        command.includes("джаным выключись") ||
        command.includes("джаным усни")
    ) {
        jarvisActive = false;
        speak("Перехожу в режим ожидания.");
        return;
    }

    if (jarvisActive) {
        sendMessage(text);
    }
}
</script>
</body>
</html>""")


# Optional camera module. Install with:
# pip install opencv-python ultralytics
try:
    import cv2
except ImportError:
    cv2 = None
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

_camera = None
_camera_model = None
_camera_lock = threading.Lock()
_camera_stats = {"people": 0, "objects": {}, "error": ""}

@app.post("/api/camera/start")
def camera_start():
    global _camera
    if cv2 is None:
        raise HTTPException(
            status_code=503,
            detail="OpenCV не установлен. Выполни: pip install opencv-python"
        )
    with _camera_lock:
        if _camera is None or not _camera.isOpened():
            _camera = cv2.VideoCapture(
                0, cv2.CAP_DSHOW if os.name == "nt" else 0
            )
        if not _camera.isOpened():
            _camera.release()
            _camera = None
            raise HTTPException(
                status_code=503,
                detail="Камера не найдена или занята другим приложением."
            )
    if YOLO is None:
        return {
            "ok": True,
            "message": "Камера включена. Для распознавания установи ultralytics."
        }
    return {"ok": True, "message": "Камера и распознавание объектов включены."}

@app.post("/api/camera/stop")
def camera_stop():
    global _camera
    with _camera_lock:
        if _camera is not None:
            _camera.release()
            _camera = None
        _camera_stats.update({"people": 0, "objects": {}, "error": ""})
    return {"ok": True, "message": "Камера выключена."}

@app.get("/api/camera/status")
def camera_status():
    active = bool(cv2 is not None and _camera is not None and _camera.isOpened())
    return {
        "active": active,
        "vision_available": YOLO is not None,
        "people": _camera_stats["people"],
        "objects": _camera_stats["objects"],
        "error": _camera_stats["error"]
    }

def _camera_frames():
    global _camera_model
    while True:
        with _camera_lock:
            if _camera is None or not _camera.isOpened():
                break
            ok, frame = _camera.read()
            if not ok:
                break
            if YOLO is not None:
                try:
                    if _camera_model is None:
                        _camera_model = YOLO("yolov8n.pt")
                    result = _camera_model.predict(
                        frame, verbose=False, conf=0.45
                    )[0]
                    counts = {}
                    people = 0
                    for box in result.boxes:
                        class_id = int(box.cls[0])
                        label = result.names.get(class_id, str(class_id))
                        counts[label] = counts.get(label, 0) + 1
                        if class_id == 0:
                            people += 1
                    _camera_stats.update({
                        "people": people,
                        "objects": counts,
                        "error": ""
                    })
                    frame = result.plot()
                except Exception as exc:
                    _camera_stats["error"] = str(exc)[:160]
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            data = encoded.tobytes()
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"

@app.get("/api/camera/stream")
def camera_stream():
    if cv2 is None:
        raise HTTPException(status_code=503, detail="OpenCV не установлен.")
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        _camera_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("JANYM AI 4.0")
    print(f"http://{HOST}:{PORT}")
    print("=" * 55)
    uvicorn.run(app, host=HOST, port=PORT, workers=1)
