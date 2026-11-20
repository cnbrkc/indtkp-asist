"""Bağlantı erişilebilirliği denetimi: iki sürümün bildirimlerini ileti ileti karşılaştırır.

Ölçüt: temizlenmiş kaynak mesajın her bağlantısı teslim edilen iletide görünür URL, gizli
hyperlink ya da (yalnız bot bildiriminde) inline klavye düğmesi olarak açılabilmelidir.
"Yeni kayıp", karşılaştırılan eski sürümde açılabilen ama bu sürümde açılamayan bağlantıdır.

    python tests/link_reach_audit.py                    # PR öncesi (13ad981) ile çalışma ağacı
    python tests/link_reach_audit.py --base 94de953     # başka bir sürümle karşılaştır
    python tests/link_reach_audit.py --show 5           # yeni kayıp örneklerini de yazdır

Değişiklik **commit edildikten sonra** ``--base HEAD`` kullanılmaz: ``HEAD`` artık yeni
sürümün kendisidir, karşılaştırma kendisiyle yapılır ve her sayaç zorunlu olarak 0 çıkar
(boş bir test). Doğru taban, değişikliğin çıktığı commit'tir::

    python tests/link_reach_audit.py --base 370b5be      # bu düzen değişikliğinin tabanı

Üç küme çalışır (hepsi çevrimdışı ve tohumlu; aynı tohum aynı mesajları üretir):

* elle kurulmuş 17 çok linkli senaryo x 3 mod x bot/hesap,
* 3.000 rastgele çok linkli mesaj x 3 mod x bot/hesap x metin/fotoğraf sınırı (36.000 ileti),
* aynı üreteç, adreslere 40-400 karakterlik sorgu eklenmiş 1.500 mesaj (18.000 ileti).

Çıkış kodu, yeni kayıp, sınırı aşan ileti, botta kaybolan buton ya da eski sürümde taşınan
mesaj adresi yeni sürümde hiç taşınmıyorsa 1'dir. Adres artık ayrı ``Mesajı Gör`` satırı
değil, en alttaki kaynak adının içine gömülüdür; ölçüt "adres taşınıyor mu"dur
(``source_line`` **ya da** ``source_name_url``).

Karşılaştırmada eski sürüm ``git archive`` ile geçici bir dizine açılır; bu yüzden tam geçmişi
olan bir depo gerekir ve CI'ın sığ kopyasında çalışmaz. Dosya adı ``test`` ile başlamadığı için
``unittest discover`` onu test olarak toplamaz; ``tests/test_product_link_qa.py`` ise üreteci
(``SEED``, ``random_messages``, ``build_message``, ``per_message_cache``) buradan içe aktarır ve
aynı mesajların bir örneklemini her test çalıştırmasında, eski sürüme gerek duymadan dener.
"""
from __future__ import annotations

import appstate
import sanitize
import argparse
import contextlib
import inspect
import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
SEED = 20261114
MESSAGE_LINK = "https://t.me/kaynak/1"
SOURCE_NAME = "Kaynak Grup"
MODES = ("smart", "all", "off")

WORDS = ("Philips Airfryer XXL Bosch Matkap Seti Nivea Krem Samsung Galaxy Kulaklık Xiaomi Robot Süpürge "
         "Arzum Tost Makinesi Paşabahçe Bardak Lego Technic Stanley Termos Braun Tıraş Oral-B Diş Fırçası "
         "stok sınırlı hızlı kargo kaçırma indirim sepette kupon ücretsiz bugün son fırsat kampanya").split()
EMOJI = ("🔥", "💥", "⚡", "🎯", "📦", "🛒", "✅", "⏰", "🎉", "👇")
TAGS = ("#reklam", "#işbirliği", "reklam", "işbirliği")


def _token(rnd: random.Random, size: int = 8) -> str:
    return "".join(rnd.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(size))


