"""--check: secret, ayar ve kaynak doğrulama raporu."""

from __future__ import annotations

import logging
import os

import composer
import dm_filters
import price_filter
from confutil import chat_values, config_flag, link_appendix_mode, parse_admin_ids, parse_chat_value
from constants import PRODUCT_LINK_TEXT
from keywords import canonical_match_mode, filter_state_of, match_mode_of

log = logging.getLogger("telegram-filter")


def _check_env_secrets(problems: list[str]) -> None:
    """API_ID, API_HASH ve SESSION_STRING secret'larını doğrular; sorunları `problems` listesine ekler."""
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_string = os.getenv("SESSION_STRING", "").strip()
    if not api_id:
        problems.append("API_ID secret'ı boş. Settings → Secrets and variables → Actions → Secrets sekmesine ekle.")
    else:
        try:
            int(api_id)
        except ValueError:
            problems.append(f"API_ID sayı olmalı, gelen değer okunamadı (uzunluk {len(api_id)}).")
    if not api_hash:
        problems.append("API_HASH secret'ı boş. my.telegram.org → API development tools'dan alıp secret olarak ekle.")
    elif len(api_hash) != 32:
        problems.append(f"API_HASH 32 karakter olmalı, gelen değer {len(api_hash)} karakter.")
    if not session_string:
        problems.append("SESSION_STRING secret'ı boş. generate_session.py ile üretip secret olarak ekle.")
    if not os.getenv("GH_PAT", "").strip():
        log.warning("GH_PAT boş: otomatik yenileme zinciri çalışmaz, workflow'u elle başlatman gerekir.")


def _check_config_flags_and_dm(config: dict, problems: list[str]) -> None:
    """config.json bayraklarını ve kişisel (DM) ayarlarını doğrular."""
    if not isinstance(config.get("private_control", False), bool):
        problems.append("config.json → private_control true/false olmalı.")
    if not isinstance(config.get("dm_enabled", False), bool):
        problems.append("config.json → dm_enabled true/false olmalı.")
    for field in ("dm_keywords", "dm_exclude_keywords"):
        dm_words = config.get(field, [])
        if (not isinstance(dm_words, list) or len(dm_words) > 100
                or any(not isinstance(w, str) or not w.strip() or len(w) > 100 for w in dm_words)):
            problems.append(f"config.json → {field} en fazla 100 dolu metinden oluşmalı (her biri ≤100 karakter).")
    price_items = config.get("dm_price_max", [])
    if not isinstance(price_items, list) or len(price_items) > 1 or any(
        price_filter.parse_threshold(item) is None for item in price_items
    ):
        problems.append("config.json → dm_price_max en fazla 1 sayı içermeli (örn. [\"10000\"]).")
    product_price_items = config.get(dm_filters.DM_PRODUCT_PRICE_FIELD, [])
    invalid_product_price_items = not isinstance(product_price_items, list) or len(product_price_items) > 100
    seen_product_price_rules: set[tuple[str, float]] = set()
    if isinstance(product_price_items, list):
        for item in product_price_items:
            try:
                rule = dm_filters.parse_product_price_rule(item)
            except (TypeError, ValueError):
                invalid_product_price_items = True
                break
            if rule.identity in seen_product_price_rules:
                invalid_product_price_items = True
                break
            seen_product_price_rules.add(rule.identity)
    if invalid_product_price_items:
        problems.append(
            "config.json → dm_product_price_rules en fazla 100 geçerli, yinelenmeyen "
            "'ürün adı < fiyat' kuralı içermeli."
        )
    for flag in ("smart_header", "fast_notify", "fast_poll"):
        if not isinstance(config.get(flag, False), bool):
            problems.append(f"config.json → {flag} true/false olmalı.")
    if config.get("private_control") and not os.getenv("NOTIFY_BOT_TOKEN", "").strip():
        log.warning("Özel komutlar için NOTIFY_BOT_TOKEN gerekli; Kayıtlı Mesajlar yedek kontrol olarak açık.")


