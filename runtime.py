"""main() içinde kurulan ve ayrı modüllerdeki işlevlere geçirilen çalışma zamanı bağlamı.

Eskiden bu değerler ``main()`` içindeki iç işlevlerin kapanış (closure) değişkenleriydi.
Şimdi işlevler modül düzeyinde durur ve ilk parametre olarak ``rt`` alır; böylece her
işlev neye bağlı olduğunu açıkça gösterir ve tek başına incelenebilir.

Ayarlar, sayaçlar ve önbellekler ``appstate`` modülündedir; ``rt`` yalnızca ``main()`` içinde
kurulan nesneleri taşır.
"""

from __future__ import annotations

from typing import Any


class Runtime:
    """Çalışan sürecin paylaşılan nesneleri (``main()`` doldurur)."""

    def __init__(self) -> None:
        self.client: Any = None                # Telethon istemcisi (kullanıcı hesabı)
        self.store: Any = None                 # ConfigStore: config.json okuma/yazma
        self.tracker: Any = None               # Akakçe ürün izleyicisi (yoksa None)
        self.private_api: Any = None           # Bot API istemcisi (özel sohbet; yoksa None)
        self.private_offers: Any = None        # kişisel fırsat (DM) kuyruğu
        self.senders: dict[str, Any] = {}      # teslim yöntemi adı -> gönderici işlev
        self.account_send_lock: Any = None     # hesap üzerinden gönderimi sıraya sokan kilit
        self.settings_lock: Any = None         # ayar değişikliklerini sıraya sokan kilit
        self.gh_pat: Any = None                # GH_PAT secret'ı (yoksa None)
        self.poll_delivery_tasks: list[Any] = []  # hızlı taramanın başlattığı teslim görevleri
