"""AI helpers for the JARVIS server: free-provider fallback chain, photo edits, image generation/search, text files.

All keys come from environment variables on Render (never from code):
  GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY   (any subset; missing ones are skipped)
  GROQ_MODEL, GROQ_VISION_MODEL, GEMINI_MODEL, OPENROUTER_MODEL, OPENROUTER_VISION_MODEL, AI_PROVIDER_ORDER
  IMAGE_GEN_URL   optional template with {prompt} {w} {h} {seed}  (default: Pollinations, no key)
"""
import io
import json
import os
import random
import re
import urllib.parse
import urllib.request

from openai import OpenAI

TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".css", ".js", ".xml", ".yaml", ".yml", ".ini", ".kv")


# ------------------------------------------------------------------ providers
def provider_chain(vision=False):
    """Providers that have a key, in order of preference: [(name, key, base_url, model), ...]."""
    env = os.environ.get
    available = {}
    key = env("GROQ_API_KEY", "").strip()
    if key:
        model = env("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct") if vision else env("GROQ_MODEL", "openai/gpt-oss-120b")
        available["groq"] = ("groq", key, "https://api.groq.com/openai/v1", model)
    key = env("GEMINI_API_KEY", "").strip()
    if key:
        available["gemini"] = ("gemini", key, "https://generativelanguage.googleapis.com/v1beta/openai/", env("GEMINI_MODEL", "gemini-2.5-flash"))
    key = env("OPENROUTER_API_KEY", "").strip()
    if key:
        model = env("OPENROUTER_VISION_MODEL", "").strip() if vision else env("OPENROUTER_MODEL", "openrouter/free")
        if model:
            available["openrouter"] = ("openrouter", key, "https://openrouter.ai/api/v1", model)
    order = [p.strip() for p in env("AI_PROVIDER_ORDER", "groq,gemini,openrouter").split(",") if p.strip()]
    chain = [available[name] for name in order if name in available]
    chain += [value for name, value in available.items() if name not in order]
    return chain


class NoProvider(Exception):
    pass


def complete(messages, vision=False, max_tokens=None, timeout=45.0):
    """Ask the first working provider. Returns {"answer", "model", "provider"}. Raises NoProvider / RuntimeError."""
    chain = provider_chain(vision)
    if not chain:
        raise NoProvider("No AI provider key is configured (set GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY)")
    failures = []
    for name, key, base, model in chain:
        try:
            client = OpenAI(api_key=key, base_url=base, timeout=timeout)
            extra = {"max_tokens": max_tokens} if max_tokens else {}
            response = client.chat.completions.create(model=model, messages=messages, **extra)
            answer = response.choices[0].message.content if response.choices else ""
            if answer and answer.strip():
                return {"answer": answer.strip(), "model": (response.model or model), "provider": name}
            failures.append(name + ": empty")
        except Exception as exc:
            print("AI provider failed:", name, type(exc).__name__, str(exc)[:150])
            failures.append(name + ": " + type(exc).__name__)
    raise RuntimeError("All AI providers failed (" + "; ".join(failures) + ")")


# ------------------------------------------------------------------ prompts / translation
def to_english(text, language):
    """Translate a short request to English for image tools (Tajik/Russian are poorly understood by them)."""
    if language == "en" or not re.search(r"[^\x00-\x7f]", text):
        return text
    try:
        result = complete([
            {"role": "system", "content": "Translate the user's text to English. Output only the translation, nothing else."},
            {"role": "user", "content": text},
        ], max_tokens=200, timeout=30.0)
        return result["answer"].strip().strip('"')[:500] or text
    except Exception:
        return text


# ------------------------------------------------------------------ image generation
def generate_image(prompt_en, width=1024, height=1024):
    """Fetch an image for the prompt from a free keyless service. Returns (bytes, content_type)."""
    template = os.environ.get("IMAGE_GEN_URL", "").strip() or \
        "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true&seed={seed}"
    url = template.format(prompt=urllib.parse.quote(prompt_en[:500]), w=width, h=height, seed=random.randint(1, 10 ** 6))
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-server/1.0"})
    with urllib.request.urlopen(req, timeout=90) as response:
        ctype = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        data = response.read(12 * 1024 * 1024)
    if not ctype.startswith("image/") or len(data) < 1000:
        raise RuntimeError("the image service returned no image")
    return data, ctype


