import unittest

from core import (
    confirmation,
    discover_release_url,
    parse_core_pce_mom,
    scenario_for_actual,
)


class ParserTests(unittest.TestCase):
    def test_monthly_value_is_not_annual_value(self) -> None:
        document = """
        <p>From the preceding month, the PCE price index increased 0.1 percent.
        Excluding food and energy, the PCE price index increased 0.2 percent.</p>
        <p>From the same month one year ago, the PCE price index increased 3.6 percent.
        Excluding food and energy, the PCE price index increased 3.3 percent.</p>
        """
        self.assertEqual(parse_core_pce_mom(document), 0.2)

    def test_decrease(self) -> None:
        document = """
        <p>From the preceding month, the PCE price index decreased 0.2 percent.
        Excluding food and energy, the PCE price index decreased by 0.1 percent.</p>
        """
        self.assertEqual(parse_core_pce_mom(document), -0.1)

    def test_unchanged(self) -> None:
        document = """
        <p>From the preceding month, the PCE price index increased 0.1 percent.
        Excluding food and energy, the PCE price index was unchanged.</p>
        """
        self.assertEqual(parse_core_pce_mom(document), 0.0)

    def test_table_fallback(self) -> None:
        document = """
        <table><tr><td>PCE price index excluding food and energy</td>
        <td>0.1</td><td>0.3</td></tr></table>
        """
        self.assertEqual(parse_core_pce_mom(document), 0.3)

    def test_rss_discovery(self) -> None:
        document = """
        <rss><item><title><![CDATA[Personal Income and Outlays, July 2026]]></title>
        <link>https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026</link>
        </item></rss>
        """
        self.assertEqual(
            discover_release_url(document, "Personal Income and Outlays, July 2026"),
            "https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026",
        )


class ScenarioTests(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(scenario_for_actual(0.1).bullish_low, 65)
        self.assertEqual(scenario_for_actual(0.2).bullish_low, 52)
        self.assertEqual(scenario_for_actual(0.3).bearish_low, 68)
        self.assertEqual(scenario_for_actual(0.4).bearish_high, 80)

    def test_confirmation(self) -> None:
        scenario = scenario_for_actual(0.3)
        self.assertEqual(confirmation(scenario, -0.6, 2.1)[0], "CONFERMA")
        self.assertEqual(confirmation(scenario, 0.4, 1.5)[0], "SMENTITA / WHIPSAW")
        self.assertEqual(confirmation(scenario, 0.05, 0.8)[0], "INDECISA")


if __name__ == "__main__":
    unittest.main()
