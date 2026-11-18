"""Ürün başlığı kelime ayıklama (başlık → anlamlı kelimeler; saf)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from keywords import normalize


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