def _product_url(rnd: random.Random) -> str:
    token = _token(rnd)
    return rnd.choice((
        f"https://www.amazon.com.tr/dp/B0{token.upper()}?tag=kanal-21",
        f"https://ty.gl/{token}", f"https://amzn.to/{token}", f"https://app.hb.biz/{token}",
        f"https://www.hepsiburada.com/urun-p-HBV{token}", f"https://www.trendyol.com/marka/urun-p-{token}",
        f"https://sl.n11.com/n/{token}", f"https://fenom.io/amzn-{token}",
    ))


def _search_url(rnd: random.Random) -> tuple[str, str]:
    query = "+".join(rnd.sample(WORDS, 3)) + "+" + _token(rnd, 4)
    return rnd.choice((
        ("Google", f"https://www.google.com/search?udm=28&q={query}"),
        ("Google web", f"https://www.google.com/search?q={query}"),
        ("Akakçe", f"https://www.akakce.com/arama/?q={query}"),
        ("Cimri", f"https://www.cimri.com/arama?q={query}"),
    ))


def _other_url(rnd: random.Random) -> tuple[str, str]:
    token = _token(rnd, 6)
    return rnd.choice((
        ("🎟 Kupon", f"https://t.me/kanal_kupon/{rnd.randint(1, 99999)}"),
        ("Kanal", f"https://t.me/kanal_{token}"),
        ("İnceleme", f"https://www.youtube.com/watch?v={token}"),
        ("Topluluk", f"https://discord.gg/{token}"),
        ("🛍 Mağaza", f"https://www.amazon.com.tr/?tag=kanal-{token}"),
        ("Duyuru", f"https://x.com/kanal/status/{rnd.randint(10 ** 6, 10 ** 7)}"),
    ))


def _link(rnd: random.Random, long_urls: bool) -> tuple[str, str]:
    """(etiket, adres): her beş bağlantıdan biri arama, üçte biri ürün dışı bir sayfadır."""
    roll = rnd.random()
    if roll < 0.2:
        label, url = _search_url(rnd)
    elif roll < 0.5:
        label, url = _other_url(rnd)
    else:
        label, url = rnd.choice(("✅ Satın Al", "🛒 Ürüne Git", "Mağaza", "FIRSATA GİT", "Sepete Ekle")), _product_url(rnd)
    if long_urls:
        url += ("&" if "?" in url else "?") + "ref_=" + _token(rnd, rnd.randint(35, 395))
    return label, url


def utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def random_message(rnd: random.Random, number: int, long_urls: bool) -> dict:
    """Bir kaynak mesajın tarifi: metin, gizli hyperlink'ler, buton satırları, önizleme."""
    size = rnd.choice(("short", "short", "medium", "medium", "long", "long", "huge"))
    filler = {"short": (0, 3), "medium": (4, 12), "long": (13, 30), "huge": (40, 90)}[size]
    lines = [f"{rnd.choice(EMOJI)} " + " ".join(rnd.sample(WORDS, rnd.randint(4, 8))),
             f"💰 {rnd.randint(20, 9000)},{rnd.randint(10, 99)} TL"]
    lines += [(f"{rnd.choice(EMOJI)} " if rnd.random() < 0.3 else "") + " ".join(rnd.sample(WORDS, rnd.randint(5, 11)))
              for _ in range(rnd.randint(*filler))]
    hidden: list[tuple[str, str]] = []
    for index in range(rnd.choice((0, 0, 1, 1, 2, 3, 4))):
        label, url = _link(rnd, long_urls)
        label = f"{label} {number}x{index}"
        lines.insert(rnd.randint(1, len(lines)), f"{rnd.choice(('🔗', '👉', '🛍'))} {label}")
        hidden.append((label, url))
    for _ in range(rnd.choice((0, 0, 0, 1, 2))):
        lines.insert(rnd.randint(1, len(lines)), "Link: " + _link(rnd, long_urls)[1])
    for index in range(rnd.choice((0, 0, 0, 0, 0, 1, 2))):  # etiketi silinen gizli linkler
        label = f"{rnd.choice(TAGS)}"
        marker = f"⟦{number}t{index}⟧"
        lines.append(f"{marker}{label}")
        hidden.append((marker + label, _link(rnd, long_urls)[1]))
    if rnd.random() < 0.15:
        lines.insert(0, "\u200b")  # kaynak botlarının önizleme görselini gömdüğü görünmez karakter
        hidden.append(("\u200b", f"https://m.media-amazon.com/images/I/{_token(rnd)}.jpg"))
    text = "\n".join(lines)
    entities = []
    for label, url in hidden:
        start = text.index(label)
        if label.startswith("⟦"):  # işaret yalnız konumu bulmak içindi; etiket tek başına kalır
            marker = label[:label.index("⟧") + 1]
            text = text.replace(marker, "", 1)
            label = label[len(marker):]
        entities.append([utf16(text[:start]), utf16(label), url])
    buttons = [_link(rnd, long_urls) for _ in range(rnd.choice((0, 0, 1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 12)))]
    if buttons and hidden and rnd.random() < 0.3:  # gizli linkle aynı adrese giden buton
        buttons[rnd.randrange(len(buttons))] = ("🛒 Ürüne Git", hidden[0][1])
    rows, cursor = [], 0
    while cursor < len(buttons):
        width = rnd.randint(1, 3)
        rows.append([[f"{label} {cursor + offset + 1}", url]
                     for offset, (label, url) in enumerate(buttons[cursor:cursor + width])])
        cursor += width
    webpage = None
    if rnd.random() < 0.3:
        pool = [url for _, url in hidden] + [url for row in rows for _, url in row]
        webpage = [rnd.choice(pool) if pool and rnd.random() < 0.5 else _link(rnd, long_urls)[1], "Önizleme başlığı"]
    return {"id": number, "text": text, "entities": entities, "rows": rows, "webpage": webpage}