def _check_sources_and_modes(config: dict, problems: list[str]) -> None:
    """Kaynak listesini, eşleşme modunu ve iletim modlarını doğrular."""
    sources = chat_values(config.get("source_chats"))
    if not sources:
        problems.append("config.json → source_chats boş. En az bir kanal/grup eklenmeli.")
    if canonical_match_mode(config.get("match_mode", "any")) is None:
        problems.append(
            "config.json → match_mode yalnızca 'any', 'all' veya 'forward_all' olabilir "
            "(forward_all, dahili kelimeleri yok sayar; hariç kelimeler yine engeller)."
        )
    if str(config.get("copy_mode", "forward")).lower() not in {"forward", "copy"}:
        problems.append("config.json → copy_mode yalnızca 'forward' veya 'copy' olabilir.")
    explicit_modes = config.get("delivery_modes")
    if explicit_modes is not None:
        if not isinstance(explicit_modes, (list, tuple)) or not explicit_modes:
            problems.append("config.json → delivery_modes boş olmayan bir liste olmalı.")
        else:
            unknown = [str(m) for m in explicit_modes if str(m).strip().lower() not in composer.DELIVERY_MODES]
            if unknown:
                problems.append(
                    "config.json → delivery_modes içinde bilinmeyen değer var: "
                    f"{unknown} (geçerli: {', '.join(composer.DELIVERY_MODES)})"
                )


def _check_limits_and_destination(config: dict, problems: list[str]) -> bool:
    """Sayısal sınırları, hedefi ve kontrol sohbetini doğrular; kontrol sohbeti bir grup mu (True) döner."""
    try:
        if int(config.get("max_media_mb", 25)) < 0:
            problems.append("config.json → max_media_mb negatif olamaz.")
    except (TypeError, ValueError):
        problems.append("config.json → max_media_mb bir sayı olmalı.")
    try:
        window = int(config.get("dedup_window_hours", 12))
        if not 1 <= window <= 72:
            problems.append("config.json → dedup_window_hours 1–72 saat arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_window_hours bir sayı olmalı.")
    try:
        scan = int(config.get("dedup_scan_limit", 100))
        if not 0 <= scan <= 100:
            problems.append("config.json → dedup_scan_limit 0–100 arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_scan_limit bir sayı olmalı.")
    try:
        parse_chat_value(config.get("destination", "me"))
    except ValueError as exc:
        problems.append(f"config.json → destination geçersiz: {exc}")
    control = config.get("control_chat", "me")
    control_values = list(control) if isinstance(control, (list, tuple)) else [control]
    group_control = False
    for value in control_values:
        try:
            if parse_chat_value(value) != "me":
                group_control = True
        except ValueError as exc:
            problems.append(f"config.json → control_chat geçersiz: {exc}")
    return group_control


def check_environment(config: dict | None = None) -> list[str]:
    """Eksik/hatalı ayarları toplar. Boş liste = her şey hazır."""
    problems: list[str] = []
    _check_env_secrets(problems)
    if config is None:
        return problems
    _check_config_flags_and_dm(config, problems)
    _check_sources_and_modes(config, problems)
    group_control = _check_limits_and_destination(config, problems)
    if group_control and not parse_admin_ids(config.get("admin_user_id")):
        problems.append(
            "config.json → control_chat bir grup ama admin_user_id boş. "
            "Grup komutları kimse tarafından kullanılamaz; kendi kullanıcı ID'ni yaz."
        )
    return problems


