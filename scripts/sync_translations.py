"""Keep the Russian-language preference mapped to the Ukrainian translation."""

from pathlib import Path


def main():
    translations = Path(__file__).resolve().parents[1] / "custom_components/dtek_monitor/translations"
    (translations / "ru.json").write_bytes((translations / "uk.json").read_bytes())


if __name__ == "__main__":
    main()
