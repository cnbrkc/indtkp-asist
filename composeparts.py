"""Mesaj kurma yardımcıları: gövde/ek/başlık kesme ve entity üretimi (saf)."""

from __future__ import annotations

from botentities import entities_for_text
from collections.abc import Callable, Sequence
from constants import APPENDIX_PRODUCT, APPENDIX_REQUIRED, APPENDIX_SPARE
from dedupbadge import shift_telethon_entities
from header import fit_header_line, split_header_line, utf16_budget
from sanitize import (
    LINK_APPENDIX_LIMIT,
    clean_url,
    utf16_length,
    visible_urls,
)
from searchbuttons import _entities_within
from telethon.tl import types
from typing import Any


def _compose_cut_body(body: str, room: int) -> str:
    """Gövdeyi ``room`` UTF-16 birimine sığdır; kırpılan gövde ``…`` ile biter, yer yoksa boş kalır."""
    if utf16_length(body) <= room:
        return body
    return utf16_budget(body, room - 1).rstrip() + "…" if room > 0 else ""


def _compose_body_links(cleaned_obj: Any, body: str, room: int) -> set[str]:
    """``room`` birime sığdırılan gövdeden açılabilen adresler: görünür URL'ler ve gizli hyperlink'ler.

    Kırpma noktasının gerisinde kalan bağlantı açılamaz. Yarım kalan bir URL'nin baştaki
    parçası da adres gibi görünür; çağıran bunu gövdenin tamamındaki adreslerle eler.
    """
    text = _compose_cut_body(body, room)
    return visible_urls(text) | {
        clean_url(entity.url).rstrip("/").lower()
        for entity in entities_for_text(cleaned_obj, text) if getattr(entity, "url", None)
    }