def random_messages(seed: int, count: int, long_urls: bool) -> list[dict]:
    """Tohumlu rastgele küme: aynı tohum her çalıştırmada aynı mesajları üretir."""
    prefix = f"{seed}-uzun-" if long_urls else f"{seed}-"
    return [random_message(random.Random(f"{prefix}{number}"), number, long_urls) for number in range(count)]


def scenarios() -> list[dict]:
    """Elle kurulmuş 17 çok linkli senaryo (testlerdeki ve bildirilen örneklerdeki düzenler)."""
    body = "Ateşler Kuruyemiş Badem İçi Kavrulmuş 150 gr\n💰 106,26 TL"
    products = [f"https://www.amazon.com.tr/dp/B0PRODUCT{number}?tag=kanal-21" for number in range(1, 10)]
    stores = [[f"Mağaza {number}", url] for number, url in enumerate(products, 1)]
    buy, coupon = ["✅ Satın Al ✅", products[0]], ["🎟 Kupon", "https://t.me/kanal_kupon/55"]
    review = ["İnceleme", "https://www.youtube.com/watch?v=abc"]
    google = ["Google", "https://www.google.com/search?udm=28&q=badem"]
    google_web = ["Google web", "https://www.google.com/search?q=badem"]
    akakce, cimri = ["Akakçe", "https://www.akakce.com/arama/?q=badem"], ["Cimri", "https://www.cimri.com/arama?q=badem"]
    searches = [google, akakce, cimri]
    discord = ["Topluluk", "https://discord.gg/AbCdEf"]
    home = ["🛍 Amazon", "https://www.amazon.com.tr/?tag=kanal-21"]
    preview = ["https://www.hepsiburada.com/urun-p-HBV000PREVIEW", "Önizleme"]
    image = "https://m.media-amazon.com/images/I/71abcDEF._AC_SL1500_.jpg"
    long_body = body + "\n" + "\n".join(f"Satır {number}: stok sınırlı, hızlı kargo, kaçırma!" for number in range(1, 21))

    def make(name, text, *, links=(), buttons=(), webpage=None):
        entities = [[utf16(text[:text.index(label)]), utf16(label), url] for label, url in links]
        return {"id": name, "text": text, "entities": entities, "rows": [list(buttons)] if buttons else [],
                "webpage": webpage}

    tagged = body + "\n\nbir #reklam iki #işbirliği üç reklam dört işbirliği beş #reklam"
    tagged_entities, cursor = [], 0
    for tag, url in zip(["#reklam", "#işbirliği", "reklam", "işbirliği", "#reklam"], products):
        start = tagged.index(tag, cursor)
        tagged_entities.append([utf16(tagged[:start]), utf16(tag), url])
        cursor = start + len(tag)
    return [
        make("ürün butonu + gövdede gizli arama", body + "\n\n🔎 Google", links=[("Google", google_web[1])], buttons=[buy]),
        make("gizli başlık + aynı adresli buton", "⚡ ONLY Kadın Pantolon\n💰 224,31 ₺",
             links=[("⚡ ONLY Kadın Pantolon", products[0])], buttons=[["🛒 Ürüne Git", products[0]]]),
        make("3 arama + ürün + kupon butonu", body, buttons=[*searches, buy, coupon]),
        make("3 arama + ürün butonu", body, buttons=[*searches, buy]),
        make("4 ürün butonu + kupon", body, buttons=[*stores[:4], coupon]),
        make("4 ürün butonu + önizleme", body, buttons=stores[:4], webpage=preview),
        make("6 ürün butonu + kupon", body, buttons=[*stores[:6], coupon]),
        make("9 ürün butonu", body, buttons=stores),
        make("arama + inceleme + ürün + kupon", body, buttons=[*searches, review, buy, coupon]),
        make("4 kupon + ürün", body, buttons=[
            *[[f"Kupon {number}", f"https://t.me/kanal_kupon/{number}"] for number in range(1, 5)], buy]),
        make("4 arama + 2 kupon", body, buttons=[*searches, google_web, coupon, ["Kupon 2", "https://t.me/kanal_kupon/56"]]),
        make("gizli linkler + butonlar", body + "\n\n🔎 Google\nFIRSATA GİT",
             links=[("Google", google_web[1]), ("FIRSATA GİT", products[7])], buttons=[akakce, *stores[:3], coupon]),
        make("görünür adres + aynı adresli buton", body + "\n" + products[0], buttons=[buy, coupon]),
        {"id": "etiketi silinen 5 gizli link", "text": tagged, "entities": tagged_entities, "rows": [], "webpage": None},
        make("topluluk daveti + ürün butonu", body, buttons=[discord, buy]),
        make("etiketli ana sayfa butonu + gizli ürün linki", body + "\n\n🔗 FIRSATA GİT",
             links=[("FIRSATA GİT", products[0])], buttons=[home]),
        make("uzun gövde + gömülü önizleme görseli + butonlar", "\u200b" + long_body + "\n\n🔗 FIRSATA GİT",
             links=[("\u200b", image), ("FIRSATA GİT", products[0])], buttons=[coupon, review], webpage=[image, "Önizleme"]),
    ]