def print_report(config: dict, problems: list[str]) -> None:
    """Actions log'unda okunabilir bir açılış raporu bırak (sır değeri basmaz)."""
    def mask(name: str) -> str:
        return "var" if os.getenv(name, "").strip() else "YOK"

    print("=" * 62, flush=True)
    print("Telegram indirim takipçisi - yapılandırma raporu", flush=True)
    print("=" * 62, flush=True)
    print(f"Ortam değişkenleri : API_ID={mask('API_ID')} API_HASH={mask('API_HASH')} "
          f"SESSION_STRING={mask('SESSION_STRING')} GH_PAT={mask('GH_PAT')} "
          f"NOTIFY_BOT_TOKEN={mask('NOTIFY_BOT_TOKEN')}", flush=True)
    print(f"Kaynaklar          : {len(config.get('source_chats') or [])} adet", flush=True)
    print(f"Hedef              : {config.get('destination', 'me')}", flush=True)
    control_label = ("Bot özel sohbeti + Kayıtlı Mesajlar"
                     if config_flag(config.get("private_control"), False)
                     else config.get("control_chat", "me"))
    print(f"Kontrol sohbeti    : {control_label}", flush=True)
    print(f"Admin ID'leri      : {sorted(parse_admin_ids(config.get('admin_user_id'))) or 'tanımsız'}", flush=True)
    filter_mode = match_mode_of(config)
    filter_label = {
        "any": "any (biri yeterli)",
        "all": "all (hepsi zorunlu)",
        "forward_all": "forward_all (dahili kelimeler yok sayılır)",
    }[filter_mode]
    print(f"Anahtar kelimeler  : {config.get('include_keywords') or '(hepsi)'} ({filter_label})", flush=True)
    print(f"Hariç kelimeler    : {config.get('exclude_keywords') or '(yok)'}", flush=True)
    filter_state = filter_state_of(config)
    print(f"Filtre durumu      : dahili {'AÇIK' if filter_state['include'] else 'KAPALI'} | "
          f"harici {'AÇIK' if filter_state['exclude'] else 'KAPALI'} "
          f"(/tgfiltreac ve /tgfiltrekapat ile değiştirilir)", flush=True)
    print(f"İletim sırası      : {' → '.join(composer.build_delivery_chain(config))}", flush=True)
    print(f"Medya sınırı       : {config.get('max_media_mb', 25)} MB", flush=True)
    mode = link_appendix_mode(config)
    mode_label = {"smart": f"akıllı ({PRODUCT_LINK_TEXT})", "all": "tüm gizli linkler", "off": "kapalı"}[mode]
    print(f"Bağlantı ekleri     : {mode_label} "
          f"| mesaj linki (grup adına gömülü): {'açık' if config_flag(config.get('message_link')) else 'kapalı'} "
          f"| bildirim medyası: {'açık' if config_flag(config.get('notify_media')) else 'kapalı'} "
          f"| tek mesaj: {'açık' if config_flag(config.get('single_message')) else 'kapalı'} "
          f"| kaynak adı (kalın, mesaja bağlı): {'açık' if config_flag(config.get('source_footer')) else 'kapalı'} "
          f"| komut temizliği: {'açık' if config_flag(config.get('clean_commands')) else 'kapalı'} "
          f"| alıntı temizliği: {'açık' if config_flag(config.get('clean_quote_duplicates'), False) else 'kapalı'}", flush=True)
    try:
        window_hours = int(config.get("dedup_window_hours", 12))
    except (TypeError, ValueError):
        window_hours = 12
    try:
        scan_limit = int(config.get("dedup_scan_limit", 100))
    except (TypeError, ValueError):
        scan_limit = 100
    print(f"Tekrar birleştirme : {'açık' if config_flag(config.get('dedup_enabled'), True) else 'kapalı'} "
          f"(pencere: {window_hours} sa · açılış taraması: son {scan_limit} mesaj)", flush=True)
    print(f"Otomatik yenileme  : {config.get('auto_restart', True)} "
          f"({os.getenv('RESTART_AFTER_MINUTES', '330')} dk sonra)", flush=True)
    if problems:
        print("-" * 62, flush=True)
        print("SORUNLAR:", flush=True)
        for problem in problems:
            print(f"  ✗ {problem}", flush=True)
    else:
        print("Sonuç              : yapılandırma geçerli ✅", flush=True)
    print("=" * 62, flush=True)
