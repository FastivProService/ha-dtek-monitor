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
