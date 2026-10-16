#!/usr/bin/env python3
"""
Parcheo de marca NPC-PuestoRemoto para el APK de Android de RustDesk.

- Se ejecuta en CI sobre un checkout limpio, ANTES de compilar (Rust + Flutter/Gradle).
- Idempotente: si un valor ya esta parcheado (p. ej. porque tu paso de Windows
  ya toco config.rs) se reescribe con el mismo valor, sin error.
- Falla de forma visible (AssertionError / SystemExit != 0) si un patron
  no coincide EXACTAMENTE una vez. Nunca falla en silencio.
- Trabaja leyendo/escribiendo bytes UTF-8 para respetar finales de linea (LF/CRLF).

Configuracion por variables de entorno (las pone el job en el YAML):
  NPC_APP_NAME      APP_NAME interno de Rust (sin espacios). Debe ser el MISMO que en Windows.
  NPC_DISPLAY_NAME  Nombre visible en el lanzador / ajustes de Android.
  NPC_SERVER        ID server (host[:puerto]); se compila como servidor "de fabrica".
  NPC_RS_PUB_KEY    Clave publica del servidor (id_ed25519.pub).
  NPC_APP_ID        applicationId de Android (identidad del paquete).
  NPC_URI_SCHEME    Esquema de enlace (por defecto: APP_NAME en minusculas, como hace RustDesk).
  NPC_ICON          PNG/ICO cuadrado del icono (por defecto: branding/android/icono.* y, si no
                    existe, res/icon.ico, el icono del cliente de Windows).
  NPC_LOGO_LIGHT    Logo (PNG) para tema claro  -> flutter/assets/logo_light.png  (opcional)
  NPC_LOGO_DARK     Logo (PNG) para tema oscuro -> flutter/assets/logo_dark.png   (opcional)
  NPC_ICON_BG       Color de fondo del icono adaptativo (por defecto #FFFFFF).
  NPC_ICON_FIT      Fraccion del lienzo que ocupa el icono (0.30-0.90, por defecto 0.50).
"""
import argparse
import os
import re
import sys
from pathlib import Path


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def die(msg):
    print(f"::error::{msg}", file=sys.stderr)
    raise SystemExit(1)


def env(name, default=None, required=False):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if required:
        die(f"Falta la variable de entorno {name}")
    return default


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def write(p: Path, s: str):
    p.write_bytes(s.encode("utf-8"))


def sub_one(path: Path, pattern: str, value: str, label: str):
    """Sustituye el hueco central de `(prefijo)[^"]*(sufijo)` exactamente 1 vez."""
    s = read(path)
    new, n = re.subn(pattern, lambda m: m.group(1) + value + m.group(2), s)
    if n != 1:
        die(f"[{label}] esperaba 1 coincidencia en {path.name}, hay {n}. "
            f"El codigo fuente de RustDesk ha cambiado: revisa el patron.")
    write(path, new)
    print(f"  ok  {label}")


def replace_exact(path: Path, old: str, new: str, label: str, expected=1):
    s = read(path)
    n = s.count(old)
    if n == 0 and new in s:
        print(f"  ok  {label} (ya estaba aplicado)")
        return
    if n != expected:
        die(f"[{label}] esperaba {expected} coincidencia(s) de {old!r} en {path.name}, hay {n}.")
    write(path, s.replace(old, new))
    print(f"  ok  {label}")