# ------------------------------------------------------------------ image search (Wikimedia Commons, no key)
def search_images(query_en, limit=6):
    params = urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrnamespace": "6", "gsrsearch": query_en + " filetype:bitmap",
        "gsrlimit": str(limit + 4), "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": "640", "format": "json"})
    req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + params,
                                 headers={"User-Agent": "JARVIS-server/1.0 (personal assistant)"})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    results = []
    for page in sorted((data.get("query", {}).get("pages", {})).values(), key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        thumb = info.get("thumburl") or info.get("url")
        if not thumb or str(info.get("mime", "")) not in ("image/jpeg", "image/png", "image/webp"):
            continue
        meta = info.get("extmetadata") or {}
        license_name = (meta.get("LicenseShortName") or {}).get("value", "")
        results.append({"title": str(page.get("title", "")).replace("File:", "")[:120], "thumb": thumb,
                        "page": info.get("descriptionurl", ""), "license": str(license_name)[:40]})
        if len(results) >= limit:
            break
    return results


# ------------------------------------------------------------------ photo edits: request -> operations -> Pillow
OPS_HELP = """Allowed operations (JSON objects in a list):
{"op":"rotate","degrees":90}  {"op":"flip_horizontal"}  {"op":"flip_vertical"}
{"op":"grayscale"}  {"op":"sepia"}  {"op":"invert"}  {"op":"autocontrast"}
{"op":"brightness","factor":1.3}  {"op":"contrast","factor":1.2}  {"op":"saturation","factor":1.4}  {"op":"sharpness","factor":2.0}
{"op":"blur","radius":4}
{"op":"crop","left":0.1,"top":0.1,"right":0.9,"bottom":0.9}   (fractions 0..1 of the image)
{"op":"square_crop"}
{"op":"resize","scale":0.5}  or  {"op":"resize","max_side":1280}
factor 1.0 = unchanged; <1 darker/weaker, >1 brighter/stronger."""


def plan_edit(request_text, language):
    """Turn the user's wish into a validated list of operations using the AI chain."""
    system = ("You convert a photo-edit request into JSON for a simple image editor. " + OPS_HELP +
              "\nReply with ONLY JSON: {\"ops\": [...], \"summary\": \"one short sentence in " +
              {"tg": "Tajik (Cyrillic)", "ru": "Russian", "en": "English"}[language] + " describing what was done\"}."
              " If the request cannot be done with these operations (for example removing the background, adding objects, "
              "changing faces), reply {\"ops\": [], \"summary\": \"short explanation in the same language why it is not possible\"}.")
    result = complete([{"role": "system", "content": system}, {"role": "user", "content": request_text[:1000]}], max_tokens=500, timeout=40.0)
    match = re.search(r"\{.*\}", result["answer"], re.S)
    if not match:
        raise RuntimeError("could not understand the edit request")
    data = json.loads(match.group(0))
    return clean_ops(data.get("ops")), str(data.get("summary", ""))[:200]


def _num(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def clean_ops(ops):
    cleaned = []
    for item in (ops if isinstance(ops, list) else [])[:12]:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op", "")).lower()
        if op == "rotate":
            cleaned.append({"op": op, "degrees": _num(item.get("degrees"), -360, 360, 90)})
        elif op in ("flip_horizontal", "flip_vertical", "grayscale", "sepia", "invert", "autocontrast", "square_crop"):
            cleaned.append({"op": op})
        elif op in ("brightness", "contrast", "saturation", "sharpness"):
            cleaned.append({"op": op, "factor": _num(item.get("factor"), 0.0, 4.0, 1.0)})
        elif op == "blur":
            cleaned.append({"op": op, "radius": _num(item.get("radius"), 0, 25, 3)})
        elif op == "crop":
            left, top = _num(item.get("left"), 0, 0.95, 0), _num(item.get("top"), 0, 0.95, 0)
            right, bottom = _num(item.get("right"), 0.05, 1, 1), _num(item.get("bottom"), 0.05, 1, 1)
            if right - left >= 0.05 and bottom - top >= 0.05:
                cleaned.append({"op": op, "left": left, "top": top, "right": right, "bottom": bottom})
        elif op == "resize":
            if item.get("max_side") is not None:
                cleaned.append({"op": op, "max_side": int(_num(item.get("max_side"), 64, 4096, 1280))})
            else:
                cleaned.append({"op": op, "scale": _num(item.get("scale"), 0.05, 3.0, 1.0)})
    return cleaned


def apply_ops(raw, ops):
    """Apply operations with Pillow. Returns (bytes, content_type, extension)."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    img = Image.open(io.BytesIO(raw))
    has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
    img = ImageOps.exif_transpose(img)
    img.thumbnail((3200, 3200))
    img = img.convert("RGBA" if has_alpha else "RGB")
    for item in ops:
        op = item["op"]
        if op == "rotate":
            img = img.rotate(-item["degrees"], expand=True, resample=Image.BICUBIC)
        elif op == "flip_horizontal":
            img = ImageOps.mirror(img)
        elif op == "flip_vertical":
            img = ImageOps.flip(img)
        elif op == "grayscale":
            img = ImageOps.grayscale(img).convert(img.mode)
        elif op == "sepia":
            gray = ImageOps.grayscale(img.convert("RGB"))
            tinted = ImageOps.colorize(gray, black=(30, 18, 8), white=(255, 236, 200), mid=(150, 110, 70))
            if has_alpha:
                tinted = tinted.convert("RGBA")
                tinted.putalpha(img.getchannel("A"))
            img = tinted
        elif op == "invert":
            if has_alpha:
                r, g, b, a = img.split()
                img = Image.merge("RGBA", (*[ImageOps.invert(c) for c in (r, g, b)], a))
            else:
                img = ImageOps.invert(img)
        elif op == "autocontrast":
            img = ImageOps.autocontrast(img.convert("RGB")).convert(img.mode)
        elif op in ("brightness", "contrast", "saturation", "sharpness"):
            enhancer = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast,
                        "saturation": ImageEnhance.Color, "sharpness": ImageEnhance.Sharpness}[op]
            img = enhancer(img).enhance(item["factor"])
        elif op == "blur":
            img = img.filter(ImageFilter.GaussianBlur(item["radius"]))
        elif op == "crop":
            w, h = img.size
            img = img.crop((int(item["left"] * w), int(item["top"] * h), int(item["right"] * w), int(item["bottom"] * h)))
        elif op == "square_crop":
            w, h = img.size
            side = min(w, h)
            img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        elif op == "resize":
            w, h = img.size
            if "max_side" in item:
                scale = item["max_side"] / float(max(w, h))
            else:
                scale = item["scale"]
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    if has_alpha:
        img.save(out, "PNG")
        return out.getvalue(), "image/png", ".png"
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue(), "image/jpeg", ".jpg"


# ------------------------------------------------------------------ text files: edit or create
def safe_filename(name, default_ext=".txt"):
    name = re.sub(r"[^\w.\- ]", "_", os.path.basename(str(name or "").replace("\\", "/")), flags=re.U).strip(" .")[:60]
    if not name:
        name = "jarvis_file"
    if not name.lower().endswith(TEXT_EXTENSIONS):
        name += default_ext
    return name


def edit_text_file(filename, content, instruction, language):
    """Edit an existing text file or, when `content` is empty, create a new one. Returns (filename, text, model)."""
    creating = not content.strip()
    lang = {"tg": "Tajik (Cyrillic)", "ru": "Russian", "en": "English"}[language]
    system = ("You are a file editor. " + ("Create a new file that fulfils the request. " if creating else "Apply the requested changes to the file and return the COMPLETE updated file. ") +
              "Reply in exactly this format and nothing else:\nFILENAME: <short_file_name_with_extension>\n---\n<complete file content>\n"
              "Do not wrap the content in code fences and do not add explanations. Unless the request says otherwise, keep the file's own language"
              + (" and write new text in " + lang if creating else "") + ".")
    user = "Request: " + instruction[:3000] + ("" if creating else "\nCurrent file name: " + filename + "\n\nFILE CONTENT:\n" + content[:30000])
    result = complete([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=6000, timeout=75.0)
    text = result["answer"].replace("\r\n", "\n")
    out_name, body = filename, text
    match = re.match(r"\s*FILENAME:\s*(.+?)\s*\n-{3,}\s*\n(.*)\Z", text, re.S)
    if match:
        out_name, body = match.group(1), match.group(2)
    body = re.sub(r"^\s*```[\w+-]*\n(.*?)\n```\s*$", r"\1", body, flags=re.S).strip("\n") + "\n"
    if not creating:
        out_name = filename  # editing keeps the original name (the app adds a suffix)
    return safe_filename(out_name), body, result["provider"] + ":" + result["model"]
