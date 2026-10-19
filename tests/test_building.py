"""Public directory matches must not guess a building from an apartment string."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from component_loader import load_component_module

mod = load_component_module("building")


class BuildingTests(unittest.IsolatedAsyncioTestCase):
    def provider(self):
        return SimpleNamespace(
            provider_id="dtek_oem",
            list_localities=AsyncMock(return_value=["Одеса"]),
            list_streets=AsyncMock(return_value=["дорога Прикладна"]),
            list_houses=AsyncMock(return_value=["12А", "12Б"]),
        )

    async def test_canonical_public_address_excludes_apartment(self):
        suggestion = mod.building_suggestion("м. Одеса, дор. Прикладна буд. 12а, к.58")
        address = await mod.resolve_building(self.provider(), suggestion)
        self.assertEqual(address.parts, ("Одеса", "дорога Прикладна", "12А"))
        self.assertNotIn("58", str(suggestion))

    async def test_regional_street_and_settlement_types_match_without_changing_public_spelling(
        self,
    ):
        # Synthetic addresses with types observed in the public regional directories.
        cases = [
            (
                "dtek_kem",
                "місто Київ",
                "м. Київ",
                "проспект Прикладний",
                "просп. Прикладний",
            ),
            (
                "dtek_krem",
                "м. Бровари",
                "м. Бровари",
                "бульвар Прикладний",
                "бул. Прикладний",
            ),
            (
                "dtek_dnem",
                "Дніпро",
                "м. Дніпро",
                "провулок Прикладний",
                "пров. Прикладний",
            ),
            (
                "dtek_krem",
                "село Прикладне",
                "с. Прикладне",
                "площа Прикладна",
                "пл. Прикладна",
            ),
            (
                "dtek_dnem",
                "селище Прикладне",
                "с-ще Прикладне",
                "узв. Прикладний",
                "узвіз Прикладний",
            ),
            (
                "dtek_krem",
                "селище міського типу Прикладне",
                "смт Прикладне",
                "вулиця Прикладна",
                "вул. Прикладна",
            ),
            (
                "dtek_krem",
                "хутір Прикладний",
                "хут. Прикладний",
                "пр-т Прикладний",
                "просп. Прикладний",
            ),
        ]
        for pid, city, canonical_city, street, canonical_street in cases:
            with self.subTest(provider=pid, city=city):
                provider = self.provider()
                provider.provider_id = pid
                provider.list_localities.return_value = [canonical_city]
                provider.list_streets.return_value = [canonical_street]
                for separator, house_label in [(" ", "буд."), (", ", "будинок")]:
                    text = f"{city}, {street}{separator}{house_label} 12а, кв.58"
                    suggestion = mod.building_suggestion(text)
                    address = await mod.resolve_building(provider, suggestion)
                    self.assertEqual(
                        address.parts, (canonical_city, canonical_street, "12А")
                    )
                    self.assertEqual(address.provider_id, pid)
                    self.assertNotIn("58", str(suggestion))

    async def test_normalization_preserves_address_type_district_and_corpus(self):
        mismatches = [
            ("city", "село Прикладне", "селище Прикладне"),
            ("city", "с. Прикладне", "Прикладне"),
            ("city", "смт Прикладне", "с-ще Прикладне"),
            ("city", "с. Прикладне (Північний р-н)", "с. Прикладне (Південний р-н)"),
            ("street", "вул. Прикладна", "площа Прикладна"),
            ("street", "пров. Прикладний", "просп. Прикладний"),
            ("street", "вул. Прикладна", "Дарницький р-н, вул. Прикладна"),
            ("house_num", "12/1", "12"),
        ]
        for key, value, choice in mismatches:
            with self.subTest(key=key, value=value):
                self.assertIsNone(mod.unique_choice(value, [choice], key))
        self.assertEqual(
            mod.building_suggestion(
                "м. Київ, Дарницький р-н, вул. Прикладна буд. 12, кв.1"
            ),
            {},
        )
        self.assertEqual(
            mod.building_suggestion("м. Київ, вул. Прикладна буд. 12 корпус 2, кв.1"),
            {},
        )
        self.assertIsNone(
            mod.unique_choice(
                "провулок Прикладний",
                ["пров. Прикладний", "провулок Прикладний"],
                "street",
            )
        )

    async def test_unknown_format_street_and_suffix_never_fall_back(self):
        for text in [
            "Одеса, Прикладна 12А, 58",
            "Одеса, дор. Прикладна буд. 12А, корпус 2, кв.8",
            "Одеса, дор. Інша буд. 12А",
            "Одеса, дор. Прикладна буд. 12",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(
                    await mod.resolve_building(
                        self.provider(), mod.building_suggestion(text)
                    )
                )

    async def test_duplicate_normalized_choices_are_ambiguous_even_with_exact_match(
        self,
    ):
        p = self.provider()
        p.list_localities.return_value = ["Одеса", "м. Одеса"]
        self.assertIsNone(
            await mod.resolve_building(
                p, mod.building_suggestion("Одеса, дор. Прикладна буд. 12А")
            )
        )
        p.list_streets.assert_not_awaited()
        p = self.provider()
        p.list_houses.return_value = ["12А", "12а"]
        self.assertIsNone(
            await mod.resolve_building(
                p, mod.building_suggestion("Одеса, дор. Прикладна буд. 12А")
            )
        )

    async def test_other_operator_identity_and_house_letter_remain_distinct(self):
        p = self.provider()
        p.provider_id = "dtek_krem"
        address = await mod.resolve_building(
            p, mod.building_suggestion("Одеса, дор. Прикладна буд. 12А")
        )
        self.assertEqual(address.provider_id, "dtek_krem")
        self.assertEqual(mod.address_unique_id(address), address.identity)
        self.assertIsNone(mod.unique_choice("12A", ["12А"], "house_num"))
