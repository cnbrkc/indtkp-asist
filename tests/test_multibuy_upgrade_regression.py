"""Regresyon: is_referenced_multibuy_upgrade helper ayrışımı davranış korumalı.

Kapsam: multi-buy örnekleri, tekil teklif, çoklu ürün, eski/yeni fiyat karşılaştırması,
eksik/bozuk veri, sınır/eşit fiyat, kimlik eşleşme/uyuşmazlığı, offer_terms/plain_offer varyantları.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bot
import price_filter as pf
from telethon.tl import types

TITLE = "Hayat Doğal Mineralli Su 6x1,5 L"
NOW = 1000000.0


def msg(text, entities=(), buttons=()):
    markup = SimpleNamespace(rows=[SimpleNamespace(buttons=buttons)]) if buttons else None
    return SimpleNamespace(message=text, entities=list(entities), media=None, reply_markup=markup)


def base_entry(**over):
    offer = bot.offer_terms(msg(TITLE + "\n🏷 79 TL"))
    d = {
        "offer": offer,
        "plain_offer": True,
        "first_seen": NOW - 10,
        "title": TITLE,
        "editable": "bot",
        "bot_message_id": 1,
        "product_asins": set(),
    }
    d.update(over)
    return d


class EntryEligibleTests(unittest.TestCase):
    def test_preload_blocks(self):
        e = base_entry(preload=True)
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))

    def test_plain_offer_required(self):
        e = base_entry(plain_offer=False)
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))

    def test_failed_and_pending_block(self):
        for k in ("failed", "pending"):
            e = base_entry(**{k: True})
            self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))

    def test_expired_and_future_first_seen_block(self):
        e = base_entry(first_seen=NOW - 200)
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))
        e2 = base_entry(first_seen=NOW + 10)
        self.assertFalse(bot._multibuy_entry_eligible(e2, TITLE, (71, ""), NOW))

    def test_dedup_key_mismatch_blocks(self):
        e = base_entry(title="Bambaşka Ürün XYZ")
        self.assertFalse(bot._multibuy_entry_eligible(e, "Diğer Başlık", (71, ""), NOW))

    def test_missing_old_or_offer_blocks(self):
        e = base_entry(offer=None)
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))
        e = base_entry(offer=(None, ""))
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))
        e = base_entry()
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, None, NOW))
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (None, ""), NOW))

    def test_better_or_different_offer_gate(self):
        # equal price -> better_or_different_offer False (whole-TL tolerance) -> blocked
        e = base_entry()
        self.assertFalse(bot._multibuy_entry_eligible(e, TITLE, (79, ""), NOW))
        # lower price -> allowed (eligible part passes)
        self.assertTrue(bot._multibuy_entry_eligible(e, TITLE, (71, ""), NOW))


class EditableTests(unittest.TestCase):
    def test_bot_needs_int(self):
        self.assertFalse(bot._multibuy_editable_valid({"editable": "bot", "bot_message_id": "1"}))
        self.assertTrue(bot._multibuy_editable_valid({"editable": "bot", "bot_message_id": 1}))

    def test_account_needs_int(self):
        self.assertFalse(bot._multibuy_editable_valid({"editable": "account", "account_message_id": None}))
        self.assertTrue(bot._multibuy_editable_valid({"editable": "account", "account_message_id": 5}))

    def test_unknown_editable(self):
        self.assertFalse(bot._multibuy_editable_valid({"editable": None}))
        self.assertFalse(bot._multibuy_editable_valid({}))


class BlockedTests(unittest.TestCase):
    def test_condition_blocks(self):
        e = base_entry()
        used = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nİkinci El")
        self.assertTrue(bot._multibuy_blocked_by_condition_or_asin(e, used))
        # other condition keywords
        for kw in ["açık kutu", "yenilenmiş", "refurbished"]:
            self.assertTrue(bot._multibuy_blocked_by_condition_or_asin(e, msg(TITLE + f"\n{kw}")))

    def test_asin_mismatch_blocks_only_when_both_present(self):
        e = base_entry(product_asins={"B09RMSLVS8"})
        same = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nhttps://www.amazon.com/dp/B09RMSLVS8")
        diff = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nhttps://www.amazon.com/dp/B0XXXXYYYY")
        empty = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL")
        # same ASINs -> not blocked
        self.assertFalse(bot._multibuy_blocked_by_condition_or_asin(e, same))
        # mismatched ASINs -> blocked
        self.assertTrue(bot._multibuy_blocked_by_condition_or_asin(e, diff))
        # no ASINs -> not blocked
        self.assertFalse(bot._multibuy_blocked_by_condition_or_asin(e, empty))
        # old empty -> never blocked by ASIN
        e2 = base_entry(product_asins=set())
        self.assertFalse(bot._multibuy_blocked_by_condition_or_asin(e2, diff))


class PriceStructureTests(unittest.TestCase):
    def test_valid_structure(self):
        e = base_entry()
        offer = bot.offer_terms(msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL"))
        # offer must be multi_unit price (71)
        obj = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL")
        self.assertTrue(bot._multibuy_price_structure_valid(e, obj, offer))

    def test_single_offer_no_multibuy_fails(self):
        e = base_entry()
        single = msg(TITLE + "\n🏷 79 TL")
        offer = bot.offer_terms(single)
        self.assertFalse(bot._multibuy_price_structure_valid(e, single, offer))

    def test_coupon_like_role_blocks(self):
        e = base_entry()
        offer = (71, "")
        # Inject a candidate with basket_coupon via mock
        text = TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL"
        # use real candidates but patch to add basket_coupon
        real_cands = pf.find_candidates(text, set())
        with patch.object(pf, "find_candidates", return_value=real_cands + [SimpleNamespace(
            cls="price", role="basket_coupon", value=10, line=0, strong=False
        )]):
            obj = msg(text)
            self.assertFalse(bot._multibuy_price_structure_valid(e, obj, offer))

    def test_multiple_normal_or_multi_values_fail(self):
        e = base_entry()
        offer = (71, "")
        # craft two different normal values -> should fail len(normal_values)==1
        cands = [
            SimpleNamespace(cls="price", role="normal", value=79, line=0, strong=True),
            SimpleNamespace(cls="price", role="normal", value=80, line=0, strong=True),
            SimpleNamespace(cls="price", role="multi_unit", value=71, line=0, strong=False),
        ]
        with patch.object(pf, "find_candidates", return_value=cands):
            with patch.object(pf, "strike_spans_from_entities", return_value=set()):
                obj = msg("dummy")
                self.assertFalse(bot._multibuy_price_structure_valid(e, obj, offer))

    def test_wrong_line_fails(self):
        e = base_entry()
        offer = (71, "")
        cands = [
            SimpleNamespace(cls="price", role="normal", value=79, line=0, strong=True),
            SimpleNamespace(cls="price", role="multi_unit", value=71, line=1, strong=False),
        ]
        with patch.object(pf, "find_candidates", return_value=cands):
            with patch.object(pf, "strike_spans_from_entities", return_value=set()):
                self.assertFalse(bot._multibuy_price_structure_valid(e, msg("dummy"), offer))

    def test_whole_tl_tolerance(self):
        # 79 vs 79.10 same whole TL -> base match via _dedup_whole_tl
        e = {"offer": (79, ""), "product_asins": set()}
        offer = (71, "")
        cands = [
            SimpleNamespace(cls="price", role="normal", value=79.10, line=0, strong=True),
            SimpleNamespace(cls="price", role="multi_unit", value=71, line=0, strong=False),
        ]
        with patch.object(pf, "find_candidates", return_value=cands):
            with patch.object(pf, "strike_spans_from_entities", return_value=set()):
                self.assertTrue(bot._multibuy_price_structure_valid(e, msg("dummy"), offer))


class IntegrationTests(unittest.TestCase):
    def test_integration_happy_path(self):
        e = base_entry()
        obj = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL")
        offer = bot.offer_terms(obj)
        self.assertTrue(bot.is_referenced_multibuy_upgrade(e, TITLE, obj, offer, NOW))

    def test_integration_secondary_blocks(self):
        e = base_entry()
        used = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nİkinci El")
        offer = bot.offer_terms(used)
        self.assertFalse(bot.is_referenced_multibuy_upgrade(e, TITLE, used, offer, NOW))

    def test_integration_missing_data_returns_false(self):
        e = {}
        self.assertFalse(bot.is_referenced_multibuy_upgrade(e, TITLE, msg("boş"), None, NOW))

    def test_integration_asin_identity(self):
        e = base_entry(product_asins={"B09RMSLVS8"})
        obj_match = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nhttps://www.amazon.com/dp/B09RMSLVS8")
        obj_mismatch = msg(TITLE + "\nFiyat: 79 TL / 13 adet alımda 71 TL\nhttps://www.amazon.com/dp/B0XXXXYYYY")
        offer = bot.offer_terms(obj_match)
        self.assertTrue(bot.is_referenced_multibuy_upgrade(e, TITLE, obj_match, offer, NOW))
        self.assertFalse(bot.is_referenced_multibuy_upgrade(e, TITLE, obj_mismatch, offer, NOW))


if __name__ == "__main__":
    unittest.main()