def _compose_fit_appendix(
    build: Callable[[int], tuple[list[tuple[str, int]], list[tuple[int, int, str]], list[str]]],
    cleaned_obj: Any, header: str, body: str, tail: Sequence[str], limit: int, sep: str,
    real_tail: Sequence[str] | None = None,
) -> tuple[str, list[tuple[int, int, str]]]:
    """Eki karakter sınırına sığdır: ``(ek metni, ürün satırı konumları)``.

    ``build(n)`` en çok ``n`` ürün satırlı eki kurar (bkz. ``_compose_build_appendix``).

    Ölçü yalın düzendir: gövde + mesajın ilk gizli bağlantılarının ``🔗`` satırları (hepsi
    birlikte gövdesiz sığmıyorsa hiçbiri) + kaynak adı. O düzenin açtığı hiçbir bağlantı
    kapanmaz (mesaj adresi adın içinde olduğu için metinde yer kaplamaz). Bunun içinde
    öncelik sırası:

    1. Kaynak mesajın metni ve içindeki bağlantılar. Gövdenin tamamı sığmıyorsa ``🔗``
       satırları sondan başlayarak bırakılır: önce yedekliler (``APPENDIX_SPARE``; botta
       butonu klavyede kalır), yalın düzen hiç satır yazamıyorsa vazgeçilmezler de (o düzende
       de yoklardı). Bu satırlar gövdeyi hiç kırpmaz. Yalın düzenin yazdığı vazgeçilmez
       satırlar yerinde kalır: klavyesiz kopyada bağlantının tek taşıyıcısıdır.
    2. ``👉 Ürün Linki 👈`` satırları. Yer yoksa gövde metninin sonundan yer alırlar; ama
       **yalın düzende (PR öncesi düzen) açılabilen hiçbir bağlantıyı kapatmazlar.** Güvence
       bu kadardır, daha güçlü bir iddia değildir: ölçü bütçesi sabit tutulduğu için kırpma
       noktası eski düzenle aynı yerde kalır, dolayısıyla yalın düzenin açtığı her bağlantı
       açık kalır. Buna karşılık "ürün satırı hiç yazılmasaydı gövdeden açılacak **her**
       bağlantı açık kalır" iddiası geçerli DEĞİLDİR: ürün satırı, ürün satırsız varyanta
       göre gövdenin sonundaki bir bağlantıyı kesebilir — o bağlantı eski düzende de
       kesiliyordu (ölçüm: 1430 ürün satırlı hücrenin 10'unda, %0,70; kapanan adreslerin
       tamamı yalın düzende de kapalı, dolayısıyla kullanıcıya dönük erişim kaybı yoktur).
       Ek ayrıca gövdesiz de sığmalıdır (yalın düzenin satırları dışarıda kalmaz). Buna
       uymayan ürün satırı sondan başlayarak yazılmaz; bağlantısı botta klavye düğmesi
       olarak kalır, hesap kopyasında ilk gizli bağlantılardansa ``🔗`` satırı olur.
    3. Kaynak adı (ve içine gömülü mesaj adresi) ek uğruna bırakılmaz; ad hiç
       yazılamıyorsa adres ``🔗 Mesajı Gör: <t.me>`` satırı olarak geri gelir.

    ``real_tail``: metne GERÇEKTEN giren kuyruk (mesaj linki artık ayrı satır
    değil, kaynak adının içinde). ``tail`` bilerek eski, geniş bütçeyi korur:
    satır kalktı diye boşalan yer fazladan ``🔗`` satırlarına verilirse onlar
    gövdenin sonundan yer alıp gövdedeki bağlantıyı kapatırdı (kullanıcı isteği:
    bildirim yalnız KISALSIN). Ama ileti her şeyiyle SİĞİYORSA hiçbir satır
    bırakılmaz: bütçedeki ayrılmış yer yalnız baskı altında satır seçimini
    belirler, sığan iletiyi kısaltmaz.
    """
    def size(texts: Sequence[str], units: int) -> int:
        """Gövde ``units`` birim olsaydı iletinin uzunluğu."""
        parts = (header, "x" * units, "\n".join(texts), *tail)
        return utf16_length(sep.join(part for part in parts if part))

    def everything_fits(texts: Sequence[str]) -> bool:
        """Gövdenin tamamı ve bütün satırlar GERÇEK uzunlukla sığıyor mu?"""
        parts = (header, body, "\n".join(texts), *(tail if real_tail is None else real_tail))
        actual = utf16_length(sep.join(part for part in parts if part))
        if actual > limit:
            return False
        parts = (header, "\n".join(texts), *(tail if real_tail is None else real_tail))
        return utf16_length(sep.join(part for part in parts if part)) < limit

    if real_tail is not None:
        everything = build(LINK_APPENDIX_LIMIT)
        if everything_fits([text for text, _ in everything[0]]):
            return "\n".join(text for text, _ in everything[0]), everything[1]

    def whole(texts: Sequence[str]) -> bool:
        """Gövdenin tamamı sığıyor mu? (Ek, gövde dışındaki yerin hepsini de doldurmamalı.)"""
        return size(texts, utf16_length(body)) <= limit and size(texts, 0) < limit

    def body_links(texts: Sequence[str]) -> set[str]:
        """Bu ekle birlikte gövdeye kalan yerden açılabilen adresler."""
        return _compose_body_links(cleaned_obj, body, max(0, limit - size(texts, 1) + 1))

    def fit(count: int) -> tuple[list[str], list[tuple[int, int, str]], int]:
        """En çok ``count`` ürün satırlı ek; ``🔗`` satırları 1. maddeye göre bırakılmış hâliyle."""
        lines, spans, plain = build(count)
        if size(plain, 0) >= limit:  # yalın düzen satırlarının hiçbirini yazamıyor
            plain = []
        texts = [text for text, _ in lines]
        ranks = [rank for _, rank in lines]
        for spare in (APPENDIX_SPARE, APPENDIX_REQUIRED)[:1 if plain else 2]:
            while spare in ranks and not whole(texts):
                last = len(ranks) - 1 - ranks[::-1].index(spare)
                del texts[last], ranks[last]
        return texts, spans, ranks.count(APPENDIX_PRODUCT)

    wanted: set[str] | None = None
    count = LINK_APPENDIX_LIMIT
    while True:
        texts, spans, count = fit(count)
        if not count or whole(texts):
            break
        # Gövde kırpılacak: ürün satırları, hiç yazılmasalardı gövdeden açılacak bağlantıyı kapatmamalı.
        if wanted is None:
            wanted = body_links(fit(0)[0]) & _compose_body_links(cleaned_obj, body, utf16_length(body))
        carried = visible_urls("\n".join(texts)) | {clean_url(url).rstrip("/").lower() for _, _, url in spans}
        if size(texts, 0) < limit and wanted <= body_links(texts) | carried:
            break
        count -= 1
    return "\n".join(texts), spans


def _compose_join(segments: Sequence[tuple[str, str]], sep: str) -> str:
    """Dolu segmentleri ayırıcı ile birleştir."""
    return sep.join(value for _, value in segments if value)


def _compose_other_than_body(segments: Sequence[tuple[str, str]], sep: str) -> str:
    """Gövde dışındaki parçaların birleşimini ver (limit kontrolü için)."""
    return _compose_join([part for part in segments if part[0] != "body" and part[1]], sep)


