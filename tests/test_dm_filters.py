"""Regression coverage for independent, explainable personal-DM filter groups."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import preflight  # noqa: E402
import commandtext  # noqa: E402
import dm_filters  # noqa: E402
import price_filter  # noqa: E402


class DMDecisionTests(unittest.TestCase):
    def config(self, **overrides):
        result = {
            "dm_enabled": False,
            "dm_keywords": [],
            "dm_exclude_keywords": [],
            "dm_price_max": [],
            "dm_product_price_rules": [],
        }
        result.update(overrides)
        return result

    def test_dmdahil_matches_full_legacy_message_without_price_limit(self):
        config = self.config(dm_enabled=True, dm_keywords=["TCL"])
        self.assertTrue(commandtext.dm_should_send(config, "TCL 65C7K Google TV\nFiyat: 50.000 TL"))
        self.assertFalse(commandtext.dm_should_send(config, "LG 65 TV\nFiyat: 50.000 TL"))
        # The old phrase matcher remains message-wide for compatibility; the
        # independent title/price group below is intentionally title-scoped.
        self.assertTrue(commandtext.dm_should_send(config, "LG TV\nTCL için 500 TL kupon"))

    def test_dmfiyat_is_product_name_independent_and_inclusive(self):
        config = self.config(dm_price_max=["100"])
        self.assertTrue(commandtext.dm_should_send(config, "Herhangi bir ürün\n90 TL"))
        self.assertTrue(commandtext.dm_should_send(config, "Ampul\n100 TL"))
        self.assertFalse(commandtext.dm_should_send(config, "Herhangi bir ürün\n150 TL"))
        self.assertFalse(commandtext.dm_should_send(
            config, "TCL 65C7K TV\nFiyat: 50.000 TL",
        ))

    def test_dmharic_is_shared_by_dmdahil_and_dmfiyat(self):
        include = self.config(
            dm_enabled=True, dm_keywords=["TCL"], dm_exclude_keywords=["TCL"],
        )
        self.assertFalse(commandtext.dm_should_send(include, "TCL 65C7K\n50.000 TL"))

        price = self.config(dm_price_max=["100"], dm_exclude_keywords=["telefon"])
        self.assertFalse(commandtext.dm_should_send(price, "Telefon\n90 TL"))
        self.assertTrue(commandtext.dm_should_send(
            self.config(dm_price_max=["100"]), "Telefon\n90 TL",
        ))

    def test_title_and_price_rule_requires_both_conditions(self):
        config = self.config(dm_product_price_rules=["telefon < 100"])
        self.assertTrue(commandtext.dm_should_send(config, "Telefon\n90 TL"))
        self.assertTrue(commandtext.dm_should_send(config, "Akıllı telefon modeli\n100 TL"))
        self.assertFalse(commandtext.dm_should_send(config, "Telefon\n150 TL"))
        self.assertFalse(commandtext.dm_should_send(config, "Televizyon\n90 TL"))

    def test_title_price_rules_are_independent_from_legacy_groups_and_exclusion(self):
        config = self.config(
            dm_enabled=False,
            dm_keywords=["başka ürün"],
            dm_price_max=["0"],
            dm_exclude_keywords=["TCL"],
            dm_product_price_rules=["TCL < 30.000"],
        )
        decision = commandtext.dm_decision(config, "TCL 65C7K TV\n25.000 TL")
        self.assertTrue(decision.should_send)
        self.assertTrue(any(
            result.group == "dm_urun_fiyat" and result.matched
            for result in decision.results
        ))
        self.assertFalse(next(result for result in decision.results if result.group == "dmdahil").matched)
        self.assertFalse(next(result for result in decision.results if result.group == "dmfiyat").matched)
        self.assertTrue(next(result for result in decision.results if result.group == "dmharic").matched)

    def test_tcl_product_rule_uses_real_price_and_inclusive_boundary(self):
        config = self.config(dm_product_price_rules=["TCL < 30.000"])
        self.assertTrue(commandtext.dm_should_send(config, "TCL 65C7K\n25.000 TL"))
        self.assertTrue(commandtext.dm_should_send(config, "TCL 65C7K\n30.000 TL"))
        self.assertFalse(commandtext.dm_should_send(config, "TCL 65C7K\n35.000 TL"))
        self.assertTrue(commandtext.dm_should_send(
            self.config(dm_product_price_rules=["LG 75 < 45.000"]),
            "LG 75QNED TV\n45.000 TL",
        ))

    def test_multiple_title_price_rules_are_or_conditions(self):
        config = self.config(dm_product_price_rules=[
            "telefon < 100", "TCL < 30000", "LG 75 < 45000",
        ])
        decision = commandtext.dm_decision(config, "TCL 65C7K TV\n25.000 TL")
        self.assertTrue(decision.should_send)
        self.assertEqual(
            [r.rule for r in decision.results if r.group == "dm_urun_fiyat" and r.matched],
            ["tcl < 30000"],
        )
        self.assertFalse(commandtext.dm_should_send(
            config, "Akıllı telefon\n150 TL",
        ))

    def test_coupon_discount_and_campaign_amounts_are_not_product_price(self):
        messages = [
            "Telefon 1.299 TL\n100 TL kupon",
            "Telefon\nFiyat: 1.299 TL\nKuponla 100 TL indirim",
            "🔥 Kampanya\nTelefon\nYeni fiyat: 1.299 TL\nEski fiyat: 999 TL\n"
            "Kupon: 100 TL\nKargo: 49 TL\nTaksit: 12 x 108,25 TL",
        ]
        config = self.config(dm_product_price_rules=["telefon < 100"])
        for text in messages:
            with self.subTest(text=text):
                decision = commandtext.dm_decision(config, text)
                self.assertFalse(decision.should_send)
                self.assertIsNotNone(decision.price)
                self.assertEqual(decision.price.value, 1299)

    def test_struck_old_price_does_not_replace_the_real_sale_price(self):
        text = "Telefon\nEski: 999 TL\nYeni: 1.299 TL"
        start = text.index("999 TL")
        strike = [(start, start + len("999 TL"))]
        decision = commandtext.dm_decision(
            self.config(dm_product_price_rules=["telefon < 1000"]), text, strike,
        )
        self.assertFalse(decision.should_send)
        self.assertEqual(decision.price.value, 1299)
        self.assertTrue(commandtext.dm_should_send(
            self.config(dm_product_price_rules=["telefon < 1300"]), text, strike,
        ))

    def test_title_must_be_detected_before_new_rule_can_match(self):
        decision = commandtext.dm_decision(
            self.config(dm_product_price_rules=["telefon < 1000"]), "100 TL",
        )
        self.assertFalse(decision.should_send)
        result = next(item for item in decision.results if item.group == "dm_urun_fiyat")
        self.assertIn("tespit edilemedi", result.reason)

    def test_turkish_case_unicode_and_repeated_spaces_normalize(self):
        config = self.config(dm_product_price_rules=["İPHONE 16   < 1.300 TL"])
        decision = commandtext.dm_decision(config, "ＩＰＨＯＮＥ\u300016 Pro\n₺1.299,90")
        self.assertTrue(decision.should_send)
        self.assertEqual(decision.price.value, 1299.9)

    def test_multiple_money_formats_share_the_existing_threshold_parser(self):
        for raw, expected in [
            ("TCL < 30.000 TL", 30000),
            ("TCL < ₺30.000", 30000),
            ("TCL < 30 000 TRY", 30000),
            ("TCL < 30.000,50 TL", 30000.5),
            ("TCL < 1,2345", 1.2345),
            ("TCL < 0,001", 0.001),
        ]:
            with self.subTest(raw=raw):
                rule = dm_filters.parse_product_price_rule(raw)
                self.assertEqual(rule.threshold, expected)
                self.assertEqual(
                    dm_filters.parse_product_price_rule(rule.canonical).threshold,
                    expected,
                )

    def test_invalid_rule_and_price_expressions_fail_closed(self):
        for raw in [
            "telefon > 100", "telefon << 100", "telefon <", "< 100",
            "telefon < 100 TL kupon", "telefon < -100", "telefon < 100000001",
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    dm_filters.parse_product_price_rule(raw)
        self.assertFalse(commandtext.dm_should_send(
            self.config(dm_product_price_rules=["telefon < bozuk"]), "Telefon\n1 TL",
        ))

    def test_diagnostic_result_explains_every_group(self):
        decision = commandtext.dm_decision(
            self.config(
                dm_enabled=True, dm_keywords=["tcl"], dm_exclude_keywords=["yenilenmiş"],
                dm_price_max=["100"], dm_product_price_rules=["tcl < 30000"],
            ),
            "TCL 65C7K yenilenmiş\n25.000 TL",
        )
        groups = {result.group for result in decision.results}
        self.assertTrue({"dmdahil", "dmfiyat", "dmharic", "dm_urun_fiyat"} <= groups)
        include_result = next(result for result in decision.results if result.group == "dmdahil")
        self.assertFalse(include_result.matched)
        self.assertIn("dmharic engelledi", include_result.reason)

    def test_one_title_and_price_extraction_is_shared_across_many_rules(self):
        real_price = price_filter.extract_price
        real_title = price_filter.extract_title
        config = self.config(
            dm_price_max=["30000"],
            dm_product_price_rules=["TCL < 30000", "65C7K < 40000", "TV < 50000"],
        )
        with mock.patch.object(price_filter, "extract_price", wraps=real_price) as extract_price, \
             mock.patch.object(price_filter, "extract_title", wraps=real_title) as extract_title:
            commandtext.dm_decision(config, "TCL 65C7K TV\n25.000 TL")
        extract_price.assert_called_once()
        extract_title.assert_called_once()

    def test_parsed_title_and_price_are_reused_when_queue_rechecks_config(self):
        text = "TCL 65C7K TV\n25.000 TL"
        first = commandtext.dm_decision(
            self.config(dm_product_price_rules=["TCL < 30000"]), text,
        )
        with mock.patch.object(price_filter, "extract_price", side_effect=AssertionError("reparsed")):
            with mock.patch.object(price_filter, "extract_title", side_effect=AssertionError("retitled")):
                second = commandtext.dm_decision(
                    self.config(dm_product_price_rules=["TCL < 26000", "TV < 30000"]),
                    text, facts=first.facts,
                )
        self.assertTrue(second.should_send)

    def test_newly_enabled_product_rules_parse_missing_facts_during_revalidation(self):
        text = "TCL 65C7K TV\n25.000 TL"
        first = commandtext.dm_decision(
            self.config(dm_enabled=True, dm_keywords=["tcl"]), text,
        )
        self.assertTrue(first.should_send)
        self.assertFalse(first.facts.title_extracted)
        self.assertFalse(first.facts.price_extracted)
        with mock.patch.object(price_filter, "extract_price", wraps=price_filter.extract_price) as extract_price:
            with mock.patch.object(price_filter, "extract_title", wraps=price_filter.extract_title) as extract_title:
                second = commandtext.dm_decision(
                    self.config(dm_product_price_rules=["TCL < 30000"]),
                    text, facts=first.facts,
                )
        self.assertTrue(second.should_send)
        extract_price.assert_called_once()
        extract_title.assert_called_once()

    def test_facts_from_another_message_are_discarded_before_revalidation(self):
        first = commandtext.dm_decision(
            self.config(dm_product_price_rules=["TCL < 30000"]),
            "TCL 65C7K TV\n25.000 TL",
        )
        text = "LG TV\n400 TL"
        with mock.patch.object(price_filter, "extract_price", wraps=price_filter.extract_price) as extract_price:
            with mock.patch.object(price_filter, "extract_title", wraps=price_filter.extract_title) as extract_title:
                second = commandtext.dm_decision(
                    self.config(dm_product_price_rules=["LG < 500"]), text,
                    facts=first.facts,
                )
        self.assertTrue(second.should_send)
        self.assertEqual(second.facts.source_text, text)
        self.assertEqual(second.title, "LG TV")
        self.assertEqual(second.price.value, 400)
        extract_price.assert_called_once()
        extract_title.assert_called_once()

    def test_facts_from_different_strike_spans_are_reparsed(self):
        text = "Telefon\n999 TL\n1.299 TL"
        config = self.config(dm_product_price_rules=["telefon < 1300"])
        unstruck = commandtext.dm_decision(config, text)
        self.assertFalse(unstruck.should_send)
        self.assertEqual(unstruck.price.value, 999)
        start = text.index("999 TL")
        strike = [(start, start + len("999 TL"))]
        with mock.patch.object(price_filter, "extract_price", wraps=price_filter.extract_price) as extract_price:
            struck = commandtext.dm_decision(config, text, strike, facts=unstruck.facts)
        self.assertTrue(struck.should_send)
        self.assertEqual(struck.price.value, 1299)
        extract_price.assert_called_once()

    def test_old_config_without_new_field_remains_valid_and_inactive(self):
        old = {"dm_enabled": False, "dm_keywords": [], "dm_exclude_keywords": [], "dm_price_max": []}
        self.assertFalse(commandtext.dm_should_send(old, "Telefon\n90 TL"))
        for config in (old, {**old, "dm_product_price_rules": []}):
            with self.subTest(config=config):
                problems = []
                preflight._check_config_flags_and_dm(config, problems)
                self.assertEqual(problems, [])

    def test_config_rejects_malformed_and_duplicate_product_price_rules(self):
        invalid_configs = [
            {"dm_product_price_rules": ["telefon > 100"]},
            {"dm_product_price_rules": ["telefon < 100", "TELEFON < 100 TL"]},
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                problems = []
                preflight._check_config_flags_and_dm(config, problems)
                self.assertTrue(any("dm_product_price_rules" in item for item in problems))


class DMProductPriceListTests(unittest.TestCase):
    field = "dm_product_price_rules"

    def test_new_rule_list_is_canonical_staged_and_deduplicated(self):
        config = {self.field: []}
        raw = "TCL < 30.000 TL, tcl < 30000, phone < 1.299,90 TL; LG 75 < 45.000"
        ok, candidate, added, note = commandtext.stage_telegram_list_change(config, self.field, "add", raw)
        self.assertTrue(ok, note)
        self.assertEqual(added, ["tcl < 30000", "phone < 1299,9", "lg 75 < 45000"])
        self.assertEqual(candidate[self.field], added)
        self.assertEqual(config[self.field], [])
        self.assertIn("zaten listede", note)

    def test_same_rule_in_different_case_spacing_and_currency_is_one_record(self):
        config = {self.field: ["tcl < 30000"]}
        ok, candidate, added, note = commandtext.stage_telegram_list_change(
            config, self.field, "add", "ＴＣＬ   < ₺30.000",
        )
        self.assertFalse(ok)
        self.assertEqual(candidate, config)
        self.assertEqual(added, [])
        self.assertIn("zaten listede", note)

    def test_invalid_rules_are_rejected_and_valid_rules_can_be_staged(self):
        ok, candidate, added, note = commandtext.stage_telegram_list_change(
            {}, self.field, "add", "Telefon < 100; TV > 100; TCL < 30.000",
        )
        self.assertTrue(ok, note)
        self.assertEqual(added, ["telefon < 100", "tcl < 30000"])
        self.assertIn("geçersiz", note)
        for invalid in ["telefon > 100", "telefon < kupon", "< 100"]:
            self.assertFalse(commandtext.stage_telegram_list_change({}, self.field, "add", invalid)[0])

    def test_decimal_comma_is_not_split_from_a_single_rule(self):
        values = commandtext.split_product_price_rule_values("Telefon < 1.299,90 TL")
        self.assertEqual(values, ["Telefon < 1.299,90 TL"])
        ok, candidate, added, note = commandtext.stage_telegram_list_change(
            {}, self.field, "add", "Telefon < 1.299,90 TL",
        )
        self.assertTrue(ok, note)
        self.assertEqual(added, ["telefon < 1299,9"])

    def test_a_single_rule_can_be_removed_without_changing_others(self):
        config = {self.field: ["telefon < 100", "tcl < 30000", "lg 75 < 45000"]}
        ok, candidate, removed, _ = commandtext.stage_telegram_list_change(config, self.field, "remove", "2")
        self.assertTrue(ok)
        self.assertEqual(removed, ["tcl < 30000"])
        self.assertEqual(candidate[self.field], ["telefon < 100", "lg 75 < 45000"])
        self.assertEqual(config[self.field], ["telefon < 100", "tcl < 30000", "lg 75 < 45000"])

    def test_a_rule_can_also_be_removed_by_normalized_full_expression(self):
        config = {self.field: ["telefon < 100", "tcl < 30000"]}
        ok, candidate, removed, _ = commandtext.stage_telegram_list_change(
            config, self.field, "remove", "TCL < 30.000 ₺",
        )
        self.assertTrue(ok)
        self.assertEqual(removed, ["tcl < 30000"])
        self.assertEqual(candidate[self.field], ["telefon < 100"])

    def test_remove_supports_several_numbers_without_treating_them_as_prices(self):
        config = {self.field: ["telefon < 100", "tcl < 30000", "lg 75 < 45000"]}
        ok, candidate, removed, _ = commandtext.stage_telegram_list_change(config, self.field, "remove", "1, 3")
        self.assertTrue(ok)
        self.assertEqual(removed, ["telefon < 100", "lg 75 < 45000"])
        self.assertEqual(candidate[self.field], ["tcl < 30000"])

    def test_rule_prompt_and_status_aliases_are_clear(self):
        prompt = commandtext.build_list_value_prompt("add", self.field, {self.field: []})
        self.assertIn("telefon < 100", prompt)
        self.assertIn("gerçek ürün fiyatı", prompt)
        self.assertEqual(commandtext.resolve_telegram_list("ürün adı < fiyat"), self.field)
        self.assertEqual(commandtext.resolve_telegram_list("8"), self.field)
        self.assertEqual(commandtext.resolve_telegram_list("7"), commandtext.WEBTAKIP_FIELD)


if __name__ == "__main__":
    unittest.main()