# ---------------------------------------------------------------------------
# Bir sürümün ürettiği iletiler (alt süreçte, o sürümün bot.py'si ile)
# ---------------------------------------------------------------------------

def build_message(types, spec: dict) -> SimpleNamespace:
    markup = SimpleNamespace(rows=[SimpleNamespace(buttons=[SimpleNamespace(text=text, url=url) for text, url in row])
                                   for row in spec["rows"]]) if spec["rows"] else None
    media = (SimpleNamespace(webpage=SimpleNamespace(url=spec["webpage"][0], title=spec["webpage"][1]))
             if spec["webpage"] else None)
    return SimpleNamespace(
        id=1, message=spec["text"], raw_text=spec["text"], media=media, reply_markup=markup,
        entities=[types.MessageEntityTextUrl(offset=offset, length=length, url=url)
                  for offset, length, url in spec["entities"]])



def _tree_attr(bot, name: str):
    """Eski ağaçta ad ``bot`` içindedir; yeni ağaçta ilgili modüle taşınmıştır (aynı ağaçtan aranır)."""
    if hasattr(bot, name):
        return getattr(bot, name)
    import importlib  # noqa: PLC0415 - sürüme göre içe aktarılır
    for module_name in ("composer", "sanitize", "constants", "appstate", "dedupbadge", "header", "producttitle",
                        "searchbuttons", "botapi"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)


