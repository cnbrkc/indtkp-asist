"""/analiz: mesaj geçmişi kelime analizi, başlık kelime ayıklama ve sorgu ayrıştırma (saf)."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import price_filter
from commandtext import telegram_list_items
from keywords import normalize
from producttitle import _SEARCH_PRICE_RE, _search_query
from sanitize import sent_message_ids, WORD_TOKEN_RE
from textutil import humanize

log = logging.getLogger("telegram-filter")


# Harf dizileri ve saf sayı dizileri (fiyat/adet sayıları ayrı yakalanır).
ANALYSIS_TOKEN_RE = re.compile(r"[0-9]+|[^\W\d_]+", re.UNICODE)

# İçerik taşımayan bağlaç/edat/zamirler: istatistikte anlamlı kelimeler kalsın.
STOP_WORDS = frozenset("""
acaba ama ancak artık asla ayrıca bana bazı belki ben beni benim beri bile bir
biraz birçok biri birkaç biz bize bizim bu buna bunda bundan bunu bunun burada
çok çünkü da daha değil diğer diye eğer en fakat falan filan gibi göre hangi
hatta hem hep hepsi her herhangi hiç için ile ise işte kadar kez ki kim kimi mı
mi mu mü nasıl ne neden nerede nereye niçin niye o olan olarak oldu olduğu olup
olsun öyle sadece sanki sen siz sonra şey şeyi şimdi şu şuna şunu tüm tümü üzere
var ve veya ya yani yine zaten
""".split())
# İlan/indirim kalıpları: neredeyse her mesajda geçtiği için varsayılan
# istatistikte gizlenir. Hepsini görmek için `/analiz tümü`.
DEAL_FILLER_WORDS = frozenset("""
adet adetli bedava bugün dahil fırsat fırsatı fırsatlar firsat güncel günün
hemen indirim indirimli kampanya kampanyası kargo kaçırma kaçmaz kupon link
links mevcut saat sepet sepette sipariş sınırlı stok stoğu stoklar tıkla tl try
ücretsiz
""".split())
ANALYSIS_STOP_WORDS = STOP_WORDS | DEAL_FILLER_WORDS


def message_title(text: str | None) -> str:
    """Mesajın başlığı: ilk boş olmayan satır (marka/ürün adının yazdığı yer)."""
    for line in (text or "").splitlines():
        title = line.strip()
        if title:
            return title
    return ""


def title_tokens(title: str, *, keep_everything: bool = False) -> list[str]:
    """Başlığı Türkçe duyarlı küçük harfe indirip kelimelere ayır.

    ``keep_everything=False`` iken saf sayılar (fiyat, adet, model kodu) atılır.
    """
    tokens: list[str] = []
    for raw in ANALYSIS_TOKEN_RE.findall(normalize(title)):
        if not keep_everything and raw.isdigit():
            continue
        tokens.append(raw)
    return tokens


def content_tokens(tokens: Sequence[str], *, keep_everything: bool = False) -> list[str]:
    """İşlevsiz kelimeleri, saf sayıları ve tek harfli gürültüyü ele.

    ``keep_everything=True`` iken hiçbiri elenmez (``/analiz tümü``).
    """
    if keep_everything:
        return list(tokens)
    return [token for token in tokens
            if len(token) > 1 and not token.isdigit() and token not in ANALYSIS_STOP_WORDS]


def chunk_text(text: str, limit: int = 3500) -> list[str]:
    """Metni satır sınırlarını bozmadan parçalara ayır."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # tek satır sınırdan uzunsa zorla böl
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Mesaj geçmişi kelime analizi (/analiz)
# ---------------------------------------------------------------------------
#
# Amaç: kaynaklarda geçmişe dönük mesajları okuyup **başlıkta** (mesajın ilk
# anlamlı satırı; marka/ürün adının yazdığı yer) en çok geçen kelimeleri
# göstermek. Böylece hiç almak istemediğin ürün türlerini bulup harici
# kelimeler listesine ekleyebilirsin. Yalnızca başlık taranır; açıklama, fiyat
# ve link satırları istatistiğe girmez.

ANALYSIS_DEFAULT_LIMIT = 300     # kaynak başına taranan mesaj sayısı
ANALYSIS_MIN_LIMIT = 20
ANALYSIS_MAX_LIMIT = 2000
ANALYSIS_TOP = 25                # her listede gösterilen kelime sayısı
ANALYSIS_QUERY_MAX_LINES = 200   # sorgu listesinde en fazla satır (spam'i önler)
# `/analiz tümü` yazıldığında kabul edilen kelimeler.
ANALYSIS_ALL_TOKENS = frozenset({
    "tumu", "tümü", "hepsi", "hepsini", "all", "hersey", "herşey", "filtresiz",
    "sinirsiz", "sınırsız",
})


