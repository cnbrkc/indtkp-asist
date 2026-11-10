"""Reported text, entity and non-regression boundaries, offline only."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from telethon.tl import types
import bot
import price_filter as pf

FROPIE = ('✅ Fropie Yer Fıstığı Ezmesi 630 gr - %100 Yer Fıstığı Glutensiz ve Vegan Uyumlu Sağlıklı Atıştırmalık\n\n'
          '🏷 229 TL — 2 adet alımda sepette "TADIM100" indirim kodunu uygulayın. (3 adet alımda adet 101 TL).\n\n'
          '👉 FIRSATA GİT !\n\n🛍 Hepsiburada')
SERVET = ('Servet Şeker Ilavesiz Badem Kreması 315g 3’lü Paket\n\n🛍️Sepet indirimi➕TADIM100 koduyla\n\n'
          '413 TL ye düşüyor (137 TL/Adet)\n\n✅Aramaya Badem yazınız')
GIGABYTE_NAME = ('GIGABYTE GAMING A18 Slim AMD Ryzen 7 260 (8C/16T, 5.1GHz Turbo, 16MB L3 cache) '
                 'RTX 5070 (8GB, 85W + Mux) 16GB/1TB (1x16GB 5600MHz DDR5 RAM, 1x RAM ve 1x Gen4 M.2 SSD yuvası boşta) '
                 '16” 165Hz 3ms %100DCI P3 300nit QHD IPS ekran, 76Wh pil, tek bölge RGB klavye, 2,8kg ağırlık, 150W adaptör, W11H')
GIGABYTE_PREFIX = "⚡ 87500₺ (🎉 Ziraat Bankkart, Axess ve Paraf'a peşin fiyatına 12 taksit + 💥 %3 indirim) "
GIGABYTE = GIGABYTE_PREFIX + GIGABYTE_NAME + '\n\n📌 Satıcı Amazon ve taksit imkanları çok iyi.\n\n💥 Bu fiyat uzun sürmez!'


def msg(text, entities=(), buttons=()):
    markup = SimpleNamespace(rows=[SimpleNamespace(buttons=buttons)]) if buttons else None
    return SimpleNamespace(message=text, entities=list(entities), media=None, reply_markup=markup)

class ParsingTests(unittest.TestCase):
    def test_reported_fropie(self):
        title, info = pf.analyze(FROPIE)
        self.assertIn('%100 Yer Fıstığı', title)
        self.assertEqual((info.value, info.confidence, info.reason), (101, 'medium', 'multi_unit'))
        self.assertEqual(pf.extract_price('Ürün\n🏷 229 TL — sepette indirim kodunu kullanın').value, 229)
        self.assertNotIn('%50', pf.extract_title('Ürün ABC 500 ml - %50 indirim'))

    def test_reported_servet_total_is_not_unit(self):
        title, info = pf.analyze(SERVET)
        self.assertEqual((info.value, info.confidence), (413, 'high'))
        self.assertIn('Servet', title)
        self.assertEqual(pf.extract_price('Ürün\n🏷 413 TL (137 TL/Adet)').value, 413)
        self.assertEqual(pf.extract_price('Ürün\n🏷 413 TL\ntanesi 137 TL').value, 137)

    def test_linked_name_utf16_and_no_entity_fallback(self):
        entity = types.MessageEntityTextUrl(offset=bot.utf16_length(GIGABYTE_PREFIX),
                                           length=bot.utf16_length(GIGABYTE_NAME), url='https://example.com/urun')
        obj = msg(GIGABYTE, [entity])
        self.assertTrue(bot.linked_product_title(obj).startswith('GIGABYTE GAMING A18'))
        self.assertTrue(bot._search_query(obj).startswith('GIGABYTE GAMING A18'))
        self.assertTrue(bot.smart_dedup_title(obj).startswith('GIGABYTE GAMING A18'))
        with patch.object(bot, 'SMART_HEADER', True):
            out = bot.compose_message(obj)
        self.assertTrue(out['header'].startswith('₺87.500 — GIGABYTE GAMING A18'), out['header'])
        self.assertTrue(pf.extract_title(GIGABYTE).startswith('GIGABYTE GAMING A18'))
        self.assertEqual(pf.extract_price(GIGABYTE).value, 87500)
        # A generic CTA with a model number is not a product hyperlink.
        cta = 'FIRSATA GİT'
        self.assertFalse(bot.linked_product_title(msg(cta, [types.MessageEntityTextUrl(
            offset=0, length=bot.utf16_length(cta), url='https://example.com/urun')])))

    def test_only_remove_button_when_its_url_is_visible(self):
        button = SimpleNamespace(text='Fırsata Git', url='https://example.com/urun')
        obj = msg('NIVEA Kadın Roll On 50ml\n268 TL', buttons=[button])
        keyboard = bot.build_inline_keyboard(obj, body_text='🔗 https://example.com/urun')
        self.assertNotIn('example.com/urun', str(keyboard))
        self.assertIn('Google Alışveriş', str(keyboard))
        keyboard = bot.build_inline_keyboard(obj, body_text='NIVEA')
        self.assertIn('example.com/urun', str(keyboard))

    def test_upgrade_refuses_used_and_conflicting_asin(self):
        from time import time
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        previous = msg(title + '\n🏷 79 TL')
        offer = bot.offer_terms(previous)
        self.assertFalse(bot.plain_offer_for_upgrade(msg(title + '\n🏷 79 TL\nİkinci El'), offer))
        entry = {'offer': offer, 'plain_offer': True, 'first_seen': time(), 'title': title,
                 'editable': 'bot', 'bot_message_id': 123, 'product_asins': {'B09RMSLVS8'}}
        used = msg(title + '\nFiyat: 79 TL / 13 adet alımda 71 TL\nİkinci El')
        self.assertFalse(bot.is_referenced_multibuy_upgrade(entry, title, used, bot.offer_terms(used), time()))
        strike_text = title + '\nFiyat: 79 TL / 13 adet alımda 71 TL'
        strike = types.MessageEntityStrike(offset=bot.utf16_length(strike_text[:strike_text.index('79')]), length=5)
        struck = msg(strike_text, [strike])
        self.assertFalse(bot.is_referenced_multibuy_upgrade(entry, title, struck, bot.offer_terms(struck), time()))
