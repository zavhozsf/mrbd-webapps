"""TG bridge: личный аккаунт Telegram (Telethon) -> JSON API для веб-аппов очков MRBD.

Режимы:
  python tg_bridge.py login   — интерактивный вход (номер, код, 2FA), сохраняет tg.session
  python tg_bridge.py serve   — HTTP API на :8788 (CORS *): /api/state /api/chats /api/messages /api/send /api/events
api_id/api_hash: bridge/config.json {"api_id":123,"api_hash":"..."} или env TG_API_ID/TG_API_HASH
"""
import asyncio, json, os, sys, time, collections, html

from aiohttp import web
from telethon import TelegramClient, events, functions, types

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("TG_BRIDGE_PORT", "8788"))
EVENTS_MAX = 100


def load_cfg():
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


CFG = load_cfg()
API_ID = int(os.getenv("TG_API_ID") or CFG.get("api_id") or 0)
API_HASH = os.getenv("TG_API_HASH") or CFG.get("api_hash") or ""
client = TelegramClient(os.path.join(HERE, "tg.session"), API_ID, API_HASH)

evbuf = collections.deque(maxlen=EVENTS_MAX)
dialogs_cache = {}  # peer id -> entity


def media_stub(msg):
    if msg.photo:
        return "[фото]"
    if msg.video:
        return "[видео]"
    if msg.voice:
        return "[голосовое]"
    if msg.document:
        name = getattr(msg.document.file, "name", "") or getattr(msg.file, "name", "") or ""
        return f"[файл {name}]".strip()
    if msg.sticker:
        return "[стикер]"
    if msg.game:
        return "[игра]"
    if msg.poll:
        return f"[опрос: {msg.poll.question}]" if msg.poll else "[опрос]"
    if msg.action:
        return "[действие]"
    return "[без текста]"


def msg_text(msg):
    return (msg.raw_text or "").strip() or media_stub(msg)


def summary(entity):
    s = getattr(entity, "string", None)
    if s is None:
        t = getattr(entity, "title", None)
        f = " ".join(x for x in [getattr(entity, "first_name", ""), getattr(entity, "last_name", "")] if x)
        s = t or f or str(entity.id)
    return html.escape(s or "?", quote=False)


@client.on(events.NewMessage(incoming=True))
async def _incoming(ev):
    ent = await ev.get_chat()
    evbuf.append({
        "ts": int(ev.date.timestamp()),
        "chat_id": str(ent.id),
        "from_me": False,
        "name": summary(ent),
        "text": html.escape(msg_text(ev.message), quote=False),
    })


@client.on(events.MessageEdited(incoming=True))
async def _edited(ev):
    await _incoming(ev)


# ---------- HTTP ----------

@web.middleware
async def cors_mw(req, handler):
    if req.method == "OPTIONS":
        return web.Response(status=204)
    resp = await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "content-type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


async def refresh_dialogs():
    global dialogs_cache
    ds = await client.get_dialogs(40)
    fresh = {}
    out = []
    for d in ds:
        fresh[d.id] = d.entity
        last = summary(d.entity)
        if d.message:
            last = f'{summary(d.entity)}: {msg_text(d.message)[:70]}'
        out.append({
            "id": str(d.id),
            "title": summary(d.entity),
            "unread": d.unread_count,
            "last": html.escape(last, quote=False),
            "ts": int(d.message.date.timestamp()) if d.message else 0,
        })
    dialogs_cache = fresh
    return out


async def resolve(cid):
    cid = int(cid)
    if cid in dialogs_cache:
        return dialogs_cache[cid]
    ent = await client.get_entity(cid)
    dialogs_cache[cid] = ent
    return ent


async def api_state(req):
    ok = False
    me = None
    try:
        ok = await client.is_user_authorized()
        if ok:
            m = await client.get_me()
            me = summary(m)
    except Exception as e:
        return web.json_response({"authed": False, "err": str(e)})
    return web.json_response({"authed": ok, "me": me, "ts": int(time.time())})


async def api_chats(req):
    return web.json_response({"chats": await refresh_dialogs()})


async def api_messages(req):
    cid = req.query.get("chat")
    limit = min(int(req.query.get("limit", "30")), 50)
    msgs = await client.get_messages(await resolve(cid), limit=limit)
    out = []
    for m in reversed(msgs):
        out.append({
            "id": m.id,
            "out": bool(m.out),
            "name": "я" if m.out else (summary(await m.get_sender()) if m.sender_id else ""),
            "text": html.escape(msg_text(m), quote=False),
            "ts": int(m.date.timestamp()),
        })
    return web.json_response({"messages": out})


async def api_send(req):
    data = await req.json()
    await client.send_message(await resolve(data["chat"]), data["text"])
    return web.json_response({"ok": True})


async def api_read(req):
    # пометить чат прочитанным
    data = await req.json()
    ent = await resolve(data["chat"])
    try:
        await client(functions.channels.ReadHistoryRequest(channel=ent, max_id=0))
    except Exception:
        try:
            await client(functions.messages.ReadHistoryRequest(peer=ent))
        except Exception as e:
            return web.json_response({"ok": False, "err": str(e)})
    return web.json_response({"ok": True})


async def api_events(req):
    since = int(req.query.get("since", "0"))
    return web.json_response({"events": [e for e in evbuf if e["ts"] > since], "now": int(time.time())})


def make_app():
    app = web.Application(middlewares=[cors_mw])
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/chats", api_chats)
    app.router.add_get("/api/messages", api_messages)
    app.router.add_post("/api/send", api_send)
    app.router.add_post("/api/read", api_read)
    app.router.add_get("/api/events", api_events)
    return app


async def login():
    if not API_ID or not API_HASH:
        sys.exit("нет api_id/api_hash — создай bridge/config.json (my.telegram.org)")
    await client.start(phone=lambda: input("Телефон (с кодом страны): "))
    print("ОК, сессия сохранена:", SESSION if (SESSION := os.path.join(HERE, "tg.session")) else "")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if mode == "login":
        asyncio.run(login())
        return
    if not API_ID or not API_HASH:
        print("ВНИМАНИЕ: нет api_id/api_hash — API будет отвечать authed:false")

    async def run():
        await client.connect()
        if await client.is_user_authorized():
            print("Сессия загружена:", (await client.get_me()).string)
        else:
            print("Сессия не авторизована — запусти: python tg_bridge.py login")
        await web._run_app(make_app(), host="0.0.0.0", port=PORT, print=lambda *a: print(*a))
        await asyncio.sleep(3600 * 100000)

    asyncio.run(run())


if __name__ == "__main__":
    main()