def list_contains_token(items: Iterable[Any], token: str) -> bool:
    """Kelime, verilen listedeki bir kaydın (ya da kaydın bir parçasının) içinde mi?"""
    for item in items:
        if token in normalize(str(item)).split():
            return True
    return False


def format_ranking(counter: Any, config: dict, top: int = ANALYSIS_TOP) -> list[str]:
    """Frekans tablosunu numaralı satırlara çevir; listede olanları işaretle."""
    exclude = telegram_list_items(config, "exclude_keywords")
    include = telegram_list_items(config, "include_keywords")
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:top]
    lines: list[str] = []
    for index, (word, count) in enumerate(ranked, start=1):
        mark = ""
        if list_contains_token(exclude, word):
            mark = " 🚫"
        elif list_contains_token(include, word):
            mark = " 🔎"
        lines.append(f"{index:02d}. {count:>4}×  {word}{mark}")
    return lines or ["(kelime bulunamadı)"]


def build_analysis_report(
    analysis: dict[str, Any],
    *,
    config: dict,
    sources: int,
    per_source: int,
    keep_everything: bool = False,
    elapsed: float = 0.0,
    failures: Sequence[str] = (),
) -> str:
    """Analiz sonucunu Telegram'da okunaklı iki istatistiğe çevir."""
    counts = analysis["counts"]
    lines = [
        "📊  BAŞLIK KELİME ANALİZİ",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Kaynak: {sources} grup · kaynak başına son {per_source} mesaj",
        f"• Taranan başlık: {counts['titles']} · tekil: {counts['unique']}"
        + (f" (tekrar birleştirildi: {counts['duplicates']})" if counts["duplicates"] else ""),
        f"• Süre: {humanize(elapsed)}",
    ]
    if keep_everything:
        lines.append("• Kapsam: TÜM kelimeler ve sayılar (filtre kapalı)")
    else:
        filtered = f" ({counts['filtered']} kelime)" if counts["filtered"] else ""
        lines.append(f"• Kapsam: işlevsiz kelimeler ve saf sayılar elendi{filtered}"
                     " · hepsi için: /analiz tümü")
    if failures:
        lines.append(f"• ⚠️ Okunamayan kaynak: {', '.join(failures)}")

    lines += ["", f"1️⃣ EN ÇOK GEÇEN {ANALYSIS_TOP} KELİME (başlıktaki tüm kelimeler)"]
    lines += format_ranking(analysis["words"], config)
    lines += ["", f"2️⃣ EN ÇOK GEÇEN {ANALYSIS_TOP} İLK KELİME (başlığın ilk kelimesi)"]
    lines += format_ranking(analysis["first_words"], config)
    lines += [
        "",
        "🚫 = harici listende · 🔎 = dahili listende",
        "İstemediğin kelimeleri harici listeye ekle: /cikar → 2 (Harici kelimeler)",
        "Örnek: bebek, oyuncak, kitap — birden çok kelimeyi virgülle ayırabilirsin.",
    ]
    return "\n".join(lines)


def parse_analysis_request(raw: str) -> tuple[int, bool, str]:
    """``/analiz [adet] [tümü] [sorgu]`` argümanlarını çöz.

    Döner: (mesaj sayısı, filtre kapalı mı, sorgu metni; yoksa "").
    Mesaj sayısı yalnızca argümanların BAŞINDA olabilir; ilk kelimeden sonra
    gelen sayılar sorgunun parçasıdır (``/analiz 1000 tcl 65`` → sorgu
    ``tcl 65``). ``tümü`` nerede geçerse geçsin filtreyi kapatır.
    """
    limit = ANALYSIS_DEFAULT_LIMIT
    keep_everything = False
    limit_set = False
    query_words: list[str] = []
    for token in str(raw or "").split():
        word = token.strip(",;")
        key = normalize(word)
        if not key:
            continue
        if key in ANALYSIS_ALL_TOKENS:
            keep_everything = True
            continue
        if not query_words and not limit_set and key.isdigit():
            limit = max(ANALYSIS_MIN_LIMIT, min(ANALYSIS_MAX_LIMIT, int(key)))
            limit_set = True
            continue
        query_words.append(word)
    return limit, keep_everything, " ".join(query_words)


def parse_analysis_args(raw: str) -> tuple[int, bool]:
    """``/analiz [adet] [tümü]`` argümanlarını çöz: (mesaj sayısı, filtre kapalı mı).

    Sorgu kelimesi varsa ``ValueError`` yükseltir (sorgulu kullanım için
    ``parse_analysis_request`` kullanılır).
    """
    limit, keep_everything, query = parse_analysis_request(raw)
    if query:
        raise ValueError(query)
    return limit, keep_everything


