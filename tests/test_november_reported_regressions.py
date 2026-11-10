"""User-reported examples and boundary cases; all tests are offline."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import price_filter as pf
from telethon.tl import functions, types

CLEAR_NAME = ('Clear Women Kil Terapisi Arınmış Ve Yumuşak Saçlar Kepeğe Karşı Etkili '
              'Şampuan 350 Ml(1) Clear Men Kepeğe Karşı Etkili Ş')
CLEAR = f'{CLEAR_NAME}\n\n🏷️ 224 TL 🤖\n\n📂 Kozmetik, Kişisel Bakım\n\n🛍️ Amazon\n\n🔗 https://onu.al/fizd'
TRUST = ('Trust GXT278 Dizüstü Bilgisayar İçin Soğutucu Fan | Kırmızı ışık, USB güç kablosu, '
         '3 pozisyonlu soğutma pedi, yüksekliği ayarlanabilir dizüstü bilgisayar soğutucu\n\n'
         '🏷️ 512,05 TL\n\n🆕 Yeni Fiyatı: 699 TL\n\n🛍️ Amazon #tanıtım\n\n📦 Depo Ürünü\n\n'
         '📌 Durumu: İkinci El: Yeni Gibi\n\n💬 Yorum: Ürün orijinal ambalajında gönderilir.\n\n🔗 FIRSATA GİT')
OTL = '🛒 OTL Technologies - SUPER MARIO TWS KULAKLIK\n\n💰 953.42 TL\n\n🛍 Amazon'


def message(text, buttons=(), entities=()):
    return SimpleNamespace(message=text, entities=list(entities), media=None,
                           reply_markup=SimpleNamespace(rows=[SimpleNamespace(buttons=list(buttons))]))


class ReportedExamples(unittest.TestCase):
    def test_clear_category_does_not_beat_long_name(self):
        self.assertEqual(pf.extract_title(CLEAR), CLEAR_NAME)
        self.assertEqual(pf.extract_price(CLEAR).value, 224)
        self.assertEqual(pf.extract_title('📂 Kozmetik, Kişisel Bakım\n🏷 224 TL'), '')
        self.assertEqual(pf.extract_title('Clear Şampuan\n📂 Kozmetik Bakım\n🏷 224 TL'), 'Clear Şampuan')
        self.assertEqual(bot._search_query(message('📂 Kozmetik, Kişisel Bakım\n🏷 224 TL')), '')
        self.assertEqual(pf.extract_title('Kozmetik Kişisel Bakım Seti 350 ml\n📂 Kozmetik'),
                         'Kozmetik Kişisel Bakım Seti 350 ml')

    def test_used_offer_and_new_reference(self):
        title, price = pf.analyze(TRUST)
        self.assertEqual(title, 'Trust GXT278 Dizüstü Bilgisayar İçin Soğutucu Fan')
        self.assertEqual((price.value, price.confidence), (512.05, 'high'))
        with patch.object(bot, 'SMART_HEADER', True):
            self.assertTrue(bot.compose_message(message(TRUST))['text'].startswith(
                '₺512 — Trust GXT278 Dizüstü Bilgisayar İçin Soğutucu Fan'))
        # Without a clearly marked used item, two unrelated prices remain ambiguous.
        self.assertEqual(pf.extract_price('Ürün\n🏷️ 512,05 TL\n🆕 Yeni Fiyatı: 699 TL').confidence, 'low')
        self.assertEqual(pf.extract_price('Ürün\n🆕 Yeni Fiyatı: 699 TL').value, 699)
        ambiguous = pf.extract_price('Ürün\n🏷 512 TL\n🏷 530 TL\nYeni Fiyatı: 699 TL\nDurum: İkinci El')
        self.assertEqual(ambiguous.confidence, 'low')
        # Struck-through offer is not evidence for demoting the new reference.
        text = 'Ürün\n🏷 512 TL\nYeni Fiyatı: 699 TL\nİkinci El'
        start = text.index('512')
        self.assertEqual(pf.extract_price(text, [(start, start + 6)]).value, 699)

    def test_button_link_in_notification_and_price_precision(self):
        button = SimpleNamespace(text='Fırsata Git', url='https://example.com/deal')
        msg = message(OTL, [button])
        with patch.object(bot, 'SMART_HEADER', True):
            result = bot.compose_message(msg, link_kinds=bot.link_kinds_for('smart', bot=True),
                                         message_link='https://t.me/depoindirim/88707')
        self.assertTrue(result['text'].startswith('₺953 — OTL Technologies - SUPER MARIO TWS KULAKLIK'), result['text'])
        self.assertEqual(pf.extract_price(OTL).value, 953.42)
        self.assertIn('💰 953.42 TL', result['text'])
        self.assertIn('🔗 Fırsata Git: https://example.com/deal', result['text'])
        self.assertIn('https://t.me/depoindirim/88707', result['text'])
        self.assertEqual(bot.build_link_appendix(message(OTL + '\nhttps://example.com/deal', [button]),
                                                  kinds=('button',)), '')
        self.assertEqual(bot.build_link_appendix(msg, kinds=bot.link_kinds_for('off', bot=True)), '')


class InviteResolution(unittest.IsolatedAsyncioTestCase):
    async def test_joined_invite_and_legacy_link(self):
        chat = types.Channel(id=1234567890, title='Özel', photo=types.ChatPhotoEmpty(), date=0)
        client = AsyncMock(return_value=types.ChatInviteAlready(chat=chat))
        for url in ('https://t.me/+Ab_C-12', 'https://t.me/joinchat/Ab_C-12'):
            peer_id, name, entity = await bot.resolve_chat(client, url)
            self.assertEqual((peer_id, name, entity), (-1001234567890, 'Özel', chat))
            self.assertIsInstance(client.call_args.args[0], functions.messages.CheckChatInviteRequest)
            self.assertEqual(client.call_args.args[0].hash, 'Ab_C-12')
        client.get_entity.assert_not_called()

    async def test_not_joined_peek_or_pending_never_joins(self):
        chat = types.Channel(id=123, title='Özel', photo=types.ChatPhotoEmpty(), date=0)
        for answer in (types.ChatInvite(title='Ücretli', photo=types.PhotoEmpty(id=1),
                                        participants_count=1, color=0, request_needed=True),
                       types.ChatInvitePeek(chat=chat, expires=None)):
            client = AsyncMock(return_value=answer)
            with self.assertRaises(ValueError):
                await bot.resolve_chat(client, 'https://t.me/+private')
            self.assertEqual(client.await_count, 1)
            self.assertIsInstance(client.call_args.args[0], functions.messages.CheckChatInviteRequest)

    async def test_private_numeric_id_refreshes_dialog_cache(self):
        chat = types.Channel(id=1234567890, title='Özel', photo=types.ChatPhotoEmpty(), date=0)
        client = SimpleNamespace(get_entity=AsyncMock(side_effect=[ValueError('uncached'), chat]),
                                 get_dialogs=AsyncMock())
        self.assertEqual((await bot.resolve_chat(client, -1001234567890))[0], -1001234567890)
        client.get_dialogs.assert_awaited_once()
        self.assertEqual(client.get_entity.await_count, 2)