# ----------------------------------------------------------------------------
# Configuracion y validacion
# ----------------------------------------------------------------------------
def load_config():
    cfg = {
        "app_name": env("NPC_APP_NAME", "NPCPuestoRemoto"),
        "display": env("NPC_DISPLAY_NAME", "NPC-PuestoRemoto"),
        "server": env("NPC_SERVER", required=True),
        "key": env("NPC_RS_PUB_KEY", required=True),
        "app_id": env("NPC_APP_ID", "com.npuntocero.puestoremoto"),
        "web": env("NPC_WEB", "npuntocero.es"),
        "icon_bg": env("NPC_ICON_BG"),  # None -> se deduce del icono (o blanco)
        "icon_fit": float(env("NPC_ICON_FIT", "0.50")),
    }
    cfg["scheme"] = env("NPC_URI_SCHEME", cfg["app_name"].lower())

    checks = [
        ("NPC_APP_NAME", cfg["app_name"], r"[A-Za-z0-9-]+"),
        ("NPC_DISPLAY_NAME", cfg["display"], r"[^<>&\"'\\\r\n]+"),
        ("NPC_SERVER", cfg["server"], r"[A-Za-z0-9.-]+(:\d{1,5})?"),
        ("NPC_RS_PUB_KEY", cfg["key"], r"[A-Za-z0-9+/]{43}="),
        ("NPC_APP_ID", cfg["app_id"], r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+"),
        ("NPC_URI_SCHEME", cfg["scheme"], r"[a-z][a-z0-9+.-]*"),
        ("NPC_WEB", cfg["web"], r"[A-Za-z0-9.-]+"),
    ]
    if cfg["icon_bg"] is not None:
        checks.append(("NPC_ICON_BG", cfg["icon_bg"], r"#[0-9A-Fa-f]{6}"))
    for name, val, rx in checks:
        if not re.fullmatch(rx, val):
            die(f"{name}={val!r} no tiene un formato valido")
    if cfg["app_id"] == "com.carriez.flutter_hbb":
        die("NPC_APP_ID no puede ser el de RustDesk oficial: no se instalaria junto a el.")
    if not 0.30 <= cfg["icon_fit"] <= 0.90:
        die("NPC_ICON_FIT debe estar entre 0.30 y 0.90")
    return cfg


# ----------------------------------------------------------------------------
# 1) Rust: nombre interno, servidor y clave de fabrica
# ----------------------------------------------------------------------------
def patch_rust(repo: Path, cfg):
    print("[1/3] Rust (libs/hbb_common/src/config.rs)")
    p = repo / "libs/hbb_common/src/config.rs"
    if not p.exists():
        die("No existe libs/hbb_common/src/config.rs: submodulos sin descargar?")
    sub_one(p, r'(pub static ref APP_NAME: RwLock<String> = RwLock::new\(")[^"]*("\.to_owned\(\)\);)',
            cfg["app_name"], "APP_NAME")
    # PROD_RENDEZVOUS_SERVER hace que la app NO se considere "servidor publico"
    # (using_public_server() -> false) y es lo que usa get_rendezvous_servers().
    sub_one(p, r'(pub static ref PROD_RENDEZVOUS_SERVER: RwLock<String> = RwLock::new\(")[^"]*("\.to_owned\(\)\);)',
            cfg["server"], "PROD_RENDEZVOUS_SERVER (servidor de fabrica)")
    sub_one(p, r'(pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[")[^"]*("\];)',
            cfg["server"], "RENDEZVOUS_SERVERS (respaldo)")
    sub_one(p, r'(pub const RS_PUB_KEY: &str = ")[^"]*(";)',
            cfg["key"], "RS_PUB_KEY (clave de fabrica)")
    # Igual que en el parche de Windows: oculta "Con tecnologia de RustDesk" y la cuenta.
    replace_exact(
        p,
        'BUILTIN_SETTINGS: RwLock<HashMap<String, String>> = Default::default();',
        'BUILTIN_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new(HashMap::from([("hide-powered-by-me".to_owned(), "Y".to_owned())]));',
        "BUILTIN_SETTINGS (ocultar 'powered by')")
    replace_exact(
        p,
        'HARD_SETTINGS: RwLock<HashMap<String, String>> = Default::default();',
        'HARD_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new(HashMap::from([("disable-account".to_owned(), "Y".to_owned())]));',
        "HARD_SETTINGS (sin cuenta)")


def patch_flutter_text(repo: Path, cfg):
    print("[1b/3] Flutter movil (enlaces y textos de RustDesk)")
    sp = repo / "flutter/lib/mobile/pages/settings_page.dart"
    web = cfg["web"]
    replace_exact(sp, "Text('rustdesk.com',", f"Text('{web}',", "Ajustes/About: texto del enlace", expected=2)
    replace_exact(sp, "const url = 'https://rustdesk.com/';", f"const url = 'https://{web}/';",
                  "Ajustes/About: URL", expected=2)


# ----------------------------------------------------------------------------
# 2) Android: nombre visible, esquema de enlace, applicationId, textos
# ----------------------------------------------------------------------------
def patch_android_text(repo: Path, cfg):
    print("[2/3] Android (manifest, gradle, textos)")
    a = repo / "flutter/android/app"
    d = cfg["display"]

    man = a / "src/main/AndroidManifest.xml"
    replace_exact(man, 'android:label="RustDesk"', f'android:label="{d}"', "manifest: etiqueta de la app")
    replace_exact(man, 'android:label="RustDesk Input"', f'android:label="{d}"',
                  "manifest: nombre del servicio de accesibilidad")
    replace_exact(man, 'android:scheme="rustdesk"', f'android:scheme="{cfg["scheme"]}"',
                  f"manifest: esquema de enlace {cfg['scheme']}://")

    gradle = a / "build.gradle"
    sub_one(gradle, r'(applicationId ")[^"]*(")', cfg["app_id"],
            f"build.gradle: applicationId = {cfg['app_id']}")

    strings = a / "src/main/res/values/strings.xml"
    s = read(strings)
    n = s.count("RustDesk")
    if n == 0 and d in s:
        print("  ok  strings.xml (ya aplicado)")
    else:
        if n < 3:
            die(f"strings.xml: esperaba >=3 apariciones de 'RustDesk', hay {n}")
        write(strings, s.replace("RustDesk", d))
        print(f"  ok  strings.xml ({n} sustituciones)")

    kt = a / "src/main/kotlin/com/carriez/flutter_hbb"
    ms = kt / "MainService.kt"
    replace_exact(ms, 'const val DEFAULT_NOTIFY_TITLE = "RustDesk"',
                  f'const val DEFAULT_NOTIFY_TITLE = "{d}"', "MainService: titulo de notificacion")
    replace_exact(ms, 'val channelName = "RustDesk Service"',
                  f'val channelName = "{d}"', "MainService: nombre del canal")
    replace_exact(ms, 'description = "RustDesk Service Channel"',
                  f'description = "{d}"', "MainService: descripcion del canal")
    replace_exact(kt / "BootReceiver.kt", '"RustDesk is Open"', f'"{d} activo"', "BootReceiver: aviso")


# ----------------------------------------------------------------------------
# 3) Iconos
# ----------------------------------------------------------------------------
DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}