def _compose_owner(bot):
    """compose_message'ın ad aradığı ad alanı: eski ağaçta ``bot``, yeni ağaçta ``composer``."""
    if hasattr(bot, "compose_message"):
        return bot
    import importlib  # noqa: PLC0415
    return importlib.import_module("composer")


def deliveries(bot, types, spec: dict, both_limits: bool) -> list[dict]:
    """Üretimdeki çağrılarla kurulan iletiler ve her birinden açılabilen adresler."""
    def key(url) -> str:
        return sanitize.clean_url(url).rstrip("/").lower()

    obj = build_message(types, spec)
    cleaned = _tree_attr(bot, "sanitize_message")(obj)
    source = {key(item["url"]): item["kind"] for item in reversed(_tree_attr(bot, "extract_links")(cleaned))}
    full_body = _tree_attr(bot, "utf16_length")(_tree_attr(bot, "message_text")(cleaned))
    accepts_keyboard = "has_keyboard" in inspect.signature(_tree_attr(bot, "compose_message")).parameters
    found = []
    for mode in MODES:
        for as_bot in (True, False):
            text_limit = _tree_attr(bot, "MESSAGE_LIMIT") - (200 if as_bot else 100)
            for name, limit in ((("text", text_limit), ("caption", _tree_attr(bot, "CAPTION_LIMIT") - 24)) if both_limits
                                else (("text", text_limit),)):
                extra = {"has_keyboard": True} if as_bot and accepts_keyboard else {}
                composed = _tree_attr(bot, "compose_message")(
                    obj, limit=limit, link_kinds=_tree_attr(bot, "link_kinds_for")(mode, bot=as_bot),
                    message_link=MESSAGE_LINK, source_name=SOURCE_NAME, **extra)
                reach = {key(url) for url in sanitize.visible_urls(composed["text"])}
                keyboard: list[str] = []
                if as_bot:
                    entities = (_tree_attr(bot, "bot_api_entities")(composed["message"], composed["content"])
                                + _tree_attr(bot, "source_name_entity")(composed))
                    markup = _tree_attr(bot, "build_inline_keyboard")(obj, body_text=composed["text"], body_entities=entities)
                    keyboard = [key(button["url"]) for row in (markup or {}).get("inline_keyboard", []) for button in row]
                    reach |= {key(item["url"]) for item in entities if item.get("type") == "text_link"}
                    reach |= set(keyboard)
                else:
                    reach |= {key(entity.url) for entity in composed["entities"] if getattr(entity, "url", None)}
                appendix = composed["appendix"].split("\n") if composed["appendix"] else []
                found.append({
                    "id": spec["id"], "cell": f"{mode}/{'bot' if as_bot else 'hesap'}/{name}", "limit": limit,
                    "length": _tree_attr(bot, "utf16_length")(composed["text"]), "reach": sorted(reach & set(source)),
                    "source": source, "keyboard": keyboard, "appendix": appendix,
                    "body": _tree_attr(bot, "utf16_length")(composed["body"]), "full_body": full_body,
                    "product_lines": sum(1 for text in appendix if text.startswith("👉")),
                    "source_line": bool(composed["source_line"]),
                    # Yeni düzende mesaj adresi kaynak adına gömülüdür; eski sürümde
                    # anahtar yoktur, o yüzden ``get``.
                    "source_name_url": bool(composed.get("source_name_url")),
                })
    return found