def _compose_drop_overflow_parts(
    segments: list[tuple[str, str]], appendix_text: str, name: str | None, source_line: str, limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str, str | None, str]:
    """Gövde hariç sığmama durumunda appendix/name/source sırasıyla düşer."""
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and appendix_text:
        segments = [(kind, "" if kind == "appendix" else value) for kind, value in segments]
        appendix_text = ""
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and name:
        segments = [(kind, "" if kind == "name" else value) for kind, value in segments]
        name = None
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and source_line:
        segments = [(kind, "" if kind == "source" else value) for kind, value in segments]
        source_line = ""
    return segments, appendix_text, name, source_line


def _compose_truncate_header(
    segments: list[tuple[str, str]], header_text: str, price_text: str, product_title: str, limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str, str, str]:
    """Üst satır hâlâ sığmıyorsa kalan oda ile kırp; fiyat korunur, ad kısalır."""
    if header_text and utf16_length(_compose_other_than_body(segments, sep)) >= limit:
        rest = _compose_join([part for part in segments if part[0] not in ("body", "header") and part[1]], sep)
        room = limit - utf16_length(rest) - (utf16_length(sep) if rest else 0) - utf16_length(sep)
        header_text = fit_header_line(price_text, product_title, room) if room >= 16 else ""
        price_text, product_title = split_header_line(header_text) if header_text else ("", "")
        segments = [(kind, header_text if kind == "header" else value) for kind, value in segments]
    return segments, header_text, price_text, product_title


def _compose_truncate_body(
    segments: list[tuple[str, str]], limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str]:
    """Gövde için kalan odaya göre kırp; yer yoksa boş bırak."""
    others_text = _compose_other_than_body(segments, sep)
    current_body = next((value for kind, value in segments if kind == "body"), "")
    gap = utf16_length(sep) if (current_body and others_text) else 0
    body_room = max(0, limit - utf16_length(others_text) - gap)
    body = _compose_cut_body(current_body, body_room)
    segments = [(kind, body if kind == "body" else value) for kind, value in segments]
    return segments, body


def _compose_assemble_text(segments: Sequence[tuple[str, str]], sep: str) -> tuple[str, str, dict[str, int], int, int]:
    """Segmentleri UTF-16 offset'lerle nihai metin ve content'e dönüştür."""
    offsets: dict[str, int] = {}
    parts: list[str] = []
    cursor = 0
    for kind, value in segments:
        if not value:
            continue
        if parts:
            parts.append(sep)
            cursor += utf16_length(sep)
        offsets.setdefault(kind, cursor)
        parts.append(value)
        cursor += utf16_length(value)
    text = "".join(parts)
    content = _compose_join([(kind, value) for kind, value in segments if kind != "name"], sep)
    name_offset = offsets["name"] if "name" in offsets else -1
    body_offset = offsets.get("body", 0)
    return text, content, offsets, body_offset, name_offset


def _compose_build_entities(
    header_text: str, body: str, body_entities: Sequence[Any], name: str | None,
    quote_body: bool, offsets: dict[str, int], body_offset: int, name_offset: int,
    appendix_links: Sequence[tuple[int, int, str]] = (),
    name_url: str | None = None,
) -> tuple[list[Any], list[Any]]:
    """Üst satır, alıntı, gövde ve ek linklerini + kaynak adı biçimini topla.

    İki liste döner: ``(içerik, kaynak adı)``. Kaynak adı bloğu Bot API'ye ayrı
    eklenir (``source_name_entity``); ayırmak, alanın metin dışına taşmadığı
    sürece iki listede de aynı biçimde durmasını sağlar.
    """
    content_entities: list[Any] = []
    if header_text and "header" in offsets:
        content_entities.append(types.MessageEntityBold(
            offset=offsets["header"], length=utf16_length(header_text),
        ))
    if quote_body and body and "body" in offsets:
        content_entities.append(types.MessageEntityBlockquote(
            offset=body_offset, length=utf16_length(body),
        ))
    content_entities.extend(_entities_within(
        shift_telethon_entities(body_entities, body_offset),
        body_offset + utf16_length(body),
    ))
    if "appendix" in offsets:
        content_entities.extend(
            types.MessageEntityTextUrl(offset=offsets["appendix"] + start, length=length, url=url)
            for start, length, url in appendix_links
        )
    name_entities: list[Any] = []
    if name_offset >= 0 and name:
        name_entities.append(types.MessageEntityBold(
            offset=name_offset, length=utf16_length(name),
        ))
        if name_url:
            # Kullanıcı isteği: "Mesajı Gör" satırı kalkar; orijinal mesajın
            # adresi en alttaki grup adının İÇİNE gömülür.
            name_entities.append(types.MessageEntityTextUrl(
                offset=name_offset, length=utf16_length(name), url=name_url,
            ))
    return content_entities, name_entities