def analysis_query_pattern(query: str) -> Any:
    """Sorguyu Türkçe duyarlı, kelime sınırlı bir desene çevir (``None``: boş)."""
    words = normalize(query).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    # Sınır: sorgudan hemen önce/sonra harf veya rakam gelmemeli
    # ("tcl 65" → "tcl 655" eşleşmez).
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])", re.UNICODE)


# Ürün adı tam gösterilir (kullanıcı isteği: "tam isim alsın, orayı kesti burayı
# kesti derdi olmaz"); yalnızca aşırı uzun tek satırlar bu sınırda kesilir.
ANALYSIS_TITLE_LIMIT = 300
# Bir satırın "fiyat satırı" sayılması için fiyat dışında kalan en fazla kelime.
# ("🏷 3.558 TL" evet · "Ürün sayfasındaki 100 TL kupon ..." hayır.)
ANALYSIS_PRICE_LINE_MAX_WORDS = 4


def tidy_product_title(title: str) -> str:
    """Ürün adındaki fiyat/ayraç artıklarını toparla (``için . `` → ``için.``)."""
    cleaned = re.sub(r"\s+", " ", title or "")
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:])\s*(?=[,;:])", r"\1", cleaned)
    return cleaned.strip(" \t,;:.|/\\-–—·•")


def query_product_title(text: str) -> str:
    """Sorgu listesi için ürün adı: başlık adayı, tam metniyle (kısaltılmaz).

    "TCL 65 inç Akıllı TV 65C7K 4K QD-Mini LED …" gibi model kodlu uzun
    başlıklar kesilmez; yalnızca fiyat/noise temizliğinden kalan boşluk ve
    noktalama artıkları toparlanır.
    """
    return tidy_product_title(_search_query(text, limit=ANALYSIS_TITLE_LIMIT)
                              or message_title(text))


# Fiyat satırı sayılan ama ürün fiyatı olmayan satırlar: taksit tutarı, "200 TL
# üzeri" eşiği, kupon/çekiliş tutarı.
_ANALYSIS_PRICE_LINE_SKIP_RE = re.compile(
    r"(?i)\btaksit\w*\b|\büzeri\b|\buzeri\b|\bkupon\w*\b|\bcoupon\b|\bçekiliş\w*\b",
)


def parse_price_value(raw: str) -> float | None:
    """``31.866 TL`` / ``5.999,90`` / ``99,90`` → sayı (Türkçe yazım)."""
    digits = "".join(char for char in str(raw or "") if char.isdigit() or char in ".,")
    if not digits:
        return None
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):   # 1.299,90
            digits = digits.replace(".", "").replace(",", ".")
        else:                                       # 1,299.90
            digits = digits.replace(",", "")
    elif "." in digits:
        tail = digits.rpartition(".")[2]
        if digits.count(".") > 1 or len(tail) == 3:  # 1.299 / 31.866 → binlik ayraç
            digits = digits.replace(".", "")
    elif "," in digits:
        tail = digits.rpartition(",")[2]
        if digits.count(",") > 1 or len(tail) == 3:
            digits = digits.replace(",", "")
        else:                                       # 99,90 → ondalık
            digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def _price_line_extra_words(line: str) -> int:
    """Fiyatı çıkarınca satırda kalan (sayı olmayan) kelime sayısı."""
    rest = _SEARCH_PRICE_RE.sub(" ", line or "")
    return len([token for token in WORD_TOKEN_RE.findall(rest) if not token.isdigit()])


def query_price(text: str) -> str:
    """Mesajdan ürün fiyatını seç.

    Öncelik fiyat satırlarındadır (``🏷 3.558 TL``, ``💰 Fiyat : 225 TL``); bunlar
    arasından **en düşük** değer alınır, böylece "eski 44.999 TL / yeni 31.866 TL"
    ikilisinde indirimli fiyat görünür. Taksit tutarı, "200 TL üzeri" eşiği ve
    kupon/çekiliş tutarı ürün fiyatı sayılmaz. Fiyat satırı yoksa mesajdaki
    fiyatların en düşüğü, hiç fiyat yoksa ``fiyat yok`` döner.
    """
    if price_filter.has_campaign_marker(text):
        # Kampanya izi varsa analiz, bildirimdeki fiyatla aynı kararı kullanır: kampanya
        # tutarı ya da belirsiz çoklu tutar analizde farklı bir fiyat olarak görünmez.
        info = price_filter.extract_price(text)
        if info.value is not None:
            return info.shown
        if info.reason.startswith("kampanya"):
            return "fiyat yok"
    listed: list[tuple[float, str]] = []
    plain: list[tuple[float, str]] = []
    fallback: list[str] = []
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        prev_line = lines[index - 1] if index else None
        if price_filter.ambiguous_campaign_line(line):
            continue  # aynı satırda kampanya + etiketsiz tutar: hangisi ürün fiyatı belirsiz
        skipped = bool(_ANALYSIS_PRICE_LINE_SKIP_RE.search(line))
        on_price_line = _price_line_extra_words(line) <= ANALYSIS_PRICE_LINE_MAX_WORDS
        for match in _SEARCH_PRICE_RE.finditer(line):
            if price_filter.is_campaign_extra_amount(line, match.start(), match.end(), prev_line):
                continue  # "2. ürün 1 TL" gibi kampanya koşulu: ürün fiyatı değil
            shown = " ".join(match.group().split())
            value = parse_price_value(match.group())
            if value is None or skipped:
                fallback.append(shown)  # taksit/kupon tutarı: yalnızca son çare
                continue
            (listed if on_price_line else plain).append((value, shown))
    if listed:
        return min(listed, key=lambda item: item[0])[1]
    if plain:
        return min(plain, key=lambda item: item[0])[1]
    return fallback[0] if fallback else "fiyat yok"