@contextlib.contextmanager
def per_message_cache(bot, enabled: bool = True):
    """Mesaja bağlı pahalı adımları (temizlik, üst satır, arama sorgusu) mesaj başına bir kez çalıştır.

    Çıktı değişmez (``--no-cache`` ile karşılaştırılabilir); yalnız aynı mesajın 12 iletisinde
    yinelenen iş atlanır. Anahtar nesne kimliğidir; nesneler önbellekte tutulduğu için kimlik
    başka bir nesneye geçemez. Çağıran, önbelleği her mesajdan sonra boşaltır (``clear()``);
    bloktan çıkınca ``bot`` işlevleri eski hâline döner.
    """
    cache: dict = {}
    owner = _compose_owner(bot)
    originals = {name: getattr(owner, name)
                 for name in (("sanitize_message", "_search_query", "_compose_resolve_header") if enabled else ())}

    def cached(name: str):
        def call(*args, **kwargs):
            slot = (name, tuple(arg if isinstance(arg, (str, int, type(None))) else id(arg) for arg in args),
                    tuple(sorted(kwargs.items())))
            if slot not in cache:
                cache[slot] = (originals[name](*args, **kwargs), args)
            return cache[slot][0]
        return call

    for name in originals:
        setattr(owner, name, cached(name))
    try:
        yield cache
    finally:
        for name, original in originals.items():
            setattr(owner, name, original)


def run_tree(tree: Path, spec_path: Path, out_path: Path, both_limits: bool, use_cache: bool = True) -> None:
    sys.path.insert(0, str(tree))
    import bot  # noqa: PLC0415 - sürüme göre içe aktarılır
    from telethon.tl import types  # noqa: PLC0415
    appstate.SMART_HEADER = True
    with per_message_cache(bot, use_cache) as cache, \
            spec_path.open(encoding="utf-8") as source, out_path.open("w", encoding="utf-8") as target:
        for raw in source:
            for item in deliveries(bot, types, json.loads(raw), both_limits):
                target.write(json.dumps(item, ensure_ascii=False) + "\n")
            cache.clear()


# ---------------------------------------------------------------------------
# Karşılaştırma
# ---------------------------------------------------------------------------

def compare(old_path: Path, new_path: Path, show: int = 0) -> dict:
    totals: Counter = Counter()
    cells: dict[str, Counter] = {}
    samples: list[str] = []
    with old_path.open(encoding="utf-8") as old_file, new_path.open(encoding="utf-8") as new_file:
        for old_raw, new_raw in zip(old_file, new_file, strict=True):
            old, new = json.loads(old_raw), json.loads(new_raw)
            assert (old["id"], old["cell"]) == (new["id"], new["cell"]) and old["source"] == new["source"]
            cell = cells.setdefault(new["cell"], Counter())
            lost = sorted(set(old["reach"]) - set(new["reach"]))
            gained = set(new["reach"]) - set(old["reach"])
            buttons = {url for url, kind in new["source"].items() if kind == "button"}
            row = {
                "ileti": 1, "yeni kayıp": len(lost), "kayıplı ileti": bool(lost), "yeniden açılan": len(gained),
                "sınırı aşan": new["length"] > new["limit"],
                "botta kayıp buton": len(buttons - set(new["reach"])) if "/bot/" in new["cell"] else 0,
                "gövdesi kısalan": new["body"] < old["body"], "gövdesi uzayan": new["body"] > old["body"],
                "gövdesi kırpılan": new["body"] < new["full_body"], "ürün satırlı": new["product_lines"] > 0,
                "adres taşınmıyor": not (new["source_line"] or new["source_name_url"]),
                "aynı metin uzunluğu": new["length"] == old["length"],
                "adres kaybı": old["source_line"] and not (new["source_line"] or new["source_name_url"]),
                "eski açılan": len(old["reach"]), "yeni açılan": len(new["reach"]), "kaynak bağlantı": len(new["source"]),
            }
            totals.update(row)
            cell.update(row)
            if lost and len(samples) < show:
                samples.append(json.dumps({
                    "id": new["id"], "cell": new["cell"], "kayıp": [(url[:70], new["source"][url]) for url in lost],
                    "eski": {"ek": [text[:60] for text in old["appendix"]], "gövde": old["body"], "uzunluk": old["length"]},
                    "yeni": {"ek": [text[:60] for text in new["appendix"]], "gövde": new["body"], "uzunluk": new["length"]},
                    "tam gövde": new["full_body"], "sınır": new["limit"]}, ensure_ascii=False, indent=1))
    return {"totals": totals, "cells": cells, "samples": samples}