def _pil():
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        die("Falta Pillow: pip install pillow")
    return Image, ImageChops, ImageDraw


def load_image(path: Path):
    Image, _, _ = _pil()
    im = Image.open(path)
    if path.suffix.lower() == ".ico":
        sizes = im.info.get("sizes") or {im.size}
        im.size = max(sizes)  # el fotograma mas grande del .ico
    im.load()
    return im.convert("RGBA")


def ensure_alpha(im):
    """Si el PNG es opaco (sin transparencia), quita el color de fondo de la esquina.
    Devuelve (imagen, color_fondo_retirado '#RRGGBB' o None). Ese color se usa como fondo
    del icono adaptativo, para que un icono 'baldosa azul + glifo blanco' siga viendose bien."""
    Image, ImageChops, _ = _pil()
    r, g, b, a = im.split()
    if a.getextrema()[0] < 250:
        return im, None
    corner = im.getpixel((0, 0))[:3]
    bg = Image.new("RGB", im.size, corner)
    diff = ImageChops.difference(im.convert("RGB"), bg).convert("L")
    alpha = diff.point(lambda v: 0 if v < 24 else min(255, v * 4))
    out = im.copy()
    out.putalpha(alpha)
    hexbg = "#%02X%02X%02X" % corner
    print(f"  aviso: el icono no tiene transparencia; se retira el fondo {hexbg} y se usa como color de fondo")
    return out, hexbg


def trim(im):
    bbox = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return im.crop(bbox) if bbox else im