ANALYSIS_SCAN_START = "🔎 Analiz başladı"
ANALYSIS_SCAN_RUNNING = "🔎 Geçmiş taranıyor"


async def start_analysis_scan(event: Any, targets: Sequence[Any], per_source: int,
                              *, purpose: str = "", query: str = "") -> Any:
    """``Analiz başladı`` ilerleme mesajını gönder (gönderilemezse ``None``).

    Mesaj ``update_analysis_scan`` ile kaynak kaynak güncellenir; rapor gelince
    ``control_reply(..., stale=[...])`` ile silinir, böylece ekranda yalnızca
    sonuç kalır. Uzun taramalarda kullanıcı çalıştığını bu mesajdan görür.
    """
    lines = [f"{ANALYSIS_SCAN_START}: {len(targets)} kaynak × son {per_source} mesaj taranıyor…"]
    if query:
        lines.append(f"• Sorgu: '{query}'")
    if purpose:
        lines.append(purpose)
    try:
        return await event.reply("\n".join(lines))
    except Exception:  # noqa: BLE001 - yanıt gitmese de tarama sürsün
        log.debug("Analiz başlangıç yanıtı gönderilemedi.")
        return None


def analysis_status_id(status: Any) -> int:
    """İlerleme mesajının ID'si (yoksa 0 → silinecek mesaj yok)."""
    return next(iter(sent_message_ids(status)), 0)


async def update_analysis_scan(status: Any, done: int, total: int, detail: str) -> None:
    """İlerleme mesajını güncelle; düzenleme başarısızsa tarama devam eder."""
    if status is None:
        return
    try:
        await status.edit(f"{ANALYSIS_SCAN_RUNNING}... {done}/{total} kaynak · {detail}")
    except Exception:  # noqa: BLE001 - düzenleme başarısızsa tarama sürsün
        pass


def build_query_lines(texts: Iterable[str], pattern: Any) -> list[str]:
    """Desenle eşleşen mesajları ``ürün adı - fiyat`` satırlarına çevir.

    Ürün adı, ürün başlığı seçicisiyle (``_search_query``) bulunur ve tam
    gösterilir ("TCL 65 inç Akıllı TV 65C7K …" gibi sonunda model kodu olan
    başlıklar kırpılmaz). Fiyat ``query_price`` ile seçilir. Aynı satır birden
    çok kaynakta varsa bir kez yazılır.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text or not pattern.search(normalize(text)):
            continue
        title = " ".join(query_product_title(text).split()) or "(başlıksız)"
        line = f"{title} - {query_price(text)}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def analyze_titles(titles: Iterable[str], *, keep_everything: bool = False) -> dict[str, Any]:
    """Başlıkları tara; kelime ve ilk kelime frekanslarını çıkar.

    Aynı başlık birden çok kanaldan gelmişse bir kez sayılır (tekrar
    birleştirme); böylece tek bir kampanya tüm istatistiği bozmaz.
    """
    words: Counter[str] = Counter()
    first_words: Counter[str] = Counter()
    seen: set[str] = set()
    counts = {"titles": 0, "unique": 0, "duplicates": 0, "filtered": 0}
    for title in titles:
        text = (title or "").strip()
        if not text:
            continue
        counts["titles"] += 1
        key = normalize(text)
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)
        counts["unique"] += 1
        # Sayılar ve işlevsiz kelimeler "elenen" sayılır: kapsam notu bunu gösterir.
        tokens = title_tokens(text, keep_everything=True)
        kept = content_tokens(tokens, keep_everything=keep_everything)
        counts["filtered"] += len(tokens) - len(kept)
        words.update(kept)
        if kept:
            first_words[kept[0]] += 1
    return {"counts": counts, "words": words, "first_words": first_words}