def worker(arguments: argparse.Namespace) -> None:
    run_tree(Path(arguments.tree), Path(arguments.specs), Path(arguments.out), arguments.both_limits,
             use_cache=not arguments.no_cache)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="13ad981", help="karşılaştırılacak eski sürüm (git revizyonu)")
    parser.add_argument("--tree", help="yeni sürümün dizini (varsayılan: bu depo)")
    parser.add_argument("--messages", type=int, default=3000)
    parser.add_argument("--long-messages", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--show", type=int, default=0, help="küme başına yazdırılacak yeni kayıp örneği")
    parser.add_argument("--cells", action="store_true", help="hücre (mod/hedef/sınır) dökümünü de yazdır")
    parser.add_argument("--keep", help="ara dosyaları bu dizinde bırak")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--specs", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    parser.add_argument("--both-limits", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-cache", action="store_true", help="mesaj başına önbelleği kapat (yavaş; doğrulama için)")
    arguments = parser.parse_args()
    if arguments.worker:
        worker(arguments)
        return 0

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(arguments.keep or scratch)
        work.mkdir(parents=True, exist_ok=True)
        old_tree = work / "old"
        old_tree.mkdir(exist_ok=True)
        archive = subprocess.run(["git", "-C", str(REPO), "archive", arguments.base], check=True, capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(old_tree)], input=archive.stdout, check=True)
        new_tree = Path(arguments.tree or REPO)
        sets = [
            ("17 senaryo x 3 mod x bot/hesap", "senaryo", scenarios(), False),
            (f"{arguments.messages} rastgele mesaj", "rastgele",
             random_messages(arguments.seed, arguments.messages, False), True),
            (f"{arguments.long_messages} mesaj, 40-400 karakter sorgu ekli adresler", "uzun",
             random_messages(arguments.seed, arguments.long_messages, True), True),
        ]
        failed = False
        print(f"eski sürüm: {arguments.base}   yeni sürüm: {new_tree}   tohum: {arguments.seed}")
        for title, slug, specs, both_limits in sets:
            spec_path = work / f"{slug}.jsonl"
            spec_path.write_text("".join(json.dumps(spec, ensure_ascii=False) + "\n" for spec in specs), encoding="utf-8")
            outputs, workers = [], []
            for name, tree in (("old", old_tree), ("new", new_tree)):
                out_path = work / f"{slug}.{name}.jsonl"
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--tree", str(tree),
                           "--specs", str(spec_path), "--out", str(out_path)]
                command += (["--both-limits"] if both_limits else []) + (["--no-cache"] if arguments.no_cache else [])
                workers.append(subprocess.Popen(command))  # iki sürüm yan yana çalışır
                outputs.append(out_path)
            if any(process.wait() for process in workers):
                raise SystemExit("ileti üretimi başarısız oldu")
            result = compare(*outputs, show=arguments.show)
            totals = result["totals"]
            failed |= bool(totals["yeni kayıp"] or totals["sınırı aşan"] or totals["botta kayıp buton"]
                           or totals["adres kaybı"])
            print(f"\n== {title}: {totals['ileti']} ileti")
            for name in ("yeni kayıp", "kayıplı ileti", "yeniden açılan", "sınırı aşan", "botta kayıp buton",
                         "gövdesi kısalan", "gövdesi uzayan", "gövdesi kırpılan", "ürün satırlı", "adres taşınmıyor",
                         "adres kaybı"):
                print(f"   {name:<20} {totals[name]}")
            print(f"   açılabilen bağlantı   {totals['eski açılan']} -> {totals['yeni açılan']} "
                  f"(kaynakta {totals['kaynak bağlantı']})")
            if arguments.cells:
                for cell, counts in sorted(result["cells"].items()):
                    print(f"     {cell:<20} ileti {counts['ileti']:>5}  yeni kayıp {counts['yeni kayıp']:>5}  "
                          f"yeniden açılan {counts['yeniden açılan']:>5}  gövdesi kısalan {counts['gövdesi kısalan']:>5}  "
                          f"ürün satırlı {counts['ürün satırlı']:>5}  açılan {counts['eski açılan']}->{counts['yeni açılan']}")
            for sample in result["samples"]:
                print(sample)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
