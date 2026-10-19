"""Unit tests for DTEK client parsing helpers."""

from __future__ import annotations

import unittest

from component_loader import load_component_module

dtek_client = load_component_module("dtek_client")


class ParseHouseDataTests(unittest.TestCase):
    """Regression tests for normalized outage payloads."""

    def test_malformed_payload_does_not_confirm_power(self):
        for payload in ({}, {"data": []}, {"data": {"12А": "broken"}}, {"data": {"12А": [None]}}, {"data": {"12А": {}}}):
            with self.subTest(payload=payload), self.assertRaises(dtek_client.DTEKApiError):
                dtek_client.DTEKClient.parse_house_data(payload, "12А")

    def test_group_array_order_does_not_change_primary(self):
        first = dtek_client.DTEKClient.parse_house_data({"data": {"12А": {"sub_type_reason": ["GPV5.1", "GPV3.1"]}}}, "12А")
        second = dtek_client.DTEKClient.parse_house_data({"data": {"12А": {"sub_type_reason": ["GPV3.1", "GPV5.1"]}}}, "12А")
        self.assertEqual(first["schedule_groups"], second["schedule_groups"])
        self.assertEqual(first["primary_schedule_group"], second["primary_schedule_group"])

    def test_planned_repairs_without_description_are_not_reported_as_power_on(self):
        for code in ("1", 1):
            for description in ("", "  ", None):
                with self.subTest(code=code, description=description):
                    parsed = dtek_client.DTEKClient.parse_house_data({"data": {"12А": {
                        "type": code, "sub_type": description,
                        "start_date": "08:00 19.10.2026",
                        "end_date": "19.10.2026 17:00",
                        "sub_type_reason": ["GPV5.1", "GPV3.1"],
                    }}}, "12А")
                    self.assertFalse(parsed["power_on"])
                    self.assertEqual(parsed["outage_type"], "planned")
                    self.assertEqual(parsed["outage_description"], "Планові ремонтні роботи")
                    self.assertEqual(parsed["outage_count"], 1)
                    self.assertEqual(parsed["schedule_groups"], ["GPV3.1", "GPV5.1"])
                    self.assertEqual(parsed["outage_start"].hour, 8)
                    self.assertEqual(parsed["outage_end"].hour, 17)
                    self.assertEqual(str(parsed["outage_start"].tzinfo), "Europe/Kyiv")

    def test_empty_planned_record_does_not_create_an_outage(self):
        for empty in ("", "  ", None):
            with self.subTest(empty=empty):
                parsed = dtek_client.DTEKClient.parse_house_data({"data": {"12А": {
                    "type": "1", "sub_type": empty,
                    "start_date": empty, "end_date": empty,
                    "sub_type_reason": ["GPV3.1"],
                }}}, "12А")
                self.assertTrue(parsed["power_on"])
                self.assertEqual(parsed["outage_count"], 0)
                self.assertEqual(parsed["schedule_groups"], ["GPV3.1"])

    def test_planned_repairs_allow_an_unknown_end(self):
        parsed = dtek_client.DTEKClient.parse_house_data({"data": {"12А": {
            "type": "1", "start_date": "08:00 19.10.2026",
        }}}, "12А")
        self.assertEqual(parsed["outage_type"], "planned")
        self.assertIsNone(parsed["outage_end"])

    def test_malformed_undocumented_repairs_do_not_confirm_power(self):
        for fields in (
            {"start_date": "invalid"},
            {"start_date": []},
            {"end_date": 123},
            {"sub_type": ["invalid"]},
            {"start_date": "17:00 19.10.2026", "end_date": "08:00 19.10.2026"},
        ):
            with self.subTest(fields=fields), self.assertRaises(dtek_client.DTEKApiError):
                dtek_client.DTEKClient.parse_house_data(
                    {"data": {"12А": {"type": "1", **fields}}}, "12А"
                )

    def test_stabilization_has_priority_over_undocumented_repairs(self):
        parsed = dtek_client.DTEKClient.parse_house_data({"data": {"12А": [
            {"type": "1", "start_date": "08:00 19.10.2026", "sub_type_reason": ["GPV3.1"]},
            {"type": "2", "sub_type": "Стабілізаційне відключення", "sub_type_reason": ["GPV5.1"]},
        ]}}, "12А")
        self.assertEqual(parsed["outage_type"], "stabilization")
        self.assertEqual(parsed["outage_count"], 2)
        self.assertEqual(parsed["schedule_groups"], ["GPV3.1", "GPV5.1"])

    def test_parse_house_data_keeps_all_groups_and_primary_group(self) -> None:
        """Multiple outages should preserve all groups and pick the severe outage."""
        api_response = {
            "updateTimestamp": "09.03.2026 08:05",
            "data": {
                "12А": [
                    {
                        "sub_type": "Планове відключення",
                        "start_date": "08:00 09.03.2026",
                        "end_date": "10:00 09.03.2026",
                        "sub_type_reason": ["GPV5.1", "GPV5.2"],
                        "type": "1",
                    },
                    {
                        "sub_type": "Екстрене відключення",
                        "start_date": "09:00 09.03.2026",
                        "end_date": "11:00 09.03.2026",
                        "sub_type_reason": ["GPV5.3"],
                        "type": "2",
                    },
                ]
            },
        }

        parsed = dtek_client.DTEKClient.parse_house_data(api_response, "12а")

        self.assertFalse(parsed["power_on"])
        self.assertEqual(parsed["outage_type"], "emergency")
        self.assertEqual(parsed["outage_description"], "Екстрене відключення")
        self.assertEqual(parsed["schedule_groups"], ["GPV5.1", "GPV5.2", "GPV5.3"])
        self.assertEqual(parsed["primary_schedule_group"], "GPV5.1")
        self.assertEqual(parsed["outage_count"], 2)

    def test_parse_schedule_from_html_handles_nested_objects(self) -> None:
        """HTML parser should extract both schedule objects without greedy overmatch."""
        html = """
        <script>
        DisconSchedule.fact = {"update":"09.03.2026 08:00","data":{"1741471200":{"GPV5.1":{"9":"no"}}}};
        const ignored = {"outer":{"inner":"value"}};
        DisconSchedule.preset = {"updateFact":"09.03.2026 08:00","data":{"GPV5.1":{"1":{"9":"maybe","10":"msecond"}}}};
        </script>
        """

        parsed = dtek_client._parse_schedule_from_html(html)

        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed["fact"]["data"]["1741471200"]["GPV5.1"]["9"],
            "no",
        )
        self.assertEqual(
            parsed["preset"]["data"]["GPV5.1"]["1"]["10"],
            "msecond",
        )


if __name__ == "__main__":
    unittest.main()