def fit_into(im, side, fraction):
    """Escala `im` para que su lado mayor mida side*fraction y lo centra en un lienzo side x side."""
    Image, _, _ = _pil()
    target = max(1, round(side * fraction))
    w, h = im.size
    k = target / max(w, h)
    small = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(small, ((side - small.width) // 2, (side - small.height) // 2), small)
    return canvas


def patch_icons(repo: Path, cfg):
    print("[3/3] Iconos")
    Image, _, ImageDraw = _pil()
    bd = Path(repo / "branding/android")
    icon_path = env("NPC_ICON")
    if icon_path:
        icon_path = Path(icon_path) if Path(icon_path).is_absolute() else repo / icon_path
    else:
        # 1) un icono especifico para Android en branding/android/, si existe
        # 2) el icono NPC que ya usa el cliente de Windows (res/icon.ico, 256 px)
        for cand in (bd / "icono.png", bd / "icono.ico", bd / "npc-puesto-remoto-icono.ico",
                     repo / "res/icon.ico"):
            if cand.exists():
                icon_path = cand
                break
    if not icon_path or not Path(icon_path).exists():
        die("No encuentro el icono. Pon branding/android/icono.png (o .ico) en el fork, "
            "o define NPC_ICON con su ruta.")
    print(f"  icono: {Path(icon_path).relative_to(repo) if str(icon_path).startswith(str(repo)) else icon_path}")

    icon, detected_bg = ensure_alpha(load_image(Path(icon_path)))
    icon = trim(icon)
    if cfg["icon_bg"] is None:
        cfg["icon_bg"] = detected_bg or "#FFFFFF"
    print(f"  fondo del icono: {cfg['icon_bg']}")
    res = repo / "flutter/android/app/src/main/res"
    bg = tuple(int(cfg["icon_bg"][i:i + 2], 16) for i in (1, 3, 5)) + (255,)

    for dens, f in DENSITIES.items():
        mip = res / f"mipmap-{dens}"
        if not mip.is_dir():
            die(f"No existe {mip}")

        # Capa frontal del icono adaptativo (108dp). El icono va dentro de la zona segura.
        fg_side = round(108 * f)
        fit_into(icon, fg_side, cfg["icon_fit"]).save(mip / "ic_launcher_foreground.png")

        # Iconos clasicos (Android < 8): placa de color + icono.
        side = round(48 * f)
        inner = fit_into(icon, side, min(0.78, cfg["icon_fit"] + 0.18))
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        ImageDraw.Draw(sq).rounded_rectangle((0, 0, side - 1, side - 1), radius=round(side * 0.18), fill=bg)
        sq.alpha_composite(inner)
        sq.save(mip / "ic_launcher.png")
        rd = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        ImageDraw.Draw(rd).ellipse((0, 0, side - 1, side - 1), fill=bg)
        rd.alpha_composite(fit_into(icon, side, min(0.70, cfg["icon_fit"] + 0.10)))
        rd.save(mip / "ic_launcher_round.png")

        # Icono pequeno de la barra de notificaciones: silueta blanca (solo cuenta el canal alfa).
        ns = round(24 * f)
        sil = fit_into(icon, ns, 0.92)
        white = Image.new("RGBA", sil.size, (255, 255, 255, 0))
        white.putalpha(sil.getchannel("A"))
        white.save(mip / "ic_stat_logo.png")
    print("  ok  mipmap-* (foreground, launcher, round, notificacion)")

    bgxml = res / "values/ic_launcher_background.xml"
    sub_one(bgxml, r'(<color name="ic_launcher_background">)#[0-9A-Fa-f]{6,8}(</color>)',
            cfg["icon_bg"], "color de fondo del icono adaptativo")

    # Recursos de Flutter: logos por tema (claro/oscuro). Opcionales. El fork ya trae
    # flutter/assets/icon.png y logo.png propios, que no se tocan.
    assets = repo / "flutter/assets"
    for var, default, dest in (("NPC_LOGO_LIGHT", "logo-azul.png", "logo_light.png"),
                               ("NPC_LOGO_DARK", "logo-blanco.png", "logo_dark.png")):
        src = env(var)
        src = (Path(src) if Path(src).is_absolute() else repo / src) if src else bd / default
        if src.exists():
            load_image(src).save(assets / dest)
            print(f"  ok  flutter/assets/{dest}")
        else:
            print(f"  info: {src.name} no existe; se omite {dest} (Ajustes usara flutter/assets/logo.png)")


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="raiz del checkout de RustDesk")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    if not (repo / "flutter/android").is_dir():
        die(f"{repo} no parece la raiz de RustDesk (falta flutter/android)")
    cfg = load_config()
    print(f"Marca: {cfg['display']} | APP_NAME={cfg['app_name']} | appId={cfg['app_id']} | "
          f"servidor={cfg['server']} | esquema={cfg['scheme']}://")
    patch_rust(repo, cfg)
    patch_flutter_text(repo, cfg)
    patch_android_text(repo, cfg)
    patch_icons(repo, cfg)
    print("Parcheo completado")


if __name__ == "__main__":
    main()
